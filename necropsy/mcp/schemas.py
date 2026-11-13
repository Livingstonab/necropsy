"""JSON Schemas for the three MCP tools (design section 7, R-11.2).

The schemas are hand-written and committed because the MCP tool surface is the
public contract Kiro consumes (design 1.1): it has to be stable and reviewable,
not regenerated from whatever the models happen to look like this week.

Output is not re-described field by field here. Every tool returns the payload
`Analysis.to_payload()` produces (or `list_graveyard`'s summary rows), so the
output contract lives with the model that produces it (E8). What these schemas
pin down is the *input*, where a bad call should be rejected before it reaches
the pipeline.
"""

from __future__ import annotations

from typing import Any

ANALYZE_REPO_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "A GitHub repository URL or bare owner/name.",
            "minLength": 1,
        },
        "force_refresh": {
            "type": "boolean",
            "default": False,
            "description": "Bypass the cache read. Still respects the 10-call budget.",
        },
        "allow_network": {
            "type": "boolean",
            "default": True,
            "description": (
                "When false, return cached results only and make no GitHub or LLM "
                "call. A repository that is not cached returns OFFLINE_CACHE_MISS."
            ),
        },
    },
    "required": ["url"],
    "additionalProperties": False,
}

GET_EXHIBIT_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo_url": {"type": "string", "description": "A GitHub URL or owner/name."},
        "slug": {"type": "string", "description": "An exhibit slug, owner--name."},
    },
    "oneOf": [{"required": ["repo_url"]}, {"required": ["slug"]}],
    "additionalProperties": False,
}

LIST_GRAVEYARD_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "sort": {
            "type": "string",
            "enum": ["curated", "deadest", "hardest"],
            "default": "curated",
        },
        "demo_only": {"type": "boolean", "default": False},
    },
    "additionalProperties": False,
}


TOOL_DESCRIPTIONS: dict[str, str] = {
    "analyze_repo": (
        "Perform a post-mortem on a public GitHub repository and return the full "
        "structured analysis: normalized facts, a transparent revival-difficulty "
        "scorecard with every factor's inputs and contribution, an evidence-backed "
        "cause of death, and a three-step resurrection plan. Uses a cache keyed by "
        "commit SHA, so an already-analyzed repository returns instantly."
    ),
    "get_exhibit": (
        "Return a cached post-mortem by repository URL or exhibit slug. Never starts "
        "an analysis and never touches the network: a repository with no cached "
        "analysis returns NOT_IN_GRAVEYARD."
    ),
    "list_graveyard": (
        "List exhibits on the graveyard wall as summaries (slug, score, band, "
        "epitaph, days since last commit). Zero network calls. Sortable by curated "
        "order, deadest, or hardest to revive."
    ),
    "write_revival_spec": (
        "Return a Kiro-ready revival spec (Markdown) for a cached exhibit: the "
        "epitaph, the cause of death with evidence, the full factor scorecard, and "
        "the three-step plan expanded into checklist tasks, each naming the exact "
        "factor it addresses. Reads the cache only; never triggers an analysis."
    ),
}

WRITE_REVIVAL_SPEC_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo_url": {"type": "string", "description": "A GitHub URL or owner/name."},
        "slug": {"type": "string", "description": "An exhibit slug, owner--name."},
    },
    "oneOf": [{"required": ["repo_url"]}, {"required": ["slug"]}],
    "additionalProperties": False,
}
