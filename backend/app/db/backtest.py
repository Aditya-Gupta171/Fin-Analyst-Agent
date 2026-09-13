"""Backtests a candidate rule against every historical filing on file, so a human can see how often it
would have fired before approving it into the active rule pack (app/engine/promotion.py).

Reuses the same evaluation path a live analysis uses (``Evaluator``/``RuleEngine``) — a candidate is just
another ``RuleDef``, evaluated on its own rather than as part of a whole catalog.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import rule_problems
from app.agent.schemas import RuleProposal
from app.db.models import Document
from app.domain.financials import FinancialDataset
from app.engine.catalog import Catalog, RuleDef
from app.engine.evaluator import Evaluator
from app.engine.pipeline import size_bucket, validate_dataset
from app.engine.rules import RuleEngine, RuleOutcome


class BacktestHit(BaseModel):
    document_id: str
    company_name: str
    period: str
    severity: str


class BacktestResult(BaseModel):
    documents_evaluated: int
    fired: list[BacktestHit]
    insufficient_data_count: int
    errors: list[str]


async def backtest_candidate(session: AsyncSession, definition: dict, catalog: Catalog) -> BacktestResult:
    problems = rule_problems(RuleProposal.model_validate(definition), catalog)
    if problems:
        return BacktestResult(documents_evaluated=0, fired=[], insufficient_data_count=0, errors=problems)

    rule = RuleDef.model_validate(definition)
    documents = (await session.scalars(select(Document))).all()
    fired: list[BacktestHit] = []
    errors: list[str] = []
    insufficient = 0
    evaluated = 0
    for document in documents:
        try:
            dataset = FinancialDataset.model_validate(document.dataset_json)
            _, clean = validate_dataset(dataset, catalog)
            evaluator = Evaluator(clean, catalog, size=size_bucket(clean))
            result = RuleEngine(evaluator).evaluate(rule)
        except Exception as exc:  # a candidate's expression can be well-formed but still fail at runtime
            errors.append(f"{document.company_name} ({document.id}): {exc}")
            continue
        if result is None:
            continue  # the rule doesn't apply to this document's type/sector
        evaluated += 1
        if result.fired:
            fired.append(
                BacktestHit(
                    document_id=document.id,
                    company_name=document.company_name,
                    period=result.latest.period,
                    severity=result.severity.value,
                )
            )
        if result.latest.outcome is RuleOutcome.INSUFFICIENT_DATA:
            insufficient += 1
    return BacktestResult(
        documents_evaluated=evaluated, fired=fired, insufficient_data_count=insufficient, errors=errors
    )
