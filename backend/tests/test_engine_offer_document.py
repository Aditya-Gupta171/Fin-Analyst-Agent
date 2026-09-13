"""A Red Herring Prospectus: the fixture's three restated years plus offer-structure facts."""

import pytest

from app.domain.enums import DocType, Severity
from app.domain.financials import FinancialDataset
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult, run_engine
from tests.builders import load_fixture, make_fact

# ₹1,000 crore offer: ₹300 crore fresh issue and ₹700 crore offer for sale, priced at ₹500 against FY25
# diluted EPS of ₹16.75 (P/E 29.9x) while listed peers trade at a median 18x.
OFFER = {
    "total_issue_size": 1000,
    "fresh_issue_size": 300,
    "offer_for_sale_size": 700,
    "general_corporate_purposes": 80,
    "objects_debt_repayment": 100,
    "price_band_upper": 500,
    "peer_median_pe": 18,
    "promoter_holding_pre_issue": 0.62,
    "promoter_holding_post_issue": 0.45,
    "promoter_average_cost_per_share": 12,
    "litigation_amount_against_company": 50,
    "litigation_criminal_against_promoters": 0,
}


@pytest.fixture(scope="module")
def result(catalog: Catalog) -> EngineResult:
    base = load_fixture("annual_report_manufacturing")
    dataset = FinancialDataset(
        company=base.company,
        document=base.document.model_copy(
            update={"doc_type": DocType.RHP, "title": "Red Herring Prospectus", "restated": True}
        ),
        facts=[*base.facts, *(make_fact(key, None, value) for key, value in OFFER.items())],
    )
    return run_engine(dataset, catalog)


def test_offer_structure_red_flags(result: EngineResult) -> None:
    fired = {rule.rule_id: rule.severity for rule in result.fired_rules if rule.pack == "offer_document"}
    assert fired == {
        "IPO_GCP_EXCEEDS_REGULATORY_CAP": Severity.CRITICAL,  # 26.7% of the fresh issue, above the 25% cap
        "IPO_OFS_HEAVY": Severity.MEDIUM,
        "IPO_VALUATION_PREMIUM_TO_PEERS": Severity.MEDIUM,
        "IPO_PROMOTER_ACQUISITION_COST_LOW": Severity.MEDIUM,
    }


def test_financial_red_flags_still_apply_to_restated_financials(result: EngineResult) -> None:
    fired = {rule.rule_id for rule in result.fired_rules}
    assert {"WC_RECEIVABLES_OUTPACE_REVENUE", "EQ_CFO_PAT_DIVERGENCE"} <= fired


def test_document_scoped_evidence(result: EngineResult) -> None:
    rule = next(r for r in result.rules if r.rule_id == "IPO_VALUATION_PREMIUM_TO_PEERS")
    evidence = {e.ref: e.display for e in rule.latest.evidence}
    assert evidence["m:issue_pe"] == "29.85x"
    assert evidence["f:peer_median_pe"] == "18.00x"
    assert evidence["m:issue_pe_premium_to_peers"] == "65.8%"
    assert evidence["m:roe@FY25"] == "18.9%"  # period-scoped evidence is evaluated at the anchor year
    assert rule.latest.period == "FY25"
