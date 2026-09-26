"""A rule that cannot be evaluated is skipped and reported, instead of failing the whole analysis."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.engine.catalog import Catalog
from app.engine.pipeline import run_engine
from app.paths import RULES_DIR
from tests.builders import load_fixture

BROKEN_PACK = """rules:
  - id: TEST_NOT_A_CONDITION
    title: Condition that is a number, not true/false
    category: test
    severity: low
    when: revenue_from_operations
    evidence: [revenue_from_operations]
    rationale: Loads (the catalog does not type-check conditions) but cannot be evaluated.
"""


def test_a_rule_that_errors_is_reported_and_the_rest_still_run(tmp_path: Path, catalog: Catalog) -> None:
    rules_dir = tmp_path / "rules"
    shutil.copytree(RULES_DIR, rules_dir)
    (rules_dir / "packs" / "learned").mkdir(parents=True, exist_ok=True)
    (rules_dir / "packs" / "learned" / "broken.yaml").write_text(BROKEN_PACK, encoding="utf-8")
    dataset = load_fixture("annual_report_manufacturing")

    result = run_engine(dataset, Catalog.load(rules_dir))

    assert "TEST_NOT_A_CONDITION" not in {rule.rule_id for rule in result.rules}
    assert any(issue.ref == "rule:TEST_NOT_A_CONDITION" for issue in result.dataset_issues)
    # every other rule still ran
    assert {r.rule_id for r in result.rules} == {r.rule_id for r in run_engine(dataset, catalog).rules}
