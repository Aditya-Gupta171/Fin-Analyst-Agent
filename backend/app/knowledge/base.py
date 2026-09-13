"""Knowledge base facade for the agent: documents, chunks, direct lookups by rule reference, and search."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.engine.catalog import Catalog
from app.knowledge.chunking import Chunk, catalog_links, chunk_document
from app.knowledge.documents import (
    KnowledgeDocument,
    KnowledgeError,
    SectionKind,
    content_version,
    load_documents,
)
from app.knowledge.embeddings import Embedder, Reranker
from app.knowledge.retriever import HybridRetriever, KnowledgeHit, SearchFilters


@dataclass
class KnowledgeBase:
    documents: dict[str, KnowledgeDocument]
    chunks: list[Chunk]
    retriever: HybridRetriever
    version: str

    @classmethod
    def load(
        cls,
        root: Path,
        catalog: Catalog,
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ) -> KnowledgeBase:
        documents = load_documents(root)
        if missing := missing_references(catalog, documents):
            raise KnowledgeError([f"no knowledge document for kb reference {ref!r}" for ref in missing])
        links = catalog_links(catalog)
        chunks = [chunk for doc_id in sorted(documents) for chunk in chunk_document(documents[doc_id], links)]
        retriever = HybridRetriever(chunks, embedder=embedder, reranker=reranker)
        return cls(documents, chunks, retriever, content_version(documents))

    def search(
        self,
        query: str,
        filters: SearchFilters | None = None,
        k: int = 3,
        boosts: Mapping[str, float] | None = None,
    ) -> list[KnowledgeHit]:
        return self.retriever.search(query, filters, k, boosts)

    def sections(self, doc_id: str, kinds: tuple[SectionKind, ...] = ()) -> list[Chunk]:
        """Chunks of one document, optionally only some section kinds — a direct lookup, no ranking involved.

        A fired rule names its knowledge document, so the agent fetches it deterministically and uses search
        only for open questions.
        """
        return [
            chunk for chunk in self.chunks if chunk.doc_id == doc_id and (not kinds or chunk.kind in kinds)
        ]


def missing_references(catalog: Catalog, documents: Mapping[str, KnowledgeDocument]) -> list[str]:
    references = {rule.kb for rule in catalog.rules.values() if rule.kb}
    references |= {metric.kb for metric in catalog.metrics.values() if metric.kb}
    return sorted(references - documents.keys())
