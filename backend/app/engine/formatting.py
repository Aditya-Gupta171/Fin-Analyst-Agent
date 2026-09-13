"""Human-readable rendering of values in Indian conventions (₹ crore, lakh-crore digit grouping)."""

from decimal import ROUND_HALF_UP, Decimal

from app.domain.enums import Unit


def group_indian(number: Decimal, decimals: int = 2) -> str:
    """``1234567.891`` -> ``12,34,567.89``: the last three digits, then pairs."""
    quantum = Decimal(1).scaleb(-decimals)
    rounded = number.quantize(quantum, rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    integer, _, fraction = f"{abs(rounded):f}".partition(".")
    if len(integer) > 3:
        head, tail = integer[:-3], integer[-3:]
        pairs = [head[max(i - 2, 0) : i] for i in range(len(head), 0, -2)][::-1]
        integer = ",".join([*pairs, tail])
    return f"{sign}{integer}.{fraction}" if fraction else f"{sign}{integer}"


def format_value(value: Decimal | str | bool | None, unit: Unit | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    match unit:
        case Unit.INR_CRORE:
            return f"{'-' if value < 0 else ''}₹{group_indian(abs(value))} cr"
        case Unit.INR:
            return f"{'-' if value < 0 else ''}₹{group_indian(abs(value))}"
        case Unit.RATIO:
            return f"{group_indian(value * 100, 1)}%"
        case Unit.TIMES:
            return f"{group_indian(value)}x"
        case Unit.DAYS:
            return f"{group_indian(value, 0)} days"
        case Unit.COUNT:
            return group_indian(value, 0)
        case _:
            return group_indian(value, 4 if abs(value) < 1 else 2)
