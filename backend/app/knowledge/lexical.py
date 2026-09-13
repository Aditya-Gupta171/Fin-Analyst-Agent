"""Okapi BM25 over chunk text, with the document title and section heading weighted up."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from app.knowledge.chunking import Chunk
from app.knowledge.text import tokenize

TITLE_WEIGHT = 2  # title and heading terms count this many times


class BM25Index:
    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._frequencies: list[Counter[str]] = []
        lengths: list[int] = []
        document_frequency: Counter[str] = Counter()
        for chunk in chunks:
            tokens = tokenize(chunk.text) + tokenize(f"{chunk.doc_title} {chunk.heading}") * TITLE_WEIGHT
            counts = Counter(tokens)
            self._frequencies.append(counts)
            lengths.append(len(tokens))
            document_frequency.update(counts.keys())
        self._lengths = lengths
        self._average_length = sum(lengths) / len(lengths) if lengths else 0.0
        n = len(chunks)
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in document_frequency.items()
        }

    def scores(self, query: str) -> list[float]:
        terms = Counter(tokenize(query))
        results = []
        for counts, length in zip(self._frequencies, self._lengths, strict=True):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * length / self._average_length)
            for term, query_count in terms.items():
                frequency = counts.get(term)
                if frequency:
                    score += query_count * self._idf[term] * frequency * (self.k1 + 1) / (frequency + norm)
            results.append(score)
        return results
