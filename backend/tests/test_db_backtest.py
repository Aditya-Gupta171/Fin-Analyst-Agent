"""Backtesting a candidate rule reuses the same Evaluator/RuleEngine path a live analysis uses. Cross
checked here against plain run_engine() on the same expression (renamed, since backtesting a rule id that's
already active is correctly rejected by the same "id already exists" guard the rule-proposer itself uses).

A candidate's stored definition always looks like ``CandidateRule.model_dump()`` (evidence as plain metric
name strings) — these build that shape by hand from a real active rule's fields, rather than dumping the
``RuleDef`` itself (whose ``evidence`` is a list of ``{expr, label, unit}`` objects).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.db.backtest import backtest_candidate
from app.db.engine import create_all, make_engine, make_session_factory
from app.engine.catalog import Catalog, RuleDef
from app.engine.pipeline import run_engine
from tests.api_seed import seed_document
from tests.builders import load_fixture


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


def _candidate_definition(rule: RuleDef, *, rule_id: str | None = None) -> dict:
    return {
        "id": rule_id or rule.id,
        "title": rule.title,
        "category": rule.category,
        "severity": rule.severity.value,
        "when": rule.when,
        "rationale": rule.rationale,
        "evidence": [spec.expr for spec in rule.evidence],
    }


def _backtest(database_url: str, definition: dict, catalog: Catalog):
    async def _run():
        engine = make_engine(database_url)
        await create_all(engine)
        async with make_session_factory(engine)() as session:
            return await backtest_candidate(session, definition, catalog)

    return asyncio.run(_run())


def test_backtest_matches_run_engine_for_the_same_expression(database_url: str, catalog: Catalog) -> None:
    dataset = load_fixture("annual_report_manufacturing")
    seed_document(database_url, dataset)
    result = run_engine(dataset, catalog)
    fired_rule = result.fired_rules[0]
    live_rule = catalog.rules[fired_rule.rule_id]
    definition = _candidate_definition(live_rule, rule_id="BACKTEST_TEST_RULE")

    backtest = _backtest(database_url, definition, catalog)

    assert backtest.errors == []
    assert backtest.documents_evaluated == 1
    assert len(backtest.fired) == 1
    assert backtest.fired[0].period == fired_rule.latest.period
    # base severity, not fired_rule.severity: the candidate carries no escalations, so if the live rule's
    # severity was escalated this run, the backtest correctly reports the un-escalated base severity.
    assert backtest.fired[0].severity == live_rule.severity.value


def test_backtest_rejects_a_colliding_id(database_url: str, catalog: Catalog) -> None:
    live_rule = next(iter(catalog.rules.values()))
    definition = _candidate_definition(live_rule)  # id left as the real, already-active one

    backtest = _backtest(database_url, definition, catalog)

    assert backtest.documents_evaluated == 0
    assert any("already exists" in problem for problem in backtest.errors)


def test_backtest_reports_an_unknown_name_in_the_expression(database_url: str, catalog: Catalog) -> None:
    definition = {
        "id": "BAD_CANDIDATE",
        "title": "broken",
        "category": "test",
        "severity": "low",
        "when": "not_a_real_metric > 0",
        "rationale": "for testing",
        "evidence": [],
    }

    backtest = _backtest(database_url, definition, catalog)

    assert backtest.documents_evaluated == 0
    assert backtest.errors
