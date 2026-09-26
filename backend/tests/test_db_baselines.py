"""Cohort baseline persistence: re-analysing a filing replaces its samples rather than adding duplicates,
and a filing is never ranked against its own earlier values."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import func, select

from app.db.baselines import load_baselines, record_baselines
from app.db.engine import create_all, make_engine, make_session_factory
from app.db.models import CohortBaseline
from app.engine.catalog import Catalog
from app.engine.pipeline import run_engine
from tests.builders import load_fixture


def _record_twice(database_url: str, result):
    async def _run():
        engine = make_engine(database_url)
        await create_all(engine)
        factory = make_session_factory(engine)
        counts = []
        for _ in range(2):
            async with factory() as session:
                await record_baselines(session, result, "doc-1")
                await session.commit()
                counts.append(await session.scalar(select(func.count()).select_from(CohortBaseline)))
        async with factory() as session:
            with_own = await load_baselines(session, result.company.sector)
            without_own = await load_baselines(session, result.company.sector, exclude_document="doc-1")
        await engine.dispose()
        return counts, with_own, without_own

    return asyncio.run(_run())


def test_recording_the_same_document_twice_keeps_one_set_of_samples(tmp_path: Path, catalog: Catalog) -> None:
    result = run_engine(load_fixture("annual_report_manufacturing"), catalog)
    assert result.size_bucket is not None

    (first, second), _, _ = _record_twice(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", result)

    assert first > 0
    assert second == first


def test_a_document_is_excluded_from_its_own_cohort(tmp_path: Path, catalog: Catalog) -> None:
    result = run_engine(load_fixture("annual_report_manufacturing"), catalog)

    _, with_own, without_own = _record_twice(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", result)

    assert with_own._samples
    assert not without_own._samples
