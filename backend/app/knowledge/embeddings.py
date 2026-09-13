"""Dense embeddings and cross-encoder reranking, run locally with fastembed (ONNX, no external API).

Groq serves no embedding models, and running them locally keeps retrieval free, private and deterministic.
Both classes load their models lazily, so importing this module never triggers a download.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class Reranker(Protocol):
    name: str

    def scores(self, query: str, texts: Sequence[str]) -> list[float]: ...


class FastEmbedEmbedder:
    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL, cache_dir: str | None = None) -> None:
        self.name = model
        self._cache_dir = cache_dir
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.name, cache_dir=self._cache_dir)
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return _normalise(np.array(list(self._load().passage_embed(list(texts)))))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalise(np.array(list(self._load().query_embed(text))))[0]


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
