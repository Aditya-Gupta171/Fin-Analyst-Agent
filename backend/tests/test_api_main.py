"""``_load_knowledge_base`` must degrade to lexical-only search when fastembed isn't installed — the
deployed backend on a memory-constrained host (Render's free tier) omits it deliberately (see the root
Dockerfile) rather than risk an OOM from its ONNX models.

Simulates "not installed" by making ``import fastembed`` raise, rather than actually uninstalling it from
this dev environment: a real regression here was the constructor call (``FastEmbedEmbedder()``) not
actually triggering fastembed's own (lazily-done) import, so the failure surfaced later and uncaught, deep
inside the first real search call instead of at startup.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from app.api.main import _load_knowledge_base
from app.engine.catalog import Catalog


def test_load_knowledge_base_falls_back_to_lexical_only_without_fastembed(catalog: Catalog) -> None:
    with patch.dict(sys.modules, {"fastembed": None}):
        kb = _load_knowledge_base(catalog)

    assert kb is not None
    assert kb.retriever.mode == "lexical"
    # a real search must still work end to end, not just construct without crashing
    assert kb.search("receivables growth")
