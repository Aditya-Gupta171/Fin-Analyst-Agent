"""Upload a filing (deterministic ingestion, no LLM) and look up what's been uploaded."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app.api.deps import AppStateDep, RequireApiKey, SessionDep
from app.api.schemas import DocumentDetail, DocumentSummary, DocumentUploadResponse, IngestionWarning
from app.db.models import Document
from app.domain.enums import Sector
from app.domain.financials import FinancialDataset
from app.ingestion.router import UnsupportedDocument, ingest

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[RequireApiKey])


@router.post("", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    state: AppStateDep,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    sector: Annotated[Sector, Form()] = Sector.OTHER,
    source_url: Annotated[str | None, Form()] = None,
) -> DocumentUploadResponse:
    content = await file.read()
    try:
        result = ingest(
            content, state.catalog, filename=file.filename or "", sector=sector, source_url=source_url
        )
    except UnsupportedDocument as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if result.errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(issue.message for issue in result.errors)
        )
    dataset = result.dataset
    row = Document(
        company_name=dataset.company.name,
        sector=dataset.company.sector.value,
        doc_type=dataset.document.doc_type.value,
        basis=dataset.document.basis.value,
        filing_date=dataset.document.filing_date.isoformat() if dataset.document.filing_date else None,
        source_url=dataset.document.source_url,
        fiscal_year_end_month=dataset.document.fiscal_year_end_month,
        restated=dataset.document.restated,
        original_filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        file_bytes=content,
        dataset_json=dataset.model_dump(mode="json"),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DocumentUploadResponse(
        document=_summary(row, dataset),
        warnings=[IngestionWarning(level=i.level, message=i.message, ref=i.ref) for i in result.issues],
        unmapped=result.unmapped,
    )


@router.get("", response_model=list[DocumentSummary])
async def list_documents(session: SessionDep) -> list[DocumentSummary]:
    rows = (await session.scalars(select(Document).order_by(Document.created_at.desc()))).all()
    return [_summary(row, FinancialDataset.model_validate(row.dataset_json)) for row in rows]


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, session: SessionDep) -> DocumentDetail:
    row = await session.get(Document, document_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    dataset = FinancialDataset.model_validate(row.dataset_json)
    summary = _summary(row, dataset)
    return DocumentDetail(**summary.model_dump(), fact_count=len(dataset.facts), dataset=row.dataset_json)


@router.get("/{document_id}/file")
async def get_document_file(document_id: str, session: SessionDep) -> Response:
    """The raw uploaded bytes — the evidence viewer renders a PDF-sourced fact's source page from this."""
    row = await session.get(Document, document_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return Response(content=row.file_bytes, media_type=row.content_type)


def _summary(row: Document, dataset: FinancialDataset) -> DocumentSummary:
    return DocumentSummary(
        id=row.id,
        company_name=row.company_name,
        sector=row.sector,
        doc_type=row.doc_type,
        basis=row.basis,
        filing_date=date.fromisoformat(row.filing_date) if row.filing_date else None,
        source_url=row.source_url,
        original_filename=row.original_filename,
        periods=[p.label for p in dataset.periods],
        created_at=row.created_at,
    )
