# Backend image for Hugging Face Spaces (Docker SDK). Build context is the repo root, not backend/,
# because app/paths.py resolves RULES_DIR and KNOWLEDGE_DIR as siblings of backend/ — this mirrors that
# same layout inside the image instead of hardcoding container-specific paths in application code.
FROM python:3.12-slim

WORKDIR /app

COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip install --no-cache-dir "./backend[embeddings]"

COPY rules rules
COPY knowledge knowledge
COPY backend/alembic backend/alembic
COPY backend/alembic.ini backend/alembic.ini

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Migrating on every start keeps the schema in sync with no manual step; a failed migration fails the
# container loudly (the `&&`) instead of serving traffic against a stale schema.
CMD ["sh", "-c", "cd backend && alembic upgrade head && uvicorn app.api.main:app --host 0.0.0.0 --port 8000"]
