"""XBRL ingestion against unmodified public NSE filings (see fixtures/xbrl/README.md)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.enums import Basis, DocType, Sector
from app.domain.financials import Fact, FinancialDataset
from app.domain.periods import Period, PeriodKind
from app.engine.catalog import Catalog
from app.engine.pipeline import run_engine
from app.engine.rules import RuleOutcome
from app.ingestion.merge import MergeError, merge_results
from app.ingestion.models import IngestionResult
from app.ingestion.xbrl import XbrlError, parse_results_xbrl

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"


def ingest(catalog: Catalog, name: str, sector: Sector = Sector.MANUFACTURING) -> IngestionResult:
    return parse_results_xbrl((FIXTURES / f"{name}.xml").read_bytes(), catalog, sector=sector)


def value(dataset: FinancialDataset, key: str, period: str) -> Decimal | None:
    fact = dataset.get(key, period)
    return fact.value if fact else None


@pytest.fixture(scope="module")
def nocil(catalog: Catalog) -> IngestionResult:
    return ingest(catalog, "nocil_q2fy25_standalone", Sector.CHEMICALS)


def test_reads_filing_metadata(nocil: IngestionResult) -> None:
    dataset = nocil.dataset
    assert dataset.company.name == "NOCIL Limited"
    assert (dataset.company.nse_symbol, dataset.company.bse_code) == ("NOCIL", "500730")
    assert dataset.document.doc_type is DocType.QUARTERLY_RESULT
    assert dataset.document.basis is Basis.STANDALONE
    assert dataset.document.filing_date == date(2024, 10, 28)
    assert [p.label for p in dataset.periods] == ["Q2FY25", "H1FY25"]


def test_converts_rupees_to_crore_and_keeps_per_share_values(nocil: IngestionResult) -> None:
    dataset = nocil.dataset
    assert value(dataset, "revenue_from_operations", "Q2FY25") == Decimal("362.70")
    assert value(dataset, "revenue_from_operations", "H1FY25") == Decimal("734.87")
    assert value(dataset, "eps_basic", "Q2FY25") == Decimal("2.49")
    assert value(dataset, "face_value_per_share", "Q2FY25") == Decimal(10)


def test_balance_sheet_instant_takes_the_year_to_date_label(nocil: IngestionResult) -> None:
    assert value(nocil.dataset, "total_assets", "H1FY25") == Decimal("2073.01")
    assert value(nocil.dataset, "trade_receivables", "H1FY25") == Decimal("322.14")  # current + non-current


def test_cash_flow_signs_and_opening_closing_cash(nocil: IngestionResult) -> None:
    dataset = nocil.dataset
    capex = dataset.get("purchase_of_property_plant_equipment", "H1FY25")
    assert capex.value == Decimal("-21.84")  # tagged as positive outflows, stored as negative
    assert "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities" in capex.source.raw_label
    assert value(dataset, "opening_cash_and_equivalents", "H1FY25") == Decimal("90.29")
    assert value(dataset, "closing_cash_and_equivalents", "H1FY25") == Decimal("21.68")


def test_contradictory_context_dates_are_corrected_and_reported(nocil: IngestionResult) -> None:
    assert any("contradict its reporting period" in issue.message for issue in nocil.issues)
    assert nocil.dataset.period("H1FY25").start_date == date(2024, 4, 1)


def test_unmapped_non_zero_elements_are_listed(nocil: IngestionResult) -> None:
    assert "EffectOfExchangeRateChangesOnCashAndCashEquivalents" in nocil.unmapped
    assert not [name for name in nocil.unmapped if name.startswith(("Date", "Adjustments"))]


@pytest.mark.parametrize(
    ("name", "basis"),
    [("nocil_q2fy25_standalone", Basis.STANDALONE), ("paramount_q2fy25_consolidated", Basis.CONSOLIDATED)],
)
def test_real_statements_pass_every_integrity_check(catalog: Catalog, name: str, basis: Basis) -> None:
    result = ingest(catalog, name)
    assert result.dataset.document.basis is basis
    engine = run_engine(result.dataset, catalog)
    integrity = {r.rule_id: r.latest.outcome for r in engine.rules if r.pack == "integrity"}
    assert integrity["INT_BALANCE_SHEET_DOES_NOT_BALANCE"] is RuleOutcome.PASSED
    assert integrity["INT_CASH_FLOW_SECTIONS"] is RuleOutcome.PASSED
    assert integrity["INT_CASH_ROLL_FORWARD"] is RuleOutcome.PASSED
    assert not [rule_id for rule_id, outcome in integrity.items() if outcome is RuleOutcome.FIRED]


def test_january_december_fiscal_year(catalog: Catalog) -> None:
    dataset = ingest(catalog, "ksb_q3fy25_consolidated").dataset
    assert dataset.document.fiscal_year_end_month == 12
    q3 = dataset.period("Q3FY25")
    assert (q3.start_date, q3.end_date) == (date(2025, 7, 1), date(2025, 9, 30))
    assert dataset.period("9MFY25").start_date == date(2025, 1, 1)
    assert dataset.company.isin == "INE999A01023"


def test_profit_bridge_includes_share_of_associates(catalog: Catalog) -> None:
    dataset = ingest(catalog, "ksb_q3fy25_consolidated").dataset
    assert value(dataset, "share_of_profit_of_associates", "Q3FY25") == Decimal("3.3")
    engine = run_engine(dataset, catalog)
    bridge = next(r for r in engine.rules if r.rule_id == "INT_PROFIT_BRIDGE")
    assert bridge.latest.outcome is RuleOutcome.PASSED


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not xml", "not well-formed"),
        (b"<root/>", "not an XBRL instance"),
        (b'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"/>', "no reporting-period context"),
    ],
)
def test_rejects_non_results_content(catalog: Catalog, content: bytes, message: str) -> None:
    with pytest.raises(XbrlError, match=message):
        parse_results_xbrl(content, catalog)


def test_entity_expansion_attacks_are_refused(catalog: Catalog) -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]><x>&b;</x>'
    with pytest.raises(XbrlError, match="unsafe XML"):
        parse_results_xbrl(bomb, catalog)


# ── merging filings ───────────────────────────────────────────────────────────────────────────────────────


def with_facts(result: IngestionResult, facts: list[Fact], filing_date: date) -> IngestionResult:
    dataset = result.dataset
    document = dataset.document.model_copy(update={"filing_date": filing_date})
    return IngestionResult(dataset=FinancialDataset(company=dataset.company, document=document, facts=facts))


def test_merging_adds_comparatives_for_year_on_year_rules(catalog: Catalog, nocil: IngestionResult) -> None:
    prior = with_facts(
        nocil,
        [
            Fact(key="revenue_from_operations", period="Q2FY24", value=Decimal("420.00")),
            Fact(key="profit_after_tax", period="Q2FY24", value=Decimal("35.00")),
        ],
        date(2023, 10, 30),
    )
    merged = merge_results([nocil, prior])  # order of arguments does not matter
    assert value(merged.dataset, "revenue_from_operations", "Q2FY24") == Decimal("420.00")
    assert merged.dataset.document.filing_date == date(2024, 10, 28)  # the latest filing describes the result

    engine = run_engine(merged.dataset, catalog)
    growth = next(m for m in engine.metrics if m.key == "revenue_growth" and m.period == "Q2FY25")
    assert growth.display == "-13.6%"
    assert "QTR_REVENUE_DECLINE_YOY" in {rule.rule_id for rule in engine.fired_rules}


def test_restatements_between_filings_are_flagged(nocil: IngestionResult) -> None:
    restated = with_facts(
        nocil,
        [Fact(key="revenue_from_operations", period="Q2FY25", value=Decimal("350.00"))],
        date(2025, 1, 30),
    )
    merged = merge_results([nocil, restated])
    assert value(merged.dataset, "revenue_from_operations", "Q2FY25") == Decimal("350.00")
    warning = next(issue for issue in merged.issues if issue.message.startswith("restated"))
    assert warning.ref == "f:revenue_from_operations@Q2FY25"


def test_refuses_to_merge_different_companies_or_bases(catalog: Catalog, nocil: IngestionResult) -> None:
    with pytest.raises(MergeError, match="different companies"):
        merge_results([nocil, ingest(catalog, "ksb_q3fy25_consolidated")])
    consolidated = nocil.dataset.document.model_copy(update={"basis": Basis.CONSOLIDATED})
    other = IngestionResult(dataset=nocil.dataset.model_copy(update={"document": consolidated}))
    with pytest.raises(MergeError, match="standalone and consolidated"):
        merge_results([nocil, other])


# ── fiscal calendars ──────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("start", "end", "year_end", "label"),
    [
        (date(2024, 7, 1), date(2024, 9, 30), 3, "Q2FY25"),
        (date(2024, 4, 1), date(2024, 9, 30), 3, "H1FY25"),
        (date(2024, 10, 1), date(2025, 3, 31), 3, "H2FY25"),
        (date(2024, 4, 1), date(2024, 12, 31), 3, "9MFY25"),
        (date(2024, 4, 1), date(2025, 3, 31), 3, "FY25"),
        (date(2025, 7, 1), date(2025, 9, 30), 12, "Q3FY25"),
        (date(2025, 1, 1), date(2025, 12, 31), 12, "FY25"),
    ],
)
def test_period_from_dates(start: date, end: date, year_end: int, label: str) -> None:
    period = Period.from_dates(start, end, year_end)
    assert period.label == label
    assert (period.start_date, period.end_date) == (start, end)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2024, 5, 1), date(2024, 7, 31)),  # not a fiscal quarter
        (date(2024, 5, 1), date(2024, 10, 31)),  # six months not starting at the fiscal year start
        (date(2024, 4, 15), date(2024, 6, 30)),  # partial month
    ],
)
def test_period_from_dates_rejects_misaligned_spans(start: date, end: date) -> None:
    with pytest.raises(ValueError):
        Period.from_dates(start, end, 3)


def test_comparisons_preserve_the_fiscal_calendar() -> None:
    q1 = Period(PeriodKind.QUARTER, 2025, 1, year_end_month=12)
    assert q1.previous_sequential().end_date == date(2024, 12, 31)
    assert q1.prior_comparable().start_date == date(2024, 1, 1)
