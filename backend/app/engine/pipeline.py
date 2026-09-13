"""Runs the deterministic engine end to end: validate the dataset, compute metrics, evaluate rules, detect
anomalies. No LLM is involved; the output is the grounded input the agent reasons over."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.domain.enums import Scope, SizeBucket, Unit
from app.domain.financials import CompanyInfo, DocumentInfo, Fact, FinancialDataset, SourceRef
from app.engine.anomalies import Anomaly, detect_anomalies
from app.engine.baselines import BaselineProvider
from app.engine.catalog import Catalog
from app.engine.evaluator import Evaluator, MissingData, anchor_period
from app.engine.formatting import format_value
from app.engine.rules import RuleEngine, RuleOutcome, RuleResult

SIZE_THRESHOLDS = ((Decimal(500), SizeBucket.SMALL), (Decimal(5000), SizeBucket.MID))


class DatasetIssue(BaseModel):
    level: Literal["error", "warning"]
    ref: str
    message: str


class FactRecord(BaseModel):
    ref: str
    key: str
    label: str
    period: str | None
    value: Decimal | str
    unit: Unit
    display: str
    source: SourceRef


class MetricRecord(BaseModel):
    key: str
    ref: str
    name: str
    category: str
    unit: Unit
    period: str | None
    value: Decimal
    display: str
    headline: bool
    inputs: list[str]
    notes: list[str]


class EngineResult(BaseModel):
    catalog_version: str
    company: CompanyInfo
    document: DocumentInfo
    periods: list[str]
    anchor_period: str
    size_bucket: SizeBucket | None
    dataset_issues: list[DatasetIssue]
    facts: list[FactRecord]
    metrics: list[MetricRecord]
    rules: list[RuleResult]
    anomalies: list[Anomaly]

    @property
    def fired_rules(self) -> list[RuleResult]:
        return [rule for rule in self.rules if rule.fired]

    @property
    def data_gaps(self) -> list[RuleResult]:
        return [rule for rule in self.rules if rule.latest.outcome is RuleOutcome.INSUFFICIENT_DATA]

    def references(self) -> dict[str, str]:
        """Every citable value, by reference, with its display string."""
        registry = {fact.ref: fact.display for fact in self.facts}
        registry.update({metric.ref: metric.display for metric in self.metrics})
        for rule in self.rules:
            for outcome in rule.outcomes:
                for evidence in outcome.evidence:
                    if evidence.value is not None:
                        registry.setdefault(evidence.ref, evidence.display)
        return registry


def run_engine(
    dataset: FinancialDataset,
    catalog: Catalog,
    *,
    baselines: BaselineProvider | None = None,
    packs: list[str] | None = None,
) -> EngineResult:
    issues, clean = validate_dataset(dataset, catalog)
    size = size_bucket(clean)
    evaluator = Evaluator(clean, catalog, size=size, baselines=baselines)
    anchor = evaluator.anchor

    return EngineResult(
        catalog_version=catalog.version,
        company=clean.company,
        document=clean.document,
        periods=[p.label for p in clean.periods],
        anchor_period=anchor.label,
        size_bucket=size,
        dataset_issues=issues,
        facts=[_fact_record(fact, catalog) for fact in clean.facts],
        metrics=_metrics(evaluator),
        rules=RuleEngine(evaluator).run(catalog.active_rules(packs)),
        anomalies=detect_anomalies(evaluator, anchor),
    )


def validate_dataset(
    dataset: FinancialDataset, catalog: Catalog
) -> tuple[list[DatasetIssue], FinancialDataset]:
    """Check facts against the taxonomy and drop the ones that cannot be used, reporting why."""
    issues: list[DatasetIssue] = []
    kept = []
    for fact in dataset.facts:
        item = catalog.items.get(fact.key)
        if item is None:
            issues.append(DatasetIssue(level="warning", ref=fact.ref, message="not in the taxonomy; ignored"))
            continue
        textual = item.unit is Unit.TEXT
        if textual != (fact.text is not None):
            expected = "text" if textual else "a numeric value"
            issues.append(DatasetIssue(level="error", ref=fact.ref, message=f"expected {expected}; dropped"))
            continue
        if (item.scope is Scope.DOCUMENT) != (fact.period is None):
            expected = "no period" if item.scope is Scope.DOCUMENT else "a period"
            issues.append(DatasetIssue(level="error", ref=fact.ref, message=f"expected {expected}; dropped"))
            continue
        kept.append(fact)
    if len(kept) == len(dataset.facts):
        return issues, dataset
    return issues, FinancialDataset(company=dataset.company, document=dataset.document, facts=kept)


def size_bucket(dataset: FinancialDataset) -> SizeBucket | None:
    """Size band from annualised revenue in the anchor period."""
    if not dataset.periods:
        return None
    anchor = anchor_period(dataset)
    revenue = dataset.get("revenue_from_operations", anchor)
    if revenue is None or revenue.value is None:
        return None
    annualised = revenue.value * 365 / anchor.days
    for ceiling, bucket in SIZE_THRESHOLDS:
        if annualised < ceiling:
            return bucket
    return SizeBucket.LARGE


def _fact_record(fact: Fact, catalog: Catalog) -> FactRecord:
    item = catalog.items[fact.key]
    return FactRecord(
        ref=fact.ref,
        key=fact.key,
        label=item.label,
        period=fact.period,
        value=fact.payload,
        unit=item.unit,
        display=format_value(fact.payload, item.unit),
        source=fact.source,
    )


def _metrics(evaluator: Evaluator) -> list[MetricRecord]:
    records: list[MetricRecord] = []
    for definition in evaluator.catalog.metrics.values():
        periods = [evaluator.anchor] if definition.scope is Scope.DOCUMENT else evaluator.dataset.periods
        for period in periods:
            try:
                value = evaluator.metric(definition.key, period)
            except MissingData:
                continue
            records.append(
                MetricRecord(
                    key=definition.key,
                    ref=value.ref,
                    name=definition.name,
                    category=definition.category,
                    unit=definition.unit,
                    period=value.period,
                    value=value.value,
                    display=format_value(value.value, definition.unit),
                    headline=definition.headline,
                    inputs=list(value.inputs),
                    notes=list(value.notes),
                )
            )
    return records
