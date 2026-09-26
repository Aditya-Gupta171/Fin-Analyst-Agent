"""The agent state machine, driven by a scripted fake gateway so no network call is ever made.

Uses the real deterministic engine result for the manufacturing fixture (see
fixtures/annual_report_manufacturing.yaml) so enquiries, evidence references and rule ids are genuine.
"""

from __future__ import annotations

import pytest

from app.agent.graph import (
    AgentContext,
    AgentState,
    _plan,
    accepted,
    deterministic_plan,
    rule_problems,
    run_agent,
)
from app.agent.schemas import (
    AnalystTurn,
    ContextRequest,
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
from app.analysis.evidence import EvidenceIndex
from app.domain.enums import Severity
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult, run_engine
from app.llm.types import LLMError
from tests.builders import load_fixture
from tests.fake_gateway import FakeGateway


@pytest.fixture(scope="module")
def result(catalog: Catalog) -> EngineResult:
    return run_engine(load_fixture("annual_report_manufacturing"), catalog)


@pytest.fixture
def index(result: EngineResult, catalog: Catalog) -> EvidenceIndex:
    return EvidenceIndex(result, catalog)


def ref(result: EngineResult, key: str, period: str = "FY25") -> str:
    """A real, resolvable evidence reference from the fixture (metric or fact ref)."""
    for metric in result.metrics:
        if metric.key == key and metric.period == period:
            return metric.ref
    for fact in result.facts:
        if fact.key == key and fact.period == period:
            return fact.ref
    raise AssertionError(f"no evidence found for {key}@{period}")


# ── deterministic_plan / rule_problems (pure, no gateway) ────────────────────────────────────────────────


def test_deterministic_plan_groups_fired_rules_by_category(result: EngineResult) -> None:
    plan = deterministic_plan(result)
    assert plan.enquiries
    assert len(plan.enquiries) <= 5
    all_rule_ids = {rule_id for enquiry in plan.enquiries for rule_id in enquiry.rule_ids}
    assert all_rule_ids == {rule.rule_id for rule in result.fired_rules}
    assert [enquiry.id for enquiry in plan.enquiries] == [f"E{i}" for i in range(1, len(plan.enquiries) + 1)]


def test_rule_problems_flags_a_duplicate_id_and_unknown_names(catalog: Catalog) -> None:
    duplicate = next(iter(catalog.rules))
    proposal = RuleProposal(
        id=duplicate,
        title="t",
        category="c",
        severity="low",
        when="not_a_real_metric > 1",
        rationale="r",
        evidence=["also_not_real"],
    )
    problems = rule_problems(proposal, catalog)
    assert any("already exists" in p for p in problems)
    assert any("unknown name" in p for p in problems)
    assert any("unknown evidence name" in p for p in problems)


def test_rule_problems_accepts_a_well_formed_proposal(catalog: Catalog) -> None:
    proposal = RuleProposal(
        id="NEW_RULE_ID",
        title="t",
        category="c",
        severity="low",
        when="dso > 100",
        rationale="r",
        evidence=["dso"],
    )
    assert rule_problems(proposal, catalog) == []


def test_rule_problems_reports_a_syntax_error_without_crashing(catalog: Catalog) -> None:
    proposal = RuleProposal(
        id="X", title="t", category="c", severity="low", when="dso >", rationale="r", evidence=[]
    )
    problems = rule_problems(proposal, catalog)
    assert len(problems) == 1


# ── accepted() ────────────────────────────────────────────────────────────────────────────────────────────


def make_draft(title: str = "t") -> FindingDraft:
    return FindingDraft(
        title=title,
        category="c",
        severity="high",
        confidence=0.8,
        summary="s",
        analysis="a",
        evidence_refs=[],
        knowledge_refs=[],
        benign_explanations_considered=[],
        questions_for_management=[],
        related_rule_ids=[],
    )


def test_accepted_drops_only_rejected_findings() -> None:
    state: AgentState = {
        "drafts": {
            "E1": make_draft("keep-accepted"),
            "E2": make_draft("keep-revised"),
            "E3": make_draft("drop"),
        },
        "verdicts": {
            "E1": Verdict(
                finding_id="E1",
                decision="accept",
                severity="high",
                reasons="fine",
                revision_instructions=None,
            ),
            "E2": Verdict(
                finding_id="E2",
                decision="revise",
                severity="high",
                reasons="needs work",
                revision_instructions=None,
            ),
            "E3": Verdict(
                finding_id="E3",
                decision="reject",
                severity="low",
                reasons="not material",
                revision_instructions=None,
            ),
        },
    }
    kept = accepted(state)
    assert set(kept) == {"E1", "E2"}


def test_accepted_keeps_drafts_with_no_verdict_at_all() -> None:
    state: AgentState = {"drafts": {"E1": make_draft()}, "verdicts": {}}
    assert set(accepted(state)) == {"E1"}


# ── run_agent end to end against a scripted gateway ──────────────────────────────────────────────────────


def test_run_agent_produces_an_accepted_and_a_reviewed_out_finding(
    result: EngineResult, catalog: Catalog, index: EvidenceIndex
) -> None:
    receivables_rule = next(r for r in result.fired_rules if r.rule_id == "WC_RECEIVABLES_OUTPACE_REVENUE")
    growth_ref = ref(result, "receivables_growth")
    other_income_rule = next(r for r in result.fired_rules if r.rule_id == "EQ_OTHER_INCOME_DEPENDENCE")

    plan = Plan(
        company_context="",
        enquiries=[
            Enquiry(
                id="E1",
                title="Receivables outpacing revenue",
                why_it_matters="Cash may be tied up.",
                rule_ids=[receivables_rule.rule_id],
                evidence_refs=[growth_ref],
                question="Is collection slowing?",
            ),
            Enquiry(
                id="E2",
                title="Other income dependence",
                why_it_matters="Profit quality risk.",
                rule_ids=[other_income_rule.rule_id],
                evidence_refs=[],
                question="Is PBT propped up?",
            ),
        ],
        immaterial_rules=[
            ImmaterialRule(rule_id=r.rule_id, reason="Not material for this company.")
            for r in _dismissible(result, exclude=(receivables_rule.rule_id, other_income_rule.rule_id))
        ],
    )
    finding_1 = FindingDraft(
        title="Receivables growing much faster than revenue",
        category="working_capital",
        severity="high",
        confidence=0.85,
        summary=f"Receivables growth outpaced revenue at {{{{{growth_ref}}}}}.",
        analysis=f"The growth of {{{{{growth_ref}}}}} in receivables is a concern.",
        evidence_refs=[growth_ref],
        knowledge_refs=[],
        benign_explanations_considered=["Considered and rejected."],
        questions_for_management=["Were terms extended?"],
        related_rule_ids=[receivables_rule.rule_id],
    )
    turn_no_finding = AnalystTurn(
        action="no_finding",
        reason="Immaterial once cash conversion is put in context.",
        requests=[],
        finding=None,
    )

    gateway = FakeGateway(
        script={
            "plan": [plan],
            "investigate:E1": [
                AnalystTurn(action="submit_finding", reason="submitting", requests=[], finding=finding_1)
            ],
            "investigate:E2": [turn_no_finding],
            "review": [
                Review(
                    verdicts=[
                        Verdict(
                            finding_id="E1",
                            decision="accept",
                            severity="critical",
                            reasons="Well supported.",
                            revision_instructions=None,
                        )
                    ]
                )
            ],
            "compose": [
                Summary(
                    headline="Receivables risk dominates the quarter",
                    overall_risk="high",
                    executive_summary=f"Receivables grew {{{{{growth_ref}}}}}, a key concern.",
                    strengths=[],
                    concerns=["Receivables growth."],
                )
            ],
            "propose": [Proposals(rules=[])],
        }
    )
    context = AgentContext(result, catalog, index, kb=None, gateway=gateway)
    state = run_agent(context)

    assert list(accepted(state)) == ["E1"]
    assert state["verdicts"]["E1"].severity == "critical"
    assert state["summary"] is not None
    assert state["summary"].headline == "Receivables risk dominates the quarter"
    # the immaterial rules from the plan carry through as dismissed
    assert {d.rule_id for d in state["dismissed"]} >= {
        r.rule_id for r in _dismissible(result, exclude=(receivables_rule.rule_id, other_income_rule.rule_id))
    }


def test_run_agent_falls_back_to_deterministic_plan_when_the_planner_fails(
    result: EngineResult, catalog: Catalog, index: EvidenceIndex
) -> None:
    gateway = FakeGateway(
        script={
            "plan": [LLMError("provider unavailable")],
            "investigate": [
                AnalystTurn(action="no_finding", reason="Skipping in this test.", requests=[], finding=None)
            ],
            "review": [Review(verdicts=[])],
            "compose": [LLMError("also down")],
            "propose": [Proposals(rules=[])],
        }
    )
    context = AgentContext(result, catalog, index, kb=None, gateway=gateway)
    state = run_agent(context)

    assert any("planner fell back" in note for note in state["notes"])
    assert any("summary written from rules" in note for note in state["notes"])
    assert state["summary"] is None
    # the fallback plan still covers every fired rule across its enquiries
    covered = {rule_id for enquiry in state["plan"].enquiries for rule_id in enquiry.rule_ids}
    assert covered == {r.rule_id for r in result.fired_rules}


def _revise_script(result: EngineResult, *, revise_turn=None, recheck=None) -> dict:
    """A run where the critic sends E1 back for revision; ``revise_turn``/``recheck`` vary what follows."""
    rule = result.fired_rules[0]
    supporting_ref = next(e.ref for e in rule.latest.evidence if e.value is not None)
    weak_finding = FindingDraft(
        title="weak",
        category="c",
        severity="low",
        confidence=0.3,
        summary="Too vague.",
        analysis="Too vague.",
        evidence_refs=[supporting_ref],
        knowledge_refs=[],
        benign_explanations_considered=[],
        questions_for_management=[],
        related_rule_ids=[rule.rule_id],
    )
    fixed_finding = weak_finding.model_copy(update={"summary": "Specific and supported.", "severity": "high"})
    plan = Plan(
        company_context="",
        enquiries=[
            Enquiry(
                id="E1",
                title=rule.title,
                why_it_matters="",
                rule_ids=[rule.rule_id],
                evidence_refs=[],
                question="?",
            )
        ],
        immaterial_rules=[],
    )
    script = {
        "plan": [plan],
        "investigate:E1": [
            AnalystTurn(action="submit_finding", reason="submitting", requests=[], finding=weak_finding)
        ],
        "review": [
            Review(
                verdicts=[
                    Verdict(
                        finding_id="E1",
                        decision="revise",
                        severity="high",
                        reasons="Too vague; be specific.",
                        revision_instructions="Add detail.",
                    )
                ]
            )
        ],
        "revise:E1": [
            revise_turn
            or AnalystTurn(action="submit_finding", reason="revised", requests=[], finding=fixed_finding)
        ],
        "compose": [
            Summary(headline="h", overall_risk="moderate", executive_summary="e", strengths=[], concerns=[])
        ],
        "propose": [Proposals(rules=[])],
    }
    if recheck is not None:
        script["recheck"] = recheck
    return script


def _accept(finding_id: str) -> Review:
    return Review(
        verdicts=[
            Verdict(
                finding_id=finding_id,
                decision="accept",
                severity="high",
                reasons="Now specific.",
                revision_instructions=None,
            )
        ]
    )


def test_run_agent_revises_a_finding_the_critic_sends_back(
    result: EngineResult, catalog: Catalog, index: EvidenceIndex
) -> None:
    gateway = FakeGateway(script=_revise_script(result, recheck=[_accept("E1")]))
    state = run_agent(AgentContext(result, catalog, index, kb=None, gateway=gateway))

    assert state["drafts"]["E1"].summary == "Specific and supported."
    # the revision was re-checked by the critic, so its verdict is the final one, not the stale "revise"
    assert state["verdicts"]["E1"].decision == "accept"
    assert any(call.node == "recheck" for call in gateway.calls)


def test_the_final_check_cannot_send_a_finding_back_again(
    result: EngineResult, catalog: Catalog, index: EvidenceIndex
) -> None:
    revise_again = Review(
        verdicts=[
            Verdict(
                finding_id="E1",
                decision="revise",
                severity="high",
                reasons="Still vague.",
                revision_instructions="More.",
            )
        ]
    )
    gateway = FakeGateway(script=_revise_script(result, recheck=[revise_again, _accept("E1")]))
    state = run_agent(AgentContext(result, catalog, index, kb=None, gateway=gateway))

    assert state["verdicts"]["E1"].decision == "accept"


def test_a_revision_the_critic_could_not_recheck_is_left_unreviewed(
    result: EngineResult, catalog: Catalog, index: EvidenceIndex
) -> None:
    gateway = FakeGateway(script=_revise_script(result, recheck=[LLMError("provider down")]))
    state = run_agent(AgentContext(result, catalog, index, kb=None, gateway=gateway))

    assert "E1" in accepted(state)
    assert "E1" not in state["verdicts"]
    assert any("re-check of revisions skipped" in note for note in state["notes"])


def test_a_finding_withdrawn_on_revision_is_dropped_and_its_rules_dismissed(
    result: EngineResult, catalog: Catalog, index: EvidenceIndex
) -> None:
    withdraw = AnalystTurn(
        action="no_finding", reason="On reflection the data does not support it.", requests=[], finding=None
    )
    gateway = FakeGateway(script=_revise_script(result, revise_turn=withdraw))
    state = run_agent(AgentContext(result, catalog, index, kb=None, gateway=gateway))

    assert "E1" not in accepted(state)
    assert result.fired_rules[0].rule_id in {d.rule_id for d in state["dismissed"]}


def test_analyst_can_request_context_before_answering(
    result: EngineResult, catalog: Catalog, index: EvidenceIndex
) -> None:
    rule = result.fired_rules[0]
    plan = Plan(
        company_context="",
        enquiries=[
            Enquiry(
                id="E1",
                title=rule.title,
                why_it_matters="",
                rule_ids=[rule.rule_id],
                evidence_refs=[],
                question="?",
            )
        ],
        immaterial_rules=[],
    )
    request_turn = AnalystTurn(
        action="request_context",
        reason="Need more history.",
        requests=[ContextRequest(tool="rule_detail", argument=rule.rule_id)],
        finding=None,
    )
    final_turn = AnalystTurn(
        action="no_finding", reason="Explained by the lookup.", requests=[], finding=None
    )
    gateway = FakeGateway(
        script={
            "plan": [plan],
            "investigate:E1": [request_turn],
            "investigate:E1:final": [final_turn],
            "review": [Review(verdicts=[])],
            "compose": [
                Summary(headline="h", overall_risk="low", executive_summary="e", strengths=[], concerns=[])
            ],
            "propose": [Proposals(rules=[])],
        }
    )
    context = AgentContext(result, catalog, index, kb=None, gateway=gateway)
    state = run_agent(context)

    assert "E1" not in state.get("drafts", {})
    assert any(rule.rule_id in d.rule_id for d in state["dismissed"])


def _dismissible(result, exclude=()):
    """Fired rules the planner may call immaterial: below high severity and not integrity checks."""
    return [
        r
        for r in result.fired_rules
        if r.rule_id not in exclude and r.severity.rank < Severity.HIGH.rank and r.pack != "integrity"
    ]


def test_the_planner_cannot_dismiss_a_high_severity_rule(result, catalog, index) -> None:
    serious = next(r for r in result.fired_rules if r.severity.rank >= Severity.HIGH.rank)
    bad_plan = Plan(
        company_context="",
        enquiries=[],
        immaterial_rules=[ImmaterialRule(rule_id=serious.rule_id, reason="Not material.")],
    )
    gateway = FakeGateway({"plan": [bad_plan]})
    context = AgentContext(result, catalog, index, kb=None, gateway=gateway)

    state = _plan(context, {"notes": [], "dismissed": []})

    # the only scripted plan is rejected, so the planner falls back to grouping every fired rule
    assert "planner fell back" in state["notes"][0]
    assert serious.rule_id in {r for e in state["plan"].enquiries for r in e.rule_ids}


def test_a_report_marks_findings_unreviewed_when_the_critic_fails(
    result: EngineResult, catalog: Catalog
) -> None:
    from app.analysis.service import build_report

    script = _revise_script(result)
    script["review"] = [LLMError("provider down")]
    report = build_report(result, catalog, kb=None, gateway=FakeGateway(script=script))

    agent_findings = [f for f in report.findings if f.origin == "agent"]
    assert agent_findings
    assert all(f.critique is not None and f.critique.decision == "unreviewed" for f in agent_findings)
