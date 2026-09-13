"""Reconstruct statement tables from word positions on a PDF page.

Plain text extraction is not reliable for Indian filings: character spacing splits numbers ("1 5,647.23" for
15,647.23, "3 78.60" for 378.60) and the split is ambiguous without positions, while period headers often wrap
over several lines ("For the six months period ended September" / "30, 2025"). Amounts in financial statements
are right-aligned, so this module finds the value columns from the right edges of the numbers in the data
rows, reads each column's header top to bottom to find its period, and assigns every number fragment to the
column it sits under.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Literal

from app.ingestion.amounts import find_dates, parse_amount

LINE_TOLERANCE = 2.5  # points: words whose tops differ by less are on the same line
EDGE_TOLERANCE = 6.0  # points: right edges closer than this belong to one column
HEADER_LINES = 12  # lines above the first data row searched for column headers

_NUMBER_FRAGMENT = re.compile(r"^[(\-–—]?[\d,.]*\d[\d,.]*\)?$|^[(]$|^[)]$|^[-–—]$")
_VALUE = re.compile(r"^\(?-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\)?$|^\(?-?\d+\.\d+\)?$")
_NOTE = re.compile(r"^\d{1,2}(?:\.\d{1,2})*[A-Z]?(?:\([a-z]\))?$")
_FOOTER = re.compile(
    r"^(the above (statement|annexure)|as per our report|for and on behalf|in terms of our report)",
    re.IGNORECASE,
)
_ENUMERATED = re.compile(r"^\(?(?:[ivxlc]+|[a-z])\)\s")
PeriodHint = Literal["quarter", "half_year", "nine_months", "year", "instant", "unknown"]


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Line:
    top: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x0


@dataclass(frozen=True, slots=True)
class Column:
    left: float  # words whose right edge falls in (left, right] belong to the column
    right: float
    date: date
    start: date | None  # when the header states the start of the period ("from 1 April 2025 to ...")
    hint: PeriodHint
    header: str


@dataclass
class Row:
    label: str
    x0: float
    top: float
    note: str | None
    values: list[Decimal | None]  # one per column; None where the cell is empty
    raw: list[str]

    @property
    def has_values(self) -> bool:
        return any(value is not None for value in self.values)


@dataclass
class Table:
    columns: list[Column]
    rows: list[Row]
    header_text: str


def lines_from_words(words: list[Word]) -> list[Line]:
    lines: list[Line] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and abs(word.top - lines[-1].top) <= LINE_TOLERANCE:
            lines[-1].words.append(word)
        else:
            lines.append(Line(word.top, [word]))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


def find_columns(lines: list[Line]) -> tuple[list[Column], int]:
    """Value columns with their periods, and the index of the first data line (0 if no columns were found)."""
    first_values = next((i for i, line in enumerate(lines[:80]) if _value_count(line) >= 2), None)
    if first_values is None:
        return [], 0
    data_start = first_values
    while data_start > 0 and first_values - data_start < 4 and _is_row_label(lines[data_start - 1]):
        data_start -= 1  # section headers and labels whose amounts sit a few points lower

    values = [
        w for line in lines[first_values : first_values + 30] for w in line.words if _VALUE.match(w.text)
    ]
    groups = _cluster(sorted(values, key=lambda w: w.x1))
    header = sorted(
        (w for line in lines[max(0, data_start - HEADER_LINES) : data_start] for w in line.words),
        key=lambda w: (w.top, w.x0),
    )

    columns: list[Column] = []
    previous_right = None
    for group in groups:
        right = median(w.x1 for w in group)
        width = max(w.x1 - w.x0 for w in group)
        # Values are assigned within a tight band; the header above a column is centred and may be much wider.
        left = right - width - 12 if previous_right is None else max(previous_right + 2, right - width - 30)
        header_left = right - width - 45 if previous_right is None else previous_right + 2
        text = " ".join(w.text for w in header if header_left < w.center <= right + 12)
        dates = find_dates(text)
        if not dates:
            continue  # a note-reference column or stray numbers, not a period
        end = dates[-1][0]
        start = dates[0][0] if len(dates) > 1 and dates[0][0] < end else None
        columns.append(Column(left, right + EDGE_TOLERANCE, end, start, _hint(text.lower()), text))
        previous_right = right
    return columns, data_start if columns else 0


def build_table(lines: list[Line], columns: list[Column] | None = None) -> Table:
    start = 0
    if columns is None:
        columns, start = find_columns(lines)
    header_text = " ".join(line.text for line in lines[:start])
    if not columns:
        return Table([], [], header_text)

    label_limit = columns[0].left
    rows: list[Row] = []
    for line in lines[start:]:
        if _FOOTER.match(line.text):
            break
        label_words: list[str] = []
        note = None
        cells: list[list[Word]] = [[] for _ in columns]
        for word in line.words:
            index = next((i for i, c in enumerate(columns) if c.left < word.x1 <= c.right), None)
            if index is not None and _NUMBER_FRAGMENT.match(word.text):
                cells[index].append(word)
            elif word.x1 <= label_limit + 2:
                if _NOTE.match(word.text) and label_words and word.x0 > line.words[0].x0 + 120:
                    note = word.text
                else:
                    label_words.append(word.text)
        raw = ["".join(w.text for w in cell) for cell in cells]
        values = [parse_amount(text) if text else None for text in raw]
        label = " ".join(label_words)
        if not label and not any(raw):
            continue
        row = Row(label, line.x0, line.top, note, values, raw)
        if not row.has_values and rows and _continues(label):
            rows[-1].label = f"{rows[-1].label} {label}"
            continue
        if row.has_values and not label and rows and not rows[-1].has_values:
            rows[-1].values, rows[-1].raw, rows[-1].note = values, raw, note or rows[-1].note
            continue
        rows.append(row)
    return Table(columns, rows, header_text)


def _cluster(words: list[Word]) -> list[list[Word]]:
    """Group value words by right edge; keep groups with enough rows to be a real column."""
    groups: list[list[Word]] = []
    for word in words:
        if groups and word.x1 - groups[-1][-1].x1 <= EDGE_TOLERANCE:
            groups[-1].append(word)
        else:
            groups.append([word])
    return [group for group in groups if len(group) >= 3]


def _value_count(line: Line) -> int:
    return sum(1 for word in line.words if _VALUE.match(word.text))


_HEADER_WORDS = re.compile(
    r"\b(as at|as on|for the|ended|particulars|notes?|(?:19|20)\d{2}|"
    r"jan(?:uary)?|feb(?:ruary)?|march|april|may|june|july|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)


def _is_row_label(line: Line) -> bool:
    """A line of the table body rather than of the column headers (which carry dates, months, "as at")."""
    return not _HEADER_WORDS.search(line.text)


def _hint(text: str) -> PeriodHint:
    if re.search(r"\b(three months|3 months|quarter)\b", text):
        return "quarter"
    if re.search(r"\b(six months|6 months|half year)\b", text):
        return "half_year"
    if re.search(r"\b(nine months|9 months)\b", text):
        return "nine_months"
    if re.search(r"\byear\b", text):
        return "year"
    if re.search(r"\bas (at|on)\b", text):
        return "instant"
    return "unknown"


def _continues(label: str) -> bool:
    """A wrapped label line: starts in lower case, or with an opening bracket followed by lower case.

    Enumerated items such as "(g) Financial assets" or "(iii) Trade payables" start new rows instead.
    """
    if _ENUMERATED.match(label):
        return False
    stripped = label.lstrip("(")
    return bool(stripped) and stripped[0].islower()
