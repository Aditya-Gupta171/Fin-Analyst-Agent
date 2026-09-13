"""Cohort baselines change what the engine considers unusual, which is how accumulated filings make later
analyses sharper without anyone editing a threshold."""

from decimal import Decimal

import pytest

from app.domain.enums import Sector, SizeBucket
from app.domain.periods import PeriodKind
from app.engine.baselines import Cohort, InMemoryBaselines
from app.engine.catalog import Catalog
from app.engine.pipeline import run_engine
from tests.builders import build_dataset, load_fixture

MID_MANUFACTURING = Cohort(Sector.MANUFACTURING, SizeBucket.MID, PeriodKind.YEAR)


def baselines_with_dso(values: list[int]) -> InMemoryBaselines:
    baselines = InMemoryBaselines()
    baselines.add("dso", MID_MANUFACTURING, [Decimal(v) for v in values])
    return baselines


def dso_rule(catalog: Catalog, baselines: InMemoryBaselines | None):
    result = run_engine(load_fixture("annual_report_manufacturing"), catalog, baselines=baselines)
    return result, next(r for r in result.rules if r.rule_id == "WC_DSO_ELEVATED")


def test_static_fallback_without_a_cohort(catalog: Catalog) -> None:
    _, rule = dso_rule(catalog, None)  # DSO 98 days is below the static 120-day fallback
    assert not rule.fired
    assert any("static threshold 120" in note for note in rule.latest.notes)


def test_same_dso_is_flagged_among_fast_collecting_peers(catalog: Catalog) -> None:
    result, rule = dso_rule(catalog, baselines_with_dso([45, 50, 52, 55, 58, 60, 62, 65, 70, 72]))
    assert rule.fired
    assert any("cohort p90" in note and "n=10" in note for note in rule.latest.notes)
    anomaly = next(a for a in result.anomalies if a.metric == "dso")
    assert (anomaly.basis, anomaly.direction, anomaly.adverse) == ("cohort", "high", True)


def test_same_dso_is_normal_among_slow_collecting_peers(catalog: Catalog) -> None:
    result, rule = dso_rule(catalog, baselines_with_dso([80, 90, 95, 100, 110, 120, 130, 140, 150, 160]))
    assert not rule.fired
    assert not [a for a in result.anomalies if a.metric == "dso"]


def test_history_anomaly_on_a_long_track_record(catalog: Catalog) -> None:
    years = ["FY20", "FY21", "FY22", "FY23", "FY24", "FY25"]
    revenue = dict.fromkeys(years, 1000)
    profit = dict(zip(years, [150, 155, 148, 152, 151, 40], strict=True))
    result = run_engine(
        build_dataset({"revenue_from_operations": revenue, "profit_after_tax": profit}), catalog
    )
    anomaly = next(a for a in result.anomalies if a.metric == "pat_margin")
    assert anomaly.basis == "history"
    assert anomaly.direction == "low" and anomaly.adverse is True
    assert anomaly.reference_display == "15.1%"
    assert anomaly.score < -3


@pytest.mark.parametrize("n", [3, 7])
def test_small_cohorts_are_not_trusted(catalog: Catalog, n: int) -> None:
    result, rule = dso_rule(catalog, baselines_with_dso([40 + i for i in range(n)]))
    assert not rule.fired
    assert not result.anomalies
