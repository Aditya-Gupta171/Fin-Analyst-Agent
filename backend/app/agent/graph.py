"""The analyst agent: plan → investigate (structured ReAct) → review → revise → compose → propose rules.

Orchestrated as a LangGraph state machine. Each node is written for a strict token budget (Groq's free tier
allows about 8,000 tokens a minute per model): the planner sees the compact fact sheet, each investigation
sees only its own rules, evidence and knowledge excerpts, and all findings are reviewed in one call. Tool use
is expressed inside the structured output (the analyst may return context requests instead of a finding)
because the provider does not combine tool calls with schema-constrained output. Every node falls back to a
deterministic result if the model call fails, so an analysis always completes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent import prompts
from app.agent.schemas import (
    AnalystTurn,
    Enquiry,
    FindingDraft,
    ImmaterialRule,
    Plan,
    Proposals,
    Review,
    RuleProposal,
    Summary,
    Verdict,
)
from app.agent.tools import Toolbox
from app.analysis.evidence import EvidenceIndex, check_prose, figures_in
from app.engine.catalog import Catalog
from app.engine.expressions import FUNCTIONS, ExpressionError, parse_expression
from app.engine.factsheet import build_fact_sheet
from app.engine.pipeline import EngineResult
from app.knowledge.base import KnowledgeBase
from app.knowledge.documents import SectionKind
from app.llm.gateway import Gateway
from app.llm.types import LLMError

_GROUPS = {
    "earnings_quality": "Quality of earnings and cash conversion",
    "cash_flow": "Quality of earnings and cash conversion",
    "working_capital": "Working capital discipline",
    "leverage": "Balance sheet strength and debt service",
    "liquidity": "Balance sheet strength and debt service",
    "profitability": "Profitability and growth",
    "growth": "Profitability and growth",
    "governance": "Governance and related parties",
    "issue_structure": "Offer structure and valuation",
    "valuation": "Offer structure and valuation",
    "integrity": "Reliability of the reported figures",
}


class AgentState(TypedDict, total=False):
    plan: Plan
    drafts: dict[str, FindingDraft]
    materials: dict[str, str]
    dismissed: list[ImmaterialRule]
    verdicts: dict[str, Verdict]
    summary: Summary | None
    proposals: list[RuleProposal]
    notes: list[str]


@dataclass
class AgentContext:
    result: EngineResult
    catalog: Catalog
    index: EvidenceIndex
    kb: KnowledgeBase | None
    gateway: Gateway
    on_progress: Callable[[str, str], None] | None = None
    knowledge_boosts: Mapping[str, float] = field(default_factory=dict)
    allowed_figures: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.toolbox = Toolbox(self.result, self.catalog, self.index, self.kb, boosts=self.knowledge_boosts)
        self.fact_sheet = build_fact_sheet(self.result)
        self.allowed_figures |= figures_in(rule.rationale for rule in self.result.rules)
        self.chunk_ids = {chunk.id for chunk in self.kb.chunks} if self.kb else set()
        self.rule_ids = {rule.rule_id for rule in self.result.rules}
        self.fired_ids = {rule.rule_id for rule in self.result.fired_rules}

    def progress(self, stage: str, message: str) -> None:
        if self.on_progress is not None:
            self.on_progress(stage, message)

    def header(self) -> str:
        return self.fact_sheet.split("\n\n", 1)[0]

    def link(self, text: str) -> str:
        """Turn figures copied from the evidence into their references (see EvidenceIndex.link_figures)."""
        return self.index.link_figures(text, self.allowed_figures)


def run_agent(context: AgentContext) -> AgentState:
    graph = StateGraph(AgentState)
    graph.add_node("plan", lambda state: _plan(context, state))
    graph.add_node("investigate", lambda state: _investigate(context, state))
    graph.add_node("review", lambda state: _review(context, state))
    graph.add_node("revise", lambda state: _revise(context, state))
    graph.add_node("compose", lambda state: _compose(context, state))
    graph.add_node("propose", lambda state: _propose(context, state))
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "investigate")
    graph.add_edge("investigate", "review")
    graph.add_conditional_edges(
        "review",
        lambda state: (
            "revise" if any(v.decision == "revise" for v in state.get("verdicts", {}).values()) else "compose"
        ),
    )
    graph.add_edge("revise", "compose")
    graph.add_edge("compose", "propose")
    graph.add_edge("propose", END)
    return graph.compile().invoke({"notes": [], "dismissed": []})


# ── plan ──────────────────────────────────────────────────────────────────────────────────────────────────


def _plan(context: AgentContext, state: AgentState) -> AgentState:
    context.progress("plan", "Choosing lines of enquiry")
    if not context.result.fired_rules and not context.result.anomalies:
        return {"plan": Plan(company_context="", enquiries=[], immaterial_rules=[])}

    def validate(plan: Plan) -> list[str]:
        problems = []
        for enquiry in plan.enquiries:
            enquiry.why_it_matters = context.link(enquiry.why_it_matters)
            problems += [
                f"{enquiry.id}: rule {r} did not fire" for r in enquiry.rule_ids if r not in context.fired_ids
            ]
            problems += [
                f"{enquiry.id}: unknown reference {r}"
                for r in enquiry.evidence_refs
                if r not in context.index
            ]
            problems += check_prose(
                enquiry.why_it_matters, context.index, context.allowed_figures, enquiry.id
            )
        problems += [
            f"immaterial rule {r.rule_id} did not fire"
            for r in plan.immaterial_rules
            if r.rule_id not in context.fired_ids
        ]
        assigned: dict[str, str] = {}
        for enquiry in plan.enquiries:
            for rule_id in enquiry.rule_ids:
                if rule_id in assigned and assigned[rule_id] != enquiry.id:
                    problems.append(
                        f"{rule_id} in both {assigned[rule_id]} and {enquiry.id}; merge these enquiries"
                    )
                assigned.setdefault(rule_id, enquiry.id)
        return problems

    try:
        plan = context.gateway.generate(
            Plan,
            node="plan",
            tier="reasoning",
            system=prompts.PLANNER,
            user=context.fact_sheet,
            max_completion_tokens=1500,
            effort="medium",
            validate=validate,
        )
    except LLMError as exc:
        return {
            "plan": deterministic_plan(context.result),
            "notes": [*state["notes"], f"planner fell back to rule groups: {exc}"],
        }
    return {"plan": plan, "dismissed": plan.immaterial_rules}


def deterministic_plan(result: EngineResult) -> Plan:
    groups: dict[str, list[str]] = defaultdict(list)
    for rule in result.fired_rules:
        groups[_GROUPS.get(rule.category, rule.category.replace("_", " ").capitalize())].append(rule.rule_id)
    enquiries = [
        Enquiry(
            id=f"E{number}",
            title=title,
            why_it_matters="Flagged by the rule library.",
            rule_ids=rule_ids,
            evidence_refs=[],
            question=f"Do the flags on {title.lower()} point to a real problem?",
        )
        for number, (title, rule_ids) in enumerate(list(groups.items())[:5], start=1)
    ]
    return Plan(company_context="", enquiries=enquiries, immaterial_rules=[])


# ── investigate ───────────────────────────────────────────────────────────────────────────────────────────


def _material(context: AgentContext, enquiry: Enquiry) -> str:
    rules = context.toolbox.fired(enquiry.rule_ids)
    doc_ids = [rule.kb for rule in rules if rule.kb][:3]
    knowledge = context.toolbox.knowledge_sections(
        doc_ids, (SectionKind.INTERPRETATION, SectionKind.RED_FLAGS, SectionKind.BENIGN_EXPLANATIONS)
    )
    if not knowledge:
        knowledge = context.toolbox.knowledge_search(f"{enquiry.title}. {enquiry.question}")
    context.allowed_figures |= figures_in([knowledge])
    evidence = "\n".join(context.index.describe(ref) for ref in enquiry.evidence_refs if ref in context.index)
    sections = [
        context.header(),
        f"ENQUIRY {enquiry.id}: {enquiry.title}\n"
        f"Why it matters: {enquiry.why_it_matters}\nQuestion: {enquiry.question}",
        "FIRED RULES\n"
        + ("\n".join(context.toolbox.rule_block(rule) for rule in rules) or "none (anomaly-led enquiry)"),
        "KEY EVIDENCE\n" + (evidence or "see rule evidence"),
        _key_metrics(context.fact_sheet),
        "KNOWLEDGE EXCERPTS\n" + knowledge,
    ]
    return "\n\n".join(section for section in sections if section)


def _key_metrics(fact_sheet: str) -> str:
    start = fact_sheet.find("KEY METRICS")
    if start < 0:
        return ""
    end = fact_sheet.find("\n\n", start)
    return fact_sheet[start : end if end > 0 else None]


def _finding_problems(context: AgentContext, finding: FindingDraft) -> list[str]:
    finding.summary = context.link(finding.summary)
    finding.analysis = context.link(finding.analysis)
    finding.benign_explanations_considered = [context.link(t) for t in finding.benign_explanations_considered]
    finding.questions_for_management = [context.link(t) for t in finding.questions_for_management]
    problems = []
    for name in ("summary", "analysis"):
        problems += check_prose(getattr(finding, name), context.index, context.allowed_figures, name)
    for i, text in enumerate(finding.benign_explanations_considered):
        problems += check_prose(text, context.index, context.allowed_figures, f"benign explanation {i + 1}")
    for i, text in enumerate(finding.questions_for_management):
        problems += check_prose(text, context.index, context.allowed_figures, f"question {i + 1}")
    problems += check_prose(finding.title, context.index, set(), "title")
    problems += [
        f"evidence_refs: unknown reference {r}" for r in finding.evidence_refs if r not in context.index
    ]
    if not finding.evidence_refs:
        problems.append("evidence_refs: cite at least one reference that supports the finding")
    # Cross-references are informational, not citations of a figure; drop bad ones instead of forcing a retry.
    finding.knowledge_refs = [r for r in finding.knowledge_refs if r.removeprefix("kb:") in context.chunk_ids]
    finding.related_rule_ids = [r for r in finding.related_rule_ids if r in context.rule_ids]
    return problems


def _analyst_call(context: AgentContext, node: str, user: str, *, allow_requests: bool) -> AnalystTurn:
    def validate(turn: AnalystTurn) -> list[str]:
        if turn.action == "request_context":
            if not allow_requests:
                return ["context requests are no longer allowed; submit_finding or no_finding"]
            return [] if turn.requests else ["request_context needs at least one request"]
        if turn.action == "submit_finding":
            return (
                _finding_problems(context, turn.finding)
                if turn.finding
                else ["submit_finding needs a finding"]
            )
        turn.reason = context.link(turn.reason)
        return check_prose(turn.reason, context.index, context.allowed_figures, "reason")

    return context.gateway.generate(
        AnalystTurn,
        node=node,
        tier="reasoning",
        system=prompts.ANALYST,
        user=user,
        max_completion_tokens=2200,
        effort="medium",
        validate=validate,
    )


def _investigate(context: AgentContext, state: AgentState) -> AgentState:
    drafts: dict[str, FindingDraft] = {}
    materials: dict[str, str] = {}
    dismissed = list(state.get("dismissed", []))
    notes = list(state["notes"])
    for enquiry in state["plan"].enquiries:
        context.progress("investigate", f"{enquiry.id}: {enquiry.title}")
        material = _material(context, enquiry)
        materials[enquiry.id] = material
        node = f"investigate:{enquiry.id}"
        try:
            turn = _analyst_call(context, node, material, allow_requests=True)
            if turn.action == "request_context":
                lookups = "\n\n".join(context.toolbox.run(request) for request in turn.requests[:3])
                context.allowed_figures |= figures_in([lookups])
                material = f"{material}\n\nRESULTS OF YOUR LOOKUPS\n{lookups}"
                materials[enquiry.id] = material
                turn = _analyst_call(context, f"{node}:final", material, allow_requests=False)
        except LLMError as exc:
            notes.append(f"{enquiry.id} not investigated: {exc}")
            continue
        if turn.action == "submit_finding" and turn.finding is not None:
            drafts[enquiry.id] = turn.finding
        else:
            dismissed += [ImmaterialRule(rule_id=rule_id, reason=turn.reason) for rule_id in enquiry.rule_ids]
    return {"drafts": drafts, "materials": materials, "dismissed": dismissed, "notes": notes}


# ── review and revise ─────────────────────────────────────────────────────────────────────────────────────


def _review(context: AgentContext, state: AgentState) -> AgentState:
    drafts = state.get("drafts", {})
    if not drafts:
        return {"verdicts": {}}
    context.progress("review", f"Reviewing {len(drafts)} findings")
    blocks = []
    for finding_id, draft in drafts.items():
        evidence = "\n".join(context.index.describe(r) for r in draft.evidence_refs if r in context.index)
        blocks.append(
            f"FINDING {finding_id} [{draft.severity}] {draft.title}\n"
            f"summary: {draft.summary}\nanalysis: {draft.analysis}\n"
            f"benign explanations considered: {' | '.join(draft.benign_explanations_considered)}\n"
            f"evidence:\n{evidence}"
        )
    user = f"{context.header()}\n\n" + "\n\n".join(blocks)

    def validate(review: Review) -> list[str]:
        ids = {v.finding_id for v in review.verdicts}
        problems = [f"missing verdict for {finding_id}" for finding_id in drafts if finding_id not in ids]
        problems += [f"unknown finding {v.finding_id}" for v in review.verdicts if v.finding_id not in drafts]
        for verdict in review.verdicts:
            verdict.reasons = context.link(verdict.reasons)
            problems += check_prose(
                verdict.reasons, context.index, context.allowed_figures, f"{verdict.finding_id} reasons"
            )
        return problems

    try:
        review = context.gateway.generate(
            Review,
            node="review",
            tier="reasoning",
            system=prompts.CRITIC,
            user=user,
            max_completion_tokens=1200 + 500 * len(drafts),  # reasoning tokens count against the cap
            effort="medium",
            validate=validate,
        )
    except LLMError as exc:
        return {"verdicts": {}, "notes": [*state["notes"], f"review skipped: {exc}"]}
    return {"verdicts": {verdict.finding_id: verdict for verdict in review.verdicts}}


def _revise(context: AgentContext, state: AgentState) -> AgentState:
    drafts = dict(state["drafts"])
    notes = list(state["notes"])
    for finding_id, verdict in state["verdicts"].items():
        if verdict.decision != "revise" or finding_id not in drafts:
            continue
        context.progress("revise", f"Revising {finding_id}")
        user = (
            f"{state['materials'][finding_id]}\n\nYOUR DRAFT\n{drafts[finding_id].model_dump_json()}\n\n"
            f"REVIEWER'S INSTRUCTIONS\n{verdict.revision_instructions or verdict.reasons}\n\n"
            "Submit the revised finding."
        )
        try:
            turn = _analyst_call(context, f"revise:{finding_id}", user, allow_requests=False)
        except LLMError as exc:
            notes.append(f"{finding_id} kept unrevised: {exc}")
            continue
        if turn.action == "submit_finding" and turn.finding is not None:
            drafts[finding_id] = turn.finding
    return {"drafts": drafts, "notes": notes}


# ── compose and propose ───────────────────────────────────────────────────────────────────────────────────


def accepted(state: AgentState) -> dict[str, FindingDraft]:
    verdicts = state.get("verdicts", {})
    return {
        finding_id: draft
        for finding_id, draft in state.get("drafts", {}).items()
        if finding_id not in verdicts or verdicts[finding_id].decision != "reject"
    }


def _compose(context: AgentContext, state: AgentState) -> AgentState:
    findings = accepted(state)
    context.progress("compose", "Writing the summary")
    verdicts = state.get("verdicts", {})
    lines = [
        f"- [{(verdicts[i].severity if i in verdicts else d.severity)}] {d.title}: {d.summary}"
        for i, d in findings.items()
    ]
    user = "\n\n".join(
        part
        for part in (
            context.header(),
            "REVIEWED FINDINGS\n" + ("\n".join(lines) or "none"),
            _key_metrics(context.fact_sheet),
        )
        if part
    )

    def validate(summary: Summary) -> list[str]:
        summary.executive_summary = context.link(summary.executive_summary)
        summary.strengths = [context.link(text) for text in summary.strengths]
        summary.concerns = [context.link(text) for text in summary.concerns]
        problems = check_prose(summary.headline, context.index, set(), "headline")
        problems += check_prose(
            summary.executive_summary, context.index, context.allowed_figures, "executive_summary"
        )
        for label, items in (("strength", summary.strengths), ("concern", summary.concerns)):
            for i, text in enumerate(items):
                problems += check_prose(text, context.index, context.allowed_figures, f"{label} {i + 1}")
        return problems

    try:
        summary = context.gateway.generate(
            Summary,
            node="compose",
            tier="reasoning",  # the fast model misstated directions ("negative" cash flow) in trials
            system=prompts.COMPOSER,
            user=user,
            max_completion_tokens=1600,
            effort="low",
            validate=validate,
        )
    except LLMError as exc:
        return {"summary": None, "notes": [*state["notes"], f"summary written from rules: {exc}"]}
    return {"summary": summary}


def _propose(context: AgentContext, state: AgentState) -> AgentState:
    uncovered = [d for d in accepted(state).values() if not set(d.related_rule_ids) & context.fired_ids]
    if not uncovered and not context.result.anomalies:
        return {"proposals": []}
    context.progress("propose", "Looking for patterns the rule library does not cover")
    functions = "\n".join(f"- {spec.name}: {spec.summary}" for spec in FUNCTIONS.values())
    example = next(iter(context.catalog.rules.values()))
    user = "\n\n".join(
        (
            "EXPRESSION LANGUAGE\nPython-style expressions over metric and line item names with"
            f" + - * /, comparisons, and / or / not, and these functions:\n{functions}",
            f"EXAMPLE RULE\nid: {example.id}\nwhen: {example.when}",
            "METRICS\n" + ", ".join(sorted(context.catalog.metrics)),
            "FINDINGS NOT COVERED BY A RULE\n"
            + ("\n".join(f"- {d.title}: {d.analysis}" for d in uncovered) or "none"),
            "ANOMALIES\n"
            + (
                "\n".join(
                    f"- {a.label} unusually {a.direction} vs {a.basis}" for a in context.result.anomalies
                )
                or "none"
            ),
        )
    )

    def validate(proposals: Proposals) -> list[str]:
        problems = []
        for rule in proposals.rules:
            problems += rule_problems(rule, context.catalog)
        return problems

    try:
        proposals = context.gateway.generate(
            Proposals,
            node="propose",
            tier="fast",
            system=prompts.PROPOSER,
            user=user,
            max_completion_tokens=1000,
            effort="medium",
            validate=validate,
        )
    except LLMError as exc:
        return {"proposals": [], "notes": [*state["notes"], f"no rule proposals: {exc}"]}
    return {"proposals": proposals.rules}


def rule_problems(rule: RuleProposal, catalog: Catalog) -> list[str]:
    problems = []
    if rule.id in catalog.rules:
        problems.append(f"{rule.id}: id already exists")
    try:
        expression = parse_expression(rule.when)
    except ExpressionError as exc:
        return [*problems, f"{rule.id}: {exc}"]
    known = catalog.items.keys() | catalog.metrics.keys()
    problems += [f"{rule.id}: unknown name {name}" for name in sorted(expression.names - known)]
    problems += [f"{rule.id}: unknown evidence name {name}" for name in rule.evidence if name not in known]
    return problems
