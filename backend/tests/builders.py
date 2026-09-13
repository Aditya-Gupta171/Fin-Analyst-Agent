"""Helpers to build datasets from compact, readable tables in tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.domain.financials import Fact, FinancialDataset

FIXTURES = Path(__file__).parent / "fixtures"

DEFAULT_COMPANY = {"name": "Test Co Ltd", "sector": "manufacturing"}
DEFAULT_DOCUMENT = {"doc_type": "annual_report", "basis": "consolidated", "title": "Test filing"}


def build_dataset(
    facts: dict[str, dict[str, Any]],
    document_facts: dict[str, Any] | None = None,
    *,
    company: dict[str, Any] | None = None,
    document: dict[str, Any] | None = None,
) -> FinancialDataset:
    """``facts`` maps line item -> {period label -> value}; ``document_facts`` maps line item -> value."""
    rows = [
        make_fact(key, period, value)
        for key, by_period in facts.items()
        for period, value in by_period.items()
    ]
    rows += [make_fact(key, None, value) for key, value in (document_facts or {}).items()]
    return FinancialDataset(
        company={**DEFAULT_COMPANY, **(company or {})},
        document={**DEFAULT_DOCUMENT, **(document or {})},
        facts=rows,
    )


def load_fixture(name: str) -> FinancialDataset:
    raw = yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))
    return build_dataset(
        raw["facts"], raw.get("document_facts"), company=raw["company"], document=raw["document"]
    )


def make_fact(key: str, period: str | None, value: Any) -> Fact:
    if isinstance(value, str):
        return Fact(key=key, period=period, text=value)
    return Fact(key=key, period=period, value=Decimal(str(value)))
