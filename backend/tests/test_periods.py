from datetime import date

import pytest

from app.domain.periods import Period, PeriodKind


@pytest.mark.parametrize("label", ["FY25", "Q1FY25", "Q4FY25", "H1FY24", "H2FY24", "9MFY25"])
def test_label_round_trip(label: str) -> None:
    assert Period.parse(label).label == label


def test_parse_is_lenient_about_case_and_spaces() -> None:
    assert Period.parse(" q3 fy25 ") == Period(PeriodKind.QUARTER, 2025, 3)


@pytest.mark.parametrize("label", ["FY2025", "Q5FY25", "H3FY25", "2025", "Q3"])
def test_rejects_unknown_labels(label: str) -> None:
    with pytest.raises(ValueError):
        Period.parse(label)


@pytest.mark.parametrize(
    ("label", "start", "end"),
    [
        ("FY25", date(2024, 4, 1), date(2025, 3, 31)),
        ("Q1FY25", date(2024, 4, 1), date(2024, 6, 30)),
        ("Q3FY25", date(2024, 10, 1), date(2024, 12, 31)),
        ("Q4FY25", date(2025, 1, 1), date(2025, 3, 31)),
        ("H2FY25", date(2024, 10, 1), date(2025, 3, 31)),
        ("9MFY25", date(2024, 4, 1), date(2024, 12, 31)),
    ],
)
def test_indian_fiscal_calendar(label: str, start: date, end: date) -> None:
    period = Period.parse(label)
    assert (period.start_date, period.end_date) == (start, end)


def test_days_accounts_for_leap_years() -> None:
    assert Period.parse("FY24").days == 366  # includes 29 Feb 2024
    assert Period.parse("FY25").days == 365


@pytest.mark.parametrize(
    ("label", "prior", "sequential"),
    [
        ("Q3FY25", "Q3FY24", "Q2FY25"),
        ("Q1FY25", "Q1FY24", "Q4FY24"),
        ("H1FY25", "H1FY24", "H2FY24"),
        ("FY25", "FY24", "FY24"),
        ("9MFY25", "9MFY24", "9MFY24"),
    ],
)
def test_comparisons(label: str, prior: str, sequential: str) -> None:
    period = Period.parse(label)
    assert period.prior_comparable().label == prior
    assert period.previous_sequential().label == sequential


def test_invalid_part_rejected() -> None:
    with pytest.raises(ValueError):
        Period(PeriodKind.YEAR, 2025, 1)
