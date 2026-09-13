"""Cohort baselines: what "normal" looks like for a metric among comparable companies.

The learning service keeps these up to date as filings are analysed; the engine only reads them through
:class:`BaselineProvider`, so tests and offline runs can use the in-memory implementation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from app.domain.enums import Sector, SizeBucket
from app.domain.periods import PeriodKind
from app.engine import statistics

# Below this many observations a cohort percentile is not trusted and rules use their static fallback.
MIN_COHORT_SIZE = 8


@dataclass(frozen=True, slots=True)
class Cohort:
    sector: Sector
    size: SizeBucket
    period_kind: PeriodKind

    def __str__(self) -> str:
        return f"{self.sector}/{self.size}/{self.period_kind}"


@dataclass(frozen=True, slots=True)
class Distribution:
    values: tuple[Decimal, ...]

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def reliable(self) -> bool:
        return self.n >= MIN_COHORT_SIZE

    def percentile(self, p: Decimal) -> Decimal:
        return statistics.percentile(self.values, p)

    def rank(self, value: Decimal) -> Decimal:
        return statistics.percent_rank(self.values, value)

    @property
    def median(self) -> Decimal:
        return statistics.median(self.values)


class BaselineProvider(Protocol):
    def distribution(self, metric: str, cohort: Cohort) -> Distribution | None: ...


@dataclass
class InMemoryBaselines:
    _samples: dict[tuple[str, Cohort], list[Decimal]] = field(default_factory=dict)

    def add(self, metric: str, cohort: Cohort, values: Iterable[Decimal]) -> None:
        self._samples.setdefault((metric, cohort), []).extend(values)

    def distribution(self, metric: str, cohort: Cohort) -> Distribution | None:
        values = self._samples.get((metric, cohort))
        return Distribution(tuple(values)) if values else None


class NoBaselines:
    def distribution(self, metric: str, cohort: Cohort) -> Distribution | None:
        return None
