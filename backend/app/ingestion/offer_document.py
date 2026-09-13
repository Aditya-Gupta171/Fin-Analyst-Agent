"""DRHP / RHP ingestion: cover-page identity plus the restated financial statements.

Offer-structure facts (issue size, offer for sale, objects of the offer) are left to the agent's document
tools: in an RHP most of them are blank until the price band is fixed, and the rest are prose rather than
tables.
"""

from __future__ import annotations

import re

from app.domain.enums import Basis, DocType, Sector
from app.domain.financials import CompanyInfo, DocumentInfo, FinancialDataset
from app.engine.catalog import Catalog
from app.ingestion.models import IngestionIssue, IngestionResult
from app.ingestion.pdf.statements import PdfSource, extract_statements

_CIN = re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")
_COMPANY = re.compile(r"^[A-Z0-9][A-Z0-9 &.,'()\-]*\bLIMITED\b$")


class OfferDocumentError(ValueError):
    """The PDF is not a recognisable offer document."""


def parse_offer_document(
    content: bytes,
    catalog: Catalog,
    *,
    sector: Sector = Sector.OTHER,
    source_url: str | None = None,
) -> IngestionResult:
    source = PdfSource(content)
    try:
        cover = "\n".join(source.texts[:2])
        doc_type = _document_type(cover)
        name = _company_name(cover)
        extracted = extract_statements(source, catalog)
    finally:
        source.close()

    issues = list(extracted.issues)
    if sector is Sector.OTHER:
        issues.append(
            IngestionIssue(level="info", message="sector not supplied; cohort comparisons are disabled")
        )
    cin = _CIN.search(cover)
    title = f"{'Draft Red Herring' if doc_type is DocType.DRHP else 'Red Herring'} Prospectus"
    dataset = FinancialDataset(
        company=CompanyInfo(name=name, sector=sector, industry=None),
        document=DocumentInfo(
            doc_type=doc_type,
            basis=Basis.CONSOLIDATED if extracted.consolidated else Basis.STANDALONE,
            title=f"{title} — {'restated ' if extracted.restated else ''}financial information",
            source_url=source_url,
            restated=extracted.restated,
            fiscal_year_end_month=extracted.fiscal_year_end_month,
        ),
        facts=extracted.facts,
    )
    if cin:
        issues.append(IngestionIssue(level="info", message=f"corporate identity number {cin.group(0)}"))
    pages = ", ".join(f"{s} p.{p[0] + 1}-{p[-1] + 1}" for s, p in extracted.pages.items())
    issues.append(IngestionIssue(level="info", message=f"statements read from {pages}"))
    return IngestionResult(dataset=dataset, issues=issues, unmapped=extracted.unmapped)


def _document_type(cover: str) -> DocType:
    upper = cover.upper()
    if "DRAFT RED HERRING PROSPECTUS" in upper:
        return DocType.DRHP
    if "RED HERRING PROSPECTUS" in upper or re.search(r"^\s*PROSPECTUS\s*$", upper, re.M):
        return DocType.RHP
    raise OfferDocumentError("cover page does not identify a draft red herring or red herring prospectus")


def _company_name(cover: str) -> str:
    for line in cover.splitlines():
        candidate = " ".join(line.split())
        if _COMPANY.match(candidate) and not candidate.startswith(("BSE", "NATIONAL STOCK")):
            return candidate
    raise OfferDocumentError("company name not found on the cover page")
