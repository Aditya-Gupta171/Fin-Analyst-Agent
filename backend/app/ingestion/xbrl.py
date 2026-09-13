"""SEBI Ind AS financial-results XBRL (Regulation 33 filings on NSE and BSE).

This is the highest-fidelity input: values are tagged, so no table extraction or label guessing is involved.
The parser handles both the older ``in-bse-fin`` schema and the 2025 integrated filing schema (``in-capmkt``),
which share element local names. Findings from real filings that shaped it:

* values are in full rupees whatever "level of rounding" the company declares, so they are converted to
  ₹ crore;
* one filing carries only the current quarter, the year to date and a balance-sheet date — comparatives come
  from merging several filings (see :mod:`app.ingestion.merge`);
* context dates are sometimes wrong while the ``DateOfStartOfReportingPeriod`` facts inside the context are
  right, so the facts win and a warning is raised;
* some companies report on a January–December fiscal year;
* opening and closing cash share one context and are distinguished only by document order;
* cash outflows such as capital expenditure are tagged as positive numbers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from defusedxml import DefusedXmlException, ElementTree

from app.domain.enums import Basis, DocType, Sector, Unit
from app.domain.financials import (
    CompanyInfo,
    DocumentInfo,
    ExtractionMethod,
    Fact,
    FinancialDataset,
    SourceRef,
    fact_ref,
)
from app.domain.periods import INDIAN_FISCAL_YEAR_END, Period
from app.engine.catalog import Catalog
from app.ingestion.models import IngestionIssue, IngestionResult

_XBRLI = "{http://www.xbrl.org/2003/instance}"
_XBRLDI = "{http://xbrl.org/2006/xbrldi}"
RUPEES_PER_CRORE = Decimal(10) ** 7

# Elements that are metadata, text, or intermediate reconciliation lines rather than statement line items.
_NOT_LINE_ITEMS = (
    "Adjustments",
    "Date",
    "Description",
    "Details",
    "Disclosure",
    "Whether",
    "Is",
    "Type",
    "Name",
)


class XbrlError(ValueError):
    """The content is not a financial-results XBRL instance this parser understands."""


@dataclass
class _Context:
    id: str
    start: date | None
    end: date  # the instant for balance-sheet contexts
    dimensional: bool
    facts: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    @property
    def instant(self) -> bool:
        return self.start is None


def parse_results_xbrl(
    content: bytes,
    catalog: Catalog,
    *,
    sector: Sector = Sector.OTHER,
    source_url: str | None = None,
) -> IngestionResult:
    contexts = _read_contexts(content)
    primary = contexts.get("OneD") or next(
        (c for c in contexts.values() if not c.instant and not c.dimensional), None
    )
    if primary is None:
        raise XbrlError("no reporting-period context found; not a financial results XBRL filing")
    issues: list[IngestionIssue] = []

    def meta(name: str) -> str | None:
        values = primary.facts.get(name)
        return values[0].strip() if values and values[0].strip() else None

    year_end_month = _fiscal_year_end_month(meta("DateOfEndOfFinancialYear"), issues)
    periods = _resolve_periods(contexts, year_end_month, issues)
    if not periods:
        raise XbrlError("no context maps to a quarter, half year, nine months or year")

    facts, unmapped = _extract_facts(contexts, periods, catalog, issues)
    facts += _document_facts(meta("DeclarationOfUnmodifiedOpinionOrStatementOnImpactOfAuditQualification"))
    _check_exceptional_items_sign(facts, contexts, periods, issues)

    company_name = meta("NameOfTheCompany")
    if not company_name:
        raise XbrlError("filing does not name the company")
    if sector is Sector.OTHER:
        issues.append(
            IngestionIssue(level="info", message="sector not supplied; cohort comparisons are disabled")
        )
    anchor = min(
        (p for p in periods.values() if p.end_date == max(q.end_date for q in periods.values())),
        key=lambda p: p.days,
    )
    basis = (
        Basis.CONSOLIDATED
        if (meta("NatureOfReportStandaloneConsolidated") or "").lower().startswith("consol")
        else Basis.STANDALONE
    )
    audited = (meta("WhetherResultsAreAuditedOrUnaudited") or "").lower() == "audited"

    dataset = FinancialDataset(
        company=CompanyInfo(
            name=company_name,
            sector=sector,
            nse_symbol=meta("Symbol") if meta("Symbol") not in (None, "NOTLISTED") else None,
            bse_code=meta("ScripCode"),
            isin=meta("ISIN"),
        ),
        document=DocumentInfo(
            doc_type=DocType.QUARTERLY_RESULT,
            basis=basis,
            title=f"{'Audited' if audited else 'Unaudited'} {basis} financial results for {anchor.label}",
            filing_date=_parse_date(meta("DateOfBoardMeetingWhenFinancialResultsWereApproved")),
            source_url=source_url,
            fiscal_year_end_month=year_end_month,
        ),
        facts=facts,
    )
    return IngestionResult(dataset=dataset, issues=issues, unmapped=sorted(unmapped))


def _read_contexts(content: bytes) -> dict[str, _Context]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise XbrlError(f"not well-formed XML: {exc}") from exc
    except DefusedXmlException as exc:
        raise XbrlError(f"rejected unsafe XML construct: {exc}") from exc
    if root.tag != f"{_XBRLI}xbrl":
        raise XbrlError("not an XBRL instance document")

    contexts: dict[str, _Context] = {}
    for element in root.iter(f"{_XBRLI}context"):
        period = element.find(f"{_XBRLI}period")
        if period is None:
            continue
        instant = period.findtext(f"{_XBRLI}instant")
        start = None if instant else _parse_date(period.findtext(f"{_XBRLI}startDate"))
        end = _parse_date(instant or period.findtext(f"{_XBRLI}endDate"))
        if end is None:
            continue
        dimensional = element.find(f".//{_XBRLDI}explicitMember") is not None or (
            element.find(f".//{_XBRLDI}typedMember") is not None
        )
        contexts[element.get("id", "")] = _Context(element.get("id", ""), start, end, dimensional)

    for element in root:
        context = contexts.get(element.get("contextRef", ""))
        if context is not None and element.text is not None:
            context.facts[element.tag.rsplit("}", 1)[-1]].append(element.text)
    return contexts


def _fiscal_year_end_month(value: str | None, issues: list[IngestionIssue]) -> int:
    end = _parse_date(value)
    if end is None:
        issues.append(IngestionIssue(level="warning", message="fiscal year end not stated; assuming March"))
        return INDIAN_FISCAL_YEAR_END
    return end.month


def _resolve_periods(
    contexts: dict[str, _Context], year_end_month: int, issues: list[IngestionIssue]
) -> dict[str, Period]:
    """Context id -> period, for non-dimensional contexts.

    Balance-sheet instants take the label of the longest reporting period ending on the same date.
    """
    periods: dict[str, Period] = {}
    for context in contexts.values():
        if context.dimensional or context.instant:
            continue
        start, end = context.start, context.end
        stated_start = _parse_date(_first(context.facts.get("DateOfStartOfReportingPeriod")))
        stated_end = _parse_date(_first(context.facts.get("DateOfEndOfReportingPeriod")))
        if stated_start and stated_end and (stated_start, stated_end) != (start, end):
            issues.append(
                IngestionIssue(
                    level="warning",
                    message=f"context {context.id} dates {start}..{end} contradict its reporting period "
                    f"{stated_start}..{stated_end}; using the reporting period",
                )
            )
            start, end = stated_start, stated_end
        try:
            periods[context.id] = Period.from_dates(start, end, year_end_month)  # type: ignore[arg-type]
        except ValueError as exc:
            issues.append(IngestionIssue(level="warning", message=f"context {context.id} skipped: {exc}"))

    for context in contexts.values():
        if context.dimensional or not context.instant:
            continue
        ending = [p for p in periods.values() if p.end_date == context.end]
        if ending:
            periods[context.id] = max(ending, key=lambda p: p.days)
        else:
            issues.append(
                IngestionIssue(level="warning", message=f"balance sheet date {context.end} has no period")
            )
    return periods


def _extract_facts(
    contexts: dict[str, _Context],
    periods: dict[str, Period],
    catalog: Catalog,
    issues: list[IngestionIssue],
) -> tuple[list[Fact], set[str]]:
    mapped_elements = {
        element.lstrip("-").split("#")[0] for item in catalog.items.values() for element in item.xbrl
    }
    facts: dict[tuple[str, str], Fact] = {}
    unmapped: set[str] = set()

    for context_id, period in periods.items():
        context = contexts[context_id]
        for item in catalog.items.values():
            if not item.xbrl:
                continue
            total: Decimal | None = None
            used: list[str] = []
            for element in item.xbrl:
                name, _, position = element.lstrip("-").partition("#")
                raw_values = context.facts.get(name)
                if not raw_values:
                    continue
                if position and len(raw_values) < 2:
                    continue  # opening and closing cannot be told apart from a single value
                raw = raw_values[-1] if position == "last" else raw_values[0]
                value = _decimal(raw)
                if value is None:
                    issues.append(IngestionIssue(level="warning", message=f"non-numeric {name}: {raw!r}"))
                    continue
                total = (total or Decimal(0)) + (-value if element.startswith("-") else value)
                used.append(name)
            if total is None:
                continue
            if item.unit is Unit.INR_CRORE:
                total = total / RUPEES_PER_CRORE
            key = (item.key, period.label)
            if key in facts and facts[key].value != total:
                issues.append(
                    IngestionIssue(
                        level="warning",
                        ref=fact_ref(*key),
                        message=f"conflicting values in contexts for {period.label}; kept the first",
                    )
                )
                continue
            facts.setdefault(
                key,
                Fact(
                    key=item.key,
                    period=period.label,
                    value=total,
                    source=SourceRef(
                        table=context_id, raw_label=" + ".join(used), method=ExtractionMethod.XBRL
                    ),
                ),
            )

        for name, values in context.facts.items():
            if name in mapped_elements or name.startswith(_NOT_LINE_ITEMS):
                continue
            number = _decimal(values[0])
            if number:  # non-zero numeric value nobody captured
                unmapped.add(name)
    return list(facts.values()), unmapped


def _document_facts(declaration: str | None) -> list[Fact]:
    """Audit opinion from the declaration required with audited results (Regulation 33(3)(d))."""
    if not declaration:
        return []
    text = declaration.lower()
    if "unmodified" in text:
        opinion = "unmodified"
    elif "qualification" in text or "modified" in text:
        opinion = "qualified"
    else:
        return []  # "Not applicable" for unaudited results
    return [
        Fact(
            key="auditor_opinion",
            text=opinion,
            source=SourceRef(method=ExtractionMethod.XBRL, raw_label=declaration),
        )
    ]


def _check_exceptional_items_sign(
    facts: list[Fact], contexts: dict[str, _Context], periods: dict[str, Period], issues: list[IngestionIssue]
) -> None:
    """Exceptional items should satisfy PBT = profit before exceptional items + exceptional items."""
    for context_id, period in periods.items():
        context = contexts[context_id]
        before = _decimal(_first(context.facts.get("ProfitBeforeExceptionalItemsAndTax")))
        exceptional = _decimal(_first(context.facts.get("ExceptionalItemsBeforeTax")))
        pbt = _decimal(_first(context.facts.get("ProfitBeforeTax")))
        if None in (before, exceptional, pbt) or not exceptional:
            continue
        if before - exceptional == pbt and before + exceptional != pbt:  # type: ignore[operator]
            for index, fact in enumerate(facts):
                if fact.key == "exceptional_items" and fact.period == period.label and fact.value is not None:
                    facts[index] = fact.model_copy(update={"value": -fact.value})
            issues.append(
                IngestionIssue(
                    level="warning",
                    ref=fact_ref("exceptional_items", period.label),
                    message="exceptional items tagged with the opposite sign; reversed to reconcile with PBT",
                )
            )


def _first(values: list[str] | None) -> str | None:
    return values[0] if values else None


def _decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(raw.strip().replace(",", ""))
    except InvalidOperation:
        return None


def _parse_date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw.strip()) if raw else None
    except ValueError:
        return None
