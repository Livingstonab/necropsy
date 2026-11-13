"""The weight table (design.md section 5.2, scorecard_version 1).

Weights are non-negative and sum to exactly 100. That, plus per-factor
monotonicity, is what makes the total monotone (Properties 3 and 4).

Changing anything here requires incrementing SCORECARD_VERSION and
ANALYSIS_SCHEMA_VERSION in necropsy/__init__.py in the same change (E5).
"""

from __future__ import annotations

import hashlib
import json

WEIGHTS: dict[str, int] = {
    "abandonment": 20,
    "dependency_rot": 18,
    "test_absence": 15,
    "maintainer_absence": 12,
    "documentation_deficit": 10,
    "codebase_scale": 10,
    "ci_absence": 8,
    "legal_risk": 7,
}

LABELS: dict[str, str] = {
    "abandonment": "Abandonment",
    "dependency_rot": "Dependency rot",
    "test_absence": "Test absence",
    "maintainer_absence": "Maintainer absence",
    "documentation_deficit": "Documentation deficit",
    "codebase_scale": "Codebase scale",
    "ci_absence": "CI absence",
    "legal_risk": "Legal risk",
}

# Order in which factors are reported. Stable, so output diffs are readable.
FACTOR_ORDER: tuple[str, ...] = tuple(WEIGHTS.keys())

TOTAL_WEIGHT = sum(WEIGHTS.values())
assert TOTAL_WEIGHT == 100, f"weights must sum to 100, got {TOTAL_WEIGHT}"
assert all(w >= 0 for w in WEIGHTS.values()), "weights must be non-negative"

# Documented neutral defaults used when an input is unavailable (R-8.4).
NEUTRAL_DEPENDENCY_ROT = 0.5
NEUTRAL_MAINTAINER_ABSENCE = 0.4


def weights_checksum() -> str:
    """Stable hash of the weight table, exposed so a reader can confirm which
    weight set produced a stored score."""
    blob = json.dumps(WEIGHTS, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]
