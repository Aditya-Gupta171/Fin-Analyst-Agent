"""Interpreter for catalog expressions over a :class:`FinancialDataset`.

Three properties matter more than anything else here:

* **Missing data is not false.** Referencing a value the filing does not report raises :class:`MissingData`
  instead of producing a default. Boolean operators use Kleene logic, so ``x > 1 and y > 2`` is still
  ``False`` when ``x`` is ``0`` and ``y`` is unavailable, but "unknown" when ``x`` is ``5``. A rule therefore
  either fires, passes, or reports exactly which inputs it lacked.
* **Every number is traceable.** Each evaluation records the facts and metrics it touched.
* **Decimal arithmetic only.** No binary floating point anywhere near reported figures.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from app.domain.enums import Nature, Scope, SizeBucket, Statement
from app.domain.financials import FinancialDataset, fact_ref
from app.domain.periods import Period, PeriodKind
from app.engine import statistics
from app.engine.baselines import BaselineProvider, Cohort, Distribution, NoBaselines
from app.engine.catalog import Catalog
from app.engine.expressions import Expression, literal_number

Value = Decimal | str | bool

MIN_HISTORY = 3  # prior periods needed before a history z-score means anything


class MissingData(Exception):
    """One or more inputs are unavailable; ``reasons`` lists them (fact refs or short explanations)."""

    def __init__(self, reasons: Iterable[str]) -> None:
        self.reasons = frozenset(reasons)
        super().__init__(", ".join(sorted(self.reasons)))


class EvaluationError(Exception):
    """The expression cannot be evaluated for a reason other than missing data (e.g. a type mismatch)."""


@dataclass
class Trace:
    inputs: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)


@dataclass(frozen=True, slots=True)
class Evaluation:
    value: Value | None
    missing: frozenset[str]
    inputs: frozenset[str]
    notes: tuple[str, ...]

    @property
    def available(self) -> bool:
        return not self.missing


@dataclass(frozen=True, slots=True)
class MetricValue:
    key: str
    period: str | None
    value: Decimal
    inputs: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def ref(self) -> str:
        return metric_ref(self.key, self.period)


def metric_ref(key: str, period: str | None) -> str:
    return f"m:{key}@{period}" if period else f"m:{key}"


class Evaluator:
    def __init__(
        self,
        dataset: FinancialDataset,
        catalog: Catalog,
        *,
        size: SizeBucket | None = None,
        baselines: BaselineProvider | None = None,
    ) -> None:
        self.dataset = dataset
        self.catalog = catalog
        self.size = size
        self.baselines = baselines or NoBaselines()
        self.anchor = anchor_period(dataset)
        self._metrics: dict[tuple[str, str | None], MetricValue | MissingData] = {}
        self._reported = _reported_statements(dataset, catalog)
        self._traces: list[Trace] = []
        self._functions: dict[str, Callable[[Sequence[ast.expr], Period], Value]] = {
            "prior": lambda args, p: self._eval(args[0], p.prior_comparable()),
            "seq": lambda args, p: self._eval(args[0], p.previous_sequential()),
            "opening": lambda args, p: self._eval(args[0], preceding_period(p)),
            "growth": lambda args, p: self._change(args[0], p, p.prior_comparable()),
            "seq_growth": lambda args, p: self._change(args[0], p, p.previous_sequential()),
            "avg": self._avg,
            "ttm": self._ttm,
            "trailing_sum": self._trailing_sum,
            "streak": self._streak,
            "days": lambda args, p: Decimal(p.days),
            "abs": lambda args, p: abs(self._number(args[0], p)),
            "min": lambda args, p: min(self._numbers(args, p)),
            "max": lambda args, p: max(self._numbers(args, p)),
            "coalesce": self._coalesce,
            "exists": self._exists,
            "rel_diff": self._rel_diff,
            "cohort_pct": self._cohort_pct,
            "cohort_rank": self._cohort_rank,
            "history_z": self._history_z,
        }

    # ── public API ────────────────────────────────────────────────────────────────────────────────────────

    def evaluate(self, expression: Expression, period: Period) -> Evaluation:
        trace = Trace()
        self._traces.append(trace)
        try:
            value: Value | None = self._eval(expression.tree, period)
            missing: frozenset[str] = frozenset()
        except MissingData as exc:
            value, missing = None, exc.reasons
        finally:
            self._traces.pop()
        return Evaluation(value, missing, frozenset(trace.inputs), tuple(trace.notes))

    def metric(self, key: str, period: Period) -> MetricValue:
        """Compute (or fetch from cache) a metric; raises :class:`MissingData` if it cannot be computed."""
        definition = self.catalog.metrics[key]
        if not definition.applies_to_sector(self.dataset.company.sector):
            raise MissingData({f"{key} is not meaningful for {self.dataset.company.sector} companies"})
        document_scoped = definition.scope is Scope.DOCUMENT
        at = self.anchor if document_scoped else period
        cache_key = (key, None if document_scoped else period.label)
        cached = self._metrics.get(cache_key)
        if isinstance(cached, MissingData):
            raise MissingData(cached.reasons)
        if cached is not None:
            return cached

        trace = Trace()
        self._traces.append(trace)
        try:
            value = self._eval(definition.expression.tree, at)
        except MissingData as exc:
            self._metrics[cache_key] = exc
            raise
        finally:
            self._traces.pop()
        if not isinstance(value, Decimal):
            raise EvaluationError(f"metric {key} produced a non-numeric value {value!r}")
        result = MetricValue(key, cache_key[1], value, tuple(sorted(trace.inputs)), tuple(trace.notes))
        self._metrics[cache_key] = result
        return result

    def distribution(self, name: str, period: Period) -> Distribution | None:
        if self.size is None:
            return None
        cohort = Cohort(self.dataset.company.sector, self.size, period.kind)
        return self.baselines.distribution(name, cohort)

    # ── tree walking ──────────────────────────────────────────────────────────────────────────────────────

    def _eval(self, node: ast.expr, period: Period) -> Value:
        match node:
            case ast.Constant(value=str() as text):
                return text
            case ast.Constant():
                return literal_number(node)
            case ast.Name(id=name):
                return self._resolve(name, period)
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return not self._boolean(operand, period)
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                return -self._number(operand, period)
            case ast.UnaryOp(op=ast.UAdd(), operand=operand):
                return self._number(operand, period)
            case ast.BinOp(left=left, op=op, right=right):
                a, b = self._numbers((left, right), period)
                if isinstance(op, ast.Add):
                    return a + b
                if isinstance(op, ast.Sub):
                    return a - b
                if isinstance(op, ast.Mult):
                    return a * b
                if b == 0:
                    raise MissingData({f"division by zero: {ast.unparse(right)} is 0 in {period}"})
                return a / b
            case ast.BoolOp(op=ast.And(), values=values):
                return self._kleene(values, period, short_circuit_on=False)
            case ast.BoolOp(op=ast.Or(), values=values):
                return self._kleene(values, period, short_circuit_on=True)
            case ast.Compare():
                return self._compare(node, period)
            case ast.Call(func=ast.Name(id=name), args=args):
                return self._functions[name](args, period)
        raise EvaluationError(f"unsupported node {ast.dump(node)}")

    def _resolve(self, name: str, period: Period) -> Value:
        item = self.catalog.items.get(name)
        if item is None:
            metric = self.metric(name, period)
            self._record(metric.ref, metric.notes)
            return metric.value
        if item.scope is Scope.DOCUMENT:
            fact = self.dataset.get(name)
            ref = fact_ref(name, None)
        else:
            fact = self.dataset.get(name, period)
            if fact is None and item.nature is Nature.STOCK:
                fact = self.dataset.find_at_date(name, period.end_date)
            ref = fact_ref(name, period.label)
        if fact is None:
            if item.nil_if_absent and self._statement_reported(item.statement, item.nature, period):
                self._note(f"{item.label} not reported for {period}; read as nil")
                return Decimal(0)
            raise MissingData({ref})
        self._record(fact.ref)
        return fact.payload

    def _statement_reported(self, statement: Statement, nature: Nature, period: Period) -> bool:
        marker = period.end_date if nature is Nature.STOCK else period.label
        return (statement, marker) in self._reported

    def _record(self, ref: str, notes: Iterable[str] = ()) -> None:
        if self._traces:
            trace = self._traces[-1]
            trace.inputs.add(ref)
            for message in notes:
                trace.note(message)

    def _note(self, message: str) -> None:
        if self._traces:
            self._traces[-1].note(message)

    def _gather(self, nodes: Iterable[tuple[ast.expr, Period]]) -> list[Value]:
        """Evaluate several operands, reporting every missing input rather than just the first."""
        values: list[Value] = []
        missing: set[str] = set()
        for node, period in nodes:
            try:
                values.append(self._eval(node, period))
            except MissingData as exc:
                missing |= exc.reasons
        if missing:
            raise MissingData(missing)
        return values

    def _numbers(self, nodes: Iterable[ast.expr], period: Period) -> list[Decimal]:
        values = self._gather((node, period) for node in nodes)
        return [_as_number(value) for value in values]

    def _number(self, node: ast.expr, period: Period) -> Decimal:
        return _as_number(self._eval(node, period))

    def _boolean(self, node: ast.expr, period: Period) -> bool:
        value = self._eval(node, period)
        if not isinstance(value, bool):
            raise EvaluationError(f"expected a condition, got {value!r} from {ast.unparse(node)}")
        return value

    def _kleene(self, nodes: Sequence[ast.expr], period: Period, *, short_circuit_on: bool) -> bool:
        """``and`` (short_circuit_on=False) / ``or`` (short_circuit_on=True) with an "unknown" third value."""
        missing: set[str] = set()
        for node in nodes:
            try:
                if self._boolean(node, period) is short_circuit_on:
                    return short_circuit_on
            except MissingData as exc:
                missing |= exc.reasons
        if missing:
            raise MissingData(missing)
        return not short_circuit_on

    def _compare(self, node: ast.Compare, period: Period) -> bool:
        operands = [node.left, *node.comparators]
        evaluable = [
            (operand, period)
            for index, operand in enumerate(operands)
            if not (index > 0 and isinstance(node.ops[index - 1], ast.In | ast.NotIn))
        ]
        values = iter(self._gather(evaluable))
        left = next(values)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            if isinstance(op, ast.In | ast.NotIn):
                assert isinstance(comparator, ast.List | ast.Tuple)
                options = [self._eval(element, period) for element in comparator.elts]
                holds = (left in options) is isinstance(op, ast.In)
                right = left
            else:
                right = next(values)
                holds = _compare_values(op, left, right)
            if not holds:
                return False
            left = right
        return True

    # ── functions ─────────────────────────────────────────────────────────────────────────────────────────

    def _change(self, node: ast.expr, period: Period, base: Period) -> Decimal:
        current, previous = (_as_number(v) for v in self._gather([(node, period), (node, base)]))
        if previous == 0:
            raise MissingData({f"change undefined: {ast.unparse(node)} was 0 in {base}"})
        return (current - previous) / abs(previous)

    def _avg(self, args: Sequence[ast.expr], period: Period) -> Decimal:
        closing = self._number(args[0], period)
        try:
            opening = self._number(args[0], preceding_period(period))
        except MissingData:
            self._note(f"{ast.unparse(args[0])}: no opening balance before {period}, closing balance used")
            return closing
        return (opening + closing) / 2

    def _ttm(self, args: Sequence[ast.expr], period: Period) -> Decimal:
        if period.kind is PeriodKind.YEAR:
            return self._number(args[0], period)
        if period.kind is not PeriodKind.QUARTER:
            raise MissingData({f"trailing twelve months is not defined for a {period.kind} period"})
        quarters = [period]
        for _ in range(3):
            quarters.append(quarters[-1].previous_sequential())
        return sum((_as_number(v) for v in self._gather((args[0], q) for q in quarters)), Decimal(0))

    def _trailing_sum(self, args: Sequence[ast.expr], period: Period) -> Decimal:
        count = int(literal_number(args[1]))
        periods = [period]
        for _ in range(count - 1):
            periods.append(periods[-1].prior_comparable())
        return sum((_as_number(v) for v in self._gather((args[0], p) for p in periods)), Decimal(0))

    def _streak(self, args: Sequence[ast.expr], period: Period) -> bool:
        count = int(literal_number(args[1]))
        missing: set[str] = set()
        current = period
        for _ in range(count):
            try:
                if not self._boolean(args[0], current):
                    return False
            except MissingData as exc:
                missing |= exc.reasons
            current = current.previous_sequential()
        if missing:
            raise MissingData(missing)
        return True

    def _coalesce(self, args: Sequence[ast.expr], period: Period) -> Value:
        missing: set[str] = set()
        for node in args:
            try:
                return self._eval(node, period)
            except MissingData as exc:
                missing |= exc.reasons
        raise MissingData(missing)

    def _exists(self, args: Sequence[ast.expr], period: Period) -> bool:
        try:
            self._eval(args[0], period)
        except MissingData:
            return False
        return True

    def _rel_diff(self, args: Sequence[ast.expr], period: Period) -> Decimal:
        a, b = self._numbers(args, period)
        scale = max(abs(a), abs(b))
        return abs(a - b) / scale if scale else Decimal(0)

    def _cohort_pct(self, args: Sequence[ast.expr], period: Period) -> Decimal:
        assert isinstance(args[0], ast.Name)
        name = args[0].id
        p, fallback = literal_number(args[1]), literal_number(args[2])
        distribution = self.distribution(name, period)
        if distribution is not None and distribution.reliable:
            threshold = distribution.percentile(p)
            self._note(f"{name}: cohort p{p} = {threshold:.4g} (n={distribution.n})")
            return threshold
        n = distribution.n if distribution else 0
        self._note(f"{name}: cohort baseline too small (n={n}), static threshold {fallback} used")
        return fallback

    def _cohort_rank(self, args: Sequence[ast.expr], period: Period) -> Decimal:
        assert isinstance(args[0], ast.Name)
        name = args[0].id
        value = self._number(args[0], period)
        distribution = self.distribution(name, period)
        if distribution is None or not distribution.reliable:
            n = distribution.n if distribution else 0
            raise MissingData({f"{name}: cohort baseline too small (n={n})"})
        return distribution.rank(value)

    def _history_z(self, args: Sequence[ast.expr], period: Period) -> Decimal:
        assert isinstance(args[0], ast.Name)
        value = self._number(args[0], period)
        history: list[Decimal] = []
        for earlier in self.dataset.periods:
            if earlier.kind is period.kind and earlier.end_date < period.end_date:
                try:
                    history.append(self._number(args[0], earlier))
                except MissingData:
                    continue
        if len(history) < MIN_HISTORY:
            raise MissingData({f"{args[0].id}: fewer than {MIN_HISTORY} earlier {period.kind} periods"})
        z = statistics.modified_z(value, history)
        if z is None:
            raise MissingData({f"{args[0].id}: no variation in earlier periods"})
        return z


def anchor_period(dataset: FinancialDataset) -> Period:
    """The period a document is "about": its latest full year, or its latest period if it has no full year."""
    periods = dataset.periods
    if not periods:
        raise ValueError("dataset has no period-scoped facts")
    latest = periods[-1]
    years = [p for p in periods if p.kind is PeriodKind.YEAR]
    if years and years[-1].end_date == latest.end_date:
        return years[-1]
    return min((p for p in periods if p.end_date == latest.end_date), key=lambda p: p.days)


def _reported_statements(dataset: FinancialDataset, catalog: Catalog) -> set[tuple[Statement, str | date]]:
    """Statements reported per period: keyed by period label for flows and by balance date for stocks."""
    reported: set[tuple[Statement, str | date]] = set()
    for fact in dataset.facts:
        item = catalog.items.get(fact.key)
        if item is None or fact.period is None:
            continue
        period = dataset.period(fact.period)
        reported.add((item.statement, period.end_date if item.nature is Nature.STOCK else period.label))
    return reported


def preceding_period(period: Period) -> Period:
    """A period ending the day before ``period`` starts, used to find opening balances."""
    if period.kind is PeriodKind.NINE_MONTHS:
        return replace(period, kind=PeriodKind.YEAR, fiscal_year=period.fiscal_year - 1, part=None)
    return period.previous_sequential()


def _as_number(value: Value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise EvaluationError(f"expected a number, got {value!r}")
    return value


def _compare_values(op: ast.cmpop, left: Value, right: Value) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    a, b = _as_number(left), _as_number(right)
    match op:
        case ast.Lt():
            return a < b
        case ast.LtE():
            return a <= b
        case ast.Gt():
            return a > b
        case ast.GtE():
            return a >= b
    raise EvaluationError(f"unsupported comparison {type(op).__name__}")
