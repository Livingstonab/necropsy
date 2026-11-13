"""JSON Schema for the one object the LLM returns (design section 4.5, R-15.1).

One call, one object, every narrative field. Three sequential calls would be a
defect, and so would a second call for "just the epitaph" (E9).

`additionalProperties: false` throughout: a model that invents a field is a
model whose output we have stopped understanding, and the repair retry exists
for exactly that case.
"""

from __future__ import annotations

from typing import Any

#: Bumping this requires updating `PROMPT_TEMPLATE_VERSION` in the same change.
RESPONSE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "NecropsyNarrative",
    "type": "object",
    "required": [
        "what_was_built_summary",
        "cause_of_death",
        "whats_missing_narrative",
        "final_letter",
        "epitaph",
        "epitaph_evidence",
        "resurrection_plan",
        "confidence_note",
    ],
    "additionalProperties": False,
    "properties": {
        "what_was_built_summary": {"type": "string", "minLength": 1, "maxLength": 400},
        "cause_of_death": {
            "type": "object",
            "required": ["headline", "narrative", "evidence"],
            "additionalProperties": False,
            "properties": {
                "headline": {"type": "string", "minLength": 1, "maxLength": 120},
                "narrative": {"type": "string", "minLength": 1, "maxLength": 700},
                "evidence": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "required": ["fact_key", "fact_value", "claim"],
                        "additionalProperties": False,
                        "properties": {
                            "fact_key": {"type": "string", "minLength": 1, "maxLength": 80},
                            "fact_value": {"type": "string", "maxLength": 200},
                            "claim": {"type": "string", "minLength": 1, "maxLength": 160},
                        },
                    },
                },
            },
        },
        "whats_missing_narrative": {"type": "string", "minLength": 1, "maxLength": 500},
        "final_letter": {"type": "string", "minLength": 1, "maxLength": 600},
        "epitaph": {"type": "string", "minLength": 1, "maxLength": 140},
        "epitaph_evidence": {
            "type": "object",
            "required": ["fact_key", "fact_value"],
            "additionalProperties": False,
            "properties": {
                "fact_key": {"type": "string", "minLength": 1, "maxLength": 80},
                "fact_value": {"type": "string", "maxLength": 200},
            },
        },
        "resurrection_plan": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": [
                    "order",
                    "title",
                    "detail",
                    "addresses_factor",
                    "estimated_effort",
                ],
                "additionalProperties": False,
                "properties": {
                    "order": {"type": "integer", "minimum": 1, "maximum": 3},
                    "title": {"type": "string", "minLength": 1, "maxLength": 80},
                    "detail": {"type": "string", "minLength": 1, "maxLength": 400},
                    "addresses_factor": {"type": "string", "minLength": 1, "maxLength": 40},
                    "estimated_effort": {"enum": ["hours", "days", "weeks"]},
                },
            },
        },
        "confidence_note": {"type": "string", "maxLength": 200},
    },
}


def schema_for_prompt() -> dict[str, Any]:
    """The schema as shown to the model: no `$schema`, no title, just the shape."""
    trimmed = {k: v for k, v in RESPONSE_SCHEMA.items() if k not in {"$schema", "title"}}
    return trimmed
