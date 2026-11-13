"""The curated graveyard manifest: parse, validate, and say what is wrong (R-5.3).

The manifest is the declaration of what the demo wall contains; the fixtures are
the materialization of it. Keeping them separate is what makes R-17.3 possible —
`--offline` can compare the two and name the difference instead of reaching for
the network to paper over it.

Validation is strict and reports every problem at once rather than the first,
because the operator fixing a manifest wants the whole list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from necropsy.scoring.bands import BANDS

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: R-5.1 requires between six and eight exhibits on the wall.
MIN_ENTRIES = 6
MAX_ENTRIES = 8

_BAND_KEYS = frozenset(b.key for b in BANDS)
_PROVENANCE_VALUES = frozenset({"compiled_specimen", "api_verified"})


class ManifestError(ValueError):
    """The manifest does not describe a loadable graveyard."""


@dataclass(frozen=True)
class ManifestEntry:
    """One curated exhibit, as declared."""

    slug: str
    canonical_url: str
    pinned_commit_sha: str
    curator_note: str
    display_order: int
    expected_band: str | None
    fixture: str
    fixture_provenance: str
    primary_language: str | None = None
    failure_mode: str | None = None

    @property
    def fixture_path(self) -> Path:
        """Resolved under `graveyard/fixtures/` only.

        The filename comes from a committed manifest rather than from a
        repository, but it still gets no opportunity to escape its directory: a
        name with a separator in it is rejected at validation (E4).
        """
        return FIXTURE_DIR / self.fixture

    @property
    def api_verified(self) -> bool:
        return self.fixture_provenance == "api_verified"

    @property
    def provenance_label(self) -> str:
        """What the exhibit footer shows about where its data came from (P7)."""
        if self.api_verified:
            return f"fetched from the GitHub API at commit {self.pinned_commit_sha[:7]}"
        return (
            "compiled specimen: this exhibit's payloads were written offline to "
            "demonstrate a failure mode, and are not a capture of a live repository"
        )


@dataclass(frozen=True)
class Manifest:
    """The whole declared graveyard."""

    entries: tuple[ManifestEntry, ...]
    version: int = 1
    compiled_at: str | None = None
    note: str | None = None

    def __len__(self) -> int:
        return len(self.entries)

    def by_slug(self, slug: str) -> ManifestEntry | None:
        return next((e for e in self.entries if e.slug == slug), None)

    @property
    def ordered(self) -> tuple[ManifestEntry, ...]:
        return tuple(sorted(self.entries, key=lambda e: (e.display_order, e.slug)))

    @property
    def all_api_verified(self) -> bool:
        return all(e.api_verified for e in self.entries)


def load(path: Path | None = None) -> Manifest:
    """Read and validate the manifest.

    Raises:
        ManifestError: the file is missing, unparseable, or describes a graveyard
            that cannot be loaded. The message lists every problem found.
    """
    source = path or MANIFEST_PATH
    if not source.exists():
        raise ManifestError(f"no graveyard manifest at {source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ManifestError(f"{source.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{source.name} must contain a JSON object")
    return parse(raw)


def parse(raw: dict[str, Any]) -> Manifest:
    """Build a `Manifest` from a decoded document, validating as we go."""
    problems: list[str] = []
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        raise ManifestError("the manifest has no 'entries' array")

    entries: list[ManifestEntry] = []
    for index, item in enumerate(entries_raw):
        if not isinstance(item, dict):
            problems.append(f"entry {index} is not an object")
            continue
        missing = [
            field
            for field in (
                "slug",
                "canonical_url",
                "pinned_commit_sha",
                "curator_note",
                "display_order",
                "fixture",
                "fixture_provenance",
            )
            if not item.get(field) and item.get(field) != 0
        ]
        if missing:
            problems.append(f"entry {index} is missing: {', '.join(missing)}")
            continue

        slug = str(item["slug"])
        fixture = str(item["fixture"])
        if "/" in fixture or "\\" in fixture or fixture.startswith("."):
            problems.append(f"{slug}: fixture must be a bare filename, got {fixture!r}")
            continue
        band = item.get("expected_band")
        if band is not None and str(band) not in _BAND_KEYS:
            problems.append(f"{slug}: expected_band {band!r} is not a known band")
        provenance = str(item["fixture_provenance"])
        if provenance not in _PROVENANCE_VALUES:
            problems.append(
                f"{slug}: fixture_provenance must be one of "
                f"{sorted(_PROVENANCE_VALUES)}, got {provenance!r}"
            )
            continue

        entries.append(
            ManifestEntry(
                slug=slug,
                canonical_url=str(item["canonical_url"]).strip().lower(),
                pinned_commit_sha=str(item["pinned_commit_sha"]),
                curator_note=str(item["curator_note"]),
                display_order=int(item["display_order"]),
                expected_band=str(band) if band else None,
                fixture=fixture,
                fixture_provenance=provenance,
                primary_language=(
                    str(item["primary_language"]) if item.get("primary_language") else None
                ),
                failure_mode=(
                    str(item["failure_mode"]) if item.get("failure_mode") else None
                ),
            )
        )

    problems.extend(_structural_problems(entries))
    if problems:
        raise ManifestError(
            "the graveyard manifest is not loadable:\n  - " + "\n  - ".join(problems)
        )

    return Manifest(
        entries=tuple(entries),
        version=int(raw.get("version", 1)),
        compiled_at=str(raw["compiled_at"]) if raw.get("compiled_at") else None,
        note=str(raw["note"]) if raw.get("note") else None,
    )


def _structural_problems(entries: list[ManifestEntry]) -> list[str]:
    """Checks that need the whole set: counts, uniqueness, failure-mode spread."""
    problems: list[str] = []
    if not MIN_ENTRIES <= len(entries) <= MAX_ENTRIES:
        problems.append(
            f"the wall needs between {MIN_ENTRIES} and {MAX_ENTRIES} exhibits (R-5.1), "
            f"found {len(entries)}"
        )

    for label, values in (
        ("slug", [e.slug for e in entries]),
        ("canonical_url", [e.canonical_url for e in entries]),
        ("fixture", [e.fixture for e in entries]),
        ("display_order", [e.display_order for e in entries]),
    ):
        duplicates = sorted({v for v in values if values.count(v) > 1})
        if duplicates:
            problems.append(f"duplicate {label}: {duplicates}")

    languages = {e.primary_language for e in entries if e.primary_language}
    if entries and len(languages) < 3:
        problems.append(
            f"R-5.7 wants at least three primary languages for spread, found "
            f"{sorted(languages)}"
        )

    modes = {e.failure_mode for e in entries if e.failure_mode}
    if entries and len(modes) < 4:
        problems.append(
            f"R-5.7 wants at least four distinct failure modes, found {len(modes)}"
        )
    return problems
