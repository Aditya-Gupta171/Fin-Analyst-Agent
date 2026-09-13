"""Normalise presentation differences between companies using the arithmetic identities of the statements.

Companies present the same numbers differently. Two cases seen in real offer documents:

* "Total expenses" that exclude finance costs and depreciation, which are shown below an EBITDA subtotal;
* exceptional losses printed as positive amounts in an expenses-style line.

Both are detected from the statement's own arithmetic, corrected, and reported, so downstream metrics see the
Schedule III definitions the taxonomy assumes.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from app.ingestion.models import IngestionIssue

Amounts = dict[tuple[str, str], Decimal]  # (line item, period label) -> amount


def reconcile_profit_and_loss(amounts: Amounts, issues: list[IngestionIssue]) -> None:
    by_period: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for (key, period), value in amounts.items():
        by_period[period][key] = value

    for period, values in by_period.items():
        before = values.get("profit_before_exceptional_items")
        exceptional = values.get("exceptional_items")
        pbt = values.get("profit_before_tax")

        if (
            before is not None
            and exceptional
            and pbt is not None
            and _close(before - exceptional, pbt)
            and not _close(before + exceptional, pbt)
        ):
            amounts[("exceptional_items", period)] = -exceptional
            exceptional = -exceptional
            issues.append(
                IngestionIssue(
                    level="info",
                    ref=f"f:exceptional_items@{period}",
                    message="exceptional items printed with the opposite sign; reversed to reconcile PBT",
                )
            )

        tax, pat = values.get("total_tax_expense"), values.get("profit_after_tax")
        if tax is None and ("current_tax" in values or "deferred_tax" in values):
            tax = values.get("current_tax", Decimal(0)) + values.get("deferred_tax", Decimal(0))
        if (
            pbt is not None
            and pat is not None
            and tax
            and _close(pbt + tax, pat)
            and not _close(pbt - tax, pat)
        ):
            for key in ("total_tax_expense", "current_tax", "deferred_tax"):
                if (key, period) in amounts:
                    amounts[(key, period)] = -amounts[(key, period)]
            issues.append(
                IngestionIssue(
                    level="info",
                    ref=f"f:total_tax_expense@{period}",
                    message="tax presented as '(expense) / credit'; signs reversed so a credit is negative",
                )
            )

        income, expenses = values.get("total_income"), values.get("total_expenses")
        target = (
            before if before is not None else (pbt - exceptional if pbt is not None and exceptional else pbt)
        )
        if income is None or expenses is None or target is None or _close(income - expenses, target):
            continue
        finance, depreciation = values.get("finance_costs"), values.get("depreciation_amortisation")
        if (
            finance is not None
            and depreciation is not None
            and _close(income - expenses - finance - depreciation, target)
        ):
            amounts[("total_expenses", period)] = expenses + finance + depreciation
            issues.append(
                IngestionIssue(
                    level="info",
                    ref=f"f:total_expenses@{period}",
                    message="total expenses excluded finance costs and depreciation; both added back",
                )
            )


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(abs(a), abs(b), Decimal(1)) * Decimal("0.002")
