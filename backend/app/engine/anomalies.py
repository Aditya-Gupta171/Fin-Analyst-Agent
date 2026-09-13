"""Statistical anomaly detection: metrics that are unusual against the company's own history or its cohort.

Rules encode what an analyst already knows to look for. Anomalies catch what no rule anticipated, and they are
what the agent's rule proposer turns into candidate rules.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.domain.enums import Scope, Unit
from app.domain.periods import Period
from app.engine import statistics
from app.engine.catalog import MetricDef
from app.engine.evaluator import MIN_HISTORY, Evaluator, MissingData, metric_ref
from app.engine.formatting import format_value

COHORT_TAIL = Decimal("0.05")  # flag values in the bottom or top 5% of the cohort

# Absolute amounts grow with the company, so only scale-free metrics are compared.
_SCALE_FREE_UNITS = {Unit.RATIO, Unit.TIMES, Unit.DAYS}

Basis = Literal["history", "cohort"]


class Anomaly(BaseModel):
    metric: str
    label: str
    ref: str
    period: str
    value: Decimal
    display: str
    basis: Basis
    direction: Literal["high", "low"]
    adverse: bool | None  # None when a metric has no better direction
    reference: Decimal  # median of the comparison sample
    reference_display: str
    score: Decimal  # modified z-score (history) or percentile rank 0-1 (cohort)
    sample_size: int


def detect_anomalies(evaluator: Evaluator, period: Period) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    for definition in evaluator.catalog.metrics.values():
        if definition.scope is Scope.DOCUMENT or definition.unit not in _SCALE_FREE_UNITS:
            continue
        try:
            current = evaluator.metric(definition.key, period).value
        except MissingData:
            continue

        history = _history(evaluator, definition.key, period)
        if len(history) >= MIN_HISTORY:
            z = statistics.modified_z(current, history)
            if z is not None and abs(z) > statistics.OUTLIER_Z:
                reference = statistics.median(history)
                anomalies.append(_anomaly(definition, period, current, "history", reference, z, len(history)))

        distribution = evaluator.distribution(definition.key, period)
        if distribution is not None and distribution.reliable:
            rank = distribution.rank(current)
            if rank <= COHORT_TAIL or rank >= 1 - COHORT_TAIL:
                reference = distribution.median
                anomalies.append(
                    _anomaly(definition, period, current, "cohort", reference, rank, distribution.n)
                )

    return sorted(anomalies, key=lambda a: (a.adverse is not True, a.metric, a.basis))


def _anomaly(
    definition: MetricDef,
    period: Period,
    value: Decimal,
    basis: Basis,
    reference: Decimal,
    score: Decimal,
    sample_size: int,
) -> Anomaly:
    direction: Literal["high", "low"] = "high" if value > reference else "low"
    adverse = (
        None if definition.better == "neutral" else (direction == "high") == (definition.better == "lower")
    )
    return Anomaly(
        metric=definition.key,
        label=definition.name,
        ref=metric_ref(definition.key, period.label),
        period=period.label,
        value=value,
        display=format_value(value, definition.unit),
        basis=basis,
        direction=direction,
        adverse=adverse,
        reference=reference,
        reference_display=format_value(reference, definition.unit),
        score=score,
        sample_size=sample_size,
    )


def _history(evaluator: Evaluator, key: str, period: Period) -> list[Decimal]:
    values: list[Decimal] = []
    for earlier in evaluator.dataset.periods:
        if earlier.kind is period.kind and earlier.end_date < period.end_date:
            try:
                values.append(evaluator.metric(key, earlier).value)
            except MissingData:
                continue
    return values
