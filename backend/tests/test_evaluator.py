from decimal import Decimal

import pytest

from app.domain.enums import Sector, SizeBucket
from app.domain.periods import Period, PeriodKind
from app.engine.baselines import Cohort, InMemoryBaselines
from app.engine.catalog import Catalog
from app.engine.evaluator import Evaluator, MissingData
from app.engine.expressions import parse_expression
from tests.builders import build_dataset

FY24, FY25 = Period.parse("FY24"), Period.parse("FY25")


@pytest.fixture
def evaluator(catalog: Catalog) -> Evaluator:
    dataset = build_dataset(
        {
            "revenue_from_operations": {"FY23": 800, "FY24": 1000, "FY25": 1200},
            "profit_after_tax": {"FY23": -10, "FY24": 50, "FY25": 90},
            "trade_receivables": {"FY24": 200, "FY25": 300},
            "total_assets": {"FY24": 900, "FY25": 1000},
            "other_income": {"FY25": 0},
        },
        {"auditor_opinion": "qualified"},
    )
    return Evaluator(dataset, catalog)


def evaluate(evaluator: Evaluator, source: str, period: Period = FY25):
    return evaluator.evaluate(parse_expression(source), period)


def test_decimal_arithmetic_is_exact(evaluator: Evaluator) -> None:
    assert evaluate(evaluator, "revenue_from_operations * 0.1 + 0.2").value == Decimal("120.2")


def test_missing_input_is_reported_not_defaulted(evaluator: Evaluator) -> None:
    result = evaluate(evaluator, "trade_payables / revenue_from_operations")
    assert result.value is None
    assert result.missing == {"f:trade_payables@FY25"}


def test_every_missing_operand_is_reported(evaluator: Evaluator) -> None:
    result = evaluate(evaluator, "trade_payables + contingent_liabilities")
    assert result.missing == {"f:trade_payables@FY25", "f:contingent_liabilities@FY25"}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("revenue_from_operations < 0 and trade_payables > 0", False),  # false dominates unknown
        ("revenue_from_operations > 0 or trade_payables > 0", True),  # true dominates unknown
    ],
)
def test_kleene_logic(evaluator: Evaluator, source: str, expected: bool) -> None:
    assert evaluate(evaluator, source).value is expected


@pytest.mark.parametrize(
    "source",
    [
        "revenue_from_operations > 0 and trade_payables > 0",
        "revenue_from_operations < 0 or trade_payables > 0",
    ],
)
def test_kleene_unknown(evaluator: Evaluator, source: str) -> None:
    assert evaluate(evaluator, source).missing == {"f:trade_payables@FY25"}


def test_division_by_zero_is_missing_with_reason(evaluator: Evaluator) -> None:
    result = evaluate(evaluator, "revenue_from_operations / other_income")
    assert result.value is None
    assert any("division by zero" in reason for reason in result.missing)


def test_growth_uses_absolute_prior_value(evaluator: Evaluator) -> None:
    assert evaluate(evaluator, "growth(revenue_from_operations)").value == Decimal("0.2")
    # from a loss of 10 to a profit of 50 is a positive change of 600%
    assert evaluate(evaluator, "growth(profit_after_tax)", FY24).value == Decimal(6)


def test_average_balance_falls_back_to_closing_with_note(evaluator: Evaluator) -> None:
    assert evaluate(evaluator, "avg(trade_receivables)").value == Decimal(250)
    first_year = evaluate(evaluator, "avg(trade_receivables)", FY24)
    assert first_year.value == Decimal(200)
    assert any("closing balance used" in note for note in first_year.notes)


def test_balances_resolve_by_date_across_period_labels(catalog: Catalog) -> None:
    dataset = build_dataset(
        {"trade_receivables": {"FY24": 200}, "revenue_from_operations": {"H1FY25": 600, "H2FY24": 500}}
    )
    evaluator = Evaluator(dataset, catalog)
    # H2FY24 ends on 31 Mar 2024, the same balance-sheet date as FY24.
    assert evaluate(evaluator, "trade_receivables", Period.parse("H2FY24")).value == Decimal(200)


def test_nil_if_absent_only_when_statement_is_reported(evaluator: Evaluator) -> None:
    reported = evaluate(evaluator, "exceptional_items")
    assert reported.value == Decimal(0)
    assert any("read as nil" in note for note in reported.notes)
    assert evaluate(evaluator, "exceptional_items", Period.parse("Q1FY25")).missing  # no P&L for Q1FY25


def test_provenance_records_facts_and_metrics(evaluator: Evaluator) -> None:
    result = evaluate(evaluator, "pat_margin > 0.05 and revenue_from_operations > 0")
    assert result.inputs == {"m:pat_margin@FY25", "f:revenue_from_operations@FY25"}
    metric = evaluator.metric("pat_margin", FY25)
    assert metric.value == Decimal("0.075")
    assert set(metric.inputs) == {"f:profit_after_tax@FY25", "f:revenue_from_operations@FY25"}


def test_text_facts_and_membership(evaluator: Evaluator) -> None:
    assert evaluate(evaluator, 'auditor_opinion in ["qualified", "adverse"]').value is True
    assert evaluate(evaluator, 'auditor_opinion == "unmodified"').value is False


def test_streak(evaluator: Evaluator) -> None:
    assert evaluate(evaluator, "streak(profit_after_tax > 0, 2)").value is True
    assert evaluate(evaluator, "streak(profit_after_tax > 0, 3)").value is False  # FY23 was a loss
    assert (
        evaluate(evaluator, "streak(profit_after_tax > 0, 4)").value is False
    )  # false dominates missing FY22


def test_trailing_sum(evaluator: Evaluator) -> None:
    assert evaluate(evaluator, "trailing_sum(profit_after_tax, 3)").value == Decimal(130)
    assert evaluate(evaluator, "trailing_sum(profit_after_tax, 4)").missing == {"f:profit_after_tax@FY22"}


def test_coalesce_and_exists(evaluator: Evaluator) -> None:
    assert evaluate(evaluator, "coalesce(trade_payables, trade_receivables)").value == Decimal(300)
    assert evaluate(evaluator, "exists(trade_payables)").value is False


def test_ttm_sums_four_quarters(catalog: Catalog) -> None:
    dataset = build_dataset(
        {"revenue_from_operations": {"Q1FY25": 10, "Q2FY25": 20, "Q3FY25": 30, "Q4FY24": 40}}
    )
    evaluator = Evaluator(dataset, catalog)
    assert evaluate(evaluator, "ttm(revenue_from_operations)", Period.parse("Q3FY25")).value == Decimal(100)


def test_cohort_percentile_uses_fallback_until_cohort_is_large_enough(catalog: Catalog) -> None:
    dataset = build_dataset({"revenue_from_operations": {"FY25": 1000}, "trade_receivables": {"FY25": 250}})
    baselines = InMemoryBaselines()
    cohort = Cohort(Sector.MANUFACTURING, SizeBucket.MID, PeriodKind.YEAR)
    baselines.add("dso", cohort, [Decimal(d) for d in (40, 50, 60)])
    evaluator = Evaluator(dataset, catalog, size=SizeBucket.MID, baselines=baselines)

    small = evaluate(evaluator, "cohort_pct(dso, 90, 120)")
    assert small.value == Decimal(120)
    assert any("static threshold" in note for note in small.notes)

    baselines.add("dso", cohort, [Decimal(d) for d in (45, 55, 65, 70, 75, 80)])
    large = evaluate(evaluator, "cohort_pct(dso, 90, 120)")
    assert large.value == Decimal(76)
    assert evaluate(evaluator, "cohort_rank(dso) > 0.95").value is True  # DSO of ~91 days tops the cohort


def test_sector_excluded_metric_is_not_applicable(catalog: Catalog) -> None:
    dataset = build_dataset(
        {"revenue_from_operations": {"FY25": 100}, "trade_receivables": {"FY25": 10}},
        company={"sector": "banking"},
    )
    with pytest.raises(MissingData, match="not meaningful"):
        Evaluator(dataset, catalog).metric("dso", FY25)
