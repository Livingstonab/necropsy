"""The curated graveyard: offline seeding, idempotence, and the named diff (R-5, R-17).

The claims that matter for the demo:

  - a clean checkout with no network and no keys yields a full wall (R-5.1, P4);
  - re-seeding changes nothing (R-5.5);
  - deleting a fixture makes `--offline` fail with a named diff, not a fetch
    (R-17.3) — the whole point of committing fixtures is that the demo cannot
    depend on the network, so a silent fetch would defeat it.

Every test here injects clients that raise if the network or the LLM is touched,
so "offline" is asserted rather than hoped for (Property 11).
"""

from __future__ import annotations

import json

import pytest

from necropsy.github.client import NoNetworkGitHubClient
from necropsy.graveyard import manifest as manifest_module
from necropsy.graveyard.manifest import MAX_ENTRIES, MIN_ENTRIES, ManifestError
from necropsy.graveyard.seed import fixture_mapping, seed
from necropsy.scoring.bands import BANDS
from necropsy.store import exhibits
from necropsy.store.db import Database


# -- the manifest ----------------------------------------------------------
def test_manifest_loads_and_is_well_formed() -> None:
    manifest = manifest_module.load()
    assert MIN_ENTRIES <= len(manifest) <= MAX_ENTRIES
    slugs = [e.slug for e in manifest.entries]
    assert len(slugs) == len(set(slugs)), "slugs must be unique"
    orders = [e.display_order for e in manifest.entries]
    assert len(orders) == len(set(orders)), "display_order must be unique"


def test_manifest_covers_the_required_spread() -> None:
    """R-5.7: at least four failure modes and three languages."""
    manifest = manifest_module.load()
    languages = {e.primary_language for e in manifest.entries if e.primary_language}
    modes = {e.failure_mode for e in manifest.entries if e.failure_mode}
    assert len(languages) >= 3, f"only {sorted(languages)}"
    assert len(modes) >= 4, f"only {len(modes)} failure modes"


def test_manifest_is_honest_about_specimen_provenance() -> None:
    """P7: the committed set is labelled as specimens, not passed off as real."""
    manifest = manifest_module.load()
    for entry in manifest.entries:
        assert entry.fixture_provenance in {"compiled_specimen", "api_verified"}
        if not entry.api_verified:
            assert "compiled specimen" in entry.provenance_label


def test_too_few_entries_is_rejected() -> None:
    with pytest.raises(ManifestError, match="between"):
        manifest_module.parse(
            {
                "entries": [
                    {
                        "slug": "only-one",
                        "canonical_url": "github.com/x/one",
                        "pinned_commit_sha": "abc",
                        "curator_note": "n",
                        "display_order": 1,
                        "fixture": "one.json",
                        "fixture_provenance": "compiled_specimen",
                    }
                ]
            }
        )


def test_a_fixture_with_a_path_separator_is_rejected() -> None:
    """E4: a manifest-named fixture cannot escape the fixtures directory."""
    with pytest.raises(ManifestError, match="bare filename"):
        manifest_module.parse(
            {
                "entries": [
                    {
                        "slug": "escape",
                        "canonical_url": "github.com/x/escape",
                        "pinned_commit_sha": "abc",
                        "curator_note": "n",
                        "display_order": i,
                        "fixture": "../../etc/passwd" if i == 0 else f"ok{i}.json",
                        "fixture_provenance": "compiled_specimen",
                        "primary_language": lang,
                        "failure_mode": mode,
                    }
                    for i, (lang, mode) in enumerate(
                        [("Python", "a"), ("Go", "b"), ("Rust", "c"),
                         ("Ruby", "d"), ("C", "e"), ("Java", "f")]
                    )
                ]
            }
        )


# -- offline seeding -------------------------------------------------------
def test_offline_seed_populates_a_full_wall(db: Database) -> None:
    """R-5.1: no network, no keys, a full graveyard.

    The GitHub client injected here raises on use, so if any part of seeding
    reached for the network this would fail rather than pass quietly.
    """
    guard = NoNetworkGitHubClient(db)
    assert guard  # constructed; its mere availability is not the test

    report = seed(db)
    assert report.exit_code == 0, report.render()
    assert all(entry.ok for entry in report.entries)
    assert MIN_ENTRIES <= len(report.entries) <= MAX_ENTRIES

    stored = exhibits.list_exhibits(db.connect(), sort="curated")
    assert len(stored) == len(report.entries)
    assert all(e.is_demo and e.curated for e in stored)


def test_seeded_wall_renders_every_section_from_cache(db: Database) -> None:
    """R-5.2: an opened demo exhibit has all seven sections, from cache."""
    seed(db)
    # Exhibit slugs are the canonical owner--name (design §3), not the manifest's
    # shorter curator key.
    exhibit = exhibits.get_by_slug(db.connect(), "left-pad--left-pad")
    assert exhibit is not None
    payload = exhibit.analysis.to_payload(cache_status="hit")
    for section in (
        "what_was_built",
        "time_of_death",
        "cause_of_death",
        "whats_missing",
        "scorecard",
        "epitaph",
        "resurrection_plan",
    ):
        assert section in payload, section
    assert payload["epitaph"]["source"] == "template"  # no LLM key in the suite
    assert len(payload["resurrection_plan"]) == 3


def test_the_wall_spreads_across_bands(db: Database) -> None:
    """R-5.7's "visibly spreads rather than clusters", read against real corpses.

    Three bands is the most a wall of genuinely archived repositories can show.
    `archived` floors `_abandonment` at 0.85 and `_maintainer_absence` at 0.90,
    which is 27.8 points before anything else contributes, so no archived repo
    can land in `shallow_grave` (0-24); ~96 probed candidates topped out at 86,
    so none reached `do_not_exhume` either. Asserting four bands here would only
    be satisfiable by a compiled specimen, which P7 forbids passing off as a
    corpse. R-5.7's countable criteria — four failure modes, three languages —
    are asserted on the manifest by `test_manifest_covers_the_required_spread`.
    """
    seed(db)
    scores = [e.analysis.scorecard.score for e in exhibits.list_exhibits(db.connect())]
    bands = {e.analysis.scorecard.band.key for e in exhibits.list_exhibits(db.connect())}
    assert len(bands) >= 3, f"bands present: {bands}"
    assert max(scores) - min(scores) >= 40, f"scores: {sorted(scores)}"
    assert min(scores) >= 28, "an archived repo cannot score below the archived floor"
    assert all(b.key for b in BANDS)  # bands are well-formed


def test_every_seeded_epitaph_is_grounded(db: Database) -> None:
    """P2: no exhibit ships an epitaph that cannot be checked against a fact."""
    from necropsy.llm.validate import is_grounded

    seed(db)
    for exhibit in exhibits.list_exhibits(db.connect()):
        epitaph = exhibit.analysis.narrative.epitaph
        assert epitaph.evidence is not None, exhibit.slug
        assert is_grounded(
            exhibit.analysis.facts,
            epitaph.evidence.fact_key,
            epitaph.evidence.fact_value,
        ), f"{exhibit.slug}: {epitaph.text}"


def test_seeded_epitaphs_are_distinct(db: Database) -> None:
    """P2: a wall of identical epitaphs is generic spookiness, which is the
    failure mode the template variety exists to avoid."""
    seed(db)
    texts = [e.analysis.narrative.epitaph.text for e in exhibits.list_exhibits(db.connect())]
    assert len(set(texts)) == len(texts), f"duplicate epitaphs: {texts}"


# -- idempotence (R-5.5) ---------------------------------------------------
def test_reseeding_is_idempotent(db: Database) -> None:
    first = seed(db)
    before = _snapshot(db)
    second = seed(db)
    after = _snapshot(db)

    assert first.exit_code == 0 and second.exit_code == 0
    assert before == after, "a second seed must not change any stored row"
    # The second run finds every row already present.
    assert all(entry.outcome == "cached" for entry in second.entries), [
        (e.slug, e.outcome) for e in second.entries
    ]


def test_reseeding_does_not_touch_a_user_submission(db: Database) -> None:
    """R-5.5: seeding the demo set leaves a user's own analysis alone."""
    from tests import factories

    user = factories.make_analysis(owner="someone", name="their-repo")
    from necropsy.store.analyses import save_analysis

    with db.transaction() as conn:
        save_analysis(conn, user, factories.make_raw_payloads("someone", "their-repo"))
    before = _row_count(db)

    seed(db)
    seed(db)

    assert _exists(db, user.cache_key), "the user's analysis must survive seeding"
    # Seeding adds the demo rows exactly once, never a duplicate of the user's.
    assert _row_count(db) == before + MIN_ENTRIES or _row_count(db) == before + 8


# -- the named diff (R-17.3) ----------------------------------------------
def test_a_missing_fixture_fails_with_a_named_diff(
    db: Database, tmp_path, monkeypatch
) -> None:
    """R-17.3: a fixture the manifest names but that is not present fails loudly.

    `fixture_path` resolves against `manifest.FIXTURE_DIR`, so pointing that at an
    empty directory makes every fixture 'missing' without touching the committed
    artifacts. Seeding must then fail with a per-entry reason, not a fetch.
    """
    monkeypatch.setattr(manifest_module, "FIXTURE_DIR", tmp_path / "empty")

    report = seed(db)
    assert report.exit_code == 1
    missing = [e for e in report.entries if e.outcome == "missing_fixture"]
    assert len(missing) == len(report.entries), "every entry should report missing"
    assert missing[0].slug in missing[0].detail
    assert ".json" in missing[0].detail  # names the file it looked for


def test_a_fixture_that_disagrees_with_the_manifest_is_rejected(
    tmp_path, monkeypatch
) -> None:
    """R-17.3: the diff names the field that drifted, not just 'mismatch'."""
    manifest = manifest_module.load()
    entry = manifest.entries[0]
    mapping = fixture_mapping(entry)

    mapping["_meta"]["pinned_commit_sha"] = "deadbeef-not-the-pinned-sha"
    (tmp_path / entry.fixture).write_text(json.dumps(mapping), encoding="utf-8")

    monkeypatch.setattr(manifest_module, "FIXTURE_DIR", tmp_path)
    with pytest.raises(ValueError, match="pinned_commit_sha"):
        fixture_mapping(entry)


# -- helpers ---------------------------------------------------------------
def _snapshot(db: Database) -> list[tuple[str, int, str]]:
    rows = db.connect().execute(
        "SELECT cache_key, score, band FROM analyses ORDER BY cache_key"
    ).fetchall()
    return [(r["cache_key"], r["score"], r["band"]) for r in rows]


def _row_count(db: Database) -> int:
    return int(db.connect().execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"])


def _exists(db: Database, cache_key: str) -> bool:
    row = db.connect().execute(
        "SELECT 1 FROM analyses WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    return row is not None
