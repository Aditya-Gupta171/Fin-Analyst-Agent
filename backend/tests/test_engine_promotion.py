"""Promoting a candidate rule writes it into a new pack file and re-validates the whole catalog still
loads — rolling the write back if it doesn't. Runs against a throwaway copy of the real ``rules/`` tree so
it never touches the actual, version-controlled directory.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.engine.catalog import Catalog
from app.engine.promotion import PromotionError, promote_rule
from app.paths import RULES_DIR


@pytest.fixture
def rules_dir(tmp_path: Path) -> Path:
    copy = tmp_path / "rules"
    shutil.copytree(RULES_DIR, copy)
    return copy


def test_promoting_a_valid_rule_makes_the_catalog_load_it(rules_dir: Path) -> None:
    definition = {
        "id": "LEARNED_TEST_RULE",
        "title": "A promoted test rule",
        "category": "test",
        "severity": "low",
        "when": "revenue_from_operations > 0",
        "rationale": "Promoted for testing.",
        "evidence": ["revenue_from_operations"],
    }

    pack_file = promote_rule(definition, rules_dir=rules_dir)

    assert pack_file.exists()
    catalog = Catalog.load(rules_dir)
    assert "LEARNED_TEST_RULE" in catalog.rules
    assert catalog.rules["LEARNED_TEST_RULE"].status == "active"


def test_promoting_twice_is_rejected_without_touching_the_file(rules_dir: Path) -> None:
    definition = {
        "id": "LEARNED_TEST_RULE",
        "title": "A promoted test rule",
        "category": "test",
        "severity": "low",
        "when": "revenue_from_operations > 0",
        "rationale": "Promoted for testing.",
        "evidence": ["revenue_from_operations"],
    }
    promote_rule(definition, rules_dir=rules_dir)

    with pytest.raises(PromotionError, match="already"):
        promote_rule(definition, rules_dir=rules_dir)


def test_a_rule_that_breaks_the_catalog_is_rolled_back(rules_dir: Path) -> None:
    pack_file = rules_dir / "packs" / "learned" / "proposed.yaml"
    definition = {
        "id": "BROKEN_TEST_RULE",
        "title": "Refers to nothing real",
        "category": "test",
        "severity": "low",
        "when": "not_a_real_metric > 0",
        "rationale": "Should fail to load.",
        "evidence": [],
    }

    with pytest.raises(PromotionError):
        promote_rule(definition, rules_dir=rules_dir)

    assert not pack_file.exists()
    Catalog.load(rules_dir)  # still loads cleanly — the bad write never stuck


def test_a_second_bad_rule_does_not_corrupt_an_existing_learned_pack(rules_dir: Path) -> None:
    good = {
        "id": "LEARNED_TEST_RULE",
        "title": "A promoted test rule",
        "category": "test",
        "severity": "low",
        "when": "revenue_from_operations > 0",
        "rationale": "Promoted for testing.",
        "evidence": ["revenue_from_operations"],
    }
    promote_rule(good, rules_dir=rules_dir)
    pack_file = rules_dir / "packs" / "learned" / "proposed.yaml"
    before = pack_file.read_text()

    bad = {
        "id": "BROKEN_TEST_RULE",
        "title": "Refers to nothing real",
        "category": "test",
        "severity": "low",
        "when": "not_a_real_metric > 0",
        "rationale": "Should fail to load.",
        "evidence": [],
    }
    with pytest.raises(PromotionError):
        promote_rule(bad, rules_dir=rules_dir)

    assert pack_file.read_text() == before
