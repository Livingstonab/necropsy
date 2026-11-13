"""Vital signs and the ambition-vs-reality view (presentation derivation only).

This module adds no data and reads no source. It is a pure projection of the
`NormalizedFacts` the pipeline already produced, shaped for the plaque. The
distinction matters for E4: NECROPSY sees GitHub metadata and the one tree call,
never file contents beyond the README and the manifest, so "Delivered" is
computed from signals we legitimately have — file count, language mix, whether a
database dependency appears *in the manifest* — never from reading routes out of
source files, which we do not fetch.

Everything here is a function of facts, so it is deterministic and cache-safe: a
stored exhibit renders the same vitals every time (E6, Property 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from necropsy.models.facts import NormalizedFacts

#: Dependency names (lowercased, substring-matched) that imply a datastore. Drawn
#: from the manifest only — the one place dependencies are legitimately read.
_DB_MARKERS: tuple[str, ...] = (
    "postgres", "psycopg", "pg", "mysql", "mariadb", "sqlite", "sqlalchemy",
    "mongo", "mongoose", "redis", "cassandra", "dynamodb", "sequelize",
    "prisma", "typeorm", "activerecord", "gorm", "ent", "diesel", "sqlx",
    "hibernate", "jpa", "mybatis", "doctrine", "eloquent",
)

#: Web-framework dependency names that imply the project serves routes. Again
#: from the manifest, not from counting endpoints in source we never read.
_WEB_MARKERS: tuple[str, ...] = (
    "flask", "django", "fastapi", "express", "koa", "hapi", "nestjs", "next",
    "rails", "sinatra", "gin", "echo", "fiber", "actix", "rocket", "spring",
    "laravel", "symfony", "phoenix", "gorilla/mux", "chi", "starlette",
)


@dataclass(frozen=True)
class VitalSign:
    """One row of the health strip: a label, a verdict, and a plain detail."""

    key: str
    label: str
    present: bool
    detail: str


@dataclass(frozen=True)
class LanguageSlice:
    """One language's share of the codebase, for the composition bar."""

    name: str
    bytes: int
    percent: float


@dataclass(frozen=True)
class Vitals:
    """The full vital-signs panel and the ambition-vs-reality pair."""

    languages: list[LanguageSlice] = field(default_factory=list)
    language_count: int = 0
    loc_estimate: int = 0
    loc_confidence: str = "low"
    signs: list[VitalSign] = field(default_factory=list)
    promised: str | None = None
    delivered: str = ""
    has_database: bool = False
    serves_web: bool = False

    @property
    def strength_line(self) -> str:
        """A one-line verdict on how much is actually here. Factual, not a grade."""
        present = sum(1 for s in self.signs if s.present)
        return f"{present} of {len(self.signs)} structural supports present"


def compute_vitals(facts: NormalizedFacts) -> Vitals:
    """Project facts onto the vital-signs panel. Pure; adds no data."""
    languages = _language_slices(facts)
    signs = _vital_signs(facts)
    has_db = _manifest_mentions(facts, _DB_MARKERS)
    serves_web = _manifest_mentions(facts, _WEB_MARKERS)
    return Vitals(
        languages=languages,
        language_count=len(facts.languages),
        loc_estimate=facts.loc_estimate,
        loc_confidence=facts.loc_confidence,
        signs=signs,
        promised=_promised(facts),
        delivered=_delivered(facts, has_db, serves_web),
        has_database=has_db,
        serves_web=serves_web,
    )


def _language_slices(facts: NormalizedFacts) -> list[LanguageSlice]:
    """Each language as a percentage of total bytes, largest first.

    This is the "which languages, and how much of each" view. GitHub reports
    bytes per language; we never need the source to say a repo is 78% Ruby.
    """
    total = sum(facts.languages.values())
    if total <= 0:
        return []
    ordered = sorted(facts.languages.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        LanguageSlice(name=name, bytes=count, percent=round(count / total * 100, 1))
        for name, count in ordered
    ]


def _vital_signs(facts: NormalizedFacts) -> list[VitalSign]:
    """The health strip: the structural supports a living project usually has."""
    return [
        VitalSign(
            "tests",
            "Tests",
            facts.has_tests,
            f"{facts.test_file_count} test file(s)" if facts.has_tests else "none in the tree",
        ),
        VitalSign(
            "ci",
            "CI",
            facts.has_ci,
            (", ".join(facts.ci_systems) + (" (legacy)" if facts.ci_is_legacy_only else ""))
            if facts.has_ci
            else "no workflow",
        ),
        VitalSign(
            "license",
            "Licence",
            facts.has_license,
            (facts.license_spdx or "present")
            + ("" if facts.license_is_osi else ", not OSI")
            if facts.has_license
            else "none",
        ),
        VitalSign(
            "docs",
            "Docs",
            facts.doc_page_count > 0,
            f"{facts.doc_page_count} page(s)" if facts.doc_page_count else "README only",
        ),
        VitalSign(
            "deps",
            "Dependencies",
            facts.manifest_found and facts.deprecated_dependency_count == 0,
            (
                f"{facts.deprecated_dependency_count} deprecated of "
                f"{facts.direct_dependency_count}"
                if facts.manifest_found
                else "no manifest found"
            ),
        ),
        VitalSign(
            "lockfile",
            "Lockfile",
            facts.has_lockfile,
            "pinned" if facts.has_lockfile else "unpinned",
        ),
    ]


def _manifest_mentions(facts: NormalizedFacts, markers: tuple[str, ...]) -> bool:
    """True when a declared dependency name matches a marker. Manifest only (E4)."""
    names = " ".join(d.name.lower() for d in facts.dependencies)
    return any(marker in names for marker in markers)


def _promised(facts: NormalizedFacts) -> str | None:
    """The README's first real descriptive sentence, markdown stripped (<=120 chars).

    "Promised" is the author's own claim about the project, quoted. If there is
    no README, there is no promise to show — we say nothing rather than invent
    one (P7).
    """
    text = facts.readme_excerpt
    if not text:
        return None
    cleaned_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!", ">", "|", "```", "---", "===")):
            continue
        line = _strip_markdown(line)
        if len(line) >= 20:
            cleaned_lines.append(line)
            break
    if not cleaned_lines:
        return None
    sentence = cleaned_lines[0].split(". ")[0].strip().rstrip(".")
    if len(sentence) > 120:
        sentence = sentence[:117].rstrip() + "…"
    return sentence


def _delivered(facts: NormalizedFacts, has_db: bool, serves_web: bool) -> str:
    """What the metadata actually shows was built. Computed from signals we have.

    Deliberately not a count of routes read out of source — NECROPSY does not
    fetch source (E4). It states file count, the web/database shape inferred from
    the manifest, and the test verdict, all of which are honest from metadata.
    """
    parts = [f"{facts.file_count} files"]
    if serves_web:
        parts.append("a web framework")
    parts.append("a database." if has_db else "no database.")
    if facts.has_tests:
        parts.append(f"{facts.test_file_count} test files.")
    else:
        parts.append("no tests.")
    return " ".join(parts)


def _strip_markdown(line: str) -> str:
    """Remove the common inline markdown so a sentence reads as prose."""
    out = line
    for token in ("**", "__", "`", "*", "_"):
        out = out.replace(token, "")
    # Collapse [text](url) to text.
    while "[" in out and "](" in out and ")" in out:
        start = out.index("[")
        mid = out.index("](", start)
        end = out.index(")", mid)
        out = out[:start] + out[start + 1 : mid] + out[end + 1 :]
    return out.strip()
