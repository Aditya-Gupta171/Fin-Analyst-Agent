"""PDF statement extraction, tested on the word geometry of real offer-document pages (fixtures/pdf_pages)."""

import json
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

import pytest

from app.domain.enums import Basis, DocType, Sector, Statement
from app.domain.financials import CompanyInfo, DocumentInfo, FinancialDataset
from app.engine.catalog import Catalog
from app.engine.pipeline import run_engine
from app.engine.rules import RuleOutcome
from app.ingestion.amounts import detect_scale, find_dates, parse_amount
from app.ingestion.labels import LabelMatcher
from app.ingestion.models import IngestionIssue
from app.ingestion.offer_document import OfferDocumentError, _company_name, _document_type
from app.ingestion.pdf.layout import Word, build_table, lines_from_words
from app.ingestion.pdf.statements import ExtractedStatements, StatementTable, assemble_statements
from app.ingestion.reconcile import reconcile_profit_and_loss
from app.paths import REPO_ROOT

PAGES = Path(__file__).parent / "fixtures" / "pdf_pages"


def tables(name: str) -> list[StatementTable]:
    raw = json.loads((PAGES / f"{name}.json").read_text(encoding="utf-8"))
    result = []
    for page in raw["pages"]:
        lines = lines_from_words([Word(text, x0, x1, top) for text, x0, x1, top in page["words"]])
        scale = detect_scale("\n".join(line.text for line in lines[:15]))
        assert scale is not None, f"{name} page {page['page']}: unit not found"
        table = build_table(lines)
        result.append(
            StatementTable(
                Statement(page["statement"]),
                page["page"],
                page["title"],
                table,
                scale,
                "\n".join(page["caption"]),
            )
        )
    return result


@cache
def extracted(name: str) -> ExtractedStatements:
    from app.paths import RULES_DIR

    return assemble_statements(tables(name), Catalog.load(RULES_DIR))


def values(result: ExtractedStatements, key: str) -> dict[str, Decimal]:
    return {fact.period: fact.value for fact in result.facts if fact.key == key}


def dataset(result: ExtractedStatements, doc_type: DocType = DocType.RHP) -> FinancialDataset:
    return FinancialDataset(
        company=CompanyInfo(name="Sample", sector=Sector.AUTO),
        document=DocumentInfo(
            doc_type=doc_type,
            basis=Basis.CONSOLIDATED if result.consolidated else Basis.STANDALONE,
            title="Restated financial information",
            restated=result.restated,
            fiscal_year_end_month=result.fiscal_year_end_month,
        ),
        facts=result.facts,
    )


# ── real documents ────────────────────────────────────────────────────────────────────────────────────────


def test_hyundai_quarterly_stub_and_annual_periods() -> None:
    result = extracted("hyundai_motor_india_rhp")
    assert values(result, "revenue_from_operations") == {
        "Q1FY25": Decimal("17344.2340"),
        "Q1FY24": Decimal("16623.5110"),
        "FY24": Decimal("69829.0570"),
        "FY23": Decimal("60307.5800"),
        "FY22": Decimal("47378.4320"),
    }  # ₹ million / 10


def test_hyundai_every_integrity_check_passes(catalog: Catalog) -> None:
    engine = run_engine(dataset(extracted("hyundai_motor_india_rhp")), catalog)
    integrity = {r.rule_id: r.latest.outcome for r in engine.rules if r.pack == "integrity"}
    assert integrity["INT_BALANCE_SHEET_DOES_NOT_BALANCE"] is RuleOutcome.PASSED
    assert integrity["INT_CASH_FLOW_SECTIONS"] is RuleOutcome.PASSED
    assert integrity["INT_PROFIT_BRIDGE"] is RuleOutcome.PASSED
    assert RuleOutcome.FIRED not in integrity.values()


def test_section_context_separates_current_and_non_current_items() -> None:
    result = extracted("hyundai_motor_india_rhp")
    assert values(result, "borrowings_non_current")["FY24"] == Decimal("622.797")
    assert values(result, "borrowings_current")["FY24"] == Decimal("145.118")
    assert values(result, "total_assets")["FY24"] == Decimal("26349.245")


def test_sub_rows_add_up_to_their_header() -> None:
    result = extracted("hyundai_motor_india_rhp")
    payables = next(f for f in result.facts if f.key == "trade_payables" and f.period == "FY24")
    assert payables.value == Decimal("7493.057")  # dues to micro and small enterprises + dues to others
    assert "micro enterprises" in payables.source.raw_label


def test_ola_split_digits_and_presentation_differences_are_reconciled(catalog: Catalog) -> None:
    result = extracted("ola_electric_rhp")
    assert values(result, "total_assets")["FY24"] == Decimal("7735.409")
    assert values(result, "instruments_entirely_equity_in_nature")["FY24"] == Decimal("2973.321")
    # printed as a positive "exceptional items" line under a loss; the PBT identity says it is a loss
    assert values(result, "exceptional_items")["FY24"] == Decimal("-6.050")
    messages = {issue.message for issue in result.issues}
    assert any("excluded finance costs and depreciation" in message for message in messages)

    engine = run_engine(dataset(result), catalog)
    outcome = {r.rule_id: r.latest.outcome for r in engine.rules}
    assert outcome["INT_BALANCE_SHEET_DOES_NOT_BALANCE"] is RuleOutcome.PASSED
    assert outcome["INT_EQUITY_COMPONENTS"] is RuleOutcome.PASSED
    assert outcome["INT_TOTAL_INCOME"] is RuleOutcome.PASSED
    assert {"PRF_LOSS_MAKING", "IPO_NEGATIVE_OPERATING_CASH_FLOW"} <= {r.rule_id for r in engine.fired_rules}


def test_rentomojo_wrapped_header_and_tax_credit(catalog: Catalog) -> None:
    result = extracted("rentomojo_drhp")
    assert result.fiscal_year_end_month == 3
    assert values(result, "revenue_from_operations")["H1FY26"] == Decimal("176.609")
    assert values(result, "total_tax_expense")["H1FY26"] == Decimal("-32.843")  # a deferred tax credit
    engine = run_engine(dataset(result, DocType.DRHP), catalog)
    outcome = {r.rule_id: r.latest.outcome for r in engine.rules}
    assert outcome["INT_PROFIT_BRIDGE"] is RuleOutcome.PASSED
    assert outcome["INT_BALANCE_SHEET_DOES_NOT_BALANCE"] is RuleOutcome.PASSED


def test_ambiguous_face_value_caption_is_not_guessed() -> None:
    # "Nominal value of shares Re. 1/- each (for previous years ... Rs. 10/- each)"
    assert not values(extracted("rentomojo_drhp"), "face_value_per_share")


def test_every_fact_is_traceable_to_a_page_and_label() -> None:
    for name in ("ola_electric_rhp", "hyundai_motor_india_rhp", "rentomojo_drhp"):
        for fact in extracted(name).facts:
            assert fact.source.page and fact.source.raw_label, fact.ref


# ── building blocks ───────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("1,23,456.78", "123456.78"),
        ("(1,602.15)", "-1602.15"),
        ("-", "0"),
        ("–", "0"),
        ("12.5", "12.5"),
        ("abc", None),
    ],
)
def test_parse_amount(token: str, expected: str | None) -> None:
    assert parse_amount(token) == (Decimal(expected) if expected is not None else None)


@pytest.mark.parametrize(
    ("header", "scale"),
    [
        ("All amounts are in INR Million unless otherwise stated", "0.1"),
        ("(All amounts in Rs lakhs)", "0.01"),
        ("(₹ in crores)", "1"),
        ("Amount in ₹ thousands", "0.0001"),
    ],
)
def test_detect_scale(header: str, scale: str) -> None:
    assert detect_scale(header) == Decimal(scale)


def test_find_dates_in_indian_and_us_formats() -> None:
    found = [value for value, _, _ in find_dates("As at 31 March 2024 June 30, 2024 31.12.2023 30-Sept-2025")]
    assert found == [date(2024, 3, 31), date(2024, 6, 30), date(2023, 12, 31), date(2025, 9, 30)]


def test_layout_joins_fragments_under_right_aligned_columns() -> None:
    words = [
        Word("As", 350, 360, 50),
        Word("at", 362, 370, 50),
        Word("31", 330, 340, 60),
        Word("March", 342, 365, 60),
        Word("2024", 367, 385, 60),
        Word("31", 410, 420, 60),
        Word("March", 422, 445, 60),
        Word("2023", 447, 465, 60),
        Word("Property", 80, 120, 80),
        Word("1", 348, 352, 80),
        Word("5,647.23", 354, 385, 80),
        Word("8,811.22", 434, 465, 80),
        Word("Cash", 80, 100, 90),
        Word("(", 350, 353, 90),
        Word("274.20)", 360, 385, 90),
        Word("-", 460, 465, 90),
        Word("Other", 80, 100, 100),
        Word("1,071.14", 354, 385, 100),
        Word("2,429.09", 434, 465, 100),
        Word("Total", 80, 100, 110),
        Word("16,444.17", 350, 385, 110),
        Word("11,240.31", 430, 465, 110),
    ]
    table = build_table(lines_from_words(words))
    assert [c.date for c in table.columns] == [date(2024, 3, 31), date(2023, 3, 31)]
    assert [(row.label, row.values) for row in table.rows] == [
        ("Property", [Decimal("15647.23"), Decimal("8811.22")]),
        ("Cash", [Decimal("-274.20"), Decimal(0)]),
        ("Other", [Decimal("1071.14"), Decimal("2429.09")]),
        ("Total", [Decimal("16444.17"), Decimal("11240.31")]),
    ]


@pytest.mark.parametrize(
    ("label", "statement", "section", "key"),
    [
        ("(i) Borrowings", Statement.BALANCE_SHEET, "Current liabilities", "borrowings_current"),
        ("(i) Borrowings", Statement.BALANCE_SHEET, "Non-current liabilities", "borrowings_non_current"),
        ("Total assets", Statement.BALANCE_SHEET, "Current assets", "total_assets"),
        ("Income", Statement.PROFIT_AND_LOSS, "", None),
        (
            "VI Loss before Exceptional items and tax",
            Statement.PROFIT_AND_LOSS,
            "",
            "profit_before_exceptional_items",
        ),
        ("IX Loss for the year (VII-VIII)", Statement.PROFIT_AND_LOSS, "", "profit_after_tax"),
        (
            "(2) Diluted Earnings per equity share (i.e. anti-dilutive)",
            Statement.PROFIT_AND_LOSS,
            "",
            "eps_diluted",
        ),
        (
            "V Loss before finance costs, depreciation and amortisation and tax",
            Statement.PROFIT_AND_LOSS,
            "",
            None,
        ),
        (
            "Net cash used in operating activities (A)",
            Statement.CASH_FLOW,
            "",
            "net_cash_from_operating_activities",
        ),
        (
            "(Bank Overdraft)/ Cash and cash equivalents at the end of the year",
            Statement.CASH_FLOW,
            "",
            "closing_cash_and_equivalents",
        ),
        ("Loss before tax", Statement.CASH_FLOW, "", None),  # not a cash flow line item
    ],
)
def test_label_matching(
    catalog: Catalog, label: str, statement: Statement, section: str, key: str | None
) -> None:
    match = LabelMatcher(catalog).match(label, statement, section)
    assert (match.key if match else None) == key


def test_reconcile_flips_tax_credit_and_restores_total_expenses() -> None:
    amounts = {
        ("total_income", "FY25"): Decimal(100),
        ("total_expenses", "FY25"): Decimal(70),
        ("finance_costs", "FY25"): Decimal(5),
        ("depreciation_amortisation", "FY25"): Decimal(10),
        ("profit_before_tax", "FY25"): Decimal(15),
        ("total_tax_expense", "FY25"): Decimal(4),
        ("profit_after_tax", "FY25"): Decimal(19),
    }
    issues: list[IngestionIssue] = []
    reconcile_profit_and_loss(amounts, issues)
    assert amounts[("total_expenses", "FY25")] == Decimal(85)
    assert amounts[("total_tax_expense", "FY25")] == Decimal(-4)
    assert len(issues) == 2


def test_cover_page_identity() -> None:
    cover = (
        "(Please scan this QR code)\nDRAFT RED HERRING PROSPECTUS\nDated: March 27, 2026\nRENTOMOJO LIMITED\n"
    )
    assert _document_type(cover) is DocType.DRHP
    assert _company_name(cover) == "RENTOMOJO LIMITED"
    with pytest.raises(OfferDocumentError):
        _document_type("ANNUAL REPORT 2024-25")


SAMPLES = REPO_ROOT / "data" / "samples"


@pytest.mark.skipif(
    not (SAMPLES / "rhp_hyundai_motor_india.pdf").exists(), reason="sample PDFs are not in the repo"
)
def test_full_offer_document_from_pdf(catalog: Catalog) -> None:
    from app.ingestion.offer_document import parse_offer_document

    result = parse_offer_document((SAMPLES / "rhp_hyundai_motor_india.pdf").read_bytes(), catalog)
    assert result.dataset.company.name == "HYUNDAI MOTOR INDIA LIMITED"
    assert result.dataset.document.doc_type is DocType.RHP
    assert len(result.dataset.facts) > 250
