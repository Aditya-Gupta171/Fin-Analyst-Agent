"""Layer B of the knowledge base: the taxonomy, metric registry and rule packs, loaded from ``rules/``.

The catalog is plain YAML under version control. Loading it compiles and cross-checks every expression, so a
typo in a rule fails at startup (and in CI) rather than silently never firing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    ValidationError,
    field_validator,
)

from app.domain.enums import DocType, Nature, Scope, Sector, Severity, Statement, Unit, expand_sectors
from app.domain.periods import PeriodKind
from app.engine.expressions import Expression, ExpressionError, parse_expression


class CatalogError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("invalid rules catalog:\n  - " + "\n  - ".join(problems))
        self.problems = problems


class LineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    statement: Statement
    unit: Unit
    nature: Nature
    scope: Scope
    description: str | None = None
    aliases: tuple[str, ...] = ()
    nil_if_absent: bool = False  # optional presentation line: zero when its statement is reported without it
    xbrl: tuple[Annotated[str, StringConstraints(pattern=r"^-?[A-Za-z]+(#first|#last)?$")], ...] = ()


class _Compiled(BaseModel):
    """Base for definitions whose expressions are compiled once at load time."""

    _expressions: dict[str, Expression] = PrivateAttr(default_factory=dict)

    def compiled(self, source: str) -> Expression:
        if source not in self._expressions:
            self._expressions[source] = parse_expression(source)
        return self._expressions[source]


class MetricDef(_Compiled):
    key: str
    name: str
    formula: str
    unit: Unit
    category: str
    better: Literal["higher", "lower", "neutral"] = "neutral"
    scope: Scope = Scope.PERIOD
    description: str
    kb: str | None = None
    headline: bool = False  # shown in the summary metrics table
    sectors_exclude: tuple[str, ...] = ()

    @property
    def expression(self) -> Expression:
        return self.compiled(self.formula)

    def applies_to_sector(self, sector: Sector) -> bool:
        return sector not in expand_sectors(list(self.sectors_exclude))


class EvidenceSpec(BaseModel):
    expr: str
    label: str | None = None
    unit: Unit | None = None


class Escalation(BaseModel):
    when: str
    severity: Severity


class RuleDef(_Compiled):
    id: str
    version: int = 1
    status: Literal["active", "candidate", "retired"] = "active"
    pack: str = ""  # set from the pack directory name
    title: str
    category: str
    severity: Severity
    when: str
    rationale: str
    applies_to: tuple[DocType, ...] = tuple(DocType)
    period_kinds: tuple[PeriodKind, ...] = (PeriodKind.YEAR,)
    scope: Scope = Scope.PERIOD
    sectors_exclude: tuple[str, ...] = ()
    sectors_only: tuple[str, ...] = ()
    evidence: tuple[EvidenceSpec, ...] = ()
    escalations: tuple[Escalation, ...] = ()
    kb: str | None = None
    questions: tuple[str, ...] = ()

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_shorthand(cls, value: Any) -> Any:
        return [{"expr": item} if isinstance(item, str) else item for item in value or []]

    @property
    def condition(self) -> Expression:
        return self.compiled(self.when)

    @property
    def versioned_id(self) -> str:
        return f"{self.id}@v{self.version}"

    def applies_to_sector(self, sector: Sector) -> bool:
        if self.sectors_only and sector not in expand_sectors(list(self.sectors_only)):
            return False
        return sector not in expand_sectors(list(self.sectors_exclude))

    def expressions(self) -> Iterable[str]:
        yield self.when
        yield from (spec.expr for spec in self.evidence)
        yield from (escalation.when for escalation in self.escalations)


class Catalog(BaseModel):
    items: dict[str, LineItem]
    metrics: dict[str, MetricDef]
    rules: dict[str, RuleDef]
    version: str = Field(description="Content hash of every catalog file; recorded with each analysis.")

    @classmethod
    def load(cls, root: Path) -> Catalog:
        files = sorted(root.rglob("*.yaml"))
        if not files:
            raise CatalogError([f"no catalog files found under {root}"])
        digest = hashlib.sha256()
        items: dict[str, LineItem] = {}
        metrics: dict[str, MetricDef] = {}
        rules: dict[str, RuleDef] = {}
        problems: list[str] = []

        for path in files:
            relative = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            digest.update(relative.encode() + b"\0" + raw.replace(b"\r\n", b"\n"))
            document = yaml.safe_load(raw) or {}
            section = relative.split("/", 1)[0]
            try:
                if section == "taxonomy":
                    _collect(items, _line_items(document), "line item", relative, problems)
                elif section == "metrics":
                    parsed = [MetricDef.model_validate(entry) for entry in document.get("metrics", [])]
                    _collect(metrics, [(m.key, m) for m in parsed], "metric", relative, problems)
                elif section == "packs":
                    pack = relative.split("/")[1]
                    parsed_rules = [
                        RuleDef.model_validate({**entry, "pack": pack}) for entry in document.get("rules", [])
                    ]
                    _collect(rules, [(r.id, r) for r in parsed_rules], "rule", relative, problems)
                else:
                    problems.append(f"{relative}: unexpected location (use taxonomy/, metrics/ or packs/)")
            except ValidationError as exc:
                problems.append(f"{relative}: {exc}")

        catalog = cls(items=items, metrics=metrics, rules=rules, version=digest.hexdigest()[:12])
        problems.extend(catalog._validate())
        if problems:
            raise CatalogError(problems)
        return catalog

    def unit_of(self, name: str) -> Unit:
        return self.items[name].unit if name in self.items else self.metrics[name].unit

    def label_of(self, name: str) -> str:
        return self.items[name].label if name in self.items else self.metrics[name].name

    def active_rules(self, packs: Iterable[str] | None = None) -> list[RuleDef]:
        wanted = set(packs) if packs is not None else None
        return [
            rule
            for rule in self.rules.values()
            if rule.status == "active" and (wanted is None or rule.pack in wanted)
        ]

    def _validate(self) -> list[str]:
        problems: list[str] = []
        for key in self.items.keys() & self.metrics.keys():
            problems.append(f"{key!r} is defined both as a line item and as a metric")

        known = self.items.keys() | self.metrics.keys()

        def check(owner: str, source: str) -> Expression | None:
            try:
                expression = parse_expression(source)
            except ExpressionError as exc:
                problems.append(f"{owner}: {exc}")
                return None
            if unknown := sorted(expression.names - known):
                problems.append(f"{owner}: unknown name(s) {', '.join(unknown)} in {source!r}")
            return expression

        dependencies: dict[str, set[str]] = {}
        for metric in self.metrics.values():
            if expression := check(f"metric {metric.key}", metric.formula):
                dependencies[metric.key] = set(expression.names & self.metrics.keys())
            for name in metric.sectors_exclude:
                problems.extend(_sector_problem(f"metric {metric.key}", name))
        for rule in self.rules.values():
            for source in rule.expressions():
                check(f"rule {rule.id}", source)
            for name in (*rule.sectors_exclude, *rule.sectors_only):
                problems.extend(_sector_problem(f"rule {rule.id}", name))

        problems.extend(_cycles(dependencies))
        return problems


def _line_items(document: dict[str, Any]) -> list[tuple[str, LineItem]]:
    defaults = document.get("defaults", {})
    statement = document["statement"]
    return [
        (entry["key"], LineItem.model_validate({**defaults, "statement": statement, **entry}))
        for entry in document.get("items", [])
    ]


def _collect(
    target: dict[str, Any], entries: list[tuple[str, Any]], kind: str, source: str, problems: list[str]
) -> None:
    for key, value in entries:
        if key in target:
            problems.append(f"{source}: duplicate {kind} {key!r}")
        target[key] = value


def _sector_problem(owner: str, name: str) -> list[str]:
    try:
        expand_sectors([name])
    except ValueError:
        return [f"{owner}: unknown sector or sector group {name!r}"]
    return []


def _cycles(dependencies: dict[str, set[str]]) -> list[str]:
    problems: list[str] = []
    state: dict[str, str] = {}  # "visiting" | "done"

    def visit(key: str, trail: list[str]) -> None:
        if state.get(key) == "done":
            return
        if state.get(key) == "visiting":
            cycle = [*trail[trail.index(key) :], key]
            problems.append("circular metric dependency: " + " -> ".join(cycle))
            return
        state[key] = "visiting"
        for dependency in sorted(dependencies.get(key, ())):
            visit(dependency, [*trail, key])
        state[key] = "done"

    for key in sorted(dependencies):
        visit(key, [])
    return problems
