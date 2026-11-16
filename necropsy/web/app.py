"""The Flask application: routes over the shared pipeline, nothing more (E8).

Every route here is a thin adapter. It canonicalizes input, calls
`AnalysisService` or the store, and renders a template or a JSON envelope. No
route contains fetch, normalization, scoring, prompt or cache logic — that all
lives behind the one entry point the MCP server also uses (E8). A reviewer should
find no business logic in this file.

The hot path is a cache hit: `/` and `/exhibit/{slug}` are indexed SQLite reads
with zero network calls, which is what keeps them under the 200 ms budget (E14,
R-2.3). The cold path (`POST /api/analyze`) never runs on the request thread; it
enqueues a job and the browser polls `/api/jobs/{id}` (R-1.5).

Repository-derived text is escaped by Jinja2 autoescaping everywhere. `|safe` on
a repo-derived value is a defect (E4, R-16.3).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, url_for

from necropsy.analysis.service import AnalysisService
from necropsy.analysis.vitals import compute_vitals
from necropsy.config import Config, load_config
from necropsy.errors import NecropsyError, NotInGraveyard, wrap_unexpected
from necropsy.github.urls import canonicalize
from necropsy.store import analyses, exhibits, ledger
from necropsy.store import jobs as jobs_store
from necropsy.store.db import Database, open_database
from necropsy.web.jobs import JobRunner

log = logging.getLogger("necropsy.web.app")

#: Progress copy per pipeline stage, keyed by the stage names the service emits.
STAGE_COPY: dict[str, str] = {
    "queued": "Preparing the examination table…",
    "canonicalizing": "Reading the toe tag…",
    "resolving_head_commit": "Establishing time of death…",
    "checking_cache": "Checking the records…",
    "fetching_metadata": "Fetching the remains…",
    "reading_file_tree": "Laying out the file tree…",
    "normalizing": "Examining the organs…",
    "scoring": "Weighing the evidence…",
    "writing_the_plaque": "Reading the last words…",
    "assembling": "Engraving the plaque…",
    "caching": "Filing the report…",
    "done": "The post-mortem is complete.",
    "failed": "The examination could not be completed.",
    "unknown": "Working…",
}

SORTS = ("curated", "deadest", "hardest")


def create_app(db: Database | None = None, config: Config | None = None) -> Flask:
    """Build the app. `db` is injectable so tests share one in-memory database."""
    cfg = config or load_config()
    database = db or open_database(cfg.database_path)
    database.migrate()

    app = Flask(__name__)
    app.config["necropsy_db"] = database
    app.config["necropsy_cfg"] = cfg

    service = AnalysisService(database, config=cfg)
    runner = JobRunner(database, service)
    app.config["necropsy_service"] = service
    app.config["necropsy_runner"] = runner

    _register_routes(app)
    _register_error_handler(app)
    return app


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def _register_routes(app: Flask) -> None:
    db: Database = app.config["necropsy_db"]
    cfg: Config = app.config["necropsy_cfg"]
    runner: JobRunner = app.config["necropsy_runner"]

    @app.get("/")
    def gallery() -> str:
        """The graveyard wall. One indexed read, zero network calls (R-2.5)."""
        sort = request.args.get("sort", "curated")
        if sort not in SORTS:
            sort = "curated"
        rows = exhibits.list_exhibits(db.connect(), sort=sort, limit=50)
        cards = [_card(exhibit) for exhibit in rows]
        # One Curator's Pick: the lowest-order curated demo with a note (design 8.4).
        pick = next(
            (c for c in cards if c["is_demo"] and c["has_note"]),
            cards[0] if cards else None,
        )
        if pick is not None:
            pick["is_curator_pick"] = True
        return render_template(
            "gallery.html",
            cards=cards,
            sort=sort,
            token_present=cfg.token_present,
            exhibit_count=len(cards),
        )

    @app.get("/exhibit/<slug>")
    def exhibit(slug: str) -> Any:
        """One plaque. Zero network calls; the spotlight reveal is pure CSS/JS."""
        found = exhibits.get_by_slug(db.connect(), slug)
        if found is None:
            raise _not_found(slug)
        payload = found.analysis.to_payload(cache_status="hit")
        vitals = compute_vitals(found.analysis.facts)
        receipts = _receipts(found.row, found.analysis)
        return render_template(
            "exhibit.html",
            exhibit=found,
            payload=payload,
            vitals=vitals,
            receipts=receipts,
            full_spec=_full_spec(found.analysis),
            inscription=found.user_inscription,
            cached_tag=_cached_tag(found),
            curator_note=found.curator_note,
            is_demo=found.is_demo,
        )

    @app.post("/api/exhibit/<slug>/inscribe")
    def api_inscribe(slug: str) -> Any:
        """Carve (or clear) a visitor's inscription on an exhibit.

        The only visitor-authored write in the app. It touches the exhibit row,
        never the cached analysis (which stays byte-identical to GitHub, E3), and
        makes no network call. The line is stored full and truncated at display.
        """
        body = request.get_json(silent=True) or {}
        text = str(body.get("inscription", "")).strip()
        if len(text) > 500:
            text = text[:500]
        with db.transaction() as conn:
            updated = exhibits.set_inscription(conn, slug, text)
        if not updated:
            raise _not_found(slug)
        return jsonify({"ok": True, "inscription": text or None})

    @app.get("/kiro-setup")
    def kiro_setup() -> str:
        """How to register the MCP server in Kiro. Static, no network."""
        return render_template("kiro_setup.html")

    @app.post("/api/analyze")
    def api_analyze() -> Any:
        """Enqueue a cache-miss analysis. Never analyzes on the request thread."""
        raw = (request.form.get("url") or (request.json or {}).get("url") or "").strip()
        try:
            ref = canonicalize(raw)
        except NecropsyError as error:
            return jsonify(error.to_envelope()), error.http_status

        # A cache hit on a pasted URL still needs one GitHub call to resolve the
        # SHA, so it goes through the job like any analysis. The job resolves
        # instantly when the row already exists.
        job_id = runner.submit(ref.display_url)
        return jsonify({"ok": True, "job_id": job_id}), 202

    @app.get("/api/jobs/<job_id>")
    def api_job(job_id: str) -> Any:
        """Poll a running analysis. Returns the slug to navigate to on success."""
        job = jobs_store.get(db.connect(), job_id)
        if job is None:
            return jsonify({"ok": False, "error": {"code": "NOT_FOUND"}}), 404

        stage = runner.stage_of(job_id)
        body: dict[str, Any] = {
            "ok": True,
            "job_id": job_id,
            "state": job.state,
            "stage": stage,
            "status_line": STAGE_COPY.get(stage, STAGE_COPY["unknown"]),
        }
        if job.state == "done":
            body["slug"] = _slug_for_job(db, job)
            body["exhibit_url"] = url_for("exhibit", slug=body["slug"])
        elif job.state == "failed":
            body["error"] = {
                "code": job.error_code,
                "message": job.error_message,
            }
        return jsonify(body)

    @app.get("/api/exhibits")
    def api_exhibits() -> Any:
        """The gallery as JSON, for anyone who wants the data behind the wall."""
        sort = request.args.get("sort", "curated")
        if sort not in SORTS:
            sort = "curated"
        rows = exhibits.list_exhibits(db.connect(), sort=sort, limit=50)
        return jsonify(
            {"ok": True, "sort": sort, "exhibits": [_summary(e) for e in rows]}
        )

    @app.get("/api/health")
    def api_health() -> Any:
        """Budget, cache and config state. `token_present` is a boolean (E12)."""
        conn = db.connect()
        state = ledger.latest(conn)
        return jsonify(
            {
                "ok": True,
                "cache_entries": analyses.count_analyses(conn),
                "demo_exhibits": exhibits.count_exhibits(conn, demo_only=True),
                "exhibits": exhibits.count_exhibits(conn),
                "token_present": cfg.token_present,
                "llm_enabled": cfg.llm_enabled,
                "rate_limit_remaining": state.remaining if state else None,
                # `reset_at` is already the ISO string GitHub sent, stored as-is
                # (ledger.RateLimitState.reset_at is `str | None`). Calling
                # `.isoformat()` on it raised, and `/api/health` is what Render's
                # healthCheckPath polls, so one API call made the whole service
                # look unhealthy.
                "reset_at": state.reset_at if state else None,
            }
        )


# --------------------------------------------------------------------------
# error handling
# --------------------------------------------------------------------------
def _register_error_handler(app: Flask) -> None:
    @app.errorhandler(NecropsyError)
    def _necropsy_error(error: NecropsyError) -> Any:
        """Render the shared envelope inside the gallery frame, never a trace (R-13.9)."""
        if _wants_json():
            return jsonify(error.to_envelope()), error.http_status
        html = render_template("_error.html", error=error.to_envelope()["error"])
        return Response(html, status=error.http_status, mimetype="text/html")

    @app.errorhandler(404)
    def _404(_: Any) -> Any:
        err = _not_found(request.path)
        if _wants_json():
            return jsonify(err.to_envelope()), 404
        return render_template("_error.html", error=err.to_envelope()["error"]), 404

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception) -> Any:
        if isinstance(exc, NecropsyError):
            raise exc
        mapped = wrap_unexpected(exc)
        log.exception("unhandled exception reached the web boundary")
        if _wants_json():
            return jsonify(mapped.to_envelope()), 500
        return render_template("_error.html", error=mapped.to_envelope()["error"]), 500


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _card(exhibit: exhibits.Exhibit) -> dict[str, Any]:
    """The fields one gallery card renders."""
    facts = exhibit.analysis.facts
    card = exhibit.analysis.scorecard
    return {
        "slug": exhibit.slug,
        "title": exhibit.plaque_title,
        "owner": facts.owner,
        "name": facts.name,
        "language": facts.primary_language or "unknown",
        "score": card.score,
        "band_key": card.band.key,
        "band_label": card.band.label,
        "epitaph": exhibit.analysis.narrative.epitaph.text,
        "cause_tag": exhibit.analysis.narrative.cause_of_death.headline,
        "last_words": facts.last_commit_message,
        "days": facts.days_since_last_commit,
        "is_demo": exhibit.is_demo,
        "has_note": bool(exhibit.curator_note),
        "is_curator_pick": False,
    }


def _summary(exhibit: exhibits.Exhibit) -> dict[str, Any]:
    facts = exhibit.analysis.facts
    card = exhibit.analysis.scorecard
    return {
        "slug": exhibit.slug,
        "repo_url": facts.canonical_url,
        "score": card.score,
        "band": card.band.key,
        "epitaph": exhibit.analysis.narrative.epitaph.text,
        "days_since_last_commit": facts.days_since_last_commit,
        "is_demo": exhibit.is_demo,
    }


#: Which raw-payload section backs each citable fact key. The receipt shows that
#: slice of the stored `raw_github_json`, so a sceptic sees real GitHub data
#: rather than generated prose (P1, P2). No new API call is ever made (R-2.5).
_RECEIPT_SOURCE: dict[str, str] = {
    "days_since_last_commit": "head_commit",
    "last_commit_message": "head_commit",
    "last_commit_sha": "head_commit",
    "last_release_tag": "latest_release",
    "last_release_at": "latest_release",
    "primary_language": "languages",
    "languages": "languages",
    "deprecated_dependency_count": "contents",
    "deprecated_dependency_names": "contents",
    "direct_dependency_count": "contents",
    "test_file_count": "tree",
    "has_tests": "tree",
    "has_ci": "tree",
    "has_license": "repo",
    "license_spdx": "repo",
    "is_archived": "repo",
    "stars": "repo",
    "open_issues_count": "repo",
    "unanswered_issue_count": "open_issues",
    "unanswered_issue_ratio": "open_issues",
    "file_count": "tree",
}

RECEIPT_MAX_CHARS = 800


def _receipts(row: analyses.AnalysisRow, analysis: Any) -> dict[str, dict[str, str]]:
    """Build one raw-JSON receipt per fact key cited anywhere on the plaque.

    The payloads are already stored; this slices the relevant section and
    truncates it. Repository-derived strings stay data: the template renders the
    receipt escaped, never `|safe` (E4).
    """
    try:
        raw = row.raw_payloads()
    except Exception:  # pragma: no cover - a malformed blob must not break the page
        return {}
    payloads = raw.get("payloads", raw) if isinstance(raw, dict) else {}

    cited: set[str] = set()
    for ev in analysis.narrative.cause_of_death.evidence:
        cited.add(ev.fact_key)
    if analysis.narrative.epitaph.evidence:
        cited.add(analysis.narrative.epitaph.evidence.fact_key)

    receipts: dict[str, dict[str, str]] = {}
    for fact_key in cited:
        section = _RECEIPT_SOURCE.get(fact_key)
        if section is None or section not in payloads:
            continue
        blob = json.dumps(payloads[section], indent=2, default=str)
        if len(blob) > RECEIPT_MAX_CHARS:
            blob = blob[:RECEIPT_MAX_CHARS].rstrip() + "\n… (truncated)"
        receipts[fact_key] = {"section": section, "json": blob}
    return receipts


def _cached_tag(exhibit: exhibits.Exhibit) -> dict[str, Any]:
    """The 'cached from GitHub' proof-of-real-data tag (the offline rule).

    For a compiled specimen it says so plainly; for an API-verified exhibit it
    links to the repository at the exact commit SHA so a judge can check. Either
    way the data's provenance is shown, never hidden (P7).
    """
    facts = exhibit.analysis.facts
    sha = facts.last_commit_sha
    is_specimen = facts.owner == "necropsy-specimens"
    return {
        "is_specimen": is_specimen,
        "fetched_at": facts.as_of.date().isoformat(),
        "commit_sha": sha,
        "short_sha": sha[:7] if sha else "",
        "commit_url": f"{facts.html_url}/commit/{sha}" if sha and not is_specimen else None,
        "repo_url": facts.html_url,
    }


def _full_spec(analysis: Any) -> str:
    """The Kiro-ready revival spec as one markdown string (R-12.2).

    Delegates to the shared builder so the web button and the MCP
    `write_revival_spec` tool emit byte-identical specs — one source, two
    surfaces (E8).
    """
    from necropsy.mcp.revival import build_revival_spec

    return build_revival_spec(analysis)


def _slug_for_job(db: Database, job: jobs_store.Job) -> str:
    """Resolve a finished job to its exhibit slug."""
    if job.result_analysis_id:
        row = analyses.get_by_id(db.connect(), job.result_analysis_id)
        if row is not None:
            from necropsy.models.analysis import RepoRef

            facts = analyses.load_analysis(row).facts
            return RepoRef(owner=facts.owner, name=facts.name).slug
    # Coalesced hit: fall back to the canonical URL's newest exhibit.
    exhibit = exhibits.get_by_canonical_url(db.connect(), job.canonical_url.lower())
    return exhibit.slug if exhibit else ""


def _wants_json() -> bool:
    if request.path.startswith("/api/"):
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _not_found(what: str) -> NecropsyError:
    return NotInGraveyard(
        f"There is no exhibit at {what!r}. It may never have been analyzed.",
        details={"requested": what},
    )
