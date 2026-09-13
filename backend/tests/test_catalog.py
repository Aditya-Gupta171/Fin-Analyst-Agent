from pathlib import Path

import pytest

from app.engine.catalog import Catalog, CatalogError


def test_shipped_catalog_is_valid(catalog: Catalog) -> None:
    assert catalog.metrics and catalog.rules and catalog.items
    assert len(catalog.version) == 12


def test_every_rule_explains_itself(catalog: Catalog) -> None:
    for rule in catalog.rules.values():
        assert len(rule.rationale.split()) >= 12, f"{rule.id}: rationale too thin"
        assert rule.kb, f"{rule.id}: no knowledge-base reference"
        assert rule.evidence, f"{rule.id}: no evidence listed"


def test_rule_ids_are_namespaced_by_category_prefix(catalog: Catalog) -> None:
    for rule in catalog.rules.values():
        assert rule.id.isupper() and "_" in rule.id, rule.id


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def minimal(tmp_path: Path) -> Path:
    write(
        tmp_path,
        "taxonomy/pl.yaml",
        """
statement: profit_and_loss
defaults: { unit: inr_crore, nature: flow, scope: period }
items:
  - { key: revenue, label: Revenue }
  - { key: profit, label: Profit }
""",
    )
    return tmp_path


def test_loads_minimal_catalog(minimal: Path) -> None:
    write(
        minimal,
        "metrics/m.yaml",
        """
metrics:
  - { key: margin, name: Margin, formula: profit / revenue, unit: ratio, category: p, description: d }
""",
    )
    write(
        minimal,
        "packs/core/r.yaml",
        """
rules:
  - { id: LOW_MARGIN, title: t, category: p, severity: low, when: margin < 0.1, rationale: r }
""",
    )
    catalog = Catalog.load(minimal)
    assert catalog.rules["LOW_MARGIN"].pack == "core"


def test_version_changes_with_content(minimal: Path) -> None:
    before = Catalog.load(minimal).version
    write(
        minimal,
        "taxonomy/pl.yaml",
        (minimal / "taxonomy/pl.yaml").read_text() + "  - { key: tax, label: Tax }\n",
    )
    assert Catalog.load(minimal).version != before


@pytest.mark.parametrize(
    ("relative", "content", "problem"),
    [
        (
            "metrics/m.yaml",
            "metrics:\n"
            "  - { key: m, name: M, formula: revnue / 2, unit: ratio, category: p, description: d }\n",
            "unknown name(s) revnue",
        ),
        (
            "metrics/m.yaml",
            "metrics:\n"
            "  - { key: a, name: A, formula: b + 1, unit: ratio, category: p, description: d }\n"
            "  - { key: b, name: B, formula: a + 1, unit: ratio, category: p, description: d }\n",
            "circular metric dependency",
        ),
        (
            "packs/core/r.yaml",
            "rules:\n"
            "  - { id: R, title: t, category: p, severity: low, when: revenue.x > 1, rationale: r }\n",
            "unsupported syntax",
        ),
        (
            "packs/core/r.yaml",
            "rules:\n"
            "  - { id: R, title: t, category: p, severity: low, when: revenue > 1, rationale: r }\n"
            "  - { id: R, title: t, category: p, severity: low, when: revenue > 2, rationale: r }\n",
            "duplicate rule 'R'",
        ),
        (
            "packs/core/r.yaml",
            "rules:\n  - { id: R, title: t, category: p, severity: low, when: revenue > 1, rationale: r, "
            "sectors_exclude: [shipping] }\n",
            "unknown sector",
        ),
        ("elsewhere/x.yaml", "foo: 1\n", "unexpected location"),
    ],
)
def test_rejects_invalid_definitions(minimal: Path, relative: str, content: str, problem: str) -> None:
    write(minimal, relative, content)
    with pytest.raises(CatalogError) as raised:
        Catalog.load(minimal)
    assert any(problem in message for message in raised.value.problems), raised.value.problems
