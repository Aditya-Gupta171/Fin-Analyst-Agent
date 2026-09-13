"""Report sections computed without any model: rule findings, scorecard, integrity, metrics and data gaps.

They make every report useful on its own — a rules-only report is produced when no LLM is configured or the
provider is unavailable — and they are the ground truth the agent's findings are built on.
"""

from __future__ import annotations

from collections import defaultdict

from app.analysis.evidence import EvidenceIndex
from app.analysis.report import (
    AreaScore,
    Finding,
    IntegrityCheck,
    KnowledgeCitation,
    MetricRow,
    RiskLevel,
    worst,
)
from app.domain.enums import Severity
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult
from app.engine.rules import RuleOutcome, RuleResult
from app.knowledge.base import KnowledgeBase
from app.knowledge.documents import SectionKind

RULE_CONFIDENCE = 0.6  # a threshold was crossed; no judgement has been applied yet


def rule_finding(rule: RuleResult, index: EvidenceIndex, kb: KnowledgeBase | None) -> Finding:
    evidence = [e for e in rule.latest.evidence if e.value is not None and e.ref in index]
    cited = "; ".join(f"{e.label} {{{{{e.ref}}}}}" for e in evidence)
    summary = f"{rule.title} ({rule.latest.period})" + (f": {cited}." if cited else ".")
    persistence = (
        f" The rule also fired in {', '.join(p for p in rule.fired_periods if p != rule.latest.period)}."
        if len(rule.fired_periods) > 1
        else ""
    )
    analysis = rule.rationale + persistence
    knowledge: list[KnowledgeCitation] = []
    if kb is not None and rule.kb:
        knowledge = [
            KnowledgeCitation(chunk_id=chunk.id, document=chunk.doc_title, section=chunk.heading)
            for chunk in kb.sections(rule.kb, (SectionKind.RED_FLAGS, SectionKind.BENIGN_EXPLANATIONS))
        ]
    return Finding(
        id=f"R-{rule.rule_id}",
        origin="rule",
        title=rule.title,
        category=rule.category,
        severity=rule.severity,
        confidence=RULE_CONFIDENCE,
        summary=index.render(summary),
        analysis=index.render(analysis),
        summary_template=summary,
        analysis_template=analysis,
        evidence=index.resolve(e.ref for e in evidence),
        rule_ids=[rule.rule_id],
        periods=rule.fired_periods,
        knowledge=knowledge,
        questions_for_management=rule.questions,
    )


def rule_findings(result: EngineResult, index: EvidenceIndex, kb: KnowledgeBase | None) -> list[Finding]:
    return [rule_finding(rule, index, kb) for rule in result.fired_rules]


def scorecard(result: EngineResult) -> list[AreaScore]:
    groups: dict[str, list[RuleResult]] = defaultdict(list)
    for rule in result.rules:
        groups[rule.category].append(rule)
    scores = []
    for area, rules in sorted(groups.items()):
        fired = [r for r in rules if r.fired]
        passed = sum(r.latest.outcome is RuleOutcome.PASSED for r in rules)
        insufficient = sum(r.latest.outcome is RuleOutcome.INSUFFICIENT_DATA for r in rules)
        severity = worst([r.severity for r in fired])
        if severity is not None and severity.rank >= Severity.HIGH.rank:
            status = "concern"
        elif severity is not None:
            status = "watch"
        elif passed:
            status = "clear"
        else:
            status = "not_assessed"
        scores.append(
            AreaScore(
                area=area,
                status=status,
                fired=len(fired),
                passed=passed,
                insufficient=insufficient,
                worst_severity=severity,
            )
        )
    return scores


def integrity_checks(result: EngineResult) -> list[IntegrityCheck]:
    checks = []
    for rule in result.rules:
        if rule.pack != "integrity":
            continue
        latest = rule.latest
        detail = None
        if latest.outcome is RuleOutcome.FIRED:
            detail = "; ".join(f"{e.label} {e.display}" for e in latest.evidence if e.value is not None)
        elif latest.outcome is RuleOutcome.INSUFFICIENT_DATA:
            detail = "missing " + ", ".join(latest.missing[:4])
        checks.append(
            IntegrityCheck(
                rule_id=rule.rule_id,
                title=rule.title,
                outcome=latest.outcome.value,
                period=latest.period,
                detail=detail,
            )
        )
    return checks


def metric_rows(result: EngineResult) -> list[MetricRow]:
    rows: dict[str, MetricRow] = {}
    for metric in result.metrics:
        if not metric.headline or metric.period is None:
            continue
        row = rows.setdefault(
            metric.key, MetricRow(key=metric.key, name=metric.name, unit=metric.unit, values={})
        )
        row.values[metric.period] = metric.display
    return list(rows.values())


def data_gaps(result: EngineResult, catalog: Catalog) -> list[str]:
    gaps = []
    for rule in result.data_gaps:
        names = sorted({_describe_missing(ref, catalog) for ref in rule.latest.missing})
        gaps.append(f"{rule.title}: needs {', '.join(names[:4])}")
    return gaps


def overall_risk(findings: list[Finding]) -> RiskLevel:
    ranks = sorted((f.severity.rank for f in findings), reverse=True)
    if not ranks:
        return "low"
    if ranks[0] >= Severity.CRITICAL.rank or ranks.count(Severity.HIGH.rank) >= 3:
        return "high"
    if ranks[0] >= Severity.HIGH.rank:
        return "elevated"
    if ranks[0] >= Severity.MEDIUM.rank:
        return "moderate"
    return "low"


def _describe_missing(ref: str, catalog: Catalog) -> str:
    if not ref.startswith("f:"):
        return ref
    key, _, period = ref[2:].partition("@")
    label = catalog.items[key].label if key in catalog.items else key
    return f"{label} ({period})" if period else label
