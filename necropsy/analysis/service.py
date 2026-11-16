"""`AnalysisService.analyze()` — the single entry point for analysis (E8).

The Flask routes and the MCP tools are adapters over this one method. A route or
tool handler that contains fetch, normalization, scoring, prompt or cache logic is
a defect, which is the whole reason this module exists.

The ten steps run in the order design section 2.1 declares, and the order is the
design, not an implementation detail:

    1. canonicalize            0 calls; bad input never reaches the network
    2. rate-limit floor        0 calls; a refused analysis spends nothing
    3. resolve head SHA        call 1; the cache key is undefined without it
    4. CACHE LOOKUP            hit returns here, having spent 1 call and 0 tokens
    5. guards + fetch          calls 2-9, size and truncation caps in between
    6. normalize               pure; `as_of` frozen once, here
    7. score                   pure; no clock, no I/O
    8. narrate                 exactly one LLM call, after the miss (E1)
    9. assemble                Analysis
   10. cache write             one transaction: analysis, exhibit, llm_calls

Two of those placements carry rules that are easy to lose and expensive to lose.
The cache lookup at step 4 is what makes "no LLM call without a cache miss"
structural rather than aspirational: the narrator is only reachable from inside
the miss branch, so a cache hit cannot pay for tokens even by accident (E1,
Property 11). And `as_of` is frozen once at step 6 and threaded through
everything downstream, so no later stage can consult a clock and make a stored
analysis drift (E6, Property 2).

Failure is per section, never per analysis (E10). A narrative that cannot be
produced leaves the facts and the scorecard intact and marks itself unavailable.
Only a failure that invalidates the facts themselves — an unreachable
repository, a cap breach, a timeout — fails the analysis, and those write no row.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from necropsy import ANALYSIS_SCHEMA_VERSION
from necropsy.analysis import fetch as fetch_stage
from necropsy.analysis.normalize import normalize
from necropsy.config import Config, load_config
from necropsy.errors import (
    AnalysisTimeout,
    NecropsyError,
    OfflineCacheMiss,
    wrap_unexpected,
)
from necropsy.github.client import BudgetScope, GitHubClient, NoNetworkGitHubClient
from necropsy.github.endpoints import decode_base64_content
from necropsy.github.urls import canonicalize
from necropsy.llm.client import LlmClient, build_client
from necropsy.llm.prompt import Excerpts
from necropsy.llm.validate import NarrationResult, narrate
from necropsy.models.analysis import Analysis, Provenance, RepoRef
from necropsy.models.facts import NormalizedFacts
from necropsy.models.scorecard import Scorecard, ScorecardInput
from necropsy.scoring import score
from necropsy.store import exhibits, llm_calls
from necropsy.store.cache import AnalysisCache, CacheResult, CacheStatus
from necropsy.store.db import Database

log = logging.getLogger("necropsy.analysis.service")

#: Stage names reported to a listener, in order. The browser's progress copy is
#: driven by these rather than by a timer, so the text is honest (design 8.3).
STAGES: tuple[str, ...] = (
    "canonicalizing",
    "resolving_head_commit",
    "checking_cache",
    "fetching_metadata",
    "reading_file_tree",
    "normalizing",
    "scoring",
    "writing_the_plaque",
    "assembling",
    "caching",
)

#: Called with each stage name as it begins. Default does nothing.
StageListener = Callable[[str], None]


def _ignore_stage(_: str) -> None:
    return None


@dataclass
class _Deadline:
    """The hard timeout, checked at every stage boundary (R-3.5)."""

    seconds: int
    started: float

    def check(self) -> None:
        elapsed = time.monotonic() - self.started
        if elapsed > self.seconds:
            raise AnalysisTimeout(
                "The analysis ran longer than NECROPSY allows and was abandoned. "
                "Nothing partial was stored.",
                details={
                    "elapsed_seconds": round(elapsed, 2),
                    "timeout_seconds": self.seconds,
                },
            )

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


class AnalysisService:
    """The pipeline. One instance per process; the cache's single-flight map is
    in-process state, so sharing the instance is what makes coalescing work."""

    def __init__(
        self,
        db: Database,
        *,
        config: Config | None = None,
        github: GitHubClient | None = None,
        llm: LlmClient | None = None,
        cache: AnalysisCache | None = None,
        now: Callable[[], datetime] | None = None,
        build_llm: bool = True,
    ) -> None:
        self.db = db
        self.config = config or load_config()
        self.cache = cache or AnalysisCache(db, schema_version=ANALYSIS_SCHEMA_VERSION)
        self._github = github
        #: `None` is a supported state: narration falls back to templates (R-13.8).
        self.llm = llm if llm is not None else (build_client(self.config) if build_llm else None)
        self._now = now or (lambda: datetime.now(UTC))
        #: Narration accounting waiting to be written, keyed by `cache_key`. The
        #: cache write returns a row, not a narrative, so the attempts have to be
        #: parked somewhere between step 8 and step 10. Keyed rather than held in
        #: a single slot, because two analyses can be in flight at once (E14).
        self._narrations: dict[str, NarrationResult] = {}

    # -- collaborators ------------------------------------------------------
    @property
    def github(self) -> GitHubClient:
        if self._github is None:
            self._github = GitHubClient(self.db, self.config)
        return self._github

    def close(self) -> None:
        if self._github is not None:
            self._github.close()

    # -- the entry point ----------------------------------------------------
    def analyze(
        self,
        url: str,
        *,
        force_refresh: bool = False,
        allow_network: bool = True,
        on_stage: StageListener = _ignore_stage,
    ) -> CacheResult:
        """Analyze `url`, serving from cache whenever the cache is valid.

        Returns `CacheResult`, which carries the `Analysis` and the `cache_status`
        that R-1.4 requires in every response. The design's component table writes
        the return type as `Analysis`; an `Analysis` alone cannot say whether it
        was computed or served, and `cache_status` is never persisted (R-2.4), so
        the status has to travel with the result rather than inside it.

        Raises:
            NecropsyError: any member of the closed taxonomy in `errors.py`.
                Nothing else escapes: an unmapped exception becomes
                `INTERNAL_ERROR` with a correlation id (E8).
        """
        deadline = _Deadline(self.config.analysis_timeout_seconds, time.monotonic())
        try:
            return self._analyze(
                url,
                force_refresh=force_refresh,
                allow_network=allow_network,
                on_stage=on_stage,
                deadline=deadline,
            )
        except NecropsyError:
            raise
        except Exception as exc:  # noqa: BLE001 - the boundary that closes the taxonomy
            log.exception("unmapped failure while analyzing a repository")
            raise wrap_unexpected(exc) from exc

    def _analyze(
        self,
        url: str,
        *,
        force_refresh: bool,
        allow_network: bool,
        on_stage: StageListener,
        deadline: _Deadline,
    ) -> CacheResult:
        # -- 1. canonicalize: zero calls, however bad the input (R-4.4) -----
        on_stage("canonicalizing")
        ref = canonicalize(url)

        if not allow_network:
            return self._cached_only(ref)

        # -- 2. the rate-limit floor, before the first call (R-14.3) --------
        scope = self.github.begin_analysis()

        # -- 3. resolve the head SHA: the one call a hit pays for -----------
        on_stage("resolving_head_commit")
        deadline.check()
        head = fetch_stage.resolve_head(scope, ref)

        # -- 4. the cache question ------------------------------------------
        on_stage("checking_cache")
        result = self.cache.get_or_compute(
            ref.canonical_url,
            head.sha,
            lambda: self._pipeline(
                ref, scope, head, on_stage=on_stage, deadline=deadline
            ),
            force_refresh=force_refresh,
        )

        if result.status is CacheStatus.MISS and result.row is not None:
            self._publish(result, ref)
        return result

    # -- steps 5 to 9: the cache-miss body ---------------------------------
    def _pipeline(
        self,
        ref: RepoRef,
        scope: BudgetScope,
        head: fetch_stage.HeadCommit,
        *,
        on_stage: StageListener,
        deadline: _Deadline,
    ) -> tuple[Analysis, dict[str, Any]]:
        """Everything a miss costs. Reached only after the step-4 lookup missed."""
        # -- 5. fetch, with the size and truncation guards interleaved ------
        on_stage("fetching_metadata")
        raw = fetch_stage.fetch(
            ref, scope, head, config=self.config, deadline=deadline.check
        )
        on_stage("reading_file_tree")

        # -- 6. normalize: `as_of` is frozen here and nowhere else ----------
        on_stage("normalizing")
        deadline.check()
        as_of = self._now()
        facts = normalize_payloads(raw, as_of, ref=ref, config=self.config)

        # -- 7. score: pure, no I/O -----------------------------------------
        on_stage("scoring")
        deadline.check()
        card = score(ScorecardInput.from_facts(facts))

        # -- 8. narrate: exactly one LLM call, plus at most one repair ------
        on_stage("writing_the_plaque")
        deadline.check()
        narration = narrate(
            self.llm,
            facts,
            card,
            excerpts=manifest_excerpts(raw, facts, self.config),
        )

        # -- 9. assemble -----------------------------------------------------
        on_stage("assembling")
        deadline.check()
        analysis = assemble(
            ref=ref,
            facts=facts,
            card=card,
            narration=narration,
            cache_key=self.cache.key_for(ref.canonical_url, head.sha),
            github_calls_used=scope.calls_used,
            duration_ms=deadline.elapsed_ms,
            created_at=as_of,
            schema_version=self.cache.schema_version,
        )
        on_stage("caching")
        self._narrations[analysis.cache_key] = narration
        return analysis, raw.to_mapping()

    # -- step 10 continued: presentation record and LLM accounting ---------
    def _publish(self, result: CacheResult, ref: RepoRef) -> None:
        """Point the exhibit at the new analysis and record the LLM attempts.

        Separate from the cache write because the cache write is the thing that
        must be atomic and racing-safe; an exhibit pointer and an accounting row
        are bookkeeping that must never be able to fail an analysis (E10).
        """
        row = result.row
        if row is None:  # pragma: no cover - callers check before calling
            return
        narration = self._narrations.pop(row.cache_key, None)
        try:
            with self.db.transaction() as conn:
                exhibits.upsert_exhibit(
                    conn,
                    repo_id=row.repo_id,
                    analysis_id=row.id,
                    slug=ref.slug,
                    plaque_title=f"{ref.owner}/{ref.name}",
                    is_living=result.analysis.narrative.living,
                )
                if narration is not None and narration.attempts:
                    llm_calls.record_attempts(
                        conn,
                        cache_key=row.cache_key,
                        attempts=narration.attempts,
                        analysis_id=row.id,
                    )
        except Exception:  # pragma: no cover - bookkeeping is not the product
            log.warning("could not update the exhibit record for %s", ref.display_url)

    # -- the zero-network path ---------------------------------------------
    def _cached_only(self, ref: RepoRef) -> CacheResult:
        """`allow_network=False`: serve what is stored, or say so (R-11.5, R-2.5).

        No SHA is resolved, so not a single call is made. This is the path the
        gallery, `get_exhibit` and `list_graveyard` take, and the reason a client
        that raises on use can be injected to prove it (Property 11).
        """
        found = self.cache.lookup_by_url(ref.canonical_url, stale_ok=True)
        analysis = found.analysis()
        if analysis is None:
            raise OfflineCacheMiss(
                "This repository has not been analyzed yet, and cached results only "
                "were requested, so NECROPSY made no network call.",
                details={"repo": ref.display_url, "github_calls_used": 0},
            )
        return CacheResult(status=CacheStatus.HIT, analysis=analysis, row=found.row)

    # -- reads --------------------------------------------------------------
    def cached_analysis(self, url: str, *, stale_ok: bool = True) -> Analysis | None:
        """Zero-network cache read by URL. Returns `None` on a miss."""
        ref = canonicalize(url)
        return self.cache.lookup_by_url(ref.canonical_url, stale_ok=stale_ok).analysis()


# --------------------------------------------------------------------------
# stage functions, exposed so the seed loader can reuse them without the
# network stages (R-17.3). Each is a thin, testable wrapper.
# --------------------------------------------------------------------------
def normalize_payloads(
    raw: fetch_stage.RawPayloads,
    as_of: datetime,
    *,
    ref: RepoRef | None = None,
    config: Config | None = None,
) -> NormalizedFacts:
    """Normalize with the configured excerpt caps.

    `normalize()` itself takes the caps as arguments rather than reading the
    environment, which is what keeps it pure; this wrapper is where the
    environment is allowed to be consulted (E6).
    """
    cfg = config or load_config()
    return normalize(
        raw,
        as_of,
        ref=ref,
        readme_chars=cfg.readme_excerpt_chars,
        manifest_chars=cfg.manifest_excerpt_chars,
    )


def manifest_excerpts(
    raw: fetch_stage.RawPayloads,
    facts: NormalizedFacts,
    config: Config | None = None,
) -> Excerpts:
    """The truncated README and manifest text the model is allowed to see (R-15.3)."""
    cfg = config or load_config()
    manifests: dict[str, str] = {}
    for path, payload in sorted(raw.contents.items()):
        text = decode_base64_content(payload, limit=cfg.manifest_excerpt_chars)
        if text:
            manifests[path] = text
    return Excerpts.from_facts(
        facts,
        manifests=manifests,
        readme_chars=cfg.readme_excerpt_chars,
        manifest_chars=cfg.manifest_excerpt_chars,
    )


def assemble(
    *,
    ref: RepoRef,
    facts: NormalizedFacts,
    card: Scorecard,
    narration: NarrationResult,
    cache_key: str,
    github_calls_used: int,
    duration_ms: int,
    created_at: datetime,
    schema_version: int = ANALYSIS_SCHEMA_VERSION,
) -> Analysis:
    """Build the `Analysis` every surface returns. No I/O, no clock."""
    return Analysis(
        repo=ref,
        slug=ref.slug,
        cache_key=cache_key,
        facts=facts,
        scorecard=card,
        narrative=narration.narrative,
        provenance=Provenance(
            github_calls_used=github_calls_used,
            llm_status=narration.llm_status,
            llm_model=narration.model,
            prompt_template_version=narration.prompt_template_version,
            scorecard_version=card.scorecard_version,
            analysis_schema_version=schema_version,
            deprecation_table_version=facts.deprecation_table_version,
            duration_ms=duration_ms,
        ),
        created_at=created_at,
    )


def offline_service(db: Database, config: Config | None = None) -> AnalysisService:
    """A service that cannot reach GitHub or an LLM provider.

    Used by the gallery, `get_exhibit`, `list_graveyard` and the offline seed
    check. Injecting clients that raise is how "this path makes no calls" becomes
    a claim the suite can fail on rather than a comment (Property 11).
    """
    cfg = config or load_config()
    return AnalysisService(
        db,
        config=cfg,
        github=NoNetworkGitHubClient(db, cfg),
        llm=None,
        build_llm=False,
    )
