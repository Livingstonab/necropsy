"""The vital-signs and ambition-vs-reality derivation (presentation only).

`compute_vitals` adds no data and reads no source — it projects the facts the
pipeline already produced. These tests pin that: the "Delivered" line is computed
from metadata we legitimately have (file count, manifest dependencies), never
from counting routes in source NECROPSY never fetched (E4).
"""

from __future__ import annotations

from necropsy.analysis.vitals import compute_vitals
from necropsy.models.facts import Dependency
from tests import factories


def test_language_slices_sum_to_100_and_sort_descending() -> None:
    facts = factories.make_facts(languages={"Ruby": 400_000, "JavaScript": 100_000})
    vitals = compute_vitals(facts)
    assert [s.name for s in vitals.languages] == ["Ruby", "JavaScript"]
    assert abs(sum(s.percent for s in vitals.languages) - 100.0) < 0.5
    assert vitals.languages[0].percent == 80.0


def test_no_languages_is_an_empty_bar_not_a_crash() -> None:
    facts = factories.make_facts(languages={})
    vitals = compute_vitals(facts)
    assert vitals.languages == []
    assert vitals.language_count == 0


def test_vital_signs_cover_the_structural_supports() -> None:
    vitals = compute_vitals(factories.make_facts())
    keys = {s.key for s in vitals.signs}
    assert keys == {"tests", "ci", "license", "docs", "deps", "lockfile"}


def test_database_is_detected_from_the_manifest_not_source() -> None:
    """has_database comes from a declared dependency, never from reading code."""
    facts = factories.make_facts(
        dependencies=[Dependency(name="pg", ecosystem="ruby"), Dependency(name="rails", ecosystem="ruby")]
    )
    vitals = compute_vitals(facts)
    assert vitals.has_database is True
    assert "a database." in vitals.delivered


def test_no_database_dependency_reads_as_no_database() -> None:
    facts = factories.make_facts(
        dependencies=[Dependency(name="rails", ecosystem="ruby")]
    )
    vitals = compute_vitals(facts)
    assert vitals.has_database is False
    assert "no database." in vitals.delivered


def test_web_framework_is_detected_from_the_manifest() -> None:
    facts = factories.make_facts(dependencies=[Dependency(name="flask", ecosystem="python")])
    vitals = compute_vitals(facts)
    assert vitals.serves_web is True
    assert "web framework" in vitals.delivered


def test_delivered_states_file_count_and_tests() -> None:
    facts = factories.make_facts(file_count=312, has_tests=True, test_file_count=18)
    delivered = compute_vitals(facts).delivered
    assert "312 files" in delivered
    assert "18 test files" in delivered


def test_promised_quotes_the_first_real_readme_sentence() -> None:
    facts = factories.make_facts(
        readme_excerpt="# Title\n\nA shopping cart engine for Rails applications that handles tax.\n"
    )
    vitals = compute_vitals(facts)
    assert vitals.promised is not None
    assert vitals.promised.startswith("A shopping cart engine")
    assert "#" not in vitals.promised


def test_promised_is_none_without_a_readme() -> None:
    """P7: no README means no promise to quote — we say nothing, not a guess."""
    facts = factories.make_facts(readme_excerpt=None)
    assert compute_vitals(facts).promised is None


def test_promised_strips_markdown_links() -> None:
    facts = factories.make_facts(
        readme_excerpt="See [the docs](https://example.com) for this wonderful library tool.\n"
    )
    promised = compute_vitals(facts).promised
    assert promised is not None
    assert "](" not in promised and "https://" not in promised


def test_strength_line_counts_present_supports() -> None:
    vitals = compute_vitals(factories.make_facts())
    assert "of 6 structural supports present" in vitals.strength_line


def test_vitals_is_deterministic() -> None:
    """E6: same facts, same vitals, every call."""
    facts = factories.make_facts()
    assert compute_vitals(facts) == compute_vitals(facts)
