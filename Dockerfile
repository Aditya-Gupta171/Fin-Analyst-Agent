# Backend image, built for Render's free web-service tier (512 MB RAM). Build context is the repo root,
# not backend/, because app/paths.py resolves RULES_DIR and KNOWLEDGE_DIR as siblings of backend/ — this
# mirrors that same layout inside the image instead of hardcoding container-specific paths in app code.
FROM python:3.12-slim

WORKDIR /app

COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
# No `[embeddings]` extra here: fastembed's ONNX runtime plus its loaded models push memory well past what
# a 512 MB free-tier instance can hold comfortably. The knowledge base's own loader (app/api/main.py's
# _load_knowledge_base) already falls back to lexical-only (BM25) search when fastembed isn't importable —
# a real retrieval-quality trade-off (measured in evals/retrieval.yaml), not a code path improvised here.
# A bigger instance can restore hybrid search by installing "./backend[embeddings]" instead.
RUN pip install --no-cache-dir "./backend"

COPY rules rules
COPY knowledge knowledge
COPY backend/alembic backend/alembic
COPY backend/alembic.ini backend/alembic.ini

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Migrating on every start keeps the schema in sync with no manual step; a failed migration fails the
# container loudly (the `&&`) instead of serving traffic against a stale schema. $PORT is Render's own
# convention (it assigns the port at runtime); it falls back to 8000 for a plain local `docker run`.
CMD ["sh", "-c", "cd backend && alembic upgrade head && uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
