"""Hybrid retrieval: metadata filter -> BM25 and dense candidates -> reciprocal rank fusion -> cross-encoder
rerank -> learned utility boost.

Lexical search catches exact terms analysts use ("CWIP", "Regulation 23"); dense search catches paraphrases
("customers are paying later"). Reciprocal rank fusion combines them without having to calibrate their very
different score scales, and the cross-encoder then reads query and passage together for the final order. The
learning service supplies per-chunk utility boosts for chunks that have supported confirmed findings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from pydantic import BaseModel

from app.domain.enums import DocType, Sector
from app.knowledge.chunking import Chunk
from app.knowledge.documents import SectionKind
from app.knowledge.embeddings import Embedder, Reranker
from app.knowledge.lexical import BM25Index

RRF_K = 60
RERANK_WEIGHT = 2.0  # the cross-encoder's ranking counts double in the fusion


@dataclass(frozen=True, slots=True)
class SearchFilters:
    doc_type: DocType | None = None
    sector: Sector | None = None
    kinds: tuple[SectionKind, ...] = ()
    doc_ids: tuple[str, ...] = ()

    def allows(self, chunk: Chunk) -> bool:
        if self.doc_type and chunk.doc_types and self.doc_type not in chunk.doc_types:
            return False
        if self.sector and chunk.sectors and self.sector not in chunk.sectors:
            return False
        if self.kinds and chunk.kind not in self.kinds:
            return False
        return not self.doc_ids or chunk.doc_id in self.doc_ids


class KnowledgeHit(BaseModel):
    chunk: Chunk
    score: float
    lexical_rank: int | None = None
    dense_rank: int | None = None
    rerank_rank: int | None = None


@dataclass
class HybridRetriever:
    chunks: Sequence[Chunk]
    embedder: Embedder | None = None
    reranker: Reranker | None = None
    use_lexical: bool = True  # disable only for ablation studies
    candidate_pool: int = 20
    rerank_pool: int = (
        8  # cross-encoder cost grows linearly with passages; fused top 8 already holds the answer
    )
    _lexical: BM25Index = field(init=False)
    _vectors: np.ndarray | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._lexical = BM25Index(self.chunks)
        if self.embedder is not None:
            self._vectors = self.embedder.embed_documents([chunk.search_text for chunk in self.chunks])

    @property
    def mode(self) -> str:
        parts = ["lexical"] if self.use_lexical else []
        if self._vectors is not None:
            parts.append("dense")
        if self.reranker is not None:
            parts.append("rerank")
        return "+".join(parts)

    def search(
        self,
        query: str,
        filters: SearchFilters | None = None,
        k: int = 3,
        boosts: Mapping[str, float] | None = None,
    ) -> list[KnowledgeHit]:
        filters = filters or SearchFilters()
        allowed = [i for i, chunk in enumerate(self.chunks) if filters.allows(chunk)]
        if not allowed:
            return []

        rankings: dict[str, dict[int, int]] = {}
        if self.use_lexical:
            scores = self._lexical.scores(query)
            rankings["lexical"] = _rank([i for i in allowed if scores[i] > 0], scores, self.candidate_pool)
        if self._vectors is not None and self.embedder is not None:
            similarities = (self._vectors @ self.embedder.embed_query(query)).tolist()
            rankings["dense"] = _rank(allowed, similarities, self.candidate_pool)

        fused = _fuse(rankings.values())
        if self.reranker is not None and fused:
            candidates = sorted(fused, key=lambda i: (-fused[i], i))[: self.rerank_pool]
            rerank_scores = self.reranker.scores(query, [self.chunks[i].search_text for i in candidates])
            rankings["rerank"] = _rank(
                candidates, dict(zip(candidates, rerank_scores, strict=True)), len(candidates)
            )
            fused = _fuse(rankings.values(), weights=[1.0] * (len(rankings) - 1) + [RERANK_WEIGHT])

        boosts = boosts or {}
        scored = [(fused[i] * (1 + boosts.get(self.chunks[i].id, 0.0)), i) for i in fused]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            KnowledgeHit(
                chunk=self.chunks[i],
                score=round(score, 6),
                lexical_rank=rankings.get("lexical", {}).get(i),
                dense_rank=rankings.get("dense", {}).get(i),
                rerank_rank=rankings.get("rerank", {}).get(i),
            )
            for score, i in scored[:k]
        ]


def pack_context(hits: Sequence[KnowledgeHit], max_tokens: int = 1200) -> str:
    """Render hits for a prompt, citing each by chunk id, within a token budget."""
    blocks: list[str] = []
    used = 0
    for hit in hits:
        chunk = hit.chunk
        block = f"[kb:{chunk.id}] {chunk.doc_title} — {chunk.heading}\n{chunk.text}"
        cost = len(block) // 4 + 1
        if blocks and used + cost > max_tokens:
            break
        blocks.append(block)
        used += cost
    return "\n\n".join(blocks)


def _rank(
    indices: Sequence[int], scores: Sequence[float] | Mapping[int, float], limit: int
) -> dict[int, int]:
    ordered = sorted(indices, key=lambda i: (-scores[i], i))[:limit]
    return {index: rank for rank, index in enumerate(ordered, start=1)}


def _fuse(rankings: Iterable[Mapping[int, int]], weights: Sequence[float] | None = None) -> dict[int, float]:
    rankings = list(rankings)
    weights = weights or [1.0] * len(rankings)
    fused: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for index, rank in ranking.items():
            fused[index] = fused.get(index, 0.0) + weight / (RRF_K + rank)
    return fused
