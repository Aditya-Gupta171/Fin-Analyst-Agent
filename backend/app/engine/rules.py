"""Evaluates rule packs against a dataset and records, for every applicable period, why each rule fired,
passed, or could not be evaluated."""

from __future__ import annotations

import ast
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from app.domain.enums import Scope, Severity, Unit
from app.domain.financials import fact_ref
from app.domain.periods import Period
from app.engine.catalog import Catalog, EvidenceSpec, RuleDef
from app.engine.evaluator import EvaluationError, Evaluator, metric_ref
from app.engine.expressions import FUNCTIONS, Expression
from app.engine.formatting import format_value

# Functions whose result has the same unit as their first argument.
_UNIT_PRESERVING = {"prior", "seq", "opening", "avg", "ttm", "trailing_sum", "abs", "min", "max", "coalesce"}


class RuleOutcome(StrEnum):
    FIRED = "fired"
    PASSED = "passed"
    INSUFFICIENT_DATA = "insufficient_data"


class EvidenceValue(BaseModel):
    ref: str
    label: str
    value: Decimal | str | None
    unit: Unit | None
    display: str
    notes: list[str] = []


class PeriodOutcome(BaseModel):
    period: str
    outcome: RuleOutcome
    severity: Severity | None = None  # after escalations; set only when fired
    evidence: list[EvidenceValue] = []
    inputs: list[str] = []
    missing: list[str] = []
    notes: list[str] = []


class RuleResult(BaseModel):
    rule_id: str
    version: int
    pack: str
    title: str
    category: str
    base_severity: Severity
    rationale: str
    kb: str | None
    questions: list[str]
    outcomes: list[PeriodOutcome]  # oldest first; the last is the document's anchor period when applicable

    @property
    def latest(self) -> PeriodOutcome:
        return self.outcomes[-1]

    @property
    def fired(self) -> bool:
        return self.latest.outcome is RuleOutcome.FIRED

    @property
    def fired_periods(self) -> list[str]:
        return [o.period for o in self.outcomes if o.outcome is RuleOutcome.FIRED]

    @property
    def severity(self) -> Severity:
        return self.latest.severity or self.base_severity


class RuleEngine:
    def __init__(self, evaluator: Evaluator) -> None:
        self.evaluator = evaluator
        self.catalog: Catalog = evaluator.catalog

    def run(self, rules: list[RuleDef]) -> list[RuleResult]:
        results = [result for rule in rules if (result := self.evaluate(rule)) is not None]
        return sorted(results, key=lambda r: (not r.fired, -r.severity.rank, r.rule_id))

    def evaluate(self, rule: RuleDef) -> RuleResult | None:
        """Evaluate ``rule`` for each applicable period; ``None`` if it does not apply to this document."""
        dataset = self.evaluator.dataset
        if dataset.document.doc_type not in rule.applies_to or not rule.applies_to_sector(
            dataset.company.sector
        ):
            return None
        if rule.scope is Scope.DOCUMENT:
            periods = [self.evaluator.anchor]
        else:
            anchor = self.evaluator.anchor
            periods = sorted(
                (p for p in dataset.periods if p.kind in rule.period_kinds),
                key=lambda p: (p.end_date, p == anchor),  # the anchor sorts last among same-date periods
            )
        if not periods:
            return None
        return RuleResult(
            rule_id=rule.id,
            version=rule.version,
            pack=rule.pack,
            title=rule.title,
            category=rule.category,
            base_severity=rule.severity,
            rationale=" ".join(rule.rationale.split()),
            kb=rule.kb,
            questions=list(rule.questions),
            outcomes=[self._evaluate_period(rule, period) for period in periods],
        )

    def _evaluate_period(self, rule: RuleDef, period: Period) -> PeriodOutcome:
        condition = self.evaluator.evaluate(rule.condition, period)
        evidence = [self._evidence(rule, spec, period) for spec in rule.evidence]
        base = {
            "period": period.label,
            "evidence": evidence,
            "inputs": sorted(condition.inputs),
            "notes": list(condition.notes),
        }
        if not condition.available:
            return PeriodOutcome(
                outcome=RuleOutcome.INSUFFICIENT_DATA, missing=sorted(condition.missing), **base
            )
        if not isinstance(condition.value, bool):
            raise EvaluationError(f"rule {rule.id}: condition did not produce true/false")
        if not condition.value:
            return PeriodOutcome(outcome=RuleOutcome.PASSED, **base)

        severity = rule.severity
        for escalation in rule.escalations:
            check = self.evaluator.evaluate(rule.compiled(escalation.when), period)
            if check.value is True and escalation.severity.rank > severity.rank:
                severity = escalation.severity
        return PeriodOutcome(outcome=RuleOutcome.FIRED, severity=severity, **base)

    def _evidence(self, rule: RuleDef, spec: EvidenceSpec, period: Period) -> EvidenceValue:
        expression = rule.compiled(spec.expr)
        result = self.evaluator.evaluate(expression, period)
        unit = spec.unit or infer_unit(expression, self.catalog)
        name = expression.bare_name
        value = result.value if not isinstance(result.value, bool) else str(result.value).lower()
        return EvidenceValue(
            ref=self.ref_for(expression, period),
            label=spec.label or (self.catalog.label_of(name) if name else expression.source),
            value=value,
            unit=unit,
            display=format_value(result.value, unit),
            notes=list(result.notes),
        )

    def ref_for(self, expression: Expression, period: Period) -> str:
        name = expression.bare_name
        if name in self.catalog.items:
            document_scoped = self.catalog.items[name].scope is Scope.DOCUMENT
            return fact_ref(name, None if document_scoped else period.label)
        if name in self.catalog.metrics:
            document_scoped = self.catalog.metrics[name].scope is Scope.DOCUMENT
            return metric_ref(name, None if document_scoped else period.label)
        return f"x:{expression.source}@{period.label}"


def infer_unit(expression: Expression, catalog: Catalog) -> Unit | None:
    def walk(node: ast.expr) -> Unit | None:
        match node:
            case ast.Name(id=name):
                return catalog.unit_of(name)
            case ast.Call(func=ast.Name(id=function), args=args):
                if FUNCTIONS[function].unit is not None:
                    return FUNCTIONS[function].unit
                if function in _UNIT_PRESERVING and args:
                    return walk(args[0])
            case ast.UnaryOp(operand=operand):
                return walk(operand)
            case ast.BinOp(op=ast.Add() | ast.Sub(), left=left, right=right):
                left_unit = walk(left)
                return left_unit if left_unit == walk(right) else None
        return None

    return walk(expression.tree)
