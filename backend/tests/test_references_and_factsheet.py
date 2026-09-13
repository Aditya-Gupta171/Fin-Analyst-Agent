import pytest

from app.engine.catalog import Catalog
from app.engine.factsheet import build_fact_sheet, estimate_tokens
from app.engine.pipeline import EngineResult, run_engine
from app.engine.references import contains_bare_figures, find_references, render
from tests.builders import load_fixture


@pytest.fixture(scope="module")
def result(catalog: Catalog) -> EngineResult:
    return run_engine(load_fixture("annual_report_manufacturing"), catalog)


def test_renders_cited_values_from_the_registry(result: EngineResult) -> None:
    rendered = render(
        "Receivables grew {{m:receivables_growth@FY25}} against revenue growth of "
        "{{ m:revenue_growth@FY25 }}.",
        result.references(),
    )
    assert rendered.text == "Receivables grew 55.0% against revenue growth of 12.0%."
    assert rendered.cited == ("m:receivables_growth@FY25", "m:revenue_growth@FY25")
    assert rendered.valid


def test_unknown_references_are_rejected_not_rendered(result: EngineResult) -> None:
    rendered = render(
        "DSO was {{m:dso@FY26}} and CFO {{f:net_cash_from_operating_activities@FY25}}.", result.references()
    )
    assert not rendered.valid
    assert rendered.unknown == ("m:dso@FY26",)
    assert "{{m:dso@FY26}}" in rendered.text


def test_registry_covers_facts_metrics_and_rule_evidence(result: EngineResult) -> None:
    registry = result.references()
    assert registry["f:trade_receivables@FY25"] == "₹403.00 cr"
    assert registry["m:dso@FY25"] == "98 days"
    assert registry["x:prior(net_cash_from_operating_activities)@FY25"] == "₹95.00 cr"
    assert registry["f:auditor_opinion"] == "unmodified"


def test_find_references() -> None:
    assert find_references("a {{m:x@FY25}} b {{f:y}} c {{not a ref}}") == ["m:x@FY25", "f:y"]


@pytest.mark.parametrize(
    ("text", "has_figures"),
    [
        ("Receivables grew {{m:receivables_growth@FY25}} in FY25.", False),
        ("Disclosed under Regulation 33 of SEBI LODR for Q3FY25.", False),
        ("Receivables grew 55% in FY25.", True),
        ("Operating cash flow was ₹50 crore.", True),
        ("Coverage fell to 1.8x.", True),
        ("DSO of 98 days is high.", True),
        ("Margins contracted by 400 bps.", True),
    ],
)
def test_detects_figures_written_outside_references(text: str, has_figures: bool) -> None:
    assert contains_bare_figures(text) is has_figures


def test_fact_sheet_leads_with_findings_and_stays_within_budget(result: EngineResult) -> None:
    sheet = build_fact_sheet(result)
    assert estimate_tokens(sheet) < 1500
    assert sheet.index("INTEGRITY") < sheet.index("RULE FINDINGS") < sheet.index("KEY METRICS")
    assert "[CRITICAL] WC_RECEIVABLES_OUTPACE_REVENUE" in sheet
    assert "[m:receivables_growth@FY25] Trade receivables growth (YoY) = 55.0%" in sheet
    assert "DATA GAPS 3 rules" in sheet


def test_every_reference_in_the_fact_sheet_resolves(result: EngineResult) -> None:
    registry = result.references()
    shown = {
        token.strip("[]")
        for token in build_fact_sheet(result).split()
        if token.startswith("[") and ":" in token
    }
    shown = {ref for ref in shown if "<period>" not in ref}
    assert shown and shown <= registry.keys()
