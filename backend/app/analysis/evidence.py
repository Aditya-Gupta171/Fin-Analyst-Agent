"""Everything a finding may cite, with labels and sources, and the checks agent-written prose must pass."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal

from app.analysis.report import EvidenceItem
from app.engine.catalog import Catalog
from app.engine.pipeline import EngineResult
from app.engine.references import BARE_FIGURE, REFERENCE, bare_figures, find_references, render


class EvidenceIndex:
    def __init__(self, result: EngineResult, catalog: Catalog) -> None:
        self.items: dict[str, EvidenceItem] = {}
        for fact in result.facts:
            self.items[fact.ref] = EvidenceItem(
                ref=fact.ref,
                label=fact.label,
                display=fact.display,
                period=fact.period,
                unit=fact.unit,
                page=fact.source.page,
                source=fact.source.raw_label,
            )
        for metric in result.metrics:
            self.items[metric.ref] = EvidenceItem(
                ref=metric.ref,
                label=metric.name,
                display=metric.display,
                period=metric.period,
                unit=metric.unit,
            )
        for rule in result.rules:
            for outcome in rule.outcomes:
                for evidence in outcome.evidence:
                    if evidence.value is not None and evidence.ref not in self.items:
                        self.items[evidence.ref] = EvidenceItem(
                            ref=evidence.ref,
                            label=evidence.label,
                            display=evidence.display,
                            period=outcome.period,
                            unit=evidence.unit,
                        )
        self.registry = {ref: item.display for ref, item in self.items.items()}
        self._by_figure: dict[tuple[str, Decimal], list[str]] | None = None

    def __contains__(self, ref: str) -> bool:
        return ref in self.items

    def resolve(self, refs: Iterable[str]) -> list[EvidenceItem]:
        seen: dict[str, EvidenceItem] = {}
        for ref in refs:
            if ref in self.items and ref not in seen:
                seen[ref] = self.items[ref]
        return list(seen.values())

    def render(self, template: str) -> str:
        return render(template, self.registry).text

    def link_figures(self, text: str, skip: set[str], preferred: Iterable[str] = ()) -> str:
        """Replace figures written in prose with the reference whose value they state, when exactly one does.

        A model sometimes copies "19.2%" instead of writing {{m:ebitda_margin@FY25}}. If that figure matches
        the displayed value of exactly one reference (among ``preferred`` first, then all evidence), it is
        replaced by the reference, so the rendered text is unchanged and the number is verified. Ambiguous or
        unmatched figures are left for the guardrail to reject. Figures in ``skip`` (quoted regulatory
        thresholds) are left alone.
        """
        if self._by_figure is None:
            self._by_figure = {}
            for ref, item in self.items.items():
                if (key := _figure_key(item.display)) is not None:
                    self._by_figure.setdefault(key, []).append(ref)
        preferred_refs = set(preferred)

        def replace(match: re.Match[str]) -> str:
            figure = match.group(0)
            key = _figure_key(figure)
            if key is None or _normalise(figure) in skip:
                return figure
            candidates = self._by_figure.get(key, [])
            narrowed = [ref for ref in candidates if ref in preferred_refs] or candidates
            return f"{{{{{narrowed[0]}}}}}" if len(narrowed) == 1 else figure

        parts = REFERENCE.split(text)
        # REFERENCE has one capturing group, so odd positions are reference bodies to keep as they are.
        return "".join(
            f"{{{{{part}}}}}" if i % 2 else BARE_FIGURE.sub(replace, part) for i, part in enumerate(parts)
        )

    def describe(self, ref: str) -> str:
        item = self.items[ref]
        period = f" {item.period}" if item.period else ""
        return f"[{ref}] {item.label}{period} = {item.display}"


def check_prose(text: str, index: EvidenceIndex, allowed_figures: set[str], field: str) -> list[str]:
    """Problems with agent-written prose: citations that do not exist, or figures written without a citation.

    ``allowed_figures`` holds figures that appear in the knowledge or rule text the agent was given, such as a
    regulatory threshold ("25%"), which may be quoted without a citation.
    """
    problems = [
        f"{field}: unknown reference {{{{{ref}}}}}" for ref in find_references(text) if ref not in index
    ]
    problems += [
        f"{field}: '{{{{{token}}}}}' is not an evidence reference; cite knowledge chunks in knowledge_refs,"
        " not in text"
        for token in _BRACES.findall(text)
        if not token.strip().startswith(("f:", "m:", "x:"))
    ]
    for figure in bare_figures(text):
        if _normalise(figure) not in allowed_figures:
            problems.append(
                f"{field}: the figure '{figure}' is written out; cite it as {{{{ref}}}} from the evidence"
                " instead"
            )
    return problems


def figures_in(texts: Iterable[str]) -> set[str]:
    return {_normalise(figure) for text in texts for figure in bare_figures(text)}


def _normalise(figure: str) -> str:
    return figure.lower().replace(" ", "")


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_BRACES = re.compile(r"\{\{([^{}]*)\}\}")


def _figure_key(text: str) -> tuple[str, Decimal] | None:
    """(kind, value) of a displayed or written figure: "-₹80.00 cr" and "₹-80 crore" both give
    ("inr", -80)."""
    lowered = text.lower()
    match = _NUMBER.search(lowered)
    if match is None or "lakh" in lowered:  # lakh amounts are on another scale from the crore values shown
        return None
    if "%" in lowered:
        kind = "percent"
    elif "day" in lowered:
        kind = "days"
    elif "₹" in lowered or re.search(r"\bcr\b|crore", lowered):
        kind = "inr"
    elif lowered.rstrip().endswith("x"):
        kind = "times"
    else:
        return None
    value = Decimal(match.group(0).replace(",", ""))
    negative = lowered.lstrip().startswith(("-", "(", "−")) or "-₹" in lowered or "₹-" in lowered
    return kind, -value if negative else value
