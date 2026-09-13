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


def render(text: str, registry: dict[str, str]) -> Rendered:
    cited: dict[str, None] = {}
    unknown: dict[str, None] = {}

    def substitute(match: re.Match[str]) -> str:
        ref = match.group(1)
        if ref in registry:
            cited[ref] = None
            return registry[ref]
        unknown[ref] = None
        return match.group(0)

    return Rendered(REFERENCE.sub(substitute, text), tuple(cited), tuple(unknown))


def contains_bare_figures(text: str) -> bool:
    """True if prose outside references contains figures (amounts, percentages, multiples, day counts).

    Used to reject agent output that states numbers directly instead of citing them. Period labels (FY25),
    regulation numbers (Regulation 33) and section references are allowed.
    """
    stripped = REFERENCE.sub("", text)
    return bool(_BARE_FIGURE.search(stripped))


_BARE_FIGURE = re.compile(
    r"(₹\s?\d|\d[\d,]*(?:\.\d+)?\s?(?:%|x\b|cr\b|crore|lakh|days\b|bps\b)|\d+\.\d+)",
    re.IGNORECASE,
)
