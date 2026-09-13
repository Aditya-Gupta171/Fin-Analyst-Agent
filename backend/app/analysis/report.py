"""AnalysisReport v1: the versioned output contract consumed by the web app and by host platforms."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import Severity, SizeBucket, Unit
from app.domain.financials import CompanyInfo, DocumentInfo
from app.engine.anomalies import Anomaly
from app.llm.types import CallRecord

SCHEMA_VERSION = "1.0"

RiskLevel = Literal["low", "moderate", "elevated", "high"]
AreaStatus = Literal["concern", "watch", "clear", "not_assessed"]


class EvidenceItem(BaseModel):
    ref: str
    label: str
    display: str
    period: str | None = None
    unit: Unit | None = None
    page: int | None = None  # page of the source filing, for facts extracted from PDFs
    source: str | None = None  # printed label or XBRL element the value came from


class KnowledgeCitation(BaseModel):
    chunk_id: str
    document: str
    section: str


class Critique(BaseModel):
    decision: Literal["accept", "revise", "reject"]
    reasons: str


class Finding(BaseModel):
    id: str
    origin: Literal["agent", "rule"]  # written by the agent, or taken directly from a fired rule
    title: str
    category: str
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    summary: str  # rendered: references replaced by verified figures
    analysis: str
    summary_template: str  # as written, with {{ref}} citations intact
    analysis_template: str
    evidence: list[EvidenceItem]
    rule_ids: list[str]
    periods: list[str] = []  # periods in which the underlying rules fired
    knowledge: list[KnowledgeCitation] = []
    benign_explanations_considered: list[str] = []
    questions_for_management: list[str] = []
    critique: Critique | None = None


class AreaScore(BaseModel):
    area: str
    status: AreaStatus
    fired: int
    passed: int
    insufficient: int
    worst_severity: Severity | None = None


class MetricRow(BaseModel):
    key: str
    name: str
    unit: Unit
    values: dict[str, str]  # period -> display


class DismissedRule(BaseModel):
    """A fired rule the agent judged immaterial for this filing, with its reason."""

    rule_id: str
    title: str
    reason: str


class IntegrityCheck(BaseModel):
    rule_id: str
    title: str
    outcome: Literal["fired", "passed", "insufficient_data"]
    period: str
    detail: str | None = None


class CandidateRule(BaseModel):
    id: str
    title: str
    category: str
    severity: Severity
    when: str
    rationale: str
    evidence: list[str]
    status: Literal["candidate"] = "candidate"
    validation_errors: list[str] = []


class TokenUsage(BaseModel):
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_calls: int = 0


class Provenance(BaseModel):
    mode: Literal["agent", "rules_only"]
    catalog_version: str
    kb_version: str | None
    prompt_version: str | None
    models: list[str]
    token_usage: TokenUsage
    notes: list[str] = []


class AnalysisReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    report_id: str
    generated_at: datetime
    company: CompanyInfo
    document: DocumentInfo
    anchor_period: str
    periods: list[str]
    size_bucket: SizeBucket | None
    overall_risk: RiskLevel
    headline: str
    executive_summary: str
    strengths: list[str] = []
    concerns: list[str] = []
    findings: list[Finding]
    dismissed_rules: list[DismissedRule] = []
    scorecard: list[AreaScore]
    integrity: list[IntegrityCheck]
    anomalies: list[Anomaly]
    data_gaps: list[str]
    metrics: list[MetricRow]
    candidate_rules: list[CandidateRule] = []
    provenance: Provenance
    trace: list[CallRecord] = []


def worst(severities: list[Severity]) -> Severity | None:
    return max(severities, key=lambda s: s.rank) if severities else None
