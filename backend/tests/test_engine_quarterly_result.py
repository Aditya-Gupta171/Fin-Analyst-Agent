"""A Regulation 33 quarterly result: the current quarter, the previous quarter, the same quarter last year and
the nine-month year-to-date column."""

import pytest

from app.domain.enums import Severity
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult, run_engine
from tests.builders import build_dataset

# Q3FY25 deteriorates on every axis: revenue -14% YoY, EBITDA margin from 17.7% to 2.3% QoQ, an exceptional
# loss pushes the quarter into a net loss, and operating EBIT no longer covers finance costs.
QUARTERLY = {
    "revenue_from_operations": {"Q3FY24": 500, "Q2FY25": 480, "Q3FY25": 430, "9MFY25": 1390},
    "other_income": {"Q3FY24": 10, "Q2FY25": 12, "Q3FY25": 40, "9MFY25": 64},
    "total_income": {"Q3FY24": 510, "Q2FY25": 492, "Q3FY25": 470, "9MFY25": 1454},
    "cost_of_materials_consumed": {"Q3FY24": 250, "Q2FY25": 240, "Q3FY25": 240},
    "employee_benefits_expense": {"Q3FY24": 60, "Q2FY25": 65, "Q3FY25": 70},
    "finance_costs": {"Q3FY24": 10, "Q2FY25": 11, "Q3FY25": 12},
    "depreciation_amortisation": {"Q3FY24": 20, "Q2FY25": 21, "Q3FY25": 22},
    "other_expenses": {"Q3FY24": 90, "Q2FY25": 90, "Q3FY25": 110},
    "total_expenses": {"Q3FY24": 430, "Q2FY25": 427, "Q3FY25": 454},
    "exceptional_items": {"Q3FY25": -30},
    "profit_before_tax": {"Q3FY24": 80, "Q2FY25": 65, "Q3FY25": -14},
    "total_tax_expense": {"Q3FY24": 20, "Q2FY25": 16, "Q3FY25": -2},
    "profit_after_tax": {"Q3FY24": 60, "Q2FY25": 49, "Q3FY25": -12},
}


@pytest.fixture(scope="module")
def result(catalog: Catalog) -> EngineResult:
    dataset = build_dataset(
        QUARTERLY, document={"doc_type": "quarterly_result", "title": "Unaudited results for Q3FY25"}
    )
    return run_engine(dataset, catalog)


def test_anchor_is_the_quarter_not_the_year_to_date(result: EngineResult) -> None:
    assert result.anchor_period == "Q3FY25"


def test_quarterly_red_flags(result: EngineResult) -> None:
    fired = {rule.rule_id: rule.severity for rule in result.fired_rules}
    assert fired == {
        "LEV_WEAK_INTEREST_COVERAGE": Severity.CRITICAL,  # operating EBIT is negative
        "QTR_REVENUE_DECLINE_YOY": Severity.HIGH,
        "QTR_MARGIN_COLLAPSE_QOQ": Severity.HIGH,
        "QTR_SWING_TO_LOSS": Severity.HIGH,
        "QTR_EXCEPTIONAL_ITEM_MATERIAL": Severity.MEDIUM,
    }


def test_rules_report_on_the_anchor_period(result: EngineResult) -> None:
    for rule in result.rules:
        assert rule.latest.period in {"Q3FY25", "9MFY25"}
    total_income = next(r for r in result.rules if r.rule_id == "INT_TOTAL_INCOME")
    assert total_income.latest.period == "Q3FY25"


def test_annual_only_rules_do_not_run_on_quarters(result: EngineResult) -> None:
    ids = {rule.rule_id for rule in result.rules}
    assert "EQ_CFO_PAT_DIVERGENCE" not in ids
    assert "PRF_MARGIN_COMPRESSION" not in ids


def test_sequential_margin_evidence(result: EngineResult) -> None:
    rule = next(r for r in result.rules if r.rule_id == "QTR_MARGIN_COLLAPSE_QOQ")
    displays = {e.label: e.display for e in rule.latest.evidence}
    assert displays["EBITDA margin"] == "2.3%"
    assert displays["EBITDA margin (previous quarter)"] == "17.7%"
