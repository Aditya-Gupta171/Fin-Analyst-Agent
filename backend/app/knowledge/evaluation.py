"""Retrieval quality evaluation for the knowledge base.

Scores are document-level: the rank of a query is the position of the first retrieved chunk that belongs to
one of its expected documents. Run the full ablation (downloads the local embedding and reranking models
once)::

    python -m app.knowledge.evaluation
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.engine.catalog import Catalog
from app.knowledge.base import KnowledgeBase
from app.knowledge.documents import SectionKind
from app.knowledge.retriever import HybridRetriever, SearchFilters
from app.paths import KNOWLEDGE_DIR, REPO_ROOT, RULES_DIR

EVAL_SET = REPO_ROOT / "evals" / "retrieval.yaml"
DEPTH = 10


class EvalQuery(BaseModel):
    query: str
    expect: tuple[str, ...]
    kinds: tuple[SectionKind, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalReport:
    mode: str
    queries: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    misses: tuple[str, ...]  # queries with no expected document in the top 5
    seconds: float

    def row(self) -> str:
        return (
            f"| {self.mode:<24} | {self.recall_at_1:6.1%} | {self.recall_at_3:6.1%} "
            f"| {self.recall_at_5:6.1%} | {self.mrr:5.3f} | {self.seconds * 1000 / self.queries:7.1f} |"
        )


def load_queries(path: Path = EVAL_SET) -> list[EvalQuery]:
    return [
        EvalQuery.model_validate(item) for item in yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]
    ]


def evaluate(retriever: HybridRetriever, queries: Sequence[EvalQuery], mode: str | None = None) -> EvalReport:
    ranks: list[int | None] = []
    retriever.search("warm-up query", k=1)  # load lazily initialised models outside the timed loop
    started = time.perf_counter()
    for item in queries:
        hits = retriever.search(item.query, SearchFilters(kinds=item.kinds), k=DEPTH)
        ranks.append(
            next((rank for rank, hit in enumerate(hits, 1) if hit.chunk.doc_id in item.expect), None)
        )
    elapsed = time.perf_counter() - started

    def recall(k: int) -> float:
        return sum(1 for rank in ranks if rank is not None and rank <= k) / len(queries)

    return EvalReport(
        mode=mode or retriever.mode,
        queries=len(queries),
        recall_at_1=recall(1),
        recall_at_3=recall(3),
        recall_at_5=recall(5),
        mrr=sum(1 / rank for rank in ranks if rank is not None) / len(queries),
        misses=tuple(q.query for q, rank in zip(queries, ranks, strict=True) if rank is None or rank > 5),
        seconds=elapsed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lexical-only", action="store_true", help="skip model-based configurations")
    args = parser.parse_args()

    catalog = Catalog.load(RULES_DIR)
    queries = load_queries()
    base = KnowledgeBase.load(KNOWLEDGE_DIR, catalog)
    configurations: list[tuple[str, HybridRetriever]] = [("lexical (BM25)", base.retriever)]
    if not args.lexical_only:
        from app.knowledge.embeddings import FastEmbedEmbedder, FastEmbedReranker

        embedder, reranker = FastEmbedEmbedder(), FastEmbedReranker()
        dense = HybridRetriever(base.chunks, embedder=embedder, use_lexical=False)
        configurations += [
            ("dense (bge-small)", dense),
            ("hybrid (RRF)", HybridRetriever(base.chunks, embedder=embedder)),
            ("hybrid + rerank", HybridRetriever(base.chunks, embedder=embedder, reranker=reranker)),
        ]

    print(f"{len(queries)} queries, {len(base.documents)} documents, {len(base.chunks)} chunks\n")
    print("| configuration            |   R@1  |   R@3  |   R@5  |  MRR  | ms/query |")
    print("| ------------------------ | ------ | ------ | ------ | ----- | -------- |")
    reports = [evaluate(retriever, queries, name) for name, retriever in configurations]
    for report in reports:
        print(report.row())
    for report in reports:
        if report.misses:
            print(f"\n{report.mode} misses (not in top 5):")
            for miss in report.misses:
                print(f"  - {miss}")


if __name__ == "__main__":
    main()
