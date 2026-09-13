"""Canonical representation of the financial data extracted from a filing.

Every ingestion path (XBRL, PDF tables, spreadsheets) produces a :class:`FinancialDataset`. The engine, the
agent and the API only ever see this shape, so a new source format never touches analysis code.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from functools import cached_property

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import Basis, DocType, Sector
from app.domain.periods import INDIAN_FISCAL_YEAR_END, Period


class ExtractionMethod(StrEnum):
    XBRL = "xbrl"
    PDF_TABLE = "pdf_table"
    PDF_TEXT = "pdf_text"
    SPREADSHEET = "spreadsheet"
    MANUAL = "manual"


class SourceRef(BaseModel):
    """Where a value came from, so every number in an analysis can be traced back to the filing."""

    model_config = ConfigDict(frozen=True)

    page: int | None = None
    section: str | None = None
    table: str | None = None
    raw_label: str | None = None
    method: ExtractionMethod = ExtractionMethod.MANUAL


class Fact(BaseModel):
    """A single reported value: one line item for one period (or for the whole document).

    Amounts are in ₹ crore, per-share values in ₹, ratios as fractions. Exactly one of ``value`` (numeric)
    or ``text`` (categorical, e.g. an auditor's opinion) is set.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    period: str | None = None  # None for document-scoped facts
    value: Decimal | None = None
    text: str | None = None
    source: SourceRef = Field(default_factory=SourceRef)

    @field_validator("period")
    @classmethod
    def _normalise_period(cls, label: str | None) -> str | None:
        return None if label is None else Period.parse(label).label

    @model_validator(mode="after")
    def _exactly_one_value(self) -> Fact:
        if (self.value is None) == (self.text is None):
            raise ValueError(f"fact {self.key!r} must set exactly one of 'value' or 'text'")
        return self

    @property
    def ref(self) -> str:
        return fact_ref(self.key, self.period)

    @property
    def payload(self) -> Decimal | str:
        return self.value if self.value is not None else self.text  # type: ignore[return-value]


def fact_ref(key: str, period: str | None) -> str:
    return f"f:{key}@{period}" if period else f"f:{key}"


class CompanyInfo(BaseModel):
    name: str
    sector: Sector
    industry: str | None = None
    nse_symbol: str | None = None
    bse_code: str | None = None
    isin: str | None = None


class DocumentInfo(BaseModel):
    doc_type: DocType
    basis: Basis
    title: str
    filing_date: date | None = None
    source_url: str | None = None
    restated: bool = False  # offer documents present restated financial information
    fiscal_year_end_month: int = Field(INDIAN_FISCAL_YEAR_END, ge=1, le=12)


class FinancialDataset(BaseModel):
    company: CompanyInfo
    document: DocumentInfo
    facts: list[Fact]

    @model_validator(mode="after")
    def _unique_facts(self) -> FinancialDataset:
        seen: set[tuple[str, str | None]] = set()
        for fact in self.facts:
            identity = (fact.key, fact.period)
            if identity in seen:
                raise ValueError(f"duplicate fact {fact.ref}")
            seen.add(identity)
        return self

    @cached_property
    def _index(self) -> dict[tuple[str, str | None], Fact]:
        return {(fact.key, fact.period): fact for fact in self.facts}

    @cached_property
    def periods(self) -> list[Period]:
        """All reporting periods present, oldest first."""
        labels = {fact.period for fact in self.facts if fact.period}
        return sorted((self.period(label) for label in labels), key=lambda p: p.sort_key)

    def period(self, label: str) -> Period:
        """Interpret a period label in this document's fiscal calendar."""
        return Period.parse(label, self.document.fiscal_year_end_month)

    def get(self, key: str, period: Period | str | None = None) -> Fact | None:
        label = period.label if isinstance(period, Period) else period
        return self._index.get((key, label))

    def find_at_date(self, key: str, as_of: date) -> Fact | None:
        """A balance reported at ``as_of`` under any period label (FY24 and H2FY24 both end 31 Mar 2024)."""
        matches = [p for p in self.periods if p.end_date == as_of and (key, p.label) in self._index]
        if not matches:
            return None
        longest = max(matches, key=lambda p: p.days)  # prefer the audited annual figure
        return self._index[(key, longest.label)]
