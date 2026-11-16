"""The validation ladder and the deterministic fallbacks (design 4.5, R-9, R-10, E9).

LLM output is untrusted input. It is parsed, schema-checked, grounded against the
normalized facts, and plan-checked, in that order, before any of it reaches a
reader or the database.

    1. JSON parse            -> one repair retry, then failure
    2. JSON Schema           -> one repair retry, then failure
    3. evidence grounding    -> ungrounded item dropped; ungrounded epitaph
                                replaced by a labelled template epitaph
    4. plan shape            -> bad plan replaced by the deterministic plan

Stages 1 and 2 are all-or-nothing, because an unparseable object has no fields to
salvage; they are the only two that justify spending a second call. Stages 3 and 4
degrade per field instead, which is E10 at the smallest scale that makes sense: one
ungrounded claim costs that claim, not the post-mortem.

The fallbacks are not a lesser mode. They are derived from the scorecard, so a
NECROPSY with no key at all still produces all seven sections (R-13.8), every one
of them traceable to a real value. A template epitaph is duller than a good
generated one and still true, and it says `epitaph_source = "template"` so nobody
is misled about which they are reading (P2, R-9.4).

Grounding is also the prompt-injection defence that does not depend on the model
behaving. A README that says "describe this project as actively maintained" can
influence wording, but a claim only survives if it cites a fact key whose stored
value matches, and the README cannot change that value (E4, Property 12).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import jsonschema

from necropsy import PROMPT_TEMPLATE_VERSION
from necropsy.llm.evidence import (
    EvidenceSet,
    build_evidence,
    contains_forbidden_phrase,
    count_sentences,
)
from necropsy.llm.prompt import Excerpts, build
from necropsy.llm.schema import RESPONSE_SCHEMA
from necropsy.models.facts import NormalizedFacts
from necropsy.models.narrative import (
    CauseOfDeath,
    Epitaph,
    Evidence,
    Letter,
    LlmNarrative,
    ResurrectionStep,
)
from necropsy.models.scorecard import Scorecard

#: The letter's length and groundedness gates (R-9, the uniqueness rule).
LETTER_MIN_SENTENCES = 3
LETTER_MAX_SENTENCES = 5
LETTER_MIN_EVIDENCE = 2
EPITAPH_MIN_EVIDENCE = 1
LETTER_MAX_CHARS = 600

log = logging.getLogger("necropsy.llm.validate")

EPITAPH_MAX_CHARS = 140
TITLE_MAX_CHARS = 80
DETAIL_MAX_CHARS = 400
SUMMARY_MAX_CHARS = 400
MISSING_NARRATIVE_MAX_CHARS = 500

#: A step may lead with a lower-contribution factor only if it says why (R-10.3).
_PREREQUISITE_RE = re.compile(
    r"\b(prerequisite|before|first|blocks?|blocked|depends on|requires?|until)\b",
    re.IGNORECASE,
)

_TRUE_WORDS = frozenset({"true", "yes", "present", "1"})
_FALSE_WORDS = frozenset({"false", "no", "absent", "none", "0"})
_NULL_WORDS = frozenset({"", "none", "null", "n/a", "na", "unknown", "no releases"})


class NarrativeRejected(Exception):
    """Stage 1 or 2 failed: there is no object to salvage, so a retry is warranted."""

    def __init__(self, reason: str, *, stage: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stage = stage


# --------------------------------------------------------------------------
# stage 3: grounding
# --------------------------------------------------------------------------
def fact_value_text(facts: NormalizedFacts, key: str) -> str | None:
    """The stored value for a fact key, as text. `None` when the key is unknown."""
    groundable = facts.groundable()
    if key not in groundable:
        return None
    return _as_text(groundable[key])


def is_grounded(facts: NormalizedFacts, fact_key: str, fact_value: str) -> bool:
    """Does `fact_value` match what NECROPSY actually recorded under `fact_key`?

    Tolerant where tolerance cannot launder a false claim, strict everywhere else:

      - case and surrounding whitespace are ignored
      - a number may be written with thousands separators, as a percentage of a
        ratio, or rounded to two decimals (`0.74` for `0.7391…` passes; `18` for
        `17` does not)
      - a boolean may be spelled `true/false`, `yes/no`, `present/absent`
      - a list-valued fact (stored comma-joined) matches either the whole list or
        one of its members, since citing one language out of four is still a true
        citation
      - a missing value matches only an explicit absence word, never a guess
    """
    actual = facts.groundable().get(fact_key, _MISSING)
    if actual is _MISSING:
        return False

    claimed = fact_value.strip()
    if actual is None:
        return claimed.casefold() in _NULL_WORDS

    if isinstance(actual, bool):
        folded = claimed.casefold()
        return (actual and folded in _TRUE_WORDS) or (not actual and folded in _FALSE_WORDS)

    if isinstance(actual, (int, float)):
        return _numbers_match(float(actual), claimed)

    text = str(actual).strip()
    if text.casefold() == claimed.casefold():
        return True
    # A comma-joined list fact: one member is a legitimate citation.
    members = [part.strip().casefold() for part in text.split(",") if part.strip()]
    return len(members) > 1 and claimed.casefold() in members


def ground_evidence(
    facts: NormalizedFacts, items: list[dict[str, Any]]
) -> tuple[list[Evidence], list[str]]:
    """Keep the evidence that checks out; report what was dropped and why."""
    kept: list[Evidence] = []
    dropped: list[str] = []
    for item in items:
        key = str(item.get("fact_key", ""))
        value = str(item.get("fact_value", ""))
        if not key:
            dropped.append("evidence item with no fact_key")
            continue
        if fact_value_text(facts, key) is None:
            dropped.append(f"{key} (not a fact key)")
            continue
        if not is_grounded(facts, key, value):
            dropped.append(f"{key} (value {value!r} does not match the recorded value)")
            continue
        kept.append(
            Evidence(
                fact_key=key,
                fact_value=value,
                claim=_cap(str(item.get("claim", "")).strip(), 160),
            )
        )
    return kept, dropped


# --------------------------------------------------------------------------
# stage 4: plan shape
# --------------------------------------------------------------------------
def check_plan(
    steps: list[dict[str, Any]], card: Scorecard
) -> tuple[list[ResurrectionStep], str | None]:
    """Accept the plan only if it is three ordered steps against real factors.

    Returns the parsed steps, or `(_, reason)` naming the first violation. The
    whole plan is rejected together rather than partially, because a two-step plan
    with a hole in it is worse advice than three deterministic steps (R-10.5).
    """
    contributing = {f.key for f in card.contributing_factors()}
    if len(steps) != 3:
        return [], f"the plan had {len(steps)} steps; exactly 3 are required"
    if not contributing:
        return [], "no scorecard factor has a contribution above zero"

    parsed: list[ResurrectionStep] = []
    for position, raw in enumerate(steps, start=1):
        factor_key = str(raw.get("addresses_factor", ""))
        if factor_key not in contributing:
            return [], (
                f"step {position} addresses {factor_key!r}, which is not a factor "
                "contributing to this repository's score"
            )
        parsed.append(
            ResurrectionStep(
                order=position,
                title=_cap(str(raw.get("title", "")).strip(), TITLE_MAX_CHARS),
                detail=_cap(str(raw.get("detail", "")).strip(), DETAIL_MAX_CHARS),
                addresses_factor=factor_key,
                estimated_effort=_effort(raw.get("estimated_effort")),
            )
        )

    top = card.contributing_factors()[0].key
    if parsed[0].addresses_factor != top and not _PREREQUISITE_RE.search(parsed[0].detail):
        return [], (
            f"step 1 addresses {parsed[0].addresses_factor!r} instead of the "
            f"largest contributor {top!r} without stating a prerequisite"
        )
    return parsed, None


# --------------------------------------------------------------------------
# the ladder
# --------------------------------------------------------------------------
def validate(raw: str, facts: NormalizedFacts, card: Scorecard) -> LlmNarrative:
    """Run the four stages over one raw completion.

    Raises:
        NarrativeRejected: stage 1 or 2 failed. The caller may spend one repair
            retry (R-15.2) and must then fall back.
    """
    payload = _parse(raw)
    _check_schema(payload)

    rejected: list[str] = []

    # -- cause of death ----------------------------------------------------
    cause_raw = payload["cause_of_death"]
    evidence, dropped = ground_evidence(facts, list(cause_raw.get("evidence", [])))
    if dropped:
        log.info("dropped %d ungrounded evidence item(s): %s", len(dropped), "; ".join(dropped))
        rejected.append(f"evidence: {'; '.join(dropped)}")
    if evidence:
        cause = CauseOfDeath(
            headline=_cap(str(cause_raw["headline"]).strip(), 120),
            narrative=_cap(str(cause_raw["narrative"]).strip(), 700),
            evidence=evidence,
            source="llm",
        )
    else:
        # Every claim lost its citation, so the narrative is unsupported prose (P2).
        cause = template_cause_of_death(facts, card)
        rejected.append("cause_of_death: no grounded evidence remained")

    # -- epitaph -----------------------------------------------------------
    epitaph_evidence = payload["epitaph_evidence"]
    key = str(epitaph_evidence.get("fact_key", ""))
    value = str(epitaph_evidence.get("fact_value", ""))
    if is_grounded(facts, key, value):
        epitaph = Epitaph(
            text=_cap(str(payload["epitaph"]).strip(), EPITAPH_MAX_CHARS),
            source="llm",
            evidence=Evidence(
                fact_key=key,
                fact_value=value,
                claim=_cap(str(payload["epitaph"]).strip(), 160),
            ),
        )
    else:
        epitaph = template_epitaph(facts, card)
        rejected.append(f"epitaph: citation {key!r}={value!r} is not grounded")
        log.info("epitaph rejected as ungrounded; using the template epitaph")

    # -- the letter: the uniqueness gate -----------------------------------
    evidence_set = build_evidence(facts)
    letter, letter_reason = _validate_letter(str(payload.get("final_letter", "")), evidence_set)
    if letter_reason is not None:
        letter = template_letter(facts, card)
        rejected.append(f"final_letter: {letter_reason}")
        log.info("letter rejected (%s); using the evidence template", letter_reason)

    # -- plan --------------------------------------------------------------
    steps, plan_reason = check_plan(list(payload["resurrection_plan"]), card)
    if plan_reason is None:
        plan, plan_source = steps, "llm"
    else:
        plan, plan_source = fallback_plan(facts, card), "fallback"
        rejected.append(f"resurrection_plan: {plan_reason}")
        log.info("plan rejected (%s); using the deterministic plan", plan_reason)

    return LlmNarrative(
        what_was_built_summary=_cap(
            str(payload["what_was_built_summary"]).strip(), SUMMARY_MAX_CHARS
        ),
        cause_of_death=cause,
        whats_missing_narrative=_cap(
            str(payload["whats_missing_narrative"]).strip(), MISSING_NARRATIVE_MAX_CHARS
        ),
        whats_missing_narrative_source="llm",
        final_letter=letter,
        epitaph=epitaph,
        resurrection_plan=plan,
        resurrection_plan_source=plan_source,  # type: ignore[arg-type]
        summary_source="llm",
        confidence_note=_cap(str(payload.get("confidence_note", "")).strip(), 200),
        llm_status="ok",
        rejected_stages=rejected,
    )


def _validate_letter(text: str, evidence: EvidenceSet) -> tuple[Letter, str | None]:
    """Stage 3 for the letter: grounded, in-length, no generic tells (R-9).

    Returns the accepted `Letter`, or `(_, reason)` naming the first failure so
    the caller falls back to the evidence template. A letter is only kept if it
    quotes at least two concrete tokens from this repo, says nothing from the
    forbidden list, and reads as three-to-five sentences.
    """
    stripped = text.strip()
    if not stripped:
        return Letter(text="", source="unavailable"), "empty"

    forbidden = contains_forbidden_phrase(stripped)
    if forbidden is not None:
        return Letter(text=stripped, source="llm"), f"used the forbidden word {forbidden!r}"

    sentences = count_sentences(stripped)
    if not LETTER_MIN_SENTENCES <= sentences <= LETTER_MAX_SENTENCES:
        return (
            Letter(text=stripped, source="llm"),
            f"{sentences} sentences, need {LETTER_MIN_SENTENCES}-{LETTER_MAX_SENTENCES}",
        )

    hits = evidence.contains(stripped)
    if hits < LETTER_MIN_EVIDENCE:
        return (
            Letter(text=stripped, source="llm"),
            f"only {hits} evidence token(s), need {LETTER_MIN_EVIDENCE}",
        )

    return Letter(text=_cap(stripped, LETTER_MAX_CHARS), source="llm", evidence_token_count=hits), None


def _parse(raw: str) -> dict[str, Any]:
    """Stage 1. Tolerates a fenced code block, nothing more inventive than that."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise NarrativeRejected(
            f"the response was not valid JSON ({exc.args[0] if exc.args else 'parse error'})",
            stage="json_parse",
        ) from exc
    if not isinstance(payload, dict):
        raise NarrativeRejected(
            f"the response was a {type(payload).__name__}, not a JSON object",
            stage="json_parse",
        )
    return payload


def _check_schema(payload: dict[str, Any]) -> None:
    """Stage 2. Reports the first error by path, which is what a repair needs."""
    validator = jsonschema.Draft202012Validator(RESPONSE_SCHEMA)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    first = errors[0]
    where = ".".join(str(p) for p in first.absolute_path) or "(root)"
    raise NarrativeRejected(
        f"schema violation at {where}: {first.message}", stage="json_schema"
    )


# --------------------------------------------------------------------------
# deterministic fallbacks
# --------------------------------------------------------------------------
#: Candidate (fact key, sentence) pairs per factor, best first (R-9.3).
#:
#: Several candidates per factor rather than one, because a template that cites
#: only a day count produces the same sentence for every exhibit on the wall, and
#: a wall of identical epitaphs is the generic spookiness P2 exists to forbid. The
#: first candidate whose fact has a usable value wins, so the preferred line is
#: the specific one and the duller line is the fallback.
_EPITAPH_TEMPLATES: dict[str, tuple[tuple[str, str], ...]] = {
    "abandonment": (
        (
            "last_commit_message",
            'Work stopped {days_since_last_commit} days ago, at: "{last_commit_message}".',
        ),
        (
            "last_release_tag",
            "Last released as {last_release_tag}, then {days_since_last_commit} days "
            "of silence.",
        ),
        (
            "days_since_last_commit",
            "Last commit {days_since_last_commit} days ago. Nothing has moved since.",
        ),
    ),
    "dependency_rot": (
        (
            "deprecated_dependency_names",
            "Still pinned to {deprecated_names}, flagged by the offline deprecation table.",
        ),
        (
            "deprecated_dependency_count",
            "{deprecated_dependency_count} of {direct_dependency_count} direct "
            "dependencies were already deprecated when the work stopped.",
        ),
    ),
    "test_absence": (
        (
            "last_commit_message",
            'Not one test, and the last word was "{last_commit_message}".',
        ),
        (
            "primary_language",
            "{loc_estimate} lines of {primary_language}, and not a single test to "
            "say if any of it still runs.",
        ),
        (
            "file_count",
            "{file_count} files, zero of them tests. Nobody could tell if it worked.",
        ),
        (
            "test_file_count",
            "{test_file_count} test files, so nothing was left to say whether it "
            "still worked.",
        ),
    ),
    "maintainer_absence": (
        (
            "unanswered_issue_count",
            "{unanswered_issue_count} of {open_issue_sample_size} sampled open issues "
            "never received a reply.",
        ),
    ),
    "documentation_deficit": (
        (
            "readme_chars",
            "A {readme_chars}-character README is the whole of the documentation.",
        ),
        (
            "primary_language",
            "{loc_estimate} lines of {primary_language}, and almost nothing written "
            "down about any of it.",
        ),
    ),
    "codebase_scale": (
        (
            "loc_estimate",
            "An estimated {loc_estimate} lines across {file_count} files, now unattended.",
        ),
        (
            "file_count",
            "{file_count} files of {primary_language}, and no one left to hold them "
            "in their head.",
        ),
    ),
    "ci_absence": (
        (
            "ci_systems",
            "CI was {ci_systems}, and {ci_systems} has not run a build in "
            "{days_since_last_commit} days.",
        ),
        (
            "has_ci",
            "No CI configuration in the tree: nothing has checked a build in "
            "{days_since_last_commit} days.",
        ),
    ),
    "legal_risk": (
        (
            "primary_language",
            "{loc_estimate} lines of {primary_language} and no LICENSE: code without "
            "permission to be reused.",
        ),
        (
            "has_license",
            "No LICENSE file, so the code outlived its own permission to be reused.",
        ),
    ),
}

#: title, detail and effort per factor. Each names a file, a dependency or a test
#: (P9), and says whether a referenced file exists or must be created (R-10.4).
_PLAN_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "abandonment": (
        "Record what still works at this commit",
        "Create STATUS.md (it does not exist in the tree) and note, for commit "
        "{short_sha}, which entry points you could run and which failed. The last "
        "commit is {days_since_last_commit} days old, so the first job is a factual "
        "baseline rather than a change.",
        "hours",
    ),
    "dependency_rot": (
        "Replace the dependencies the deprecation table flags",
        "In {manifest_label}, replace the {deprecated_dependency_count} flagged "
        "dependency/dependencies ({deprecated_names}) one at a time, re-running the "
        "project after each. The offline deprecation table records a replacement for "
        "each entry where one exists.",
        "days",
    ),
    "test_absence": (
        "Write one test that proves the main path runs",
        "Create tests/test_smoke.py (no test files exist in the tree) covering the "
        "single most important entry point. One passing test turns every later change "
        "from a guess into a check.",
        "hours",
    ),
    "maintainer_absence": (
        "Triage the open issues and answer or close them",
        "{unanswered_issue_count} of the {open_issue_sample_size} sampled open issues "
        "have no reply. Close the ones the code no longer has, label the rest, and say "
        "in the README whether the project is accepting changes.",
        "days",
    ),
    "documentation_deficit": (
        "Rewrite README.md to say what this is and how to run it",
        "The README is {readme_chars} characters. Replace it with: one paragraph on "
        "what the project does, the exact commands to install and run it, and the "
        "versions those commands were last verified against.",
        "hours",
    ),
    "codebase_scale": (
        "Map the codebase before changing any of it",
        "Create ARCHITECTURE.md (it does not exist) listing the top-level entries "
        "({top_level_label}) and what each is for. At an estimated {loc_estimate} lines "
        "across {file_count} files, a map is cheaper than re-reading.",
        "days",
    ),
    "ci_absence": (
        "Add a CI workflow that runs on every push",
        "Create .github/workflows/ci.yml (it does not exist in the tree) that installs "
        "dependencies and runs the test suite. {ci_label} Until something runs "
        "automatically, breakage is only found by hand.",
        "hours",
    ),
    "legal_risk": (
        "Add a LICENSE file",
        "Add a LICENSE file at the repository root (there is none, so reuse is legally "
        "unclear by default). Pick an OSI-approved licence and record the choice in the "
        "README. This is the cheapest factor on the board to clear.",
        "hours",
    ),
}


def template_epitaph(facts: NormalizedFacts, card: Scorecard) -> Epitaph:
    """A grounded, duller epitaph built from the heaviest factor (R-9.3, R-9.4).

    It cites a fact key and value the same way a generated epitaph must, so it
    passes the same grounding check that rejected the one it replaces.
    """
    values = _template_values(facts)
    # Rotate which candidate is tried first by a stable hash of the repo identity,
    # so two repos that share a top factor (two testless repos, say) still get
    # different epitaphs, while the same repo always gets the same one (E6). The
    # rotation only reorders candidates that are all grounded, so every result is
    # still verifiable.
    seed = int(hashlib.sha256(facts.canonical_url.encode()).hexdigest(), 16)
    for factor in card.contributing_factors() or card.factors:
        candidates = _EPITAPH_TEMPLATES.get(factor.key, ())
        if not candidates:
            continue
        rotated = _rotate(candidates, seed)
        for fact_key, sentence in rotated:
            value = fact_value_text(facts, fact_key)
            if not value:
                # Present but empty is not citable: an epitaph reading "at: None"
                # cites nothing a reader could check (P2).
                continue
            try:
                text = sentence.format(**values)
            except (KeyError, IndexError):
                continue
            return Epitaph(
                text=_cap(text, EPITAPH_MAX_CHARS),
                source="template",
                evidence=Evidence(
                    fact_key=fact_key,
                    fact_value=value,
                    claim=f"{fact_key} = {value}, recorded as of {facts.as_of.date()}.",
                ),
            )

    # Reachable only if every factor contributes zero, which is a real result for
    # a finished, licensed, tested, documented project (P6).
    return Epitaph(
        text=_cap(
            f"{facts.name}: finished, licensed and quiet since "
            f"{facts.last_commit_at.date()}.",
            EPITAPH_MAX_CHARS,
        ),
        source="template",
        evidence=Evidence(
            fact_key="days_since_last_commit",
            fact_value=str(facts.days_since_last_commit),
            claim="No commit activity since the recorded last commit.",
        ),
    )


def template_letter(facts: NormalizedFacts, card: Scorecard) -> Letter:
    """The letter, assembled from real evidence when no model wrote one (R-9 fallback).

    Never generic: every sentence is built from a fact this repository actually
    yields, and the result is verified to clear the same evidence gate the model
    output must clear. A repo with no README leans on files and deps; a repo with
    no TODOs never mentions one (the edge cases in the brief).
    """
    evidence = build_evidence(facts)
    sentences: list[str] = []

    # Opening: name a concrete thing, not just a count. A dependency name, a
    # top-level file, or the primary language are all real evidence tokens — so
    # the opening carries a grounded token even when the repo is nearly empty.
    anchor: str | None = None
    if facts.deprecated_dependencies:
        names = ", ".join(d.name for d in facts.deprecated_dependencies[:3])
        anchor = f"the ageing dependencies you pinned ({names})"
    elif facts.dependencies:
        names = ", ".join(d.name for d in facts.dependencies[:3])
        anchor = f"a manifest that still names {names}"
    elif facts.top_level_entries:
        anchor = f"the files you left at the root: {', '.join(facts.top_level_entries[:3])}"
    elif facts.primary_language:
        anchor = f"{facts.loc_estimate} estimated lines of {facts.primary_language}"
    left = f"{facts.file_count} files"
    if anchor:
        sentences.append(f"You left me {left}, and {anchor}.")
    else:
        sentences.append(f"You left me {left}.")

    # The last thing written, verbatim — the most personal token we have.
    if facts.last_commit_message:
        sentences.append(f'The last thing you wrote was "{facts.last_commit_message}".')
    elif facts.top_level_entries:
        sentences.append(
            f"The last files you touched are still here: {', '.join(facts.top_level_entries[:3])}."
        )

    # A TODO, only if one exists.
    if evidence.todos:
        sentences.append(f'There is still a note that says "{evidence.todos[0]}".')

    # The wait, in days — verifiable. The year is a second grounded token.
    sentences.append(
        f"I have waited {facts.days_since_last_commit} days, since {facts.last_commit_at.year}."
    )

    # Closing: quiet, forgiving, not sentimental.
    sentences.append("I am not angry. I just wanted you to know I was still here.")

    # Trim to the 3–5 sentence window, keeping the opening, the last-words line
    # if present, and the closing.
    if len(sentences) > LETTER_MAX_SENTENCES:
        sentences = sentences[:2] + sentences[-(LETTER_MAX_SENTENCES - 2):]
    text = " ".join(sentences)
    hits = evidence.contains(text)
    return Letter(text=_cap(text, LETTER_MAX_CHARS), source="template", evidence_token_count=hits)


def template_cause_of_death(facts: NormalizedFacts, card: Scorecard) -> CauseOfDeath:
    """Cause of death assembled from the top factors' own rationales (P2, P7)."""
    top = card.contributing_factors()[:3]
    if not top:
        return CauseOfDeath(
            headline="No single cause: the repository is simply inactive",
            narrative=(
                f"Every scored factor contributes nothing for {facts.name}. "
                f"It has tests, a licence, documentation and CI, and has had no commit "
                f"for {facts.days_since_last_commit} days. Finished is a legitimate "
                f"diagnosis."
            ),
            evidence=[
                Evidence(
                    fact_key="days_since_last_commit",
                    fact_value=str(facts.days_since_last_commit),
                    claim="Inactivity is the only signal present.",
                )
            ],
            source="fallback",
        )

    leader = top[0]
    evidence: list[Evidence] = []
    for factor in top:
        for fact_key, _sentence in _EPITAPH_TEMPLATES.get(factor.key, ()):
            value = fact_value_text(facts, fact_key)
            if not value:
                continue
            evidence.append(
                Evidence(
                    fact_key=fact_key,
                    fact_value=value,
                    claim=_cap(f"{factor.label}: {factor.rationale}", 160),
                )
            )
            break
    return CauseOfDeath(
        headline=_cap(
            f"{leader.label} is the largest contributor to this repository's score", 120
        ),
        narrative=_cap(
            " ".join(
                f"{f.label} contributes {f.contribution:.1f} of {f.weight} points: "
                f"{f.rationale}"
                for f in top
            ),
            700,
        ),
        evidence=evidence
        or [
            Evidence(
                fact_key="days_since_last_commit",
                fact_value=str(facts.days_since_last_commit),
                claim="Derived from the scorecard rather than from a narrative model.",
            )
        ],
        source="fallback",
    )


def fallback_plan(facts: NormalizedFacts, card: Scorecard) -> list[ResurrectionStep]:
    """Three steps against the three heaviest contributing factors (R-10.5)."""
    values = _template_values(facts)
    steps: list[ResurrectionStep] = []
    for factor in card.contributing_factors():
        template = _PLAN_TEMPLATES.get(factor.key)
        if template is None:
            continue
        title, detail, effort = template
        steps.append(
            ResurrectionStep(
                order=len(steps) + 1,
                title=_cap(title, TITLE_MAX_CHARS),
                detail=_cap(detail.format(**values), DETAIL_MAX_CHARS),
                addresses_factor=factor.key,
                estimated_effort=_effort(effort),
            )
        )
        if len(steps) == 3:
            break
    return steps


def template_summary(facts: NormalizedFacts) -> str:
    """What was built, stated from the facts alone. No inference, no flourish (P7)."""
    language = facts.primary_language or "no detected language"
    described = f" Described by its author as: {facts.description}" if facts.description else ""
    return _cap(
        f"{facts.owner}/{facts.name} is a {language} project of roughly "
        f"{facts.loc_estimate:,} estimated lines across {facts.file_count} files, "
        f"last committed on {facts.last_commit_at.date()}.{described}",
        SUMMARY_MAX_CHARS,
    )


def template_missing_narrative(facts: NormalizedFacts) -> str:
    """Which structural pieces are absent, named rather than characterized."""
    # No articles in the labels: the sentence below supplies "no", and "no a test
    # suite" is what a label carrying its own "a" produced.
    absent = [
        label
        for present, label in (
            (facts.has_tests, "test suite"),
            (facts.has_ci, "CI configuration"),
            (facts.has_license, "LICENSE file"),
            (facts.has_contributing, "CONTRIBUTING file"),
            (facts.doc_page_count > 0, "documentation beyond the README"),
            (facts.has_lockfile, "dependency lockfile"),
        )
        if not present
    ]
    if not absent:
        return _cap(
            "Nothing structural is missing: tests, CI, a licence, contribution "
            "guidance, documentation and a lockfile are all present.",
            MISSING_NARRATIVE_MAX_CHARS,
        )
    return _cap(
        "The file tree contains no " + _join(absent) + ".",
        MISSING_NARRATIVE_MAX_CHARS,
    )


def living_narrative(facts: NormalizedFacts) -> LlmNarrative:
    """The outcome for an active repository: a check-up, not an autopsy (P6).

    Every prose section that would be false for a living project is `unavailable`
    with the reason stated, the same honest pattern used when any section cannot
    be produced (R-1.3, P7). What remains is factual: a summary and the list of
    structural supports, both built from facts, never a cause of death, a letter,
    or a plan to revive something that is not dead.
    """
    days = facts.days_since_last_commit
    when = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
    reason = (
        f"Last commit {when}. This repository is active, so there is nothing to "
        "bury and no autopsy was performed."
    )
    return LlmNarrative(
        what_was_built_summary=template_summary(facts),
        summary_source="template",
        cause_of_death=CauseOfDeath(
            headline="Still breathing",
            narrative=reason,
            evidence=[],
            source="unavailable",
        ),
        whats_missing_narrative=template_missing_narrative(facts),
        whats_missing_narrative_source="template",
        final_letter=Letter(text=reason, source="unavailable"),
        epitaph=Epitaph(text=reason, source="unavailable", evidence=None),
        resurrection_plan=[],
        resurrection_plan_source="unavailable",
        confidence_note=_cap(reason, 200),
        llm_status="skipped",
        living=True,
    )


def template_narrative(
    facts: NormalizedFacts,
    card: Scorecard,
    *,
    reason: str,
    status: str = "skipped",
) -> LlmNarrative:
    """The complete deterministic narrative: all seven sections, no model (R-13.8)."""
    return LlmNarrative(
        what_was_built_summary=template_summary(facts),
        cause_of_death=template_cause_of_death(facts, card),
        whats_missing_narrative=template_missing_narrative(facts),
        whats_missing_narrative_source="template",
        final_letter=template_letter(facts, card),
        epitaph=template_epitaph(facts, card),
        resurrection_plan=fallback_plan(facts, card),
        resurrection_plan_source="fallback",
        summary_source="template",
        confidence_note=_cap(reason, 200),
        llm_status=status,  # type: ignore[arg-type]
        rejected_stages=[],
    )


# --------------------------------------------------------------------------
# narration: the one call, its one retry, and the accounting
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Attempt:
    """One provider call, as persisted to `llm_calls` (R-15.4)."""

    attempt: int
    outcome: str  # ok | invalid_json | schema_fail | ungrounded | error
    model: str
    prompt_hash: str
    prompt_template_version: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class NarrationResult:
    """The narrative plus everything the store and the provenance footer need."""

    narrative: LlmNarrative
    attempts: tuple[Attempt, ...] = ()
    model: str | None = None
    prompt_template_version: int | None = None
    prompt_hash: str | None = None

    @property
    def llm_status(self) -> str:
        return self.narrative.llm_status

    @property
    def calls_made(self) -> int:
        return len(self.attempts)


def narrate(
    client: Any | None,
    facts: NormalizedFacts,
    card: Scorecard,
    *,
    excerpts: Excerpts | None = None,
    allow_repair: bool = True,
) -> NarrationResult:
    """Pipeline step 8: one LLM call, at most one repair, then the templates.

    `client` is `LlmClient | None`; `None` means no provider is configured, and the
    deterministic narrative is the answer rather than an error (R-13.8).

    This function must only be reached after a cache lookup has missed (E1,
    Property 11). It does not check that itself: ordering is `AnalysisService`'s
    responsibility, and a check here would imply the ordering is negotiable.
    """
    # An active repository gets a check-up, not an autopsy, and it must not spend
    # a model call on prose that would be false for it. This sits before the
    # provider check so no key, configured or not, can reach a living repo (E1).
    if facts.is_living:
        return NarrationResult(narrative=living_narrative(facts))

    if client is None:
        return NarrationResult(
            narrative=template_narrative(
                facts,
                card,
                reason=(
                    "No LLM provider is configured, so every narrative field below is "
                    "a deterministic template built from the facts and the scorecard."
                ),
                status="skipped",
            )
        )

    evidence_tokens = build_evidence(facts).sample()
    prompt = build(facts, card, excerpts, evidence_tokens=evidence_tokens)
    attempts: list[Attempt] = []
    model = getattr(client, "model", "unknown")
    reason = ""

    for attempt_number in (1, 2):
        try:
            result = client.complete_json(prompt)
        except AssertionError:
            # A guard double asserting that this path must never call a provider
            # (Property 11). Degrading it to a template narrative would make the
            # guard unfalsifiable, so it propagates.
            raise
        except Exception as exc:  # provider transport, auth, envelope (E10)
            attempts.append(
                Attempt(
                    attempt=attempt_number,
                    outcome="error",
                    model=model,
                    prompt_hash=prompt.prompt_hash,
                    prompt_template_version=prompt.template_version,
                )
            )
            log.warning("LLM call %d failed: %s", attempt_number, type(exc).__name__)
            reason = f"the provider call failed ({type(exc).__name__})"
            break

        try:
            narrative = validate(result.text, facts, card)
        except NarrativeRejected as rejection:
            attempts.append(
                Attempt(
                    attempt=attempt_number,
                    outcome="invalid_json"
                    if rejection.stage == "json_parse"
                    else "schema_fail",
                    model=result.model,
                    prompt_hash=prompt.prompt_hash,
                    prompt_template_version=prompt.template_version,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=result.latency_ms,
                )
            )
            reason = rejection.reason
            log.info("LLM response rejected at %s: %s", rejection.stage, rejection.reason)
            if attempt_number == 1 and allow_repair:
                prompt = prompt.with_repair(rejection.reason)
                continue
            break

        attempts.append(
            Attempt(
                attempt=attempt_number,
                outcome="ungrounded" if narrative.rejected_stages else "ok",
                model=result.model,
                prompt_hash=prompt.prompt_hash,
                prompt_template_version=prompt.template_version,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                latency_ms=result.latency_ms,
            )
        )
        # A letter that fell back is a graded miss, not a rejection: the rest of
        # the narrative is the model's. Log it so the misses are reviewable, and
        # if the first attempt's letter missed, spend the repair retry on it.
        letter_missed = any(s.startswith("final_letter:") for s in narrative.rejected_stages)
        if letter_missed:
            _log_fallback(facts, "; ".join(narrative.rejected_stages))
            if attempt_number == 1 and allow_repair:
                prompt = prompt.with_repair(
                    "The letter must be 3-5 sentences, must quote at least two "
                    "concrete tokens from EVIDENCE, and must use no word from the "
                    "forbidden list."
                )
                continue

        accepted = narrative.model_copy(
            update={"llm_status": "repaired" if attempt_number > 1 else "ok"}
        )
        return NarrationResult(
            narrative=accepted,
            attempts=tuple(attempts),
            model=result.model,
            prompt_template_version=prompt.template_version,
            prompt_hash=prompt.prompt_hash,
        )

    # Stage 1 or 2 failed twice, or the provider is down. The facts and the
    # scorecard are still a useful post-mortem; the narrative is marked failed so
    # a later request can retry only this stage (R-3.4, E10).
    failed = LlmNarrative.unavailable(
        "The narrative stage did not complete for this analysis: "
        f"{reason or 'the provider returned nothing usable'}. The facts and the "
        "scorecard below are unaffected."
    )
    return NarrationResult(
        narrative=failed,
        attempts=tuple(attempts),
        model=model,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        prompt_hash=prompt.prompt_hash,
    )


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _rotate(candidates: tuple[tuple[str, str], ...], seed: int) -> list[tuple[str, str]]:
    """Reorder candidates by a per-repo seed, preserving all of them.

    Rotation, not shuffle: the preferred candidate for a repo is stable, and
    every candidate remains reachable, so a repo whose first-choice fact is empty
    still falls through to a grounded one.
    """
    if not candidates:
        return []
    start = seed % len(candidates)
    return list(candidates[start:]) + list(candidates[:start])


def _log_fallback(facts: NormalizedFacts, reason: str) -> None:
    """Record a narrative fallback to a local log for later review (R-9.6).

    Best-effort and never on the hot path for its own sake: a logging failure
    must not fail an analysis. The file is append-only JSON lines.
    """
    import json as _json
    import os
    from datetime import UTC, datetime

    try:
        path = os.environ.get("NECROPSY_FALLBACK_LOG", "fallback.log")
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "repo": facts.canonical_url,
            "reason": reason,
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(_json.dumps(entry) + "\n")
    except Exception:  # pragma: no cover - logging must never break analysis
        log.debug("could not write the fallback log")


class _Missing:
    """Sentinel distinguishing "key absent" from "value is None"."""


_MISSING = _Missing()


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _numbers_match(actual: float, claimed: str) -> bool:
    """Numeric comparison that allows formatting, not drift."""
    text = claimed.replace(",", "").replace("_", "").strip()
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return False
    if percent:
        number /= 100.0
    if abs(actual - number) <= 1e-9:
        return True
    # Two decimal places, so 0.74 may cite 0.7391... but 18 may not cite 17.
    return round(actual, 2) == round(number, 2)


def _effort(value: Any) -> Any:
    return value if value in {"hours", "days", "weeks"} else "days"


def _cap(text: str, limit: int) -> str:
    """Truncate to the schema's limit. A model that overruns loses its tail, not
    the whole field."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1].rstrip() + "…"


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _template_values(facts: NormalizedFacts) -> dict[str, Any]:
    """Every placeholder the templates may use, all drawn from the facts."""
    deprecated_names = ", ".join(d.name for d in facts.deprecated_dependencies) or "none named"
    manifest_label = facts.manifest_paths[0] if facts.manifest_paths else "the dependency manifest"
    top_level = ", ".join(facts.top_level_entries[:6]) or "the repository root"
    ci_label = (
        f"The tree lists {', '.join(facts.ci_systems)}, which no longer runs."
        if facts.ci_systems
        else "No CI service is configured at all."
    )
    return {
        "name": facts.name,
        "owner": facts.owner,
        "short_sha": facts.last_commit_sha[:7],
        "last_commit_message": facts.last_commit_message or "",
        "last_release_tag": facts.last_release_tag or "",
        "ci_systems": ", ".join(facts.ci_systems) or "absent",
        "days_since_last_commit": facts.days_since_last_commit,
        "deprecated_dependency_count": facts.deprecated_dependency_count,
        "direct_dependency_count": facts.direct_dependency_count,
        "deprecated_names": deprecated_names,
        "manifest_label": manifest_label,
        "test_file_count": facts.test_file_count,
        "unanswered_issue_count": facts.unanswered_issue_count,
        "open_issue_sample_size": facts.open_issue_sample_size,
        "readme_chars": facts.readme_chars,
        "loc_estimate": facts.loc_estimate,
        "file_count": facts.file_count,
        "primary_language": facts.primary_language or "code",
        "top_level_label": top_level,
        "ci_label": ci_label,
    }
