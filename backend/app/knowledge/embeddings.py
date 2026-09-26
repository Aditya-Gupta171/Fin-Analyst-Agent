"""Dense embeddings and cross-encoder reranking, run locally with fastembed (ONNX, no external API).

Groq serves no embedding models, and running them locally keeps retrieval free, private and deterministic.
Both classes load their models lazily, so importing this module never triggers a download.

The defaults were chosen by an ablation on ``evals/retrieval.yaml`` (see the README): arctic-embed-m with its
query instruction plus the 12-layer MiniLM cross-encoder scored 98.4% recall@1, against 93.8% for the previous
bge-small + 6-layer MiniLM pair.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

DEFAULT_EMBEDDING_MODEL = "snowflake/snowflake-arctic-embed-m"
DEFAULT_RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-12-v2"

_RETRIEVAL_INSTRUCTION = "Represent this sentence for searching relevant passages: "
# Retrieval models trained with an instruction on the query side only (documents are embedded as-is).
# fastembed does not add it, and leaving it out costs a lot: arctic-embed-m scores 31% recall@1 without it
# and 91% with it on the dense-only eval.
QUERY_PREFIXES = {
    "snowflake/snowflake-arctic-embed-xs": _RETRIEVAL_INSTRUCTION,
    "snowflake/snowflake-arctic-embed-s": _RETRIEVAL_INSTRUCTION,
    "snowflake/snowflake-arctic-embed-m": _RETRIEVAL_INSTRUCTION,
    "snowflake/snowflake-arctic-embed-l": _RETRIEVAL_INSTRUCTION,
    "BAAI/bge-small-en-v1.5": _RETRIEVAL_INSTRUCTION,
    "BAAI/bge-base-en-v1.5": _RETRIEVAL_INSTRUCTION,
    "BAAI/bge-large-en-v1.5": _RETRIEVAL_INSTRUCTION,
    "mixedbread-ai/mxbai-embed-large-v1": _RETRIEVAL_INSTRUCTION,
}
# ONNX's default of 256 texts per batch pushes activation memory past 1 GB for base-size models on long
# chunks; small batches keep peak memory low for a one-off corpus embedding at little cost in speed.
_BATCH_SIZE = 8


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class Reranker(Protocol):
    name: str

    def scores(self, query: str, texts: Sequence[str]) -> list[float]: ...


class FastEmbedEmbedder:
    """``vector_cache_dir``, when given, stores document embeddings on disk keyed by the model and the exact
    texts, so a restart with an unchanged knowledge base skips re-embedding it (tens of seconds on CPU)."""

    def __init__(
        self,
        model: str = DEFAULT_EMBEDDING_MODEL,
        cache_dir: str | None = None,
        vector_cache_dir: Path | None = None,
    ) -> None:
        self.name = model
        self.query_prefix = QUERY_PREFIXES.get(model, "")
        self._cache_dir = cache_dir
        self._vector_cache_dir = vector_cache_dir
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.name, cache_dir=self._cache_dir)
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        cached = self._vector_cache_path(texts)
        if cached is not None and cached.exists():
            try:
                vectors = np.load(cached)
                if vectors.shape[0] == len(texts):
                    return vectors
            except (OSError, ValueError):
                pass  # a corrupt or partial file is just a miss
        vectors = _normalise(np.array(list(self._load().passage_embed(list(texts), batch_size=_BATCH_SIZE))))
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            partial = cached.with_suffix(".partial.npy")
            np.save(partial, vectors)
            partial.replace(cached)  # atomic, so a crash mid-write never leaves a truncated cache file
        return vectors

    def embed_query(self, text: str) -> np.ndarray:
        return _normalise(np.array(list(self._load().query_embed(self.query_prefix + text))))[0]

    def _vector_cache_path(self, texts: Sequence[str]) -> Path | None:
        if self._vector_cache_dir is None:
            return None
        digest = hashlib.sha256()
        digest.update(self.name.encode())
        for text in texts:
            digest.update(b"\0")
            digest.update(text.encode())
        return self._vector_cache_dir / f"{digest.hexdigest()[:24]}.npy"


class FastEmbedReranker:
    def __init__(self, model: str = DEFAULT_RERANK_MODEL, cache_dir: str | None = None) -> None:
        self.name = model
        self._cache_dir = cache_dir
        self._model: Any = None

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(model_name=self.name, cache_dir=self._cache_dir)
        return [float(score) for score in self._model.rerank(query, list(texts))]


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)
