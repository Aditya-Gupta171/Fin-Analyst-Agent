"""Read-only tools the analyst can ask for, and the context blocks assembled for each model call.

Every tool is deterministic and returns compact text with citable references, so a tool result can be pasted
into a prompt within the token budget and anything the model repeats from it can be verified.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from app.agent.schemas import ContextRequest
from app.analysis.evidence import EvidenceIndex
from app.domain.periods import Period
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult
from app.engine.rules import RuleResult
from app.knowledge.base import KnowledgeBase
from app.knowledge.documents import SectionKind
from app.knowledge.retriever import SearchFilters, pack_context

MAX_SECTION_CHARS = 900


@dataclass
class Toolbox:
    result: EngineResult
    catalog: Catalog
    index: EvidenceIndex
    kb: KnowledgeBase | None
    boosts: Mapping[str, float] = field(default_factory=dict)

    def run(self, request: ContextRequest) -> str:
        argument = request.argument.strip()
        if request.tool == "metric_history":
            return self.metric_history(argument)
        if request.tool == "rule_detail":
            return self.rule_detail(argument)
        return self.knowledge_search(argument)

    def metric_history(self, key: str) -> str:
        key = key.removeprefix("m:").removeprefix("f:").split("@")[0]
        refs = [
            ref
            for ref in self.index.items
            if ref.split("@")[0] in (f"m:{key}", f"f:{key}") and not ref.startswith("x:")
        ]
        if not refs:
            return f"metric_history({key}): no values available"
        ordered = sorted(refs, key=lambda ref: self._period_order(self.index.items[ref].period))
        return f"metric_history({key}):\n" + "\n".join(self.index.describe(ref) for ref in ordered)

    def rule_detail(self, rule_id: str) -> str:
        rule = self._rule(rule_id)
        if rule is None:
            return f"rule_detail({rule_id}): no such rule evaluated for this document"
        lines = [f"rule_detail({rule.rule_id}): {rule.title} — {rule.rationale}"]
        for outcome in rule.outcomes:
            evidence = "; ".join(
                f"[{e.ref}] {e.label} = {e.display}" for e in outcome.evidence if e.value is not None
            )
            lines.append(
                f"- {outcome.period}: {outcome.outcome.value}" + (f" | {evidence}" if evidence else "")
            )
        return "\n".join(lines)

    def knowledge_search(self, query: str, kinds: tuple[SectionKind, ...] = ()) -> str:
        if self.kb is None:
            return "knowledge_search: knowledge base unavailable"
        filters = SearchFilters(
            doc_type=self.result.document.doc_type, sector=self.result.company.sector, kinds=kinds
        )
        hits = self.kb.search(query, filters, k=3, boosts=self.boosts)
        return f"knowledge_search({query}):\n" + pack_context(hits, max_tokens=700) if hits else "no matches"

    def knowledge_sections(self, doc_ids: Iterable[str], kinds: tuple[SectionKind, ...]) -> str:
        if self.kb is None:
            return ""
        blocks = []
        for doc_id in dict.fromkeys(doc_ids):
            for chunk in self.kb.sections(doc_id, kinds):
                text = (
                    chunk.text
                    if len(chunk.text) <= MAX_SECTION_CHARS
                    else chunk.text[:MAX_SECTION_CHARS] + " ..."
                )
                blocks.append(f"[kb:{chunk.id}] {chunk.doc_title} — {chunk.heading}\n{text}")
        return "\n\n".join(blocks)

    def rule_block(self, rule: RuleResult) -> str:
        latest = rule.latest
        evidence = "; ".join(
            f"[{e.ref}] {e.label} = {e.display}" for e in latest.evidence if e.value is not None
        )
        history = ", ".join(f"{o.period} {o.outcome.value}" for o in rule.outcomes)
        return (
            f"{rule.rule_id} [{rule.severity.upper()}] {rule.title}\n"
            f"  evidence ({latest.period}): {evidence}\n"
            f"  history: {history}\n"
            f"  rationale: {rule.rationale}"
        )

    def fired(self, rule_ids: Iterable[str]) -> list[RuleResult]:
        wanted = set(rule_ids)
        return [rule for rule in self.result.rules if rule.rule_id in wanted and rule.fired]

    def _rule(self, rule_id: str) -> RuleResult | None:
        return next((rule for rule in self.result.rules if rule.rule_id == rule_id.strip()), None)

    def _period_order(self, label: str | None) -> tuple[date, int]:
        if label is None:
            return (date.min, 0)
        period = Period.parse(label, self.result.document.fiscal_year_end_month)
        return (period.end_date, period.days)
