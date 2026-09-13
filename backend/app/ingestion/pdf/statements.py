"""Locate and extract the primary financial statements from a PDF filing."""

from __future__ import annotations

import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cached_property

import pdfplumber
import pypdfium2

from app.domain.enums import Statement, Unit
from app.domain.financials import ExtractionMethod, Fact, SourceRef
from app.domain.periods import INDIAN_FISCAL_YEAR_END, Period, PeriodKind
from app.engine.catalog import Catalog
from app.ingestion.amounts import detect_scale
from app.ingestion.labels import LabelMatcher
from app.ingestion.models import IngestionIssue
from app.ingestion.pdf.layout import (
    Column,
    Line,
    Row,
    Table,
    Word,
    build_table,
    find_columns,
    lines_from_words,
)
from app.ingestion.reconcile import reconcile_profit_and_loss

# A heading names the statement, optionally after "Annexure II -", "Restated", "Consolidated" and similar, and
# is followed by nothing but "as at ...", "for the year ended ..." or "(continued)".
_QUALIFIERS = (
    r"^(?:annexure\s+[ivx\d]+\s*[-–:]\s*)?(?:(?:restated|audited|unaudited|consolidated|standalone)\s+)*"
)
_TAIL = r"\s*(?:\(continued\)|\(contd\.?\)|as\s+(?:at|on)\s[^,]{0,40}|for\s+the\s[^,]{0,50})?\s*$"
_TITLES = {
    Statement.BALANCE_SHEET: re.compile(
        _QUALIFIERS + r"(?:balance\s+sheet|statement\s+of\s+assets\s+and\s+liabilities)" + _TAIL, re.I
    ),
    Statement.PROFIT_AND_LOSS: re.compile(
        _QUALIFIERS
        + r"(?:statement\s+of\s+profit\s+(?:and|or)\s+loss|profit\s+and\s+loss\s+account)"
        + r"(?:\s*\(including\s+other\s+comprehensive\s+income\))?"
        + _TAIL,
        re.I,
    ),
    Statement.CASH_FLOW: re.compile(
        _QUALIFIERS + r"(?:cash\s+flow\s+statement|statement\s+of\s+cash\s+flows?)" + _TAIL, re.I
    ),
}
_SECTION = re.compile(
    r"\b(non[\s-]*current|current)\s+(assets|liabilities)\b|^(equity|liabilities|assets)\b|"
    r"\b(operating|investing|financing)\s+activities\b",
    re.I,
)
_ATTRIBUTION = re.compile(
    r"^(owners|equity\s+holders|shareholders)\s+of\b|^non[\s-]*controlling\s+interests?\b", re.I
)
_OWNERS = re.compile(r"^(owners|equity\s+holders|shareholders)\s+of\b", re.I)
_FACE_VALUE = re.compile(
    r"(?:face\s+value|nominal\s+value|equity\s+shares?)\s+of\s+(?:shares\s+)?(?:₹|rs\.?|re\.?|inr)\s*(\d+(?:\.\d+)?)",
    re.I,
)
_EACH = re.compile(r"(?:₹|rs\.?|re\.?|inr)\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*each\b", re.I)
MIN_MAPPED_ROWS = 5  # fewer mapped rows means a mention in prose or a summary, not the statement itself
_KIND_MONTHS = {
    PeriodKind.QUARTER: 3,
    PeriodKind.HALF_YEAR: 6,
    PeriodKind.NINE_MONTHS: 9,
    PeriodKind.YEAR: 12,
}
_HINT_KINDS = {
    "quarter": PeriodKind.QUARTER,
    "half_year": PeriodKind.HALF_YEAR,
    "nine_months": PeriodKind.NINE_MONTHS,
    "year": PeriodKind.YEAR,
}


class PdfSource:
    """A PDF opened once: fast full-text access through pdfium, word geometry through pdfplumber on demand."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._pdfium = pypdfium2.PdfDocument(content)
        self._plumber: pdfplumber.PDF | None = None
        self._lines: dict[int, list[Line]] = {}

    def __len__(self) -> int:
        return len(self._pdfium)

    @cached_property
    def texts(self) -> list[str]:
        return [self._pdfium[i].get_textpage().get_text_range() for i in range(len(self._pdfium))]

    def lines(self, index: int) -> list[Line]:
        if index not in self._lines:
            if self._plumber is None:
                self._plumber = pdfplumber.open(io.BytesIO(self._content))
            words = [
                Word(w["text"], w["x0"], w["x1"], w["top"])
                for w in self._plumber.pages[index].extract_words(x_tolerance=1.5, y_tolerance=2)
            ]
            self._lines[index] = lines_from_words(words)
        return self._lines[index]

    def close(self) -> None:
        if self._plumber is not None:
            self._plumber.close()
        self._pdfium.close()


@dataclass(frozen=True, slots=True)
class StatementPages:
    statement: Statement
    pages: tuple[int, ...]
    title: str

    @property
    def consolidated(self) -> bool:
        return "consolidated" in self.title.lower()

    @property
    def restated(self) -> bool:
        return "restated" in self.title.lower()


@dataclass
class ExtractedStatements:
    facts: list[Fact]
    issues: list[IngestionIssue] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    fiscal_year_end_month: int = INDIAN_FISCAL_YEAR_END
    consolidated: bool = False
    restated: bool = False
    pages: dict[Statement, tuple[int, ...]] = field(default_factory=dict)


def locate_statements(source: PdfSource) -> dict[Statement, list[StatementPages]]:
    """Runs of consecutive pages carrying a heading that names a primary statement."""
    found: dict[Statement, list[StatementPages]] = defaultdict(list)
    for index, text in enumerate(source.texts):
        lines = [" ".join(line.split()) for line in text.splitlines()]
        for statement, pattern in _TITLES.items():
            title = next((line for line in lines if len(line) <= 140 and pattern.match(line)), None)
            if title is None:
                continue
            runs = found[statement]
            if runs and runs[-1].pages[-1] == index - 1:
                runs[-1] = StatementPages(statement, (*runs[-1].pages, index), runs[-1].title)
            else:
                runs.append(StatementPages(statement, (index,), title))
            break
    return found


def choose(
    source: PdfSource, runs: list[StatementPages], matcher: LabelMatcher, prefer_consolidated: bool = True
) -> StatementPages | None:
    """The run that reads as the fullest statement: most rows mapped, then consolidated, then restated."""
    best: tuple[tuple[int, bool, bool], StatementPages] | None = None
    for run in runs:
        table = build_table(source.lines(run.pages[0]))
        mapped = sum(1 for row in table.rows if row.has_values and matcher.match(row.label, run.statement))
        if mapped < MIN_MAPPED_ROWS:
            continue
        score = (mapped, run.consolidated == prefer_consolidated, run.restated)
        if best is None or score > best[0]:
            best = (score, run)
    return best[1] if best else None


@dataclass(frozen=True, slots=True)
class StatementTable:
    """One page of a statement, read into a table. ``page`` is zero-based."""

    statement: Statement
    page: int
    title: str
    table: Table
    scale: Decimal  # crore per unit of the amounts on the page
    text: str  # the page's text, for captions such as the face value of shares


def extract_statements(
    source: PdfSource, catalog: Catalog, prefer_consolidated: bool = True
) -> ExtractedStatements:
    matcher = LabelMatcher(catalog)
    located = locate_statements(source)
    chosen = {
        s: run for s in Statement if (run := choose(source, located.get(s, []), matcher, prefer_consolidated))
    }
    issues: list[IngestionIssue] = []
    tables = [table for run in chosen.values() for table in _read_tables(source, run, issues)]
    result = assemble_statements(tables, catalog)
    result.issues[:0] = issues
    return result


def assemble_statements(tables: list[StatementTable], catalog: Catalog) -> ExtractedStatements:
    """Turn statement tables into facts: map rows, derive periods, and reconcile presentation differences."""
    result = ExtractedStatements(facts=[])
    if not tables:
        result.issues.append(IngestionIssue(level="error", message="no primary financial statements found"))
        return result
    matcher = LabelMatcher(catalog)
    by_statement: dict[Statement, list[StatementTable]] = defaultdict(list)
    for table in tables:
        by_statement[table.statement].append(table)
    result.pages = {s: tuple(t.page for t in pages) for s, pages in by_statement.items()}
    result.fiscal_year_end_month = _fiscal_year_end(tables)
    result.consolidated = any("consolidated" in t.title.lower() for t in tables)
    result.restated = any("restated" in t.title.lower() for t in tables)

    amounts: dict[tuple[str, str], Decimal] = {}
    sources: dict[tuple[str, str], SourceRef] = {}
    known_periods: dict[date, Period] = {}
    for statement in (Statement.PROFIT_AND_LOSS, Statement.CASH_FLOW, Statement.BALANCE_SHEET):
        if statement not in by_statement:
            result.issues.append(IngestionIssue(level="warning", message=f"{statement} not found"))
            continue
        for page in by_statement[statement]:
            periods = _column_periods(
                page.table.columns, statement, result.fiscal_year_end_month, known_periods
            )
            if statement is not Statement.BALANCE_SHEET:
                for period in periods:
                    if period is not None and period.days >= known_periods.get(period.end_date, period).days:
                        known_periods[period.end_date] = period
            context = _Context(page.page, periods, page.scale, statement, page.title)
            _map_rows(page.table, context, matcher, catalog, amounts, sources, result)
            if statement is Statement.PROFIT_AND_LOSS:
                _face_value(page.text, context, amounts, sources)

    reconcile_profit_and_loss(amounts, result.issues)
    result.facts = [
        Fact(key=key, period=period, value=value, source=sources[(key, period)])
        for (key, period), value in amounts.items()
    ]
    return result


def _read_tables(
    source: PdfSource, run: StatementPages, issues: list[IngestionIssue]
) -> list[StatementTable]:
    tables: list[StatementTable] = []
    columns: list[Column] | None = None
    scale: Decimal | None = None
    for page in run.pages:
        lines = source.lines(page)
        own_columns, _ = find_columns(lines)
        if own_columns:
            table = build_table(lines)
            columns = own_columns
        elif columns:
            table = build_table(lines, columns)  # continuation page without its own header
        else:
            issues.append(
                IngestionIssue(level="warning", message=f"page {page + 1}: no period columns found")
            )
            continue
        # pdfium's text order does not always follow the layout, so read the unit from layout-ordered lines
        # too.
        top = "\n".join([line.text for line in lines[:15]] + source.texts[page].splitlines()[:20])
        scale = detect_scale(top) or scale
        if scale is None:
            issues.append(IngestionIssue(level="error", message=f"page {page + 1}: currency unit not stated"))
            continue
        tables.append(StatementTable(run.statement, page, run.title, table, scale, source.texts[page]))
    return tables


def _fiscal_year_end(tables: list[StatementTable]) -> int:
    """The month in which annual periods end, taken from the income or cash flow statement columns."""
    months = [
        column.date.month
        for table in tables
        if table.statement is not Statement.BALANCE_SHEET
        for column in table.table.columns
        if column.hint == "year"
    ]
    return max(set(months), key=months.count) if months else INDIAN_FISCAL_YEAR_END


def _column_periods(
    columns: list[Column], statement: Statement, year_end_month: int, known: dict[date, Period]
) -> list[Period | None]:
    periods: list[Period | None] = []
    for column in columns:
        if column.start is not None and statement is not Statement.BALANCE_SHEET:
            try:
                periods.append(Period.from_dates(column.start, column.date, year_end_month))
                continue
            except ValueError:
                pass
        kind = _HINT_KINDS.get(column.hint)
        if statement is Statement.BALANCE_SHEET or kind is None:
            if column.date in known:
                periods.append(known[column.date])
                continue
            kind = PeriodKind.YEAR if column.date.month == year_end_month else None
        periods.append(_period_ending(column.date, kind, year_end_month) if kind else None)
    return periods


def _period_ending(end: date, kind: PeriodKind, year_end_month: int) -> Period | None:
    months = _KIND_MONTHS[kind]
    index = end.year * 12 + end.month - months  # month before the period starts
    start = date(index // 12, index % 12 + 1, 1)
    try:
        return Period.from_dates(start, end, year_end_month)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class _Context:
    page: int
    periods: list[Period | None]
    scale: Decimal
    statement: Statement
    title: str


@dataclass
class _Contribution:
    value: Decimal
    label: str
    from_parent: bool


def _map_rows(
    table: Table,
    context: _Context,
    matcher: LabelMatcher,
    catalog: Catalog,
    amounts: dict[tuple[str, str], Decimal],
    sources: dict[tuple[str, str], SourceRef],
    result: ExtractedStatements,
) -> None:
    statement = context.statement
    section = ""
    header = ""
    parent: tuple[str, float] | None = None
    contributions: dict[tuple[str, str], list[_Contribution]] = defaultdict(list)

    for row in table.rows:
        if not row.label:
            continue
        if not row.has_values:
            header = row.label
            if _SECTION.search(row.label):
                section = row.label
            match = matcher.match(row.label, statement, section)
            parent = (match.key, row.x0) if match and match.score == 1.0 else None
            continue
        if parent and row.x0 <= parent[1] + 1:
            parent = None  # back at the header's indentation: its sub-rows have ended
        if _ATTRIBUTION.match(row.label):
            # "Owners of the Company" repeats under the profit, OCI and total comprehensive income headers.
            lowered = header.lower()
            if (
                statement is Statement.PROFIT_AND_LOSS
                and "profit" in lowered
                and "comprehensive" not in lowered
                and _OWNERS.match(row.label)
            ):
                _contribute(contributions, catalog, context, "profit_attributable_to_owners", row, False)
            continue
        if parent:
            _contribute(contributions, catalog, context, parent[0], row, True)
            continue
        match = matcher.match(row.label, statement, section)
        if match is None:
            result.unmapped.append(f"{statement} p.{context.page + 1}: {row.label}")
            continue
        _contribute(contributions, catalog, context, match.key, row, False)

    for identity, parts in contributions.items():
        key, period = identity
        if identity in amounts:
            continue  # an earlier page or statement already supplied this value
        direct = [part for part in parts if not part.from_parent]
        if not direct or catalog.items[key].sum_rows:
            used = direct or parts  # sub-rows of a matched header add up to it
            amounts[identity] = sum((part.value for part in used), Decimal(0))
        else:
            used = direct[:1]  # a printed line beats a sum of its sub-rows
            amounts[identity] = direct[0].value
            ignored = [part.label for part in direct[1:] if part.value != direct[0].value]
            if ignored:
                result.issues.append(
                    IngestionIssue(
                        level="warning",
                        ref=f"f:{key}@{period}",
                        message=f"several rows map to this item; kept '{direct[0].label}', "
                        f"ignored '{'; '.join(ignored)}'",
                    )
                )
        sources[identity] = SourceRef(
            page=context.page + 1,
            table=context.title,
            raw_label=" + ".join(part.label for part in used),
            method=ExtractionMethod.PDF_TABLE,
        )


def _contribute(
    contributions: dict[tuple[str, str], list[_Contribution]],
    catalog: Catalog,
    context: _Context,
    key: str,
    row: Row,
    from_parent: bool,
) -> None:
    unit = catalog.items[key].unit
    if unit not in (Unit.INR_CRORE, Unit.INR):
        return
    for period, value in zip(context.periods, row.values, strict=True):
        if period is None or value is None:
            continue
        amount = value * context.scale if unit is Unit.INR_CRORE else value
        contributions[(key, period.label)].append(_Contribution(amount, row.label, from_parent))


def _face_value(
    text: str,
    context: _Context,
    amounts: dict[tuple[str, str], Decimal],
    sources: dict[tuple[str, str], SourceRef],
) -> None:
    """Face value per share, stated in the EPS caption ("face value of ₹10 each"), for EPS reconciliation."""
    flat = " ".join(text.split())
    matches = list(_FACE_VALUE.finditer(flat))
    stated = {Decimal(m.group(1)) for m in matches} | {Decimal(m.group(1)) for m in _EACH.finditer(flat)}
    if not matches or len(stated) > 1:
        return  # absent, or different face values across periods (after a split) that cannot be told apart
    match = matches[0]
    for period in context.periods:
        identity = ("face_value_per_share", period.label) if period else None
        if identity and identity not in amounts:
            amounts[identity] = Decimal(match.group(1))
            sources[identity] = SourceRef(
                page=context.page + 1,
                table=context.title,
                raw_label=match.group(0),
                method=ExtractionMethod.PDF_TEXT,
            )
