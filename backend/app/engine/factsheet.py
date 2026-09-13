"""The fact sheet: a compact, citable text rendering of an engine result for the agent's context window.

The agent works under tight per-call token budgets (Groq's free tier allows about 8K tokens a minute), so the
fact sheet leads with what needs judgement (fired rules, anomalies, integrity failures) and includes only the
metrics those items and the headline table depend on. Everything else stays reachable through agent tools.
"""

from __future__ import annotations

from collections import defaultdict

from app.engine.pipeline import EngineResult, MetricRecord
from app.engine.rules import RuleOutcome, RuleResult

CITATION_GUIDE = (
    "Cite values only as {{ref}} using the [ref] shown, e.g. {{m:dso@FY25}}. "
    "Never write a number yourself; the server substitutes the verified figure."
)


def build_fact_sheet(result: EngineResult) -> str:
    sections = [
        _header(result),
        _integrity(result),
        _findings(result),
        _anomalies(result),
        _metrics_table(result),
        _document_facts(result),
        _data_gaps(result),
    ]
    return "\n\n".join(section for section in sections if section)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (about four characters per token for English and figures)."""
    return len(text) // 4 + 1


def _header(result: EngineResult) -> str:
    company = result.company
    listing = ", ".join(
        part for part in (company.nse_symbol and f"NSE: {company.nse_symbol}", company.bse_code) if part
    )
    document = result.document
    return "\n".join(
        [
            f"COMPANY   {company.name}{f' ({listing})' if listing else ''} | sector {company.sector}"
            f" | size {result.size_bucket or 'unknown'}",
            f"DOCUMENT  {document.title} | {document.doc_type} | {document.basis}"
            f"{' | restated' if document.restated else ''}",
            f"PERIODS   {', '.join(result.periods)} | analysis period {result.anchor_period}",
            f"RULES     catalog {result.catalog_version}",
            CITATION_GUIDE,
        ]
    )


def _integrity(result: EngineResult) -> str:
    checks = [rule for rule in result.rules if rule.pack == "integrity"]
    failed = [rule for rule in checks if rule.fired]
    passed = sum(rule.latest.outcome is RuleOutcome.PASSED for rule in checks)
    lines = [f"INTEGRITY {passed} of {len(checks)} checks passed, {len(failed)} failed"]
    lines += [_rule_block(rule) for rule in failed]
    lines += [f"  dataset {issue.level}: {issue.ref} {issue.message}" for issue in result.dataset_issues]
    return "\n".join(lines)


def _findings(result: EngineResult) -> str:
    fired = [rule for rule in result.fired_rules if rule.pack != "integrity"]
    if not fired:
        return "RULE FINDINGS none fired"
    return "\n".join([f"RULE FINDINGS {len(fired)} fired, most severe first", *map(_rule_block, fired)])


def _rule_block(rule: RuleResult) -> str:
    latest = rule.latest
    history = ", ".join(rule.fired_periods)
    lines = [f"- [{rule.severity.upper()}] {rule.rule_id}: {rule.title} (fired in {history})"]
    evidence = "; ".join(f"[{e.ref}] {e.label} = {e.display}" for e in latest.evidence if e.value is not None)
    if evidence:
        lines.append(f"  evidence: {evidence}")
    if latest.notes:
        lines.append(f"  notes: {'; '.join(latest.notes)}")
    lines.append(f"  kb: {rule.kb}")
    return "\n".join(lines)


def _anomalies(result: EngineResult) -> str:
    if not result.anomalies:
        return ""
    lines = ["ANOMALIES (statistical, no rule attached)"]
    for anomaly in result.anomalies:
        against = "own history" if anomaly.basis == "history" else "sector-size cohort"
        tone = {True: "adverse", False: "favourable", None: "neutral"}[anomaly.adverse]
        lines.append(
            f"- [{anomaly.ref}] {anomaly.label} = {anomaly.display}, unusually {anomaly.direction}"
            f" vs {against} (median {anomaly.reference_display}, n={anomaly.sample_size}, {tone})"
        )
    return "\n".join(lines)


def _metrics_table(result: EngineResult) -> str:
    cited = {e.ref for rule in result.fired_rules for e in rule.latest.evidence}
    cited |= {anomaly.ref for anomaly in result.anomalies}
    by_key: dict[str, list[MetricRecord]] = defaultdict(list)
    for metric in result.metrics:
        if metric.period is not None:
            by_key[metric.key].append(metric)

    rows = []
    for records in by_key.values():
        first = records[0]
        if not (first.headline or any(record.ref in cited for record in records)):
            continue
        cells = " | ".join(f"{record.period} {record.display}" for record in records)
        rows.append(f"- [m:{first.key}@<period>] {first.name}: {cells}")
    return "\n".join(["KEY METRICS", *rows]) if rows else ""


def _document_facts(result: EngineResult) -> str:
    rows = [(fact.ref, fact.label, fact.display) for fact in result.facts if fact.period is None]
    rows += [(metric.ref, metric.name, metric.display) for metric in result.metrics if metric.period is None]
    if not rows:
        return ""
    lines = [f"- [{ref}] {label} = {display}" for ref, label, display in rows]
    return "\n".join(["DOCUMENT-LEVEL VALUES", *lines])


def _data_gaps(result: EngineResult) -> str:
    gaps = result.data_gaps
    if not gaps:
        return ""
    missing = sorted({ref for rule in gaps for ref in rule.latest.missing if ref.startswith("f:")})
    return "\n".join(
        [
            f"DATA GAPS {len(gaps)} rules could not be evaluated for {result.anchor_period}",
            f"  missing inputs: {', '.join(missing) if missing else 'derived values undefined'}",
        ]
    )
