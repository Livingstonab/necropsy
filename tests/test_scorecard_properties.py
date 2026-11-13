"""Property-based tests for the scorecard (R-20, design "Correctness Properties").

Eight of the ten properties live here; cache invariance (Property 5) needs SQLite
and sits at the bottom of the file with its own fixture.

The ones that earn their keep are 3 and 4. Monotonicity is the claim that makes
the score an argument rather than a number: marking the test suite missing can
never lower revival difficulty, and adding tests can never raise it. That is easy
to state, easy to believe, and easy to break with one plausible-sounding heuristic
three months from now. A generated counterexample names the factor and the field,
which is what makes the failure cheap to read.

Determinism (Property 2) is checked in a fresh interpreter as well as in-process,
because "no clock, no RNG" is not provable by calling the function twice in a row.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from necropsy import SCORECARD_VERSION
from necropsy.models.scorecard import ScorecardInput
from necropsy.scoring import score
from necropsy.scoring.bands import BANDS, band_for
from necropsy.scoring.scorecard import clamp, linear_ramp, log_ramp
from necropsy.scoring.weights import WEIGHTS
from necropsy.store.analyses import cache_key, load_analysis, save_analysis
from necropsy.store.db import Database
from tests import factories
from tests.strategies import (
    ASSET_FIELDS,
    DEFICIT_FIELDS,
    increase_asset,
    increase_deficit,
    scorecard_inputs,
)

ROOT = Path(__file__).resolve().parents[1]

# Scoring is pure and cheap, so a wide run is affordable. `deadline=None`
# because a cold first call includes import time on Windows.
PURE = settings(max_examples=200, deadline=None)


# -- Property 1: bounds ----------------------------------------------------
@PURE
@given(scorecard_inputs())
def test_bounds(inp: ScorecardInput) -> None:
    """R-20.1. The total is an integer in [0,100]; every factor is in [0,1]."""
    card = score(inp)
    assert isinstance(card.score, int)
    assert 0 <= card.score <= 100
    for factor in card.factors:
        assert 0.0 <= factor.normalized <= 1.0, factor.key
        assert 0.0 <= factor.contribution <= factor.weight, factor.key


# -- Property 2: determinism ----------------------------------------------
@PURE
@given(scorecard_inputs())
def test_determinism_in_process(inp: ScorecardInput) -> None:
    """R-20.2. Same input, same output: total, breakdown and band."""
    first = score(inp)
    second = score(inp)
    assert first.model_dump() == second.model_dump()


def test_determinism_across_processes() -> None:
    """R-20.2. A fresh interpreter must agree, which is what rules out a clock.

    Scoring the same input twice in one process cannot distinguish a pure
    function from one that memoized a `datetime.now()` on first call. A separate
    process can.
    """
    inp = factories.make_facts()
    expected = score(ScorecardInput.from_facts(inp))
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from necropsy.models.scorecard import ScorecardInput\n"
        "from necropsy.scoring import score\n"
        "from tests import factories\n"
        "card = score(ScorecardInput.from_facts(factories.make_facts()))\n"
        "print(card.model_dump_json())\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no repository-derived input
        [sys.executable, "-I", "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr
    fresh = json.loads(completed.stdout.strip().splitlines()[-1])
    assert fresh == json.loads(expected.model_dump_json())


# -- Property 3: deficit monotonicity -------------------------------------
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scorecard_inputs(), st.sampled_from(DEFICIT_FIELDS))
def test_deficit_monotonicity(inp: ScorecardInput, field: str) -> None:
    """R-20.3. Worse condition cannot score easier."""
    worse = increase_deficit(inp, field)
    if worse is None:
        return  # already at its maximum; nothing to compare
    before = score(inp).total_unrounded
    after = score(worse).total_unrounded
    assert after >= before - 1e-9, (
        f"raising the deficit {field!r} lowered the score "
        f"from {before} to {after}; inputs={inp.model_dump()}"
    )


# -- Property 4: asset monotonicity ---------------------------------------
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scorecard_inputs(), st.sampled_from(ASSET_FIELDS))
def test_asset_monotonicity(inp: ScorecardInput, field: str) -> None:
    """R-20.4. Better condition cannot score harder. Adding tests never hurts."""
    better = increase_asset(inp, field)
    if better is None:
        return
    before = score(inp).total_unrounded
    after = score(better).total_unrounded
    assert after <= before + 1e-9, (
        f"raising the asset {field!r} raised the score "
        f"from {before} to {after}; inputs={inp.model_dump()}"
    )


# -- Property 6: band partition -------------------------------------------
@PURE
@given(scorecard_inputs())
def test_band_is_a_pure_function_of_the_total(inp: ScorecardInput) -> None:
    """R-20.6. The band follows from the total and from nothing else."""
    card = score(inp)
    assert card.band == band_for(card.score)
    low, high = card.band.range
    assert low <= card.score <= high


@given(st.integers(min_value=0, max_value=100))
def test_bands_partition_the_range(total: int) -> None:
    """R-20.6. Every score in [0,100] lands in exactly one band."""
    matches = [b for b in BANDS if b.range[0] <= total <= b.range[1]]
    assert len(matches) == 1, f"{total} matched {[b.key for b in matches]}"


# -- Property 7: contribution identity ------------------------------------
@PURE
@given(scorecard_inputs())
def test_contribution_identity(inp: ScorecardInput) -> None:
    """R-20.7. The breakdown adds up to the total, and the total rounds to the score.

    This is the property that makes the scorecard auditable by hand, which is the
    entire point of publishing the inputs (E5).
    """
    card = score(inp)
    summed = sum(f.contribution for f in card.factors)
    assert abs(summed - card.total_unrounded) <= 1e-9
    assert card.score == round(card.total_unrounded)


# -- the division-by-zero guard the prompt calls out ----------------------
@given(
    days=st.integers(min_value=0, max_value=5_000),
    manifest_found=st.booleans(),
)
def test_zero_dependencies_never_maxes_dependency_rot(
    days: int, manifest_found: bool
) -> None:
    """A repository with no dependencies is not maximally rotten.

    `base = deprecated / max(direct, 1)` is the guard. With zero dependencies the
    numerator is zero, so a manifest that declares nothing scores 0.0 before the
    lockfile adjustment, not 1.0 and not a NaN. Without a manifest at all the
    documented neutral default applies instead, and says so in its rationale.
    """
    card = score(
        ScorecardInput(
            days_since_last_commit=days,
            manifest_found=manifest_found,
            direct_dependency_count=0,
            deprecated_dependency_count=0,
            fresh_dependency_count=0,
            has_lockfile=True,
        )
    )
    rot = card.factor("dependency_rot")
    assert rot is not None
    if manifest_found:
        assert rot.normalized == 0.0
        assert "dependencies" not in card.data_gaps
    else:
        assert rot.normalized == 0.5
        assert rot.confidence == "low"
        assert "dependencies" in card.data_gaps
        assert "neutral default" in rot.rationale


# -- ramp helpers ----------------------------------------------------------
@given(
    x=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    lo=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
)
def test_linear_ramp_is_bounded_and_monotone(x: float, lo: float) -> None:
    hi = lo + 10.0
    value = linear_ramp(x, lo, hi)
    assert 0.0 <= value <= 1.0
    assert linear_ramp(x + 1.0, lo, hi) >= value - 1e-9


@given(x=st.integers(min_value=0, max_value=10_000_000))
def test_log_ramp_is_bounded_and_monotone(x: int) -> None:
    value = log_ramp(x, 500, 200_000)
    assert 0.0 <= value <= 1.0
    assert log_ramp(x + 1, 500, 200_000) >= value - 1e-9


@given(x=st.floats(allow_nan=False, allow_infinity=False))
def test_clamp_stays_in_range(x: float) -> None:
    assert 0.0 <= clamp(x) <= 1.0


# -- structural invariants of the weight table ----------------------------
def test_weights_are_non_negative_and_sum_to_100() -> None:
    """The precondition every monotonicity argument rests on (design 5.2)."""
    assert sum(WEIGHTS.values()) == 100
    assert all(w >= 0 for w in WEIGHTS.values())


def test_every_factor_is_reported_exactly_once() -> None:
    card = score(ScorecardInput())
    keys = [f.key for f in card.factors]
    assert keys == list(WEIGHTS)
    assert card.scorecard_version == SCORECARD_VERSION


def test_an_empty_input_is_the_worst_case() -> None:
    """Defaults describe a repository with nothing: no tests, no CI, no licence.

    Worth pinning as a golden value because it is the one input every factor
    responds to, so an accidental sign flip anywhere shows up here.
    """
    card = score(ScorecardInput())
    assert card.score == 54
    assert card.band.key == "serious_decay"
    # The two neutral defaults are visible rather than silent (R-8.4).
    assert card.data_gaps == ["dependencies", "issues"]


# -- Property 5: cache invariance -----------------------------------------
@pytest.mark.parametrize("llm_status", ["ok", "failed"])
def test_cache_invariance(db: Database, llm_status: str) -> None:
    """R-20.5. A score recomputed from rehydrated facts equals the in-memory score.

    Not a tautology: the facts make a round trip through SQLite as JSON, so this
    would fail if serialization lost a field the scorer reads, or if a float came
    back as a string, or if `as_of` picked up a timezone on the way out.
    """
    analysis = factories.make_analysis(llm_status=llm_status)
    in_memory = score(ScorecardInput.from_facts(analysis.facts))

    with db.transaction() as conn:
        row = save_analysis(conn, analysis, factories.make_raw_payloads())
    rehydrated = load_analysis(row)
    recomputed = score(ScorecardInput.from_facts(rehydrated.facts))

    assert recomputed.model_dump() == in_memory.model_dump()
    assert rehydrated.scorecard.model_dump() == in_memory.model_dump()
    assert row.cache_key == cache_key(
        analysis.facts.canonical_url,
        analysis.facts.last_commit_sha,
        analysis.provenance.analysis_schema_version,
    )


@PURE
@given(scorecard_inputs())
def test_scoring_does_not_mutate_its_input(inp: ScorecardInput) -> None:
    """E6. A pure function cannot leave a fingerprint on its argument."""
    before = inp.model_dump()
    score(inp)
    assert inp.model_dump() == before
