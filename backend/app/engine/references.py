"""Numbers by reference.

The agent never writes a figure. It writes a reference such as ``{{m:dso@FY25}}`` and the server substitutes
the engine's formatted value. A reference that does not exist is reported rather than rendered, so a
hallucinated number cannot reach an analysis.

Reference forms: ``f:<line item>@<period>`` (reported fact), ``m:<metric>@<period>`` (computed metric),
``x:<expression>@<period>`` (rule evidence); the ``@<period>`` part is omitted for document-level values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REFERENCE = re.compile(r"\{\{\s*([fmx]:[^{}]+?)\s*\}\}")


@dataclass(frozen=True, slots=True)
class Rendered:
    text: str
    cited: tuple[str, ...]  # references that resolved, in order of first use
    unknown: tuple[str, ...]  # references that do not exist in the registry

    @property
    def valid(self) -> bool:
        return not self.unknown


def find_references(text: str) -> list[str]:
    return [match.group(1) for match in REFERENCE.finditer(text)]


_UNIT_AFTER = re.compile(r"\{\{\s*([fmx]:[^{}]+?)\s*\}\}(\s?(?:days?\b|x\b|%|crores?\b|cr\b))?")


def render(text: str, registry: dict[str, str]) -> Rendered:
    cited: dict[str, None] = {}
    unknown: dict[str, None] = {}

    def substitute(match: re.Match[str]) -> str:
        ref, unit = match.group(1), match.group(2) or ""
        if ref not in registry:
            unknown[ref] = None
            return match.group(0)
        cited[ref] = None
        display = registry[ref]
        # The display already carries its unit ("98 days", "0.30x"), so a unit written after the citation
        # repeats.
        if unit and _same_unit(display, unit):
            unit = ""
        return display + unit

    rendered = _UNIT_AFTER.sub(substitute, text)
    for ref in cited:  # a figure written out right after its own citation ("{{m:ebitda@FY25}} ₹237.00 cr")
        display = registry[ref]
        rendered = re.sub(f"{re.escape(display)}\\s?{re.escape(display)}", display, rendered)
    return Rendered(rendered, tuple(cited), tuple(unknown))


def _same_unit(display: str, unit: str) -> bool:
    unit = unit.strip().lower()
    shown = display.lower()
    return (
        (unit.startswith("day") and shown.endswith("days"))
        or (unit == "x" and shown.endswith("x"))
        or (unit == "%" and shown.endswith("%"))
        or (unit.startswith("cr") and shown.endswith(" cr"))
    )


def bare_figures(text: str) -> list[str]:
    """Figures (amounts, percentages, multiples, day counts) written in prose outside references.

    Used to reject agent output that states numbers directly instead of citing them. Period labels (FY25),
    regulation numbers (Regulation 33) and section references are allowed.
    """
    return [" ".join(match.group(0).split()) for match in BARE_FIGURE.finditer(REFERENCE.sub("", text))]


def contains_bare_figures(text: str) -> bool:
    return bool(bare_figures(text))


BARE_FIGURE = re.compile(
    r"[-−]?₹\s?-?\d[\d,]*(?:\.\d+)?(?:\s?(?:cr\b|crores?|lakhs?))?"
    r"|[-−]?\d[\d,]*(?:\.\d+)?\s?(?:%|x\b|cr\b|crores?|lakhs?|days\b|bps\b)"
    r"|\d+\.\d+",
    re.IGNORECASE,
)
