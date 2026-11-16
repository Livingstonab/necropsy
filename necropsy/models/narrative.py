"""Narrative models: everything the single LLM call returns (R-15.1).

Each narrative field carries its own `source` so the UI can label template
fallbacks honestly (P2, P7).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["llm", "template", "fallback", "unavailable"]
Effort = Literal["hours", "days", "weeks"]


class Evidence(BaseModel):
    fact_key: str
    fact_value: str
    claim: str


class CauseOfDeath(BaseModel):
    headline: str
    narrative: str
    evidence: list[Evidence] = Field(default_factory=list)
    source: Source = "fallback"


class Epitaph(BaseModel):
    text: str
    source: Source = "template"
    evidence: Evidence | None = None


class Letter(BaseModel):
    """The letter the dead repo never sent its creator (the emotional layer).

    First person, from the repo, 3–5 sentences, built only from evidence tokens.
    `source` labels whether a model wrote it or the deterministic template
    assembled it from facts — the fallback is never hidden (P2, P7).
    """

    text: str
    source: Source = "template"
    #: How many distinct evidence tokens the text was verified to contain.
    evidence_token_count: int = 0


class ResurrectionStep(BaseModel):
    order: int
    title: str
    detail: str
    addresses_factor: str
    estimated_effort: Effort


class MissingItem(BaseModel):
    key: str
    label: str
    present: bool
    detail: str


class LlmNarrative(BaseModel):
    what_was_built_summary: str
    cause_of_death: CauseOfDeath
    whats_missing_narrative: str
    whats_missing_narrative_source: Source = "fallback"
    final_letter: Letter = Field(default_factory=lambda: Letter(text="", source="unavailable"))
    epitaph: Epitaph
    resurrection_plan: list[ResurrectionStep]
    resurrection_plan_source: Source = "fallback"
    summary_source: Source = "fallback"
    confidence_note: str = ""
    llm_status: Literal["ok", "repaired", "failed", "skipped", "fallback"] = "skipped"
    rejected_stages: list[str] = Field(default_factory=list)
    #: True for an active repository: the prose sections are `unavailable` with a
    #: reason, because there is nothing to bury (P6, P7). Stored with the
    #: narrative so a cached exhibit renders the same way on every read.
    living: bool = False

    @classmethod
    def unavailable(cls, reason: str) -> LlmNarrative:
        """The narrative for a stored analysis whose LLM stage failed (R-1.3, R-3.4).

        Deterministic, so an analysis row with `narrative_json IS NULL` rebuilds
        to the same object every time it is read. Every prose field is labelled
        `unavailable` with its reason rather than guessed (P7).
        """
        return cls(
            what_was_built_summary=reason,
            cause_of_death=CauseOfDeath(
                headline="Narrative unavailable",
                narrative=reason,
                evidence=[],
                source="unavailable",
            ),
            whats_missing_narrative=reason,
            whats_missing_narrative_source="unavailable",
            final_letter=Letter(text=reason, source="unavailable"),
            epitaph=Epitaph(text=reason, source="unavailable", evidence=None),
            resurrection_plan=[],
            resurrection_plan_source="unavailable",
            summary_source="unavailable",
            confidence_note=reason,
            llm_status="failed",
            rejected_stages=["narrative"],
        )
