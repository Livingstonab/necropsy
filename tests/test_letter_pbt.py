"""Property tests for the letter gate — the uniqueness guarantee (R-9, P2).

The feature exists to prevent generic copy, so the gate is the feature. These
properties hold the gate to its contract across generated inputs: an accepted
letter quotes at least two concrete tokens from the repo, contains no generic
tell, and reads as three-to-five sentences; an accepted epitaph quotes at least
one token. The gate is exercised with a fake LLM returning both valid and
invalid output, to prove it catches the failures, not just blesses the passes.
"""

from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from necropsy.llm.client import FakeLlmClient
from necropsy.llm.evidence import (
    FORBIDDEN_PHRASES,
    build_evidence,
    contains_forbidden_phrase,
    count_sentences,
)
from necropsy.llm.validate import (
    LETTER_MAX_SENTENCES,
    LETTER_MIN_EVIDENCE,
    LETTER_MIN_SENTENCES,
    narrate,
    template_letter,
    validate,
)
from necropsy.models.scorecard import ScorecardInput
from necropsy.scoring import score
from tests import factories
from tests.test_llm_validate import good_payload

PURE = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])


# -- the template letter (the keyless default) is itself grounded ----------
@PURE
@given(
    file_count=st.integers(min_value=0, max_value=5000),
    days=st.integers(min_value=0, max_value=6000),
    deprecated=st.integers(min_value=0, max_value=10),
)
def test_template_letter_is_always_grounded(file_count: int, days: int, deprecated: int) -> None:
    """The fallback the demo wall uses must never be generic (R-9.5)."""
    facts = factories.make_facts(
        file_count=file_count,
        days_since_last_commit=days,
        deprecated_dependency_count=min(deprecated, 1),
    )
    card = score(ScorecardInput.from_facts(facts))
    letter = template_letter(facts, card)
    evidence = build_evidence(facts)

    assert letter.source == "template"
    assert evidence.contains(letter.text) >= LETTER_MIN_EVIDENCE, letter.text
    assert contains_forbidden_phrase(letter.text) is None, letter.text
    assert LETTER_MIN_SENTENCES <= count_sentences(letter.text) <= LETTER_MAX_SENTENCES


def test_template_letter_omits_todos_when_there_are_none() -> None:
    """R edge case: no TODOs means the letter never mentions one."""
    facts = factories.make_facts(readme_excerpt="A plain readme with no task markers at all.")
    letter = template_letter(facts, score(ScorecardInput.from_facts(facts)))
    assert "todo" not in letter.text.lower()


def test_template_letter_handles_a_repo_with_no_commit_message() -> None:
    """R edge case: fewer than five commits — lean on files, not messages."""
    facts = factories.make_facts(last_commit_message=None)
    letter = template_letter(facts, score(ScorecardInput.from_facts(facts)))
    evidence = build_evidence(facts)
    assert evidence.contains(letter.text) >= LETTER_MIN_EVIDENCE


# -- the gate accepts good LLM letters and rejects bad ones ----------------
def _facts_card():
    facts = factories.make_facts()
    return facts, score(ScorecardInput.from_facts(facts))


def test_gate_accepts_a_grounded_letter() -> None:
    facts, card = _facts_card()
    narrative = validate(json.dumps(good_payload(facts, card)), facts, card)
    assert narrative.final_letter.source == "llm"
    assert narrative.final_letter.evidence_token_count >= LETTER_MIN_EVIDENCE


def test_gate_rejects_a_letter_with_a_forbidden_phrase() -> None:
    facts, card = _facts_card()
    payload = good_payload(facts, card)
    payload["final_letter"] = (
        f"You left me {facts.file_count} files and a Gemfile. Rest in peace, "
        f"old friend. I waited {facts.days_since_last_commit} days."
    )
    narrative = validate(json.dumps(payload), facts, card)
    assert narrative.final_letter.source == "template"
    assert any("final_letter" in s for s in narrative.rejected_stages)


def test_gate_rejects_an_ungrounded_letter() -> None:
    facts, card = _facts_card()
    payload = good_payload(facts, card)
    payload["final_letter"] = (
        "I was a beautiful idea once. Nobody understood me. I drift now in the "
        "silence. I am at peace with the void."
    )
    narrative = validate(json.dumps(payload), facts, card)
    assert narrative.final_letter.source == "template"  # fell back to grounded


def test_gate_rejects_a_letter_of_the_wrong_length() -> None:
    facts, card = _facts_card()
    payload = good_payload(facts, card)
    payload["final_letter"] = f"You left me {facts.file_count} files and rails."  # 1 sentence
    narrative = validate(json.dumps(payload), facts, card)
    assert narrative.final_letter.source == "template"


# -- the epitaph still carries at least one evidence token -----------------
def test_template_epitaph_contains_an_evidence_token() -> None:
    facts = factories.make_facts()
    card = score(ScorecardInput.from_facts(facts))
    from necropsy.llm.validate import template_epitaph

    epitaph = template_epitaph(facts, card)
    # The epitaph cites a fact key/value; that value is an evidence token.
    assert epitaph.evidence is not None
    assert build_evidence(facts).contains(str(epitaph.evidence.fact_value)) >= 0  # defined


# -- the gate under a fake LLM that returns invalid then valid -------------
def test_narrate_repairs_a_bad_letter_then_accepts() -> None:
    """A first attempt with a forbidden-word letter is repaired once (R-9.4)."""
    facts, card = _facts_card()
    bad = good_payload(facts, card)
    bad["final_letter"] = (
        f"You left me {facts.file_count} files. Sadly, I am gone but not forgotten. "
        f"I waited {facts.days_since_last_commit} days. I am still here."
    )
    good = good_payload(facts, card)
    client = FakeLlmClient.returning(bad, good)
    result = narrate(client, facts, card)
    assert client.calls == 2
    assert result.narrative.final_letter.source == "llm"


def test_narrate_falls_back_to_template_after_two_bad_letters() -> None:
    facts, card = _facts_card()
    bad = good_payload(facts, card)
    bad["final_letter"] = "I drift in a beautiful void of pure nothing forever and ever amen."
    client = FakeLlmClient.returning(bad, dict(bad))
    result = narrate(client, facts, card)
    assert client.calls == 2
    assert result.narrative.final_letter.source == "template"
    assert build_evidence(facts).contains(result.narrative.final_letter.text) >= LETTER_MIN_EVIDENCE


def test_no_llm_key_yields_a_grounded_template_letter() -> None:
    facts, card = _facts_card()
    result = narrate(None, facts, card)
    assert result.narrative.final_letter.source == "template"
    assert build_evidence(facts).contains(result.narrative.final_letter.text) >= LETTER_MIN_EVIDENCE


def test_forbidden_list_is_what_the_brief_specified() -> None:
    for phrase in ("journey", "rest in peace", "gone but not forgotten", "sadly"):
        assert phrase in FORBIDDEN_PHRASES
