"""Analyse a filing end to end: engine, then agent (when a model is configured), then the AnalysisReport."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.agent.graph import AgentContext, AgentState, accepted, rule_problems, run_agent
from app.agent.prompts import prompt_version
from app.analysis.baseline import (
    data_gaps,
    integrity_checks,
    metric_rows,
    overall_risk,
    rule_finding,
    scorecard,
)
from app.analysis.evidence import EvidenceIndex
from app.analysis.report import (
    AnalysisReport,
    CandidateRule,
    Critique,
    DismissedRule,
    Finding,
    KnowledgeCitation,
    Provenance,
    TokenUsage,
)
from app.domain.enums import Severity
from app.domain.financials import FinancialDataset
from app.engine.baselines import BaselineProvider
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult, run_engine
from app.knowledge.base import KnowledgeBase
from app.llm.gateway import Gateway

ProgressCallback = Callable[[str, str], None]


def analyze(
    dataset: FinancialDataset,
    catalog: Catalog,
    *,
    kb: KnowledgeBase | None = None,
    gateway: Gateway | None = None,
    baselines: BaselineProvider | None = None,
    on_progress: ProgressCallback | None = None,
) -> AnalysisReport:
    if on_progress:
        on_progress("engine", "Computing metrics and evaluating rules")
    result = run_engine(dataset, catalog, baselines=baselines)
    return build_report(result, catalog, kb=kb, gateway=gateway, on_progress=on_progress)


def build_report(
    result: EngineResult,
    catalog: Catalog,
    *,
    kb: KnowledgeBase | None,
    gateway: Gateway | None,
    on_progress: ProgressCallback | None = None,
) -> AnalysisReport:
    index = EvidenceIndex(result, catalog)
    rule_results = {rule.rule_id: rule for rule in result.fired_rules}
    notes: list[str] = []
    state: AgentState = {}
    if gateway is not None:
        state = run_agent(AgentContext(result, catalog, index, kb, gateway, on_progress=on_progress))
        notes = state.get("notes", [])

    findings = _agent_findings(state, result, index, kb)
    covered = {rule_id for finding in findings for rule_id in finding.rule_ids}
    dismissed_reasons = {d.rule_id: d.reason for d in state.get("dismissed", []) if d.rule_id in rule_results}
    dismissed = [
        DismissedRule(rule_id=rule_id, title=rule_results[rule_id].title, reason=reason)
        for rule_id, reason in dismissed_reasons.items()
        if rule_id not in covered
    ]
    findings += [
        rule_finding(rule, index, kb)
        for rule_id, rule in rule_results.items()
        if rule_id not in covered and rule_id not in dismissed_reasons
    ]
    findings.sort(key=lambda f: (-f.severity.rank, f.origin != "agent", -f.confidence))

    summary = state.get("summary")
    risk = summary.overall_risk if summary else overall_risk(findings)
    if summary:
        headline = summary.headline
        executive_summary = index.render(summary.executive_summary)
        strengths = [index.render(text) for text in summary.strengths]
        concerns = [index.render(text) for text in summary.concerns]
    else:
        headline, executive_summary = _deterministic_summary(result, findings)
        strengths, concerns = [], [f.title for f in findings[:4]]

    if on_progress:
        on_progress("done", f"{len(findings)} findings")
    return AnalysisReport(
        report_id=uuid.uuid4().hex,
        generated_at=datetime.now(UTC),
        company=result.company,
        document=result.document,
        anchor_period=result.anchor_period,
        periods=result.periods,
        size_bucket=result.size_bucket,
        overall_risk=risk,
        headline=headline,
        executive_summary=executive_summary,
        strengths=strengths,
        concerns=concerns,
        findings=findings,
        dismissed_rules=dismissed,
        scorecard=scorecard(result),
        integrity=integrity_checks(result),
        anomalies=result.anomalies,
        data_gaps=data_gaps(result, catalog),
        metrics=metric_rows(result),
        candidate_rules=_candidates(state, catalog),
        provenance=Provenance(
            mode="agent" if gateway is not None else "rules_only",
            catalog_version=result.catalog_version,
            kb_version=kb.version if kb else None,
            prompt_version=prompt_version() if gateway is not None else None,
            models=sorted({call.model for call in gateway.calls}) if gateway else [],
            token_usage=_usage(gateway),
            notes=notes,
        ),
        trace=list(gateway.calls) if gateway else [],
    )


def _agent_findings(
    state: AgentState, result: EngineResult, index: EvidenceIndex, kb: KnowledgeBase | None
) -> list[Finding]:
    if not state:
        return []
    enquiries = {enquiry.id: enquiry for enquiry in state["plan"].enquiries} if "plan" in state else {}
    verdicts = state.get("verdicts", {})
    evaluated = {rule.rule_id: rule for rule in result.rules}
    chunks = {chunk.id: chunk for chunk in kb.chunks} if kb else {}
    findings = []
    for number, (finding_id, draft) in enumerate(accepted(state).items(), start=1):
        enquiry = enquiries.get(finding_id)
        rule_ids = list(dict.fromkeys([*draft.related_rule_ids, *(enquiry.rule_ids if enquiry else [])]))
        rule_ids = [rule_id for rule_id in rule_ids if rule_id in evaluated]
        rule_refs = [
            e.ref for rule_id in rule_ids for e in evaluated[rule_id].latest.evidence if e.value is not None
        ]
        verdict = verdicts.get(finding_id)
        periods = sorted({p for rule_id in rule_ids for p in evaluated[rule_id].fired_periods})
        findings.append(
            Finding(
                id=f"A{number}",
                origin="agent",
                title=draft.title,
                category=draft.category,
                severity=Severity(verdict.severity if verdict else draft.severity),
                confidence=draft.confidence,
                summary=index.render(draft.summary),
                analysis=index.render(draft.analysis),
                summary_template=draft.summary,
                analysis_template=draft.analysis,
                evidence=index.resolve([*draft.evidence_refs, *rule_refs]),
                rule_ids=rule_ids,
                periods=periods,
                knowledge=[
                    KnowledgeCitation(chunk_id=chunk.id, document=chunk.doc_title, section=chunk.heading)
                    for ref in draft.knowledge_refs
                    if (chunk := chunks.get(ref.removeprefix("kb:"))) is not None
                ],
                benign_explanations_considered=[
                    index.render(t) for t in draft.benign_explanations_considered
                ],
                questions_for_management=[index.render(t) for t in draft.questions_for_management],
                critique=Critique(decision=verdict.decision, reasons=verdict.reasons) if verdict else None,
            )
        )
    return findings


def _candidates(state: AgentState, catalog: Catalog) -> list[CandidateRule]:
    return [
        CandidateRule(
            id=rule.id,
            title=rule.title,
            category=rule.category,
            severity=Severity(rule.severity),
            when=rule.when,
            rationale=rule.rationale,
            evidence=rule.evidence,
            validation_errors=rule_problems(rule, catalog),
        )
        for rule in state.get("proposals", [])
    ]


def _usage(gateway: Gateway | None) -> TokenUsage:
    if gateway is None:
        return TokenUsage()
    return TokenUsage(
        calls=sum(call.attempts for call in gateway.calls),
        prompt_tokens=sum(call.prompt_tokens for call in gateway.calls),
        completion_tokens=sum(call.completion_tokens for call in gateway.calls),
        cached_calls=sum(1 for call in gateway.calls if call.cached),
    )


def _deterministic_summary(result: EngineResult, findings: list[Finding]) -> tuple[str, str]:
    if not findings:
        return (
            f"No material red flags for {result.anchor_period}",
            "None of the analyst rules fired for the analysis period. Review the data gaps before relying"
            " on this.",
        )
    top = findings[0]
    headline = f"{len(findings)} findings; most severe: {top.title.lower()}"
    return headline, " ".join(finding.summary for finding in findings[:3])
