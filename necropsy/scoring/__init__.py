"""Revival difficulty scoring. Pure functions only (E6).

Nothing in this package may import a clock, an RNG, the store, the network, or
the environment. The import-guard test in tests/test_safety_guards.py enforces it.
"""

from necropsy.scoring.bands import BANDS, band_for
from necropsy.scoring.scorecard import score
from necropsy.scoring.weights import WEIGHTS, weights_checksum

__all__ = ["BANDS", "WEIGHTS", "band_for", "score", "weights_checksum"]
