"""Robust statistics used for anomaly detection and cohort baselines.

Financial ratios are heavy-tailed and samples are small (three to five reported periods, a few dozen peers),
so median / MAD based measures are used instead of mean / standard deviation.
"""

from collections.abc import Sequence
from decimal import Decimal

# Modified z-score (Iglewicz & Hoaglin): 0.6745 * (x - median) / MAD, with |z| > 3.5 as the usual outlier cut.
_MAD_SCALE = Decimal("0.6745")
# When MAD is zero, fall back to the mean absolute deviation, scaled to be comparable (1.253314 = sqrt(pi/2)).
_MEAN_AD_SCALE = Decimal("1.253314")
OUTLIER_Z = Decimal("3.5")


def median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("median of an empty sample")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def mad(values: Sequence[Decimal]) -> Decimal:
    """Median absolute deviation from the median."""
    centre = median(values)
    return median([abs(value - centre) for value in values])


def modified_z(value: Decimal, sample: Sequence[Decimal]) -> Decimal | None:
    """How unusual ``value`` is relative to ``sample``; ``None`` when the sample has no spread."""
    if len(sample) < 2:
        return None
    centre = median(sample)
    spread = mad(sample)
    if spread:
        return _MAD_SCALE * (value - centre) / spread
    mean_ad = sum((abs(v - centre) for v in sample), Decimal(0)) / len(sample)
    if mean_ad:
        return (value - centre) / (_MEAN_AD_SCALE * mean_ad)
    return None


def percentile(values: Sequence[Decimal], p: Decimal) -> Decimal:
    """Linear-interpolated percentile, ``p`` in [0, 100]."""
    if not values:
        raise ValueError("percentile of an empty sample")
    if not 0 <= p <= 100:
        raise ValueError(f"percentile out of range: {p}")
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def percent_rank(values: Sequence[Decimal], value: Decimal) -> Decimal:
    """Share of the sample below ``value``, counting ties as half (0 to 1)."""
    if not values:
        raise ValueError("percent rank of an empty sample")
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (Decimal(below) + Decimal(equal) / 2) / len(values)
