"""List candidate rules the agent's rule-proposer has emitted, backtest one against historical filings,
record a human approve/reject decision (approving promotes it into the active rule pack — see
app/engine/promotion.py), and surface feedback-calibrated rule reliability.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import AppStateDep, RequireApiKey, SessionDep
from app.api.schemas import RuleCandidateOut, RuleDecisionIn, RuleStatus
from app.db.backtest import backtest_candidate
from app.db.models import RuleVersion
from app.db.reliability import RuleReliability, rule_reliabilities
from app.engine.promotion import PromotionError, promote_rule

router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[RequireApiKey])


@router.get("/candidates", response_model=list[RuleCandidateOut])
async def list_candidates(
    session: SessionDep, status_filter: RuleStatus | None = None
) -> list[RuleCandidateOut]:
    stmt = select(RuleVersion).order_by(RuleVersion.created_at.desc())
    if status_filter:
        stmt = stmt.where(RuleVersion.status == status_filter)
    rows = (await session.scalars(stmt)).all()
    return [_out(row) for row in rows]


@router.post("/candidates/{candidate_id}/backtest", response_model=RuleCandidateOut)
async def backtest(candidate_id: str, state: AppStateDep, session: SessionDep) -> RuleCandidateOut:
    row = await session.get(RuleVersion, candidate_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate rule not found")
    result = await backtest_candidate(session, row.definition_json, state.catalog)
    row.backtest_result = result.model_dump(mode="json")
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.post("/candidates/{candidate_id}/decision", response_model=RuleCandidateOut)
async def decide_candidate(
    candidate_id: str, body: RuleDecisionIn, state: AppStateDep, session: SessionDep
) -> RuleCandidateOut:
    row = await session.get(RuleVersion, candidate_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate rule not found")
    if body.decision == "approve":
        try:
            promote_rule(row.definition_json, rules_dir=state.settings.rules_dir)
        except PromotionError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row.status = "approved" if body.decision == "approve" else "rejected"
    row.decided_by = body.decided_by
    row.decided_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.get("/reliability", response_model=list[RuleReliability])
async def reliability(session: SessionDep) -> list[RuleReliability]:
    return sorted(
        (await rule_reliabilities(session)).values(), key=lambda r: (-r.confirms - r.dismisses, r.rule_id)
    )


def _out(row: RuleVersion) -> RuleCandidateOut:
    return RuleCandidateOut(
        id=row.id,
        rule_id=row.rule_id,
        definition=row.definition_json,
        status=row.status,
        source_analysis_id=row.source_analysis_id,
        created_at=row.created_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        backtest_result=row.backtest_result,
    )
