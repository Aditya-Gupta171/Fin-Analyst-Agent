"""Reading amounts, units and dates as they are printed in Indian financial statements."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

NIL_MARKERS = frozenset({"-", "–", "—", "nil", "Nil", "NIL"})

_AMOUNT = re.compile(r"^\(?-?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?\)?$|^\(?-?\d+(?:\.\d+)?\)?$")

# Crore per unit of the stated currency denomination.
_SCALES: tuple[tuple[re.Pattern[str], Decimal], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), Decimal(scale))
    for pattern, scale in (
        (r"\b(crores?|cr\.?)\b", "1"),
        (r"\b(lakhs?|lacs?)\b", "0.01"),
        (r"\b(millions?|mn)\b", "0.1"),
        (r"\b(billions?|bn)\b", "100"),
        (r"\b(thousands?|'000)\b", "0.0001"),
    )
)
_CURRENCY_CONTEXT = re.compile(r"(₹|\bINR\b|\bRs\.?|\brupees?\b|amounts?\s+(are\s+)?in)", re.IGNORECASE)

_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = r"(?P<month>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
DATE_PATTERNS = (
    re.compile(
        rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[\s\-]+{_MONTH}[\s,\-]+(?P<year>\d{{4}})\b", re.IGNORECASE
    ),
    re.compile(rf"\b{_MONTH}\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})\b", re.IGNORECASE),
    re.compile(r"\b(?P<day>\d{1,2})[./-](?P<month_number>\d{1,2})[./-](?P<year>\d{4})\b"),
)


def is_amount_token(token: str) -> bool:
    return token in NIL_MARKERS or bool(_AMOUNT.match(token))


def parse_amount(token: str) -> Decimal | None:
    """``(1,602.15)`` -> -1602.15, ``-`` -> 0; ``None`` when the text is not an amount."""
    text = token.strip().replace(" ", "")
    if text in NIL_MARKERS:
        return Decimal(0)
    if not _AMOUNT.match(text):
        return None
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    digits = text.strip("()-").replace(",", "")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def detect_scale(text: str) -> Decimal | None:
    """Crore per unit for statements "in ₹ million", "Rs. in lakhs", "INR crore" and similar headers."""
    for line in text.splitlines():
        if not _CURRENCY_CONTEXT.search(line):
            continue
        for pattern, scale in _SCALES:
            if pattern.search(line):
                return scale
    return None


def find_dates(text: str) -> list[tuple[date, int, int]]:
    """Every date in ``text`` with its character span."""
    found: list[tuple[date, int, int]] = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for _, start, end in found):
                continue
            groups = match.groupdict()
            month = _MONTHS[groups["month"].lower()] if groups.get("month") else int(groups["month_number"])
            try:
                found.append(
                    (date(int(groups["year"]), month, int(groups["day"])), match.start(), match.end())
                )
            except ValueError:
                continue
    return sorted(found, key=lambda item: item[1])
