"""Trigger an analysis, poll or stream its progress, fetch the finished report, list its findings."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import AppStateDep, RequireApiKey, SessionDep
from app.api.schemas import AnalysisDetail, AnalysisSummary, FeedbackIn, FindingOut
from app.api.state import AppState
from app.db.models import Analysis, Document, Feedback, FindingRow
from app.jobs.runner import run_analysis

router = APIRouter(tags=["analyses"], dependencies=[RequireApiKey])


@router.post(
    "/documents/{document_id}/analyses",
    response_model=AnalysisSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_analysis(document_id: str, state: AppStateDep, session: SessionDep) -> AnalysisSummary:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    analysis = Analysis(document_id=document_id)
    session.add(analysis)
    await session.commit()
    await session.refresh(analysis)

    async def job() -> None:
        await run_analysis(
            analysis.id,
            document_id,
            session_factory=state.session_factory,
            catalog=state.catalog,
            kb=state.kb,
            gateway=state.gateway,
            progress=state.progress,
        )

    await state.queue.submit(job)
    await session.refresh(analysis)
    return _summary(analysis)


@router.get("/analyses", response_model=list[AnalysisSummary])
async def list_analyses(session: SessionDep, document_id: str | None = None) -> list[AnalysisSummary]:
    stmt = select(Analysis).order_by(Analysis.created_at.desc())
    if document_id:
        stmt = stmt.where(Analysis.document_id == document_id)
    rows = (await session.scalars(stmt)).all()
    return [_summary(row) for row in rows]


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetail)
async def get_analysis(analysis_id: str, state: AppStateDep, session: SessionDep) -> AnalysisDetail:
    row = await session.get(Analysis, analysis_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "analysis not found")
    return _detail(row, state)


@router.get("/analyses/{analysis_id}/events")
async def stream_analysis_events(analysis_id: str, state: AppStateDep) -> StreamingResponse:
    """Server-sent events with the live stage/message, polling the DB every second until a terminal status.

    Uses its own session per poll (not the request-scoped ``SessionDep``) because that dependency's session
    is closed as soon as this function returns the ``StreamingResponse`` — before the generator below, which
    outlives the request handler, ever runs.
    """

    async def events() -> AsyncIterator[str]:
        while True:
            async with state.session_factory() as session:
                row = await session.get(Analysis, analysis_id)
            if row is None:
                yield _sse({"error": "analysis not found"})
                return
            live = state.progress.get(analysis_id)
            yield _sse(
                {
                    "status": row.status,
                    "stage": live.stage if live else row.stage,
                    "message": live.message if live else row.stage_message,
                }
            )
            if row.status in ("succeeded", "failed"):
                return
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/analyses/{analysis_id}/findings", response_model=list[FindingOut])
async def list_findings(analysis_id: str, session: SessionDep) -> list[FindingOut]:
    rows = (await session.scalars(select(FindingRow).where(FindingRow.analysis_id == analysis_id))).all()
    return [_finding_out(row) for row in rows]


@router.post("/analyses/{analysis_id}/findings/{finding_id}/feedback", response_model=FindingOut)
async def submit_feedback(
    analysis_id: str, finding_id: str, body: FeedbackIn, session: SessionDep
) -> FindingOut:
    stmt = select(FindingRow).where(
        FindingRow.analysis_id == analysis_id, FindingRow.finding_id == finding_id
    )
    row = (await session.scalars(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found")
    session.add(
        Feedback(
            analysis_id=analysis_id,
            finding_id=finding_id,
            verdict=body.verdict,
            reason=body.reason,
            created_by=body.created_by,
        )
    )
    row.status = "confirmed" if body.verdict == "confirm" else "dismissed"
    await session.commit()
    await session.refresh(row)
    return _finding_out(row)


def _summary(row: Analysis) -> AnalysisSummary:
    return AnalysisSummary(
        id=row.id,
        document_id=row.document_id,
        status=row.status,
        stage=row.stage,
        stage_message=row.stage_message,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _detail(row: Analysis, state: AppState) -> AnalysisDetail:
    summary = _summary(row)
    live = state.progress.get(row.id)
    if live and row.status == "running":
        summary = summary.model_copy(update={"stage": live.stage, "stage_message": live.message})
    return AnalysisDetail(**summary.model_dump(), report=row.report_json)


def _finding_out(row: FindingRow) -> FindingOut:
    return FindingOut(
        id=row.id,
        finding_id=row.finding_id,
        title=row.title,
        category=row.category,
        severity=row.severity,
        origin=row.origin,
        rule_ids=row.rule_ids,
        status=row.status,
    )
