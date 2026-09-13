"""Cohort baseline persistence: turns analysed filings into the samples ``InMemoryBaselines`` needs.

The expression language's cohort functions (``cohort_pct``/``cohort_rank``) and the engine's
``BaselineProvider`` protocol (app/engine/baselines.py) are synchronous, so rather than force an async
Postgres-backed implementation of that protocol, a job loads the relevant samples into an in-memory snapshot
*before* running the synchronous engine, then persists the new filing's own metric values afterwards.

Calibrating confidence from these samples, decaying old ones, and backtesting candidate rules against them
is step 6's learning service; this module only wires the accumulation through so cohort percentiles start
working the moment two filings share a cohort.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CohortBaseline
from app.domain.enums import Sector, SizeBucket
from app.domain.periods import Period, PeriodKind
from app.engine.baselines import Cohort, InMemoryBaselines
from app.engine.pipeline import EngineResult


async def load_baselines(session: AsyncSession, sector: Sector) -> InMemoryBaselines:
    """Every stored sample for ``sector``, across all size bands and period kinds."""
    rows = (await session.scalars(select(CohortBaseline).where(CohortBaseline.sector == sector.value))).all()
    baselines = InMemoryBaselines()
    for row in rows:
        cohort = Cohort(
            sector=Sector(row.sector),
            size=SizeBucket(row.size_bucket),
            period_kind=PeriodKind(row.period_kind),
        )
        baselines.add(row.metric, cohort, [Decimal(row.value)])
    return baselines


async def record_baselines(session: AsyncSession, result: EngineResult, document_id: str) -> None:
    """Add every metric value from a finished analysis as a new sample for its cohort."""
    if result.size_bucket is None:
        return  # no revenue to classify a size band from; nothing to file this filing's metrics under
    fiscal_year_end_month = result.document.fiscal_year_end_month
    for metric in result.metrics:
        if metric.period is None:
            continue
        period_kind = Period.parse(metric.period, fiscal_year_end_month).kind
        session.add(
            CohortBaseline(
                metric=metric.key,
                sector=result.company.sector.value,
                size_bucket=result.size_bucket.value,
                period_kind=period_kind.value,
                value=str(metric.value),
                document_id=document_id,
            )
        )
