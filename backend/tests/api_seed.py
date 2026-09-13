"""Direct-to-database seeding for API tests that need a row in place without going through the HTTP
surface that creates it (e.g. a rule candidate, which normally only appears after a full agent run).

Each helper opens its own throwaway engine against the same SQLite file the app under test uses and runs
to completion inside a fresh ``asyncio.run`` call — independent of the app's own engine and of the
TestClient's background event loop, so there's no cross-loop connection sharing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.db.engine import create_all, make_engine, make_session_factory
from app.db.models import Analysis, Document, Feedback, FindingRow, RuleVersion
from app.domain.financials import FinancialDataset


def seed_document(database_url: str, dataset: FinancialDataset) -> str:
    async def _seed() -> str:
        engine = make_engine(database_url)
        await create_all(engine)
        async with make_session_factory(engine)() as session:
            row = Document(
                company_name=dataset.company.name,
                sector=dataset.company.sector.value,
                doc_type=dataset.document.doc_type.value,
                basis=dataset.document.basis.value,
                fiscal_year_end_month=dataset.document.fiscal_year_end_month,
                restated=dataset.document.restated,
                original_filename="fixture.yaml",
                content_type="application/x-yaml",
                file_bytes=b"",
                dataset_json=dataset.model_dump(mode="json"),
            )
            session.add(row)
            await session.commit()
            document_id = row.id
        await engine.dispose()
        return document_id

    return asyncio.run(_seed())


def seed_analysis(database_url: str, document_id: str, *, status: str = "succeeded") -> str:
    async def _seed() -> str:
        engine = make_engine(database_url)
        await create_all(engine)
        async with make_session_factory(engine)() as session:
            row = Analysis(document_id=document_id, status=status, finished_at=datetime.now(UTC))
            session.add(row)
            await session.commit()
            analysis_id = row.id
        await engine.dispose()
        return analysis_id

    return asyncio.run(_seed())


def seed_rule_candidate(
    database_url: str, source_analysis_id: str, *, rule_id: str = "candidate_rule"
) -> str:
    async def _seed() -> str:
        engine = make_engine(database_url)
        await create_all(engine)
        async with make_session_factory(engine)() as session:
            row = RuleVersion(
                rule_id=rule_id,
                definition_json={
                    "id": rule_id,
                    "title": "Test candidate rule",
                    "category": "test",
                    "severity": "low",
                    "when": "revenue_from_operations > 0",
                    "rationale": "Seeded directly for API testing.",
                    "evidence": ["revenue_from_operations"],
                },
                source_analysis_id=source_analysis_id,
            )
            session.add(row)
            await session.commit()
            candidate_id = row.id
        await engine.dispose()
        return candidate_id

    return asyncio.run(_seed())


def seed_finding(
    database_url: str,
    analysis_id: str,
    finding_id: str,
    *,
    rule_ids: list[str] = (),
    chunk_ids: list[str] = (),
    **overrides,
) -> str:
    async def _seed() -> str:
        engine = make_engine(database_url)
        await create_all(engine)
        async with make_session_factory(engine)() as session:
            row = FindingRow(
                analysis_id=analysis_id,
                finding_id=finding_id,
                title=overrides.pop("title", "Seeded finding"),
                category=overrides.pop("category", "test"),
                severity=overrides.pop("severity", "medium"),
                origin=overrides.pop("origin", "rule"),
                rule_ids=list(rule_ids),
                chunk_ids=list(chunk_ids),
                **overrides,
            )
            session.add(row)
            await session.commit()
            row_id = row.id
        await engine.dispose()
        return row_id

    return asyncio.run(_seed())


def seed_feedback(
    database_url: str, analysis_id: str, finding_id: str, verdict: str, *, reason: str | None = None
) -> str:
    async def _seed() -> str:
        engine = make_engine(database_url)
        await create_all(engine)
        async with make_session_factory(engine)() as session:
            row = Feedback(analysis_id=analysis_id, finding_id=finding_id, verdict=verdict, reason=reason)
            session.add(row)
            await session.commit()
            row_id = row.id
        await engine.dispose()
        return row_id

    return asyncio.run(_seed())
