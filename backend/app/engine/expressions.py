"""Parsing and static validation for the metric / rule expression language.

Metrics and rules are written as small Python-syntax expressions, for example::

    growth(trade_receivables) - growth(revenue_from_operations) > 0.20 and dso > cohort_pct(dso, 90, 120)

Only a whitelisted subset of Python's grammar is accepted and nothing is ever passed to ``eval``: the parsed
tree is interpreted by :mod:`app.engine.evaluator`. Names refer to taxonomy line items or other metrics.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal

from app.domain.enums import Unit


class ExpressionError(ValueError):
    """The expression is malformed or uses something the language does not allow."""


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    name: str
    min_args: int
    max_args: int
    summary: str
    name_args: tuple[int, ...] = ()  # positions that must be a bare metric / line-item name
    constant_args: tuple[int, ...] = ()  # positions that must be a numeric literal
    unit: Unit | None = None  # unit of the result when it does not follow from the arguments


FUNCTIONS: dict[str, FunctionSpec] = {
    spec.name: spec
    for spec in (
        FunctionSpec("prior", 1, 1, "Value for the same period one fiscal year earlier."),
        FunctionSpec("seq", 1, 1, "Value for the immediately preceding period of the same length."),
        FunctionSpec("opening", 1, 1, "Value at the start of the period (the preceding balance-sheet date)."),
        FunctionSpec("growth", 1, 1, "Year-on-year change: (x - prior(x)) / |prior(x)|.", unit=Unit.RATIO),
        FunctionSpec("seq_growth", 1, 1, "Sequential change: (x - seq(x)) / |seq(x)|.", unit=Unit.RATIO),
        FunctionSpec("avg", 1, 1, "Average of opening and closing balance; closing balance if no opening."),
        FunctionSpec("ttm", 1, 1, "Trailing twelve months: sum of the last four quarters."),
        FunctionSpec("trailing_sum", 2, 2, "Sum over this and the previous n-1 years.", constant_args=(1,)),
        FunctionSpec(
            "streak", 2, 2, "True if the condition holds for n consecutive periods.", constant_args=(1,)
        ),
        FunctionSpec("days", 0, 0, "Number of days in the period.", unit=Unit.DAYS),
        FunctionSpec("abs", 1, 1, "Absolute value."),
        FunctionSpec("min", 2, 8, "Smallest argument."),
        FunctionSpec("max", 2, 8, "Largest argument."),
        FunctionSpec("coalesce", 2, 8, "First argument that is available."),
        FunctionSpec("exists", 1, 1, "True if the value is available, never missing."),
        FunctionSpec("rel_diff", 2, 2, "|a - b| / max(|a|, |b|).", unit=Unit.RATIO),
        FunctionSpec(
            "cohort_pct",
            3,
            3,
            "p-th percentile of a metric across the sector x size cohort; the fallback is used until the "
            "cohort is large enough.",
            name_args=(0,),
            constant_args=(1, 2),
        ),
        FunctionSpec(
            "cohort_rank",
            1,
            1,
            "Percentile rank (0-1) of the value within its cohort.",
            (0,),
            unit=Unit.RATIO,
        ),
        FunctionSpec(
            "history_z", 1, 1, "Robust z-score of the value against the company's own history.", (0,)
        ),
    )
}

_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_COMPARE = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.In, ast.NotIn)
_UNARY = (ast.Not, ast.USub, ast.UAdd)


@dataclass(frozen=True, slots=True)
class Expression:
    source: str
    tree: ast.expr
    names: frozenset[str]  # every metric / line-item name referenced
    functions: frozenset[str]

    def __str__(self) -> str:
        return self.source

    @property
    def bare_name(self) -> str | None:
        """The referenced name if the whole expression is a single name."""
        return self.tree.id if isinstance(self.tree, ast.Name) else None


def parse_expression(source: str) -> Expression:
    text = " ".join(source.split())
    try:
        tree = ast.parse(text, mode="eval").body
    except SyntaxError as exc:
        raise ExpressionError(f"syntax error in {text!r}: {exc.msg}") from exc
    names: set[str] = set()
    functions: set[str] = set()
    _validate(tree, text, names, functions)
    return Expression(text, tree, frozenset(names), frozenset(functions))


def _validate(node: ast.AST, source: str, names: set[str], functions: set[str]) -> None:
    def fail(message: str) -> ExpressionError:
        return ExpressionError(f"{message} in {source!r}")

    match node:
        case ast.Constant(value=value):
            if isinstance(value, bool) or not isinstance(value, int | float | str):
                raise fail(f"unsupported literal {value!r}")
        case ast.Name(id=name):
            if name in FUNCTIONS:
                raise fail(f"function {name!r} used without calling it")
            names.add(name)
        case ast.BinOp(op=op, left=left, right=right):
            if not isinstance(op, _BINARY):
                raise fail(f"operator {type(op).__name__} is not allowed")
            _validate(left, source, names, functions)
            _validate(right, source, names, functions)
        case ast.UnaryOp(op=op, operand=operand):
            if not isinstance(op, _UNARY):
                raise fail(f"operator {type(op).__name__} is not allowed")
            _validate(operand, source, names, functions)
        case ast.BoolOp(values=values):
            for value in values:
                _validate(value, source, names, functions)
        case ast.Compare(left=left, ops=ops, comparators=comparators):
            if not all(isinstance(op, _COMPARE) for op in ops):
                raise fail("unsupported comparison")
            _validate(left, source, names, functions)
            for op, comparator in zip(ops, comparators, strict=True):
                if isinstance(op, ast.In | ast.NotIn):
                    if not isinstance(comparator, ast.List | ast.Tuple) or not all(
                        isinstance(element, ast.Constant) for element in comparator.elts
                    ):
                        raise fail("'in' requires a list of literals")
                else:
                    _validate(comparator, source, names, functions)
        case ast.Call(func=ast.Name(id=name), args=args, keywords=keywords):
            spec = FUNCTIONS.get(name)
            if spec is None:
                raise fail(f"unknown function {name!r}")
            if keywords:
                raise fail(f"{name}() does not take keyword arguments")
            if not spec.min_args <= len(args) <= spec.max_args:
                expected = (
                    spec.min_args if spec.min_args == spec.max_args else f"{spec.min_args}-{spec.max_args}"
                )
                raise fail(f"{name}() takes {expected} arguments, got {len(args)}")
            functions.add(name)
            for position, arg in enumerate(args):
                if position in spec.name_args:
                    if not isinstance(arg, ast.Name):
                        raise fail(f"argument {position + 1} of {name}() must be a metric or line-item name")
                    names.add(arg.id)
                elif position in spec.constant_args:
                    if not _is_number_literal(arg):
                        raise fail(f"argument {position + 1} of {name}() must be a number")
                else:
                    _validate(arg, source, names, functions)
        case _:
            raise fail(f"unsupported syntax {type(node).__name__}")


def _is_number_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        node = node.operand
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    )


def literal_number(node: ast.expr) -> Decimal:
    """Value of a numeric literal accepted by :func:`_is_number_literal`."""
    if isinstance(node, ast.UnaryOp):
        return -literal_number(node.operand)
    assert isinstance(node, ast.Constant)
    return Decimal(repr(node.value))
