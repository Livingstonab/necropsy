"""Generate the committed specimen manifest and fixtures from `specimens.py`.

Run after editing a descriptor:

    python scripts/build_specimen_graveyard.py

It writes `necropsy/graveyard/manifest.json` and one fixture per entry, then
scores each one offline and records the result as `expected_band`. The band is
therefore an observation of what the scorer produced, not a wish: `seed` warns
when a later change makes an exhibit drift out of it.

This script generates *specimen* data. It never contacts GitHub. To build a wall
of real repositories instead, edit `manifest.json` to name them and run
`python -m necropsy.cli seed-graveyard --refresh` with a `GITHUB_TOKEN` set.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from necropsy.graveyard import manifest as manifest_module  # noqa: E402
from necropsy.graveyard import specimens  # noqa: E402
from necropsy.graveyard.seed import seed, write_specimen_fixtures  # noqa: E402
from necropsy.store.db import open_database  # noqa: E402


def build_manifest_document(expected_bands: dict[str, str] | None = None) -> dict[str, object]:
    bands = expected_bands or {}
    return {
        "version": 1,
        "compiled_at": specimens.AS_OF[:10],
        "note": (
            "The eight exhibits below are compiled specimens, not captures of named "
            "third-party repositories: NECROPSY cannot verify a live repository's "
            "numbers offline, and printing unverified counts under a real project's "
            "name would be a fabrication (P7). Each specimen exhibits one failure "
            "mode from design.md section 8.4 and exercises the real pipeline. To "
            "build a wall of real repositories, replace these entries with the ones "
            "you want and run `seed-graveyard --refresh` with a GITHUB_TOKEN set; "
            "that rewrites each fixture from the API and sets fixture_provenance to "
            "api_verified."
        ),
        "entries": [
            {
                "slug": spec.slug,
                "canonical_url": spec.canonical_url,
                "pinned_commit_sha": spec.commit_sha,
                "curator_note": spec.curator_note,
                "why_it_is_a_good_exhibit": spec.curator_note.split(".")[0] + ".",
                "verified_dead": True,
                "display_order": spec.display_order,
                "expected_band": bands.get(spec.slug),
                "fixture": f"{spec.slug}.json",
                "fixture_provenance": specimens.PROVENANCE,
                "primary_language": spec.primary_language,
                "failure_mode": spec.failure_mode,
            }
            for spec in sorted(specimens.SPECIMENS, key=lambda s: s.display_order)
        ],
    }


def write_manifest(document: dict[str, object]) -> Path:
    path = manifest_module.MANIFEST_PATH
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    # Pass 1: manifest without bands, so the fixtures can be written and scored.
    write_manifest(build_manifest_document())
    fixtures = write_specimen_fixtures(manifest_module.load())
    print(f"wrote {len(fixtures)} fixtures to {manifest_module.FIXTURE_DIR}")

    # Pass 2: score offline and record the observed band per entry.
    db = open_database(":memory:")
    try:
        report = seed(db, manifest=manifest_module.load())
    finally:
        db.close()
    print(report.render())
    if report.failures:
        return 1

    bands = {e.slug: e.band for e in report.entries if e.band}
    write_manifest(build_manifest_document(bands))
    print(f"recorded expected_band for {len(bands)} entries")

    spread = sorted({b for b in bands.values()})
    print(f"band spread: {spread}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
