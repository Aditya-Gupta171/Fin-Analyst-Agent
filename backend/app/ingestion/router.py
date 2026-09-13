"""Single entry point for ingestion: detect what a file is and send it to the parser that understands it."""

from __future__ import annotations

from app.domain.enums import Sector
from app.engine.catalog import Catalog
from app.ingestion.models import IngestionResult
from app.ingestion.offer_document import OfferDocumentError, parse_offer_document
from app.ingestion.xbrl import parse_results_xbrl


class UnsupportedDocument(ValueError):
    """The file is not a format or document type the ingestion pipeline handles yet."""


def ingest(
    content: bytes,
    catalog: Catalog,
    *,
    filename: str = "",
    sector: Sector = Sector.OTHER,
    source_url: str | None = None,
) -> IngestionResult:
    head = content[:1024].lstrip()
    if head.startswith(b"%PDF"):
        try:
            return parse_offer_document(content, catalog, sector=sector, source_url=source_url)
        except OfferDocumentError as exc:
            raise UnsupportedDocument(
                f"{filename or 'PDF'}: {exc}. Annual report and results PDFs are not supported yet; "
                "upload the NSE / BSE XBRL filing for quarterly results."
            ) from exc
    if head.startswith(b"<") or filename.lower().endswith(".xml"):
        return parse_results_xbrl(content, catalog, sector=sector, source_url=source_url)
    raise UnsupportedDocument(
        f"{filename or 'file'}: expected a PDF offer document or an XBRL results filing"
    )
