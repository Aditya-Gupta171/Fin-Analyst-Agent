"""Request/response models for the HTTP API — distinct from the ORM models in ``app/db/models.py`` and
from the internal ``AnalysisReport`` (which ``AnalysisDetail`` simply embeds once an analysis succeeds)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.analysis.report import AnalysisReport
from app.domain.enums import Basis, DocType, Sector

AnalysisStatus = Literal["queued", "running", "succeeded", "failed"]
FindingStatus = Literal["open", "confirmed", "dismissed"]
RuleStatus = Literal["candidate", "approved", "rejected"]


class DocumentSummary(BaseModel):
    id: str
    company_name: str
    sector: Sector
    doc_type: DocType
    basis: Basis
    filing_date: date | None
    source_url: str | None
    original_filename: str
    periods: list[str]
    created_at: datetime


class DocumentDetail(DocumentSummary):
    fact_count: int
    dataset: dict  # the raw FinancialDataset, for clients that want full source traceability


class IngestionWarning(BaseModel):
    level: Literal["error", "warning", "info"]
    message: str
    ref: str | None = None


class DocumentUploadResponse(BaseModel):
    document: DocumentSummary
    warnings: list[IngestionWarning]
    unmapped: list[str]


class AnalysisSummary(BaseModel):
    id: str
    document_id: str
    status: AnalysisStatus
    stage: str | None
    stage_message: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AnalysisDetail(AnalysisSummary):
    report: AnalysisReport | None = None


class FindingOut(BaseModel):
    id: str
    finding_id: str
    title: str
    category: str
    severity: str
    origin: str
    rule_ids: list[str]
    status: FindingStatus


class FeedbackIn(BaseModel):
    verdict: Literal["confirm", "dismiss"]
    reason: str | None = None
    created_by: str | None = None


class RuleCandidateOut(BaseModel):
    id: str
    rule_id: str
    definition: dict
    status: RuleStatus
    source_analysis_id: str
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    backtest_result: dict | None = None


class RuleDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    decided_by: str | None = None
