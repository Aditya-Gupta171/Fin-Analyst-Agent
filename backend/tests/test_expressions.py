import pytest

from app.engine.expressions import ExpressionError, parse_expression


def test_collects_names_and_functions() -> None:
    expression = parse_expression(
        "growth(trade_receivables) - growth(revenue) > 0.2 and dso > cohort_pct(dso, 90, 120)"
    )
    assert expression.names == {"trade_receivables", "revenue", "dso"}
    assert expression.functions == {"growth", "cohort_pct"}


def test_normalises_whitespace() -> None:
    assert parse_expression("a  +\n   b").source == "a + b"


def test_bare_name() -> None:
    assert parse_expression("dso").bare_name == "dso"
    assert parse_expression("prior(dso)").bare_name is None


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("__import__('os')", "unknown function"),
        ("x.attribute", "unsupported syntax"),
        ("x[0]", "unsupported syntax"),
        ("lambda: 1", "unsupported syntax"),
        ("x ** 2", "not allowed"),
        ("x // 2", "not allowed"),
        ("growth(x, y)", "takes 1 arguments"),
        ("growth(x=1)", "keyword"),
        ("cohort_pct(dso * 2, 90, 120)", "must be a metric or line-item name"),
        ("cohort_pct(dso, p, 120)", "must be a number"),
        ("x in y", "requires a list of literals"),
        ("growth", "without calling"),
        ("x >", "syntax error"),
        ("True", "unsupported literal"),
    ],
)
def test_rejects_unsafe_or_malformed_expressions(source: str, message: str) -> None:
    with pytest.raises(ExpressionError, match=message):
        parse_expression(source)


def test_membership_against_literal_list() -> None:
    assert parse_expression('auditor_opinion in ["qualified", "adverse"]').names == {"auditor_opinion"}
