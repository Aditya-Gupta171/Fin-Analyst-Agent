"""Filesystem locations shared across the backend."""

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
RULES_DIR = REPO_ROOT / "rules"
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
