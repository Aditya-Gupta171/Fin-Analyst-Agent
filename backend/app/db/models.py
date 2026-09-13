"""SQLAlchemy models for the persisted tables: documents, analyses, findings, feedback, cohort_baselines
and rule_versions.

Columns use the generic ``JSON`` type rather than Postgres-only ``JSONB``/``ARRAY`` so the same schema works
unchanged against SQLite in tests and Postgres (Supabase) at runtime.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, LargeBinary
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


_TIMESTAMP = DateTime(timezone=True)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    company_name: Mapped[str]
    sector: Mapped[str]
    doc_type: Mapped[str]
    basis: Mapped[str]
    filing_date: Mapped[str | None] = mapped_column(default=None)
    source_url: Mapped[str | None] = mapped_column(default=None)
    fiscal_year_end_month: Mapped[int]
    restated: Mapped[bool] = mapped_column(default=False)
    original_filename: Mapped[str]
    content_type: Mapped[str]
    file_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    dataset_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMP, default=utcnow)


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    status: Mapped[str] = mapped_column(default="queued")  # queued | running | succeeded | failed
    stage: Mapped[str | None] = mapped_column(default=None)
    stage_message: Mapped[str | None] = mapped_column(default=None)
    catalog_version: Mapped[str | None] = mapped_column(default=None)
    kb_version: Mapped[str | None] = mapped_column(default=None)
    prompt_version: Mapped[str | None] = mapped_column(default=None)
    report_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMP, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(_TIMESTAMP, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(_TIMESTAMP, default=None)


class FindingRow(Base):
    """A normalized snapshot of one finding from a finished report, for listing/filtering without
    parsing ``Analysis.report_json``."""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"))
    finding_id: Mapped[str]  # the AnalysisReport-local id, e.g. "A1"
    title: Mapped[str]
    category: Mapped[str]
    severity: Mapped[str]
    origin: Mapped[str]
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(default="open")  # open | confirmed | dismissed


class Feedback(Base):
    """Audit trail of analyst verdicts on a finding; a finding's ``status`` reflects the latest one."""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"))
    finding_id: Mapped[str]
    verdict: Mapped[str]  # confirm | dismiss
    reason: Mapped[str | None] = mapped_column(default=None)
    created_by: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMP, default=utcnow)


class CohortBaseline(Base):
    """One observed metric value, contributing to the running distribution for its cohort."""

    __tablename__ = "cohort_baselines"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    metric: Mapped[str]
    sector: Mapped[str]
    size_bucket: Mapped[str]
    period_kind: Mapped[str]
    value: Mapped[str]  # Decimal serialized as a string to preserve precision
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMP, default=utcnow)


class RuleVersion(Base):
    """A candidate rule proposed by the agent's rule-proposer node, pending human approval."""

    __tablename__ = "rule_versions"

    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    rule_id: Mapped[str]
    definition_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="candidate")  # candidate | approved | rejected
    source_analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"))
    decided_by: Mapped[str | None] = mapped_column(default=None)
    decided_at: Mapped[datetime | None] = mapped_column(_TIMESTAMP, default=None)
    created_at: Mapped[datetime] = mapped_column(_TIMESTAMP, default=utcnow)
