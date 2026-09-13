"""Results shared by every ingestion path."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.domain.financials import FinancialDataset


class IngestionIssue(BaseModel):
    level: Literal["error", "warning", "info"]
    message: str
    ref: str | None = None  # fact reference or source element the issue concerns


class IngestionResult(BaseModel):
    dataset: FinancialDataset
    issues: list[IngestionIssue] = []
    unmapped: list[str] = []  # source items with non-zero values that no taxonomy line item captures

    @property
    def errors(self) -> list[IngestionIssue]:
        return [issue for issue in self.issues if issue.level == "error"]
