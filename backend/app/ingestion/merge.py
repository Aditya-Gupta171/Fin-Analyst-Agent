"""Combine several filings of one company into a single dataset.

A Regulation 33 XBRL filing carries only the current quarter, the year to date and one balance-sheet date, so
year-on-year and sequential analysis needs earlier filings alongside it. Merging is also an analytical check:
when a later filing reports a different figure for a period an earlier filing already covered, the number was
restated, and that is surfaced rather than silently overwritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app.domain.financials import Fact, FinancialDataset
from app.ingestion.models import IngestionIssue, IngestionResult

RESTATEMENT_TOLERANCE = Decimal("0.005")  # relative change treated as rounding rather than restatement


class MergeError(ValueError):
    """The filings cannot be combined (different companies, bases or fiscal calendars)."""


def merge_results(results: Sequence[IngestionResult]) -> IngestionResult:
    if not results:
        raise MergeError("nothing to merge")
    ordered = sorted(results, key=_chronology)
    latest = ordered[-1].dataset
    _check_compatible([result.dataset for result in ordered])

    facts: dict[tuple[str, str | None], tuple[Fact, FinancialDataset]] = {}
    issues: list[IngestionIssue] = []
    for result in ordered:
        issues.extend(result.issues)
        for fact in result.dataset.facts:
            identity = (fact.key, fact.period)
            earlier = facts.get(identity)
            if earlier is not None and _restated(earlier[0], fact):
                before, after = _filed(earlier[1]), _filed(result.dataset)
                issues.append(
                    IngestionIssue(
                        level="warning",
                        ref=fact.ref,
                        message=f"restated: {earlier[0].payload} ({before}) became {fact.payload} ({after})",
                    )
                )
            facts[identity] = (fact, result.dataset)

    issues.append(IngestionIssue(level="info", message=f"merged {len(ordered)} filings"))
    dataset = FinancialDataset(
        company=latest.company,
        document=latest.document,
        facts=[fact for fact, _ in facts.values()],
    )
    return IngestionResult(
        dataset=dataset,
        issues=issues,
        unmapped=sorted({name for result in ordered for name in result.unmapped}),
    )


def _chronology(result: IngestionResult) -> tuple[date, date]:
    dataset = result.dataset
    latest_period = max((p.end_date for p in dataset.periods), default=date.min)
    return (dataset.document.filing_date or latest_period, latest_period)


def _filed(dataset: FinancialDataset) -> str:
    filing_date = dataset.document.filing_date
    return f"filed {filing_date}" if filing_date else "filing date unknown"


def _check_compatible(datasets: Sequence[FinancialDataset]) -> None:
    first = datasets[0]
    for other in datasets[1:]:
        if not _same_company(first, other):
            raise MergeError(
                f"filings belong to different companies: {first.company.name} / {other.company.name}"
            )
        if other.document.basis != first.document.basis:
            raise MergeError("cannot merge standalone and consolidated figures")
        if other.document.fiscal_year_end_month != first.document.fiscal_year_end_month:
            raise MergeError("filings use different fiscal year ends")


def _same_company(a: FinancialDataset, b: FinancialDataset) -> bool:
    for attribute in ("isin", "bse_code", "nse_symbol"):
        left, right = getattr(a.company, attribute), getattr(b.company, attribute)
        if left and right:
            return left == right
    return a.company.name.casefold() == b.company.name.casefold()


def _restated(before: Fact, after: Fact) -> bool:
    if before.value is None or after.value is None:
        return before.text != after.text
    scale = max(abs(before.value), abs(after.value))
    return bool(scale) and abs(before.value - after.value) / scale > RESTATEMENT_TOLERANCE
