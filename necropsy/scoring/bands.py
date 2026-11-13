"""Score bands (design.md section 5.3).

The five ranges partition [0, 100] with no gap and no overlap, and the band is
a pure function of the integer total (Property 6).
"""

from __future__ import annotations

from necropsy.models.scorecard import Band

_BAND_SPEC: tuple[tuple[int, int, str, str], ...] = (
    (0, 24, "shallow_grave", "Shallow grave — revivable in a weekend"),
    (25, 49, "recoverable", "Recoverable with effort"),
    (50, 74, "serious_decay", "Serious decay"),
    (75, 89, "deep_decay", "Deep decay — effectively a rewrite"),
    (90, 100, "do_not_exhume", "Do not exhume"),
)

BANDS: tuple[Band, ...] = tuple(
    Band(key=key, label=label, range=(lo, hi)) for lo, hi, key, label in _BAND_SPEC
)

# Partition check runs at import, so a bad edit fails fast rather than silently.
_prev_hi = -1
for _band in BANDS:
    _lo, _hi = _band.range
    assert _lo == _prev_hi + 1, f"band gap/overlap at {_band.key}"
    _prev_hi = _hi
assert _prev_hi == 100, "bands must cover up to 100"
assert BANDS[0].range[0] == 0, "bands must start at 0"


def band_for(score: int) -> Band:
    """Map a total to its band. Takes the total only — no other input."""
    for band in BANDS:
        lo, hi = band.range
        if lo <= score <= hi:
            return band
    # Unreachable for scores in [0,100]; clamp defensively rather than raise.
    return BANDS[-1] if score > 100 else BANDS[0]
