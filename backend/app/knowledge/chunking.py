"""Heading-aware chunking.

Each document section becomes one chunk, so a retrieved chunk is always a coherent unit ("red flags for
receivables") rather than an arbitrary window of text. Only sections longer than the token budget are split,
and then only at paragraph or list-item boundaries.
"""

from __future__ import annotations

import re
from collections import defaultdict

from pydantic import BaseModel, ConfigDict

from app.domain.enums import DocType, Sector
from app.engine.catalog import Catalog
from app.knowledge.documents import KnowledgeDocument, SectionKind

MAX_CHUNK_TOKENS = 380
_BLOCK = re.compile(r"\n\s*\n|\n(?=\s*(?:[-*]|\d+\.)\s)")


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # "<document id>#<section slug>" with "-2", "-3" suffixes when a section is split
    doc_id: str
    doc_title: str
    heading: str
    kind: SectionKind
    text: str
    doc_types: tuple[DocType, ...]
    sectors: tuple[Sector, ...]
    regulations: tuple[str, ...]
    rules: tuple[str, ...]  # rules whose kb reference is this document
    metrics: tuple[str, ...]

    @property
    def search_text(self) -> str:
        return f"{self.doc_title}. {self.heading}. {self.text}"

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.search_text)


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def catalog_links(catalog: Catalog) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Document id -> (rule ids, metric keys) that reference it."""
    rules: dict[str, list[str]] = defaultdict(list)
    metrics: dict[str, list[str]] = defaultdict(list)
    for rule in catalog.rules.values():
        if rule.kb:
            rules[rule.kb].append(rule.id)
    for metric in catalog.metrics.values():
        if metric.kb:
            metrics[metric.kb].append(metric.key)
    return {
        doc_id: (tuple(sorted(rules[doc_id])), tuple(sorted(metrics[doc_id])))
        for doc_id in rules.keys() | metrics.keys()
    }


def chunk_document(
    document: KnowledgeDocument,
    links: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] | None = None,
    max_tokens: int = MAX_CHUNK_TOKENS,
) -> list[Chunk]:
    rules, metrics = (links or {}).get(document.id, ((), ()))
    chunks: list[Chunk] = []
    for section in document.sections:
        base_id = f"{document.id}#{_slug(section.heading)}"
        for part, text in enumerate(_split(section.text, max_tokens), start=1):
            chunks.append(
                Chunk(
                    id=base_id if part == 1 else f"{base_id}-{part}",
                    doc_id=document.id,
                    doc_title=document.title,
                    heading=section.heading,
                    kind=section.kind,
                    text=text,
                    doc_types=document.doc_types,
                    sectors=document.sectors,
                    regulations=document.regulations,
                    rules=rules,
                    metrics=metrics,
                )
            )
    return chunks


def _split(text: str, max_tokens: int) -> list[str]:
    if estimate_tokens(text) <= max_tokens:
        return [text]
    parts: list[str] = []
    current = ""
    for block in (b.strip() for b in _BLOCK.split(text)):
        if not block:
            continue
        candidate = f"{current}\n{block}" if current else block
        if current and estimate_tokens(candidate) > max_tokens:
            parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
