"""Chunk utility boosts: confirms nudge a cited knowledge chunk up in future retrieval, dismisses nudge
it down, clipped to a sane range so one wild verdict can't dominate ranking."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.db.chunk_utility import chunk_boosts
from app.db.engine import create_all, make_engine, make_session_factory
from tests.api_seed import seed_feedback, seed_finding

ANALYSIS_ID = "analysis-1"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


def _boosts(database_url: str) -> dict[str, float]:
    async def _run():
        engine = make_engine(database_url)
        await create_all(engine)
        async with make_session_factory(engine)() as session:
            return await chunk_boosts(session)

    return asyncio.run(_run())


def test_a_chunk_with_no_feedback_is_absent(database_url: str) -> None:
    assert _boosts(database_url) == {}


def test_confirms_produce_a_positive_boost(database_url: str) -> None:
    seed_finding(database_url, ANALYSIS_ID, "A1", chunk_ids=["kb-chunk-1"])
    seed_feedback(database_url, ANALYSIS_ID, "A1", "confirm")

    assert _boosts(database_url)["kb-chunk-1"] == pytest.approx(0.2)


def test_dismisses_produce_a_negative_boost(database_url: str) -> None:
    seed_finding(database_url, ANALYSIS_ID, "A1", chunk_ids=["kb-chunk-1"])
    seed_feedback(database_url, ANALYSIS_ID, "A1", "dismiss")

    assert _boosts(database_url)["kb-chunk-1"] == pytest.approx(-0.1)


def test_the_boost_is_clipped_to_a_sane_range(database_url: str) -> None:
    seed_finding(database_url, ANALYSIS_ID, "A1", chunk_ids=["kb-chunk-1"])
    for _ in range(50):
        seed_feedback(database_url, ANALYSIS_ID, "A1", "confirm")

    boost = _boosts(database_url)["kb-chunk-1"]
    assert boost == pytest.approx(1.0)
