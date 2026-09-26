"""Runs one analysis job: engine + agent off the event loop, progress reported live, result persisted.

``analyze()`` (app/analysis/service.py) is synchronous and, with a real Groq gateway, takes minutes — it
must never run directly on the event loop that also serves HTTP requests. It runs in a worker thread via
``asyncio.to_thread``; the thread reports progress into ``ProgressStore`` (plain dict, not the DB, since the
async session belongs to the event loop's thread) and the coroutine persists the final result once the
thread returns.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.analysis.report import AnalysisReport
from app.analysis.service import build_report
from app.db.baselines import load_baselines, record_baselines
from app.db.chunk_utility import chunk_boosts
from app.db.models import Analysis, Document, FindingRow, RuleVersion
from app.db.reliability import rule_reliabilities, scores
from app.domain.financials import FinancialDataset
from app.engine.baselines import BaselineProvider
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult, run_engine
from app.jobs.progress import ProgressStore
from app.knowledge.base import KnowledgeBase
from app.llm.gateway import Gateway

logger = logging.getLogger(__name__)


def _run_sync(
    dataset: FinancialDataset,
    catalog: Catalog,
    kb: KnowledgeBase | None,
    gateway: Gateway | None,
    baselines: BaselineProvider,
    reliability: dict[str, float],
    knowledge_boosts: dict[str, float],
    progress: ProgressStore,
    analysis_id: str,
) -> tuple[EngineResult, AnalysisReport]:
    def on_progress(stage: str, message: str) -> None:
        progress.set(analysis_id, stage, message)

    on_progress("engine", "Computing metrics and evaluating rules")
    result = run_engine(dataset, catalog, baselines=baselines)
    report = build_report(
        result,
        catalog,
        kb=kb,
        gateway=gateway,
        on_progress=on_progress,
        reliability=reliability,
        knowledge_boosts=knowledge_boosts,
    )
    return result, report


async def run_analysis(
    analysis_id: str,
    document_id: str,
    *,
    session_factory: async_sessionmaker,
    catalog: Catalog,
    kb: KnowledgeBase | None,
    gateway: Gateway | None,
    progress: ProgressStore,
) -> None:
    """Run one job; whatever fails — loading, the engine/agent, or persisting — the analysis always ends
    ``succeeded`` or ``failed``, never stuck ``queued``/``running`` with an SSE stream that never closes."""
    try:
        await _run_analysis(
            analysis_id,
            document_id,
            session_factory=session_factory,
            catalog=catalog,
            kb=kb,
            gateway=gateway,
            progress=progress,
        )
    except Exception as exc:
        logger.exception("analysis %s failed", analysis_id)
        await _mark_failed(session_factory, analysis_id, exc)
    finally:
        progress.clear(analysis_id)


async def _mark_failed(session_factory: async_sessionmaker, analysis_id: str, exc: Exception) -> None:
    try:
        async with session_factory() as session:
            analysis = await session.get(Analysis, analysis_id)
            if analysis is not None:
                analysis.status = "failed"
                analysis.error_message = f"{type(exc).__name__}: {exc}"
                analysis.finished_at = datetime.now(UTC)
                await session.commit()
    except Exception:
        logger.exception("could not mark analysis %s as failed", analysis_id)


async def _run_analysis(
    analysis_id: str,
    document_id: str,
    *,
    session_factory: async_sessionmaker,
    catalog: Catalog,
    kb: KnowledgeBase | None,
    gateway: Gateway | None,
    progress: ProgressStore,
) -> None:
    async with session_factory() as session:
        analysis = await session.get(Analysis, analysis_id)
        if analysis is None:
            return
        document = await session.get(Document, document_id)
        if document is None:
            raise LookupError(f"document {document_id} no longer exists")
        dataset = FinancialDataset.model_validate(document.dataset_json)
        baselines = await load_baselines(session, dataset.company.sector, exclude_document=document_id)
        reliability = scores(await rule_reliabilities(session))
        boosts = await chunk_boosts(session)
        analysis.status = "running"
        analysis.started_at = datetime.now(UTC)
        await session.commit()

    result, report = await asyncio.to_thread(
        _run_sync, dataset, catalog, kb, gateway, baselines, reliability, boosts, progress, analysis_id
    )

    async with session_factory() as session:
        analysis = await session.get(Analysis, analysis_id)
        if analysis is None:
            return
        await record_baselines(session, result, document_id)
        analysis.status = "succeeded"
        analysis.catalog_version = report.provenance.catalog_version
        analysis.kb_version = report.provenance.kb_version
        analysis.prompt_version = report.provenance.prompt_version
        analysis.report_json = report.model_dump(mode="json")
        analysis.finished_at = datetime.now(UTC)
        session.add_all(
            FindingRow(
                analysis_id=analysis_id,
                finding_id=finding.id,
                title=finding.title,
                category=finding.category,
                severity=finding.severity.value,
                origin=finding.origin,
                rule_ids=finding.rule_ids,
                chunk_ids=[k.chunk_id for k in finding.knowledge],
            )
            for finding in report.findings
        )
        session.add_all(
            RuleVersion(
                rule_id=candidate.id,
                definition_json=candidate.model_dump(mode="json"),
                source_analysis_id=analysis_id,
            )
            for candidate in report.candidate_rules
        )
        await session.commit()
