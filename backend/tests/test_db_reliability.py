"""Rule reliability: a Beta-calibrated score per rule id from confirm/dismiss feedback on the findings
that cite it. SQLite doesn't enforce the foreign keys by default, so these seed Feedback/FindingRow rows
directly without needing a real Document/Analysis behind them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.analysis.baseline import RULE_CONFIDENCE
from app.db.engine import create_all, make_engine, make_session_factory
from app.db.reliability import rule_reliabilities, scores
from tests.api_seed import seed_feedback, seed_finding

ANALYSIS_ID = "analysis-1"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


def _reliabilities(database_url: str) -> dict:
    async def _run():
        engine = make_engine(database_url)
        await create_all(engine)
        async with make_session_factory(engine)() as session:
            return await rule_reliabilities(session)

    return asyncio.run(_run())


def test_a_rule_with_no_feedback_is_absent(database_url: str) -> None:
    assert _reliabilities(database_url) == {}


def test_confirms_raise_the_score_above_the_default(database_url: str) -> None:
    seed_finding(database_url, ANALYSIS_ID, "A1", rule_ids=["RULE_X"])
    for _ in range(5):
        seed_feedback(database_url, ANALYSIS_ID, "A1", "confirm")

    result = _reliabilities(database_url)
    assert result["RULE_X"].confirms == 5
    assert result["RULE_X"].dismisses == 0
    assert result["RULE_X"].score > RULE_CONFIDENCE


def test_dismisses_lower_the_score_below_the_default(database_url: str) -> None:
    seed_finding(database_url, ANALYSIS_ID, "A1", rule_ids=["RULE_X"])
    for _ in range(5):
        seed_feedback(database_url, ANALYSIS_ID, "A1", "dismiss")

    result = _reliabilities(database_url)
    assert result["RULE_X"].score < RULE_CONFIDENCE


def test_a_finding_covering_two_rules_credits_feedback_to_both(database_url: str) -> None:
    seed_finding(database_url, ANALYSIS_ID, "A1", rule_ids=["RULE_X", "RULE_Y"])
    seed_feedback(database_url, ANALYSIS_ID, "A1", "confirm")

    result = _reliabilities(database_url)
    assert result["RULE_X"].confirms == 1
    assert result["RULE_Y"].confirms == 1


def test_scores_helper_flattens_to_a_plain_mapping(database_url: str) -> None:
    seed_finding(database_url, ANALYSIS_ID, "A1", rule_ids=["RULE_X"])
    seed_feedback(database_url, ANALYSIS_ID, "A1", "confirm")

    flattened = scores(_reliabilities(database_url))
    assert flattened == {"RULE_X": _reliabilities(database_url)["RULE_X"].score}
