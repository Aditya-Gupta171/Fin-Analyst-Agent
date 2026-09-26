"""FastEmbedEmbedder's own logic — query instruction and the on-disk document-vector cache — exercised with a
stand-in for the ONNX model, so nothing is downloaded and the tests stay offline."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.knowledge.embeddings import QUERY_PREFIXES, FastEmbedEmbedder


class FakeModel:
    def __init__(self) -> None:
        self.passage_calls = 0
        self.queries: list[str] = []

    def passage_embed(self, texts, batch_size=256):
        self.passage_calls += 1
        return [np.array([len(t), 1.0]) for t in texts]

    def query_embed(self, text):
        self.queries.append(text)
        return [np.array([1.0, 0.0])]


def _embedder(model: str, cache: Path | None = None) -> tuple[FastEmbedEmbedder, FakeModel]:
    embedder = FastEmbedEmbedder(model, vector_cache_dir=cache)
    fake = FakeModel()
    embedder._model = fake
    return embedder, fake


def test_queries_get_the_models_retrieval_instruction() -> None:
    embedder, fake = _embedder("snowflake/snowflake-arctic-embed-m")
    embedder.embed_query("receivables growth")
    assert fake.queries == [QUERY_PREFIXES["snowflake/snowflake-arctic-embed-m"] + "receivables growth"]


def test_a_model_without_an_instruction_embeds_the_query_as_is() -> None:
    embedder, fake = _embedder("thenlper/gte-base")
    embedder.embed_query("receivables growth")
    assert fake.queries == ["receivables growth"]


def test_document_vectors_are_normalised(tmp_path: Path) -> None:
    embedder, _ = _embedder("m", tmp_path)
    vectors = embedder.embed_documents(["abc", "de"])
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_document_vectors_are_reused_from_disk_across_instances(tmp_path: Path) -> None:
    first, first_model = _embedder("m", tmp_path)
    vectors = first.embed_documents(["abc", "de"])

    second, second_model = _embedder("m", tmp_path)  # a restart: new process, same corpus
    again = second.embed_documents(["abc", "de"])

    assert first_model.passage_calls == 1
    assert second_model.passage_calls == 0
    assert np.array_equal(vectors, again)


def test_a_changed_corpus_or_model_misses_the_cache(tmp_path: Path) -> None:
    embedder, _ = _embedder("m", tmp_path)
    embedder.embed_documents(["abc", "de"])

    edited, edited_model = _embedder("m", tmp_path)
    edited.embed_documents(["abc", "de!"])
    other, other_model = _embedder("other-model", tmp_path)
    other.embed_documents(["abc", "de"])

    assert edited_model.passage_calls == 1
    assert other_model.passage_calls == 1


def test_a_corrupt_cache_file_is_treated_as_a_miss(tmp_path: Path) -> None:
    embedder, _ = _embedder("m", tmp_path)
    embedder.embed_documents(["abc"])
    for file in tmp_path.glob("*.npy"):
        file.write_bytes(b"not a numpy file")

    again, model = _embedder("m", tmp_path)
    assert again.embed_documents(["abc"]).shape == (1, 2)
    assert model.passage_calls == 1
