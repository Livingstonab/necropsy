"""LOC estimation without cloning (design section 4.4). Pure.

GitHub reports bytes per language, not lines. Dividing by a per-language average
line length gives a figure that is good enough to separate a weekend script from
a monolith, which is all `codebase_scale` needs it for.

It is an estimate and it is always labelled one (P7, R-1). The constants are
committed here so a reader can check the arithmetic, and `loc_confidence` drops
to `low` whenever the inputs make the estimate less trustworthy: no language data
at all, a tiny codebase where rounding dominates, or a single language claiming
almost every byte while the tree is full of non-source files, which is the
signature of vendored or generated content.

No clock, no I/O, no environment (E6).
"""

from __future__ import annotations

from typing import Literal

Confidence = Literal["high", "medium", "low"]

#: Average bytes per line, by language. Committed constants, not magic numbers:
#: a reader can divide the reported byte count and get the reported estimate.
BYTES_PER_LINE: dict[str, int] = {
    "Python": 30,
    "JavaScript": 32,
    "TypeScript": 34,
    "Java": 38,
    "Go": 30,
    "C": 28,
    "C++": 30,
    "C#": 34,
    "Ruby": 26,
    "PHP": 30,
    "Rust": 32,
    "Swift": 32,
    "Kotlin": 34,
    "Scala": 34,
    "Objective-C": 32,
    "Perl": 28,
    "Shell": 24,
    "PowerShell": 28,
    "Lua": 26,
    "Elixir": 28,
    "Erlang": 30,
    "Haskell": 28,
    "Dart": 30,
    "R": 28,
    "MATLAB": 28,
    "HTML": 45,
    "CSS": 28,
    "SCSS": 28,
    "Less": 28,
    "Vue": 34,
    "Markdown": 60,
    "JSON": 40,
    "YAML": 30,
    "XML": 45,
    "SQL": 30,
    "Dockerfile": 30,
    "Makefile": 26,
    "TeX": 45,
}

#: Used for any language not in the table above (design section 4.4).
DEFAULT_BYTES_PER_LINE = 32

#: Below this many total bytes the estimate is dominated by rounding.
TINY_CODEBASE_BYTES = 1_000

#: A single language above this share, with a file-heavy tree, suggests
#: vendored or generated content inflating the byte count.
SKEW_SHARE = 0.95


def bytes_per_line(language: str) -> int:
    return BYTES_PER_LINE.get(language, DEFAULT_BYTES_PER_LINE)


def estimate_loc(
    languages: dict[str, int],
    *,
    file_count: int = 0,
    skew_suspected: bool = False,
) -> tuple[int, Confidence]:
    """Return `(loc_estimate, confidence)` from language byte counts.

    `skew_suspected` is the tree-derived hint described above; `normalize()`
    passes it so this function stays free of tree-parsing logic.
    """
    usable = {
        lang: int(count)
        for lang, count in (languages or {}).items()
        if isinstance(count, (int, float)) and count > 0
    }
    if not usable:
        return 0, "low"

    estimate = sum(round(count / bytes_per_line(lang)) for lang, count in usable.items())
    total_bytes = sum(usable.values())

    dominant_share = max(usable.values()) / total_bytes
    single_language_skew = (
        len(usable) == 1 or dominant_share >= SKEW_SHARE
    ) and (skew_suspected or file_count > 400)

    if total_bytes < TINY_CODEBASE_BYTES or single_language_skew:
        confidence: Confidence = "low"
    elif len(usable) == 1 or total_bytes < 50_000:
        confidence = "medium"
    else:
        confidence = "high"

    return estimate, confidence


def derivation(languages: dict[str, int]) -> list[dict[str, object]]:
    """Per-language working, so the estimate can be checked by hand (P7, R-7.2)."""
    rows: list[dict[str, object]] = []
    for lang, count in sorted((languages or {}).items(), key=lambda kv: -kv[1]):
        divisor = bytes_per_line(lang)
        rows.append(
            {
                "language": lang,
                "bytes": count,
                "bytes_per_line": divisor,
                "lines": round(count / divisor),
                "assumed_default": lang not in BYTES_PER_LINE,
            }
        )
    return rows
