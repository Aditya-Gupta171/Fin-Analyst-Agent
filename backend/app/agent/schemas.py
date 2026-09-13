"""Structured outputs the agent's model calls must return (sent to the provider as strict JSON schemas)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SeverityName = Literal["low", "medium", "high", "critical"]


class Enquiry(BaseModel):
    id: str = Field(description="Short id such as E1")
    title: str = Field(description="The line of enquiry, specific to this company")
    why_it_matters: str = Field(
        description="One sentence on why an investor or lender should care; no figures"
    )
    rule_ids: list[str] = Field(description="Fired rule ids this enquiry examines")
    evidence_refs: list[str] = Field(
        description="References from the fact sheet most relevant to the enquiry"
    )
    question: str = Field(description="The question the investigation must answer")


class ImmaterialRule(BaseModel):
    rule_id: str
    reason: str = Field(description="Why the flag does not deserve a finding for this company; no figures")


class Plan(BaseModel):
    company_context: str = Field(description="One or two sentences on the business and document; no figures")
    enquiries: list[Enquiry] = Field(max_length=5, description="Most material first")
    immaterial_rules: list[ImmaterialRule] = Field(description="Fired rules judged not worth a finding")


class ContextRequest(BaseModel):
    tool: Literal["metric_history", "knowledge_search", "rule_detail"]
    argument: str = Field(description="Metric or line item key, a search question, or a rule id")


class FindingDraft(BaseModel):
    title: str = Field(description="Specific headline of the finding, no figures")
    category: str
    severity: SeverityName
    confidence: float = Field(ge=0, le=1, description="How well the evidence supports the finding")
    summary: str = Field(description="One sentence; every figure cited as {{ref}}")
    analysis: str = Field(
        description="Three to six sentences explaining the mechanism; figures cited as {{ref}}"
    )
    evidence_refs: list[str]
    knowledge_refs: list[str] = Field(description="Knowledge chunk ids (kb:...) that informed the judgement")
    benign_explanations_considered: list[str] = Field(
        description="Innocent explanations weighed, each saying whether the filing's data supports or"
        " undermines it"
    )
    questions_for_management: list[str] = Field(max_length=4)
    related_rule_ids: list[str]


class AnalystTurn(BaseModel):
    action: Literal["request_context", "submit_finding", "no_finding"]
    reason: str = Field(description="Brief reasoning for the action; no figures")
    requests: list[ContextRequest] = Field(max_length=3, description="Only for request_context")
    finding: FindingDraft | None = Field(description="Only for submit_finding")


class Verdict(BaseModel):
    finding_id: str
    decision: Literal["accept", "revise", "reject"]
    severity: SeverityName = Field(description="Severity after review")
    reasons: str = Field(description="What holds up and what does not; no figures")
    revision_instructions: str | None = Field(description="Specific changes required when decision is revise")


class Review(BaseModel):
    verdicts: list[Verdict]


class Summary(BaseModel):
    headline: str = Field(description="At most fifteen words, specific to this company; no figures")
    overall_risk: Literal["low", "moderate", "elevated", "high"]
    executive_summary: str = Field(description="Three to five sentences; figures cited as {{ref}}")
    strengths: list[str] = Field(max_length=4, description="One sentence each; figures cited as {{ref}}")
    concerns: list[str] = Field(max_length=4, description="One sentence each; figures cited as {{ref}}")


class RuleProposal(BaseModel):
    id: str = Field(description="UPPER_SNAKE_CASE id with a category prefix")
    title: str
    category: str
    severity: SeverityName
    when: str = Field(description="Condition in the rule expression language")
    rationale: str = Field(description="Why the pattern matters in general, not only for this company")
    evidence: list[str] = Field(description="Metric or line item names to show as evidence")


class Proposals(BaseModel):
    rules: list[RuleProposal] = Field(max_length=2)
