"""End-to-end engine run on the fictional annual-report fixture, whose planted red flags are listed at the top
of ``fixtures/annual_report_manufacturing.yaml``."""

from decimal import Decimal

import pytest

from app.domain.enums import Severity, SizeBucket
from app.domain.financials import Fact, FinancialDataset
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult, run_engine
from app.engine.rules import RuleOutcome
from tests.builders import build_dataset, load_fixture


@pytest.fixture(scope="module")
def result(catalog: Catalog) -> EngineResult:
    return run_engine(load_fixture("annual_report_manufacturing"), catalog)


def test_document_context(result: EngineResult) -> None:
    assert result.anchor_period == "FY25"
    assert result.periods == ["FY23", "FY24", "FY25"]
    assert result.size_bucket is SizeBucket.MID
    assert result.dataset_issues == []


def test_detects_exactly_the_planted_red_flags(result: EngineResult) -> None:
    fired = {rule.rule_id: rule.severity for rule in result.fired_rules}
    assert fired == {
        "WC_RECEIVABLES_OUTPACE_REVENUE": Severity.CRITICAL,  # escalated: cash conversion is also weak
        "EQ_CFO_PAT_DIVERGENCE": Severity.HIGH,
        "EQ_PROFIT_UP_CASH_DOWN": Severity.HIGH,
        "GOV_CONTINGENT_LIABILITIES_LARGE": Severity.HIGH,
        "EQ_OTHER_INCOME_DEPENDENCE": Severity.MEDIUM,
    }


def test_fired_rules_are_ordered_by_severity(result: EngineResult) -> None:
    ranks = [rule.severity.rank for rule in result.fired_rules]
    assert ranks == sorted(ranks, reverse=True)


def test_clean_statements_pass_every_integrity_check(result: EngineResult) -> None:
    integrity = [rule for rule in result.rules if rule.pack == "integrity"]
    assert integrity
    assert all(rule.latest.outcome is RuleOutcome.PASSED for rule in integrity)


def test_rule_history_shows_persistence(result: EngineResult) -> None:
    rule = next(r for r in result.rules if r.rule_id == "EQ_PROFIT_UP_CASH_DOWN")
    assert rule.fired_periods == ["FY24", "FY25"]
    receivables = next(r for r in result.rules if r.rule_id == "WC_RECEIVABLES_OUTPACE_REVENUE")
    assert [o.outcome for o in receivables.outcomes] == [
        RuleOutcome.INSUFFICIENT_DATA,  # FY23 has no prior year to compare with
        RuleOutcome.PASSED,  # FY24 gap is exactly 20 points, not above the threshold
        RuleOutcome.FIRED,
    ]


def test_evidence_is_computed_formatted_and_traceable(result: EngineResult) -> None:
    rule = next(r for r in result.rules if r.rule_id == "WC_RECEIVABLES_OUTPACE_REVENUE")
    evidence = {e.ref: e for e in rule.latest.evidence}
    assert evidence["m:receivables_growth@FY25"].value == Decimal("0.55")
    assert evidence["m:receivables_growth@FY25"].display == "55.0%"
    assert evidence["m:revenue_growth@FY25"].display == "12.0%"
    assert evidence["m:cfo_to_pat@FY25"].display == "0.30x"
    assert set(rule.latest.inputs) == {
        "m:receivables_growth@FY25",
        "m:revenue_growth@FY25",
        "f:trade_receivables@FY25",
        "f:revenue_from_operations@FY25",
    }
    assert rule.kb and rule.questions


def test_prior_period_evidence_gets_its_own_reference(result: EngineResult) -> None:
    rule = next(r for r in result.rules if r.rule_id == "EQ_PROFIT_UP_CASH_DOWN")
    prior = next(e for e in rule.latest.evidence if e.ref.startswith("x:"))
    assert prior.ref == "x:prior(net_cash_from_operating_activities)@FY25"
    assert prior.display == "₹95.00 cr"


def test_missing_disclosures_are_reported_as_data_gaps(result: EngineResult) -> None:
    gaps = {rule.rule_id: rule.latest.missing for rule in result.data_gaps}
    assert gaps["WC_UNBILLED_REVENUE_HIGH"] == ["f:unbilled_revenue@FY24", "f:unbilled_revenue@FY25"]
    assert "EQ_RECURRING_EXCEPTIONAL_ITEMS" not in gaps  # absent exceptional items line is read as nil


@pytest.mark.parametrize(
    ("key", "period", "display"),
    [
        ("ebitda", "FY25", "₹237.00 cr"),
        ("ebitda_margin", "FY25", "19.2%"),
        ("dso", "FY25", "98 days"),
        ("debt_to_equity", "FY25", "0.49x"),
        ("interest_coverage", "FY25", "4.92x"),
        ("free_cash_flow", "FY25", "-₹80.00 cr"),
        ("cumulative_cfo_to_pat_3y", "FY25", "0.62x"),
    ],
)
def test_metric_values(result: EngineResult, key: str, period: str, display: str) -> None:
    record = next(m for m in result.metrics if m.key == key and m.period == period)
    assert record.display == display


def test_offer_and_quarterly_rules_do_not_apply(result: EngineResult) -> None:
    assert not [rule for rule in result.rules if rule.pack in {"offer_document", "quarterly"}]


def test_balance_sheet_that_does_not_balance_is_flagged(catalog: Catalog) -> None:
    dataset = load_fixture("annual_report_manufacturing")
    broken = [
        Fact(key=f.key, period=f.period, value=Decimal(1700))
        if (f.key, f.period) == ("total_assets", "FY25")
        else f
        for f in dataset.facts
    ]
    result = run_engine(
        FinancialDataset(company=dataset.company, document=dataset.document, facts=broken), catalog
    )
    rule = next(r for r in result.rules if r.rule_id == "INT_BALANCE_SHEET_DOES_NOT_BALANCE")
    assert rule.fired and rule.fired_periods == ["FY25"]


def test_invalid_facts_are_dropped_and_reported(catalog: Catalog) -> None:
    dataset = build_dataset(
        {"revenue_from_operations": {"FY25": 100}, "made_up_item": {"FY25": 1}},
        {"total_issue_size": "large"},
    )
    result = run_engine(dataset, catalog)
    assert {(issue.level, issue.ref) for issue in result.dataset_issues} == {
        ("warning", "f:made_up_item@FY25"),
        ("error", "f:total_issue_size"),
    }
