"""List candidate rules the agent's rule-proposer has emitted, and record a human approve/reject decision.

Backtesting a candidate against historical filings before promoting it into the active rule pack is step 6
(the learning service); this only persists the candidate and the decision.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import RequireApiKey, SessionDep
from app.api.schemas import RuleCandidateOut, RuleDecisionIn, RuleStatus
from app.db.models import RuleVersion

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


@router.post("/candidates/{candidate_id}/decision", response_model=RuleCandidateOut)
async def decide_candidate(candidate_id: str, body: RuleDecisionIn, session: SessionDep) -> RuleCandidateOut:
    row = await session.get(RuleVersion, candidate_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate rule not found")
    row.status = "approved" if body.decision == "approve" else "rejected"
    row.decided_by = body.decided_by
    row.decided_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return _out(row)


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
    )
