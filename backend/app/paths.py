"""Filesystem locations shared across the backend."""

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
RULES_DIR = REPO_ROOT / "rules"
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
# Knowledge-base document embeddings, cached so a restart skips re-embedding an unchanged corpus.
EMBEDDING_CACHE_DIR = REPO_ROOT / "data" / "embedding_cache"
