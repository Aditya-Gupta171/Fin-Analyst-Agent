"""Promotes an approved candidate rule into a dedicated, version-controlled pack file.

Loading it into the *running* app still needs a restart — the catalog loads once at startup
(app/api/main.py) — but writing it here, in the same place every other rule lives, makes the promotion
durable and reviewable before anyone commits it, the same way every other rule pack file is.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.engine.catalog import Catalog, CatalogError
from app.paths import RULES_DIR

_ENTRY_FIELDS = ("id", "title", "category", "severity", "when", "rationale", "evidence")


class PromotionError(ValueError):
    """The candidate could not be promoted — an id collision, or the resulting catalog fails to load."""


def promote_rule(definition: dict, rules_dir: Path = RULES_DIR) -> Path:
    """Append ``definition`` to ``rules/packs/learned/proposed.yaml`` as an active rule, then re-validate
    the whole catalog still loads — rolling the write back if it doesn't."""
    entry = {field: definition[field] for field in _ENTRY_FIELDS}
    entry["status"] = "active"  # a stored candidate's status is always "candidate"; promoting means active

    pack_file = rules_dir / "packs" / "learned" / "proposed.yaml"
    document = yaml.safe_load(pack_file.read_text()) if pack_file.exists() else {"rules": []}
    if any(rule["id"] == entry["id"] for rule in document["rules"]):
        raise PromotionError(f"{entry['id']} is already in the learned pack")

    original = pack_file.read_text() if pack_file.exists() else None
    document["rules"].append(entry)
    pack_file.parent.mkdir(parents=True, exist_ok=True)
    pack_file.write_text(yaml.safe_dump(document, sort_keys=False))
    try:
        Catalog.load(rules_dir)
    except CatalogError as exc:
        if original is not None:
            pack_file.write_text(original)
        else:
            pack_file.unlink()
        raise PromotionError(str(exc)) from exc
    return pack_file
