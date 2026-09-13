"""Fiscal-year reporting periods.

Most Indian companies use an April–March fiscal year: FY25 runs 1 Apr 2024 – 31 Mar 2025 and Q1 is April–June,
so Q3FY25 ends 31 Dec 2024. Some, typically subsidiaries of multinationals, report on a January–December year;
``year_end_month`` covers them. Labels follow Indian filings — ``FY25``, ``Q3FY25``, ``H1FY25``, ``9MFY25`` —
and FY is always named after the calendar year in which the fiscal year ends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum


class PeriodKind(StrEnum):
    QUARTER = "quarter"
    HALF_YEAR = "half_year"
    NINE_MONTHS = "nine_months"
    YEAR = "year"


INDIAN_FISCAL_YEAR_END = 3  # March

_LABEL = re.compile(r"^(?:(?P<quarter>Q[1-4])|(?P<half>H[12])|(?P<nine>9M))?FY(?P<yy>\d{2})$")
_MONTHS = {PeriodKind.QUARTER: 3, PeriodKind.HALF_YEAR: 6, PeriodKind.NINE_MONTHS: 9, PeriodKind.YEAR: 12}
_KIND_BY_MONTHS = {months: kind for kind, months in _MONTHS.items()}


def _add_months(first_of_month: date, months: int) -> date:
    index = first_of_month.year * 12 + first_of_month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True, slots=True)
class Period:
    kind: PeriodKind
    fiscal_year: int  # calendar year in which the fiscal year ends (FY25 -> 2025)
    part: int | None = None  # quarter 1-4 or half 1-2; None for nine-month and annual periods
    year_end_month: int = INDIAN_FISCAL_YEAR_END

    def __post_init__(self) -> None:
        allowed = {
            PeriodKind.QUARTER: (1, 2, 3, 4),
            PeriodKind.HALF_YEAR: (1, 2),
            PeriodKind.NINE_MONTHS: (None,),
            PeriodKind.YEAR: (None,),
        }[self.kind]
        if self.part not in allowed:
            raise ValueError(f"invalid part {self.part!r} for a {self.kind} period")
        if not 1 <= self.year_end_month <= 12:
            raise ValueError(f"invalid fiscal year end month {self.year_end_month}")

    @classmethod
    def parse(cls, label: str, year_end_month: int = INDIAN_FISCAL_YEAR_END) -> Period:
        match = _LABEL.match(label.strip().upper().replace(" ", ""))
        if not match:
            raise ValueError(f"unrecognised period label: {label!r}")
        fiscal_year = 2000 + int(match["yy"])
        if match["quarter"]:
            return cls(PeriodKind.QUARTER, fiscal_year, int(match["quarter"][1]), year_end_month)
        if match["half"]:
            return cls(PeriodKind.HALF_YEAR, fiscal_year, int(match["half"][1]), year_end_month)
        if match["nine"]:
            return cls(PeriodKind.NINE_MONTHS, fiscal_year, None, year_end_month)
        return cls(PeriodKind.YEAR, fiscal_year, None, year_end_month)

    @classmethod
    def from_dates(cls, start: date, end: date, year_end_month: int = INDIAN_FISCAL_YEAR_END) -> Period:
        """The period covering ``start``..``end``; raises unless it is a quarter or a fiscal year-to-date."""
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        kind = _KIND_BY_MONTHS.get(months)
        if kind is None or start.day != 1 or end + timedelta(days=1) != _add_months(start, months):
            raise ValueError(f"{start} to {end} is not a whole quarter, half year, nine months or year")
        fiscal_year = end.year + (end.month > year_end_month)
        fiscal_start = _add_months(date(fiscal_year - 1, year_end_month, 1), 1)
        offset = (start.year - fiscal_start.year) * 12 + start.month - fiscal_start.month
        if kind is PeriodKind.QUARTER:
            if offset % 3:
                raise ValueError(
                    f"{start} to {end} does not align with fiscal quarters ending in month {year_end_month}"
                )
            return cls(kind, fiscal_year, offset // 3 + 1, year_end_month)
        if kind is PeriodKind.HALF_YEAR and offset in (0, 6):
            return cls(kind, fiscal_year, offset // 6 + 1, year_end_month)
        if offset != 0:
            raise ValueError(f"{start} to {end} does not start at the beginning of the fiscal year")
        return cls(kind, fiscal_year, None, year_end_month)

    @property
    def label(self) -> str:
        fy = f"FY{self.fiscal_year % 100:02d}"
        match self.kind:
            case PeriodKind.QUARTER:
                return f"Q{self.part}{fy}"
            case PeriodKind.HALF_YEAR:
                return f"H{self.part}{fy}"
            case PeriodKind.NINE_MONTHS:
                return f"9M{fy}"
            case _:
                return fy

    def __str__(self) -> str:
        return self.label

    @property
    def start_date(self) -> date:
        fiscal_start = _add_months(date(self.fiscal_year - 1, self.year_end_month, 1), 1)
        offset = {PeriodKind.QUARTER: 3, PeriodKind.HALF_YEAR: 6}.get(self.kind, 0) * ((self.part or 1) - 1)
        return _add_months(fiscal_start, offset)

    @property
    def end_date(self) -> date:
        return _add_months(self.start_date, _MONTHS[self.kind]) - timedelta(days=1)

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def prior_comparable(self) -> Period:
        """The same period one fiscal year earlier (year-on-year)."""
        return replace(self, fiscal_year=self.fiscal_year - 1)

    def previous_sequential(self) -> Period:
        """The immediately preceding period of the same length (quarter-on-quarter, half-on-half)."""
        if self.kind in (PeriodKind.QUARTER, PeriodKind.HALF_YEAR):
            assert self.part is not None
            last_part = 4 if self.kind is PeriodKind.QUARTER else 2
            if self.part == 1:
                return replace(self, fiscal_year=self.fiscal_year - 1, part=last_part)
            return replace(self, part=self.part - 1)
        return self.prior_comparable()

    @property
    def sort_key(self) -> tuple[date, int]:
        return (self.end_date, self.days)
