from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from app.domain.enums import DocType, Sector
from app.engine.catalog import Catalog
from app.knowledge.base import KnowledgeBase, missing_references
from app.knowledge.chunking import chunk_document
from app.knowledge.documents import KnowledgeError, SectionKind, load_documents, parse_document
from app.knowledge.evaluation import evaluate, load_queries
from app.knowledge.retriever import HybridRetriever, SearchFilters, pack_context
from app.knowledge.text import stem, tokenize
from app.paths import KNOWLEDGE_DIR

ESSENTIAL = {
    SectionKind.OVERVIEW,
    SectionKind.RED_FLAGS,
    SectionKind.BENIGN_EXPLANATIONS,
    SectionKind.QUESTIONS,
}


@pytest.fixture(scope="module")
def kb(catalog: Catalog) -> KnowledgeBase:
    return KnowledgeBase.load(KNOWLEDGE_DIR, catalog)


# ── content ───────────────────────────────────────────────────────────────────────────────────────────────


def test_every_rule_and_metric_reference_has_a_document(catalog: Catalog, kb: KnowledgeBase) -> None:
    assert missing_references(catalog, kb.documents) == []


def test_referenced_documents_cover_what_an_analyst_needs(catalog: Catalog, kb: KnowledgeBase) -> None:
    referenced = {rule.kb for rule in catalog.rules.values()}
    for doc_id in referenced:
        kinds = {section.kind for section in kb.documents[doc_id].sections}
        assert kinds >= ESSENTIAL, f"{doc_id} lacks {ESSENTIAL - kinds}"


def test_chunks_are_linked_back_to_rules(kb: KnowledgeBase) -> None:
    red_flags = kb.sections("working-capital/receivables", (SectionKind.RED_FLAGS,))
    assert len(red_flags) == 1
    assert "WC_RECEIVABLES_OUTPACE_REVENUE" in red_flags[0].rules
    assert "dso" in red_flags[0].metrics


def test_chunk_ids_are_unique_and_within_budget(kb: KnowledgeBase) -> None:
    assert len({chunk.id for chunk in kb.chunks}) == len(kb.chunks)
    assert max(chunk.tokens for chunk in kb.chunks) <= 400


def test_scoped_documents_carry_metadata(kb: KnowledgeBase) -> None:
    assert kb.documents["sectors/banking"].sectors == (Sector.BANKING,)
    assert kb.documents["offer-document/objects-of-the-offer"].doc_types == (DocType.DRHP, DocType.RHP)


# ── parsing and chunking ──────────────────────────────────────────────────────────────────────────────────

VALID = """---
title: Example
summary: An example document.
sectors: [it_services]
---
# Example

## What it measures
Something.

## Red flags
- One
- Two

## A custom heading
Free text.
"""


def test_parse_document_maps_standard_headings() -> None:
    document = parse_document("topic/example", VALID)
    assert [s.kind for s in document.sections] == [
        SectionKind.OVERVIEW,
        SectionKind.RED_FLAGS,
        SectionKind.GENERAL,
    ]
    assert document.section(SectionKind.RED_FLAGS).text == "- One\n- Two"


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("# No front matter\n\n## Red flags\nx\n", "missing front matter"),
        (VALID.replace("summary: An example document.\n", ""), "summary"),
        (VALID.replace("it_services", "shipping"), "sectors"),
        (VALID.replace("Free text.", ""), "is empty"),
        (VALID.replace("title: Example", "title: Example\nauthor: someone"), "author"),
    ],
)
def test_invalid_documents_are_rejected(tmp_path: Path, text: str, problem: str) -> None:
    (tmp_path / "topic").mkdir()
    (tmp_path / "topic" / "bad.md").write_text(text, encoding="utf-8")
    with pytest.raises(KnowledgeError, match=problem):
        load_documents(tmp_path)


def test_long_sections_split_at_block_boundaries() -> None:
    items = "\n".join(f"- Item {i}: " + "word " * 40 for i in range(20))
    document = parse_document("topic/long", VALID.replace("- One\n- Two", items))
    chunks = [c for c in chunk_document(document, max_tokens=150) if c.kind is SectionKind.RED_FLAGS]
    assert len(chunks) > 1
    assert [c.id for c in chunks[:2]] == ["topic/long#red-flags", "topic/long#red-flags-2"]
    assert all(c.text.startswith("- Item") for c in chunks)


# ── text processing ───────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("variants"),
    [("receivable", "receivables"), ("pledge", "pledged", "pledges"), ("increase", "increased", "increases")],
)
def test_stemming_conflates_variants(variants: tuple[str, ...]) -> None:
    assert len({stem(word) for word in variants}) == 1


def test_abbreviations_expand_to_the_words_documents_use() -> None:
    assert set(tokenize("operating cash flow")) <= set(tokenize("CFO"))
    assert set(tokenize("offer for sale")) <= set(tokenize("OFS"))


# ── retrieval ─────────────────────────────────────────────────────────────────────────────────────────────


def test_lexical_search_finds_paraphrased_questions(kb: KnowledgeBase) -> None:
    hits = kb.search("customers are taking much longer to pay their invoices")
    assert hits[0].chunk.doc_id == "working-capital/receivables"


def test_filters_restrict_section_kind_sector_and_document_type(kb: KnowledgeBase) -> None:
    critic = kb.search(
        "receivables rose at year end", SearchFilters(kinds=(SectionKind.BENIGN_EXPLANATIONS,)), k=5
    )
    assert critic and all(hit.chunk.kind is SectionKind.BENIGN_EXPLANATIONS for hit in critic)

    bank = kb.search("asset quality and capital adequacy", SearchFilters(sector=Sector.IT_SERVICES), k=10)
    assert all(not hit.chunk.sectors or Sector.IT_SERVICES in hit.chunk.sectors for hit in bank)

    quarterly = kb.search(
        "general corporate purposes", SearchFilters(doc_type=DocType.QUARTERLY_RESULT), k=10
    )
    assert all(hit.chunk.doc_id != "offer-document/objects-of-the-offer" for hit in quarterly)


def test_learned_boosts_change_the_order(kb: KnowledgeBase) -> None:
    query = "operating cash flow lower than profit"
    baseline = kb.search(query, k=5)
    second = baseline[1].chunk.id
    boosted = kb.search(query, k=5, boosts={second: 1.0})
    assert boosted[0].chunk.id == second


def test_pack_context_cites_chunks_within_budget(kb: KnowledgeBase) -> None:
    hits = kb.search("promoter pledge", k=5)
    context = pack_context(hits, max_tokens=300)
    assert context.startswith(f"[kb:{hits[0].chunk.id}]")
    assert len(context) // 4 <= 300 or context.count("[kb:") == 1


class KeywordEmbedder:
    """Deterministic stand-in for a neural embedder: one dimension per vocabulary term."""

    name = "keyword-test"
    vocabulary = ("receivable", "cash", "promoter", "auditor", "offer", "inventory", "debt", "tax")

    def _vector(self, text: str) -> np.ndarray:
        tokens = tokenize(text)
        vector = np.array([sum(t.startswith(stem(v)) for t in tokens) for v in self.vocabulary], dtype=float)
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([self._vector(text) for text in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


class ReverseReranker:
    """Scores passages so that the fused order is reversed, to prove the reranker influences the result."""

    name = "reverse-test"

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        return [float(i) for i in range(len(texts))]


def test_hybrid_fusion_and_reranking_paths(kb: KnowledgeBase) -> None:
    hybrid = HybridRetriever(kb.chunks, embedder=KeywordEmbedder())
    assert hybrid.mode == "lexical+dense"
    hits = hybrid.search("promoter shares pledged", k=5)
    assert hits[0].dense_rank is not None and hits[0].lexical_rank is not None

    reranked = HybridRetriever(
        kb.chunks, embedder=KeywordEmbedder(), reranker=ReverseReranker(), rerank_pool=5
    )
    top = reranked.search("promoter shares pledged", k=5)
    assert top[0].rerank_rank == 1
    assert top[0].chunk.id != hits[0].chunk.id


def test_lexical_retrieval_quality_does_not_regress(kb: KnowledgeBase) -> None:
    """Guards the measured baseline; `python -m app.knowledge.evaluation` runs the full ablation."""
    report = evaluate(kb.retriever, load_queries())
    assert report.queries >= 60
    assert report.recall_at_3 >= 0.93
    assert report.mrr >= 0.88
