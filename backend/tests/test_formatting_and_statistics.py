from decimal import Decimal

import pytest

from app.domain.enums import Unit
from app.engine import statistics
from app.engine.formatting import format_value, group_indian


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("0", "0.00"),
        ("999.5", "999.50"),
        ("1234.567", "1,234.57"),
        ("123456", "1,23,456.00"),
        ("12345678.9", "1,23,45,678.90"),
        ("-9876543.21", "-98,76,543.21"),
    ],
)
def test_indian_digit_grouping(number: str, expected: str) -> None:
    assert group_indian(Decimal(number)) == expected


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("123456.7"), Unit.INR_CRORE, "₹1,23,456.70 cr"),
        (Decimal("-80"), Unit.INR_CRORE, "-₹80.00 cr"),
        (Decimal("16.755"), Unit.INR, "₹16.76"),
        (Decimal("0.12345"), Unit.RATIO, "12.3%"),
        (Decimal("2.5"), Unit.TIMES, "2.50x"),
        (Decimal("97.6"), Unit.DAYS, "98 days"),
        ("qualified", Unit.TEXT, "qualified"),
        (None, Unit.RATIO, "n/a"),
    ],
)
def test_format_value(value: Decimal | str | None, unit: Unit, expected: str) -> None:
    assert format_value(value, unit) == expected


def numbers(*values: float) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


def test_median_and_mad() -> None:
    assert statistics.median(numbers(3, 1, 2)) == 2
    assert statistics.median(numbers(4, 1, 3, 2)) == Decimal("2.5")
    assert statistics.mad(numbers(1, 2, 3, 4, 100)) == 1


def test_percentile_interpolates() -> None:
    sample = numbers(10, 20, 30, 40, 50)
    assert statistics.percentile(sample, Decimal(0)) == 10
    assert statistics.percentile(sample, Decimal(50)) == 30
    assert statistics.percentile(sample, Decimal(90)) == 46


def test_percent_rank_counts_ties_as_half() -> None:
    assert statistics.percent_rank(numbers(1, 2, 3, 4), Decimal(3)) == Decimal("0.625")


def test_modified_z_is_robust_to_outliers_in_the_sample() -> None:
    sample = numbers(10, 11, 9, 10, 500)  # one extreme point barely moves median / MAD
    assert abs(statistics.modified_z(Decimal(10), sample)) < 1


def test_modified_z_falls_back_when_mad_is_zero() -> None:
    assert statistics.modified_z(Decimal(20), numbers(10, 10, 10, 12)) is not None
    assert statistics.modified_z(Decimal(20), numbers(10, 10, 10)) is None
