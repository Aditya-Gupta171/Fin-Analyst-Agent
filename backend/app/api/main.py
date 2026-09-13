"""Builds the FastAPI app: loads the Catalog/KnowledgeBase/Gateway once, wires the DB and job queue, and
mounts the document/analysis/rules/health routers.

``build_app`` takes explicit overrides for ``catalog``/``kb``/``gateway`` (a sentinel distinguishes "not
given, compute the real one" from "given as None") so tests can swap in a scripted ``FakeGateway`` and skip
the fastembed-backed knowledge base, keeping the test suite offline and fast.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.routes import analyses, documents, health, rules
from app.api.state import AppState
from app.db.engine import create_all, make_engine, make_session_factory
from app.db.models import Analysis
from app.engine.catalog import Catalog
from app.jobs.progress import ProgressStore
from app.jobs.queue import JobQueue
from app.knowledge.base import KnowledgeBase
from app.llm.gateway import Gateway, build_gateway
from app.paths import KNOWLEDGE_DIR, RULES_DIR
from app.settings import Settings, get_settings

_UNSET: Any = object()


def build_app(
    settings: Settings | None = None,
    *,
    catalog: Catalog | Any = _UNSET,
    kb: KnowledgeBase | Any | None = _UNSET,
    gateway: Gateway | Any | None = _UNSET,
    eager_jobs: bool = False,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Resolved on startup, not at import time, so importing this module never loads fastembed
        # models or builds a live Groq client — only running the app (uvicorn, or a TestClient) does.
        resolved_catalog = Catalog.load(RULES_DIR) if catalog is _UNSET else catalog
        resolved_kb = _load_knowledge_base(resolved_catalog) if kb is _UNSET else kb
        resolved_gateway = build_gateway(settings) if gateway is _UNSET else gateway
        engine = make_engine(settings.database_url)
        await create_all(engine)
        session_factory = make_session_factory(engine)
        await _reconcile_interrupted_jobs(session_factory)
        app.state.app_state = AppState(
            settings=settings,
            catalog=resolved_catalog,
            kb=resolved_kb,
            gateway=resolved_gateway,
            engine=engine,
            session_factory=session_factory,
            queue=JobQueue(settings.max_concurrent_analyses, eager=eager_jobs),
            progress=ProgressStore(),
        )
        yield
        await engine.dispose()

    app = FastAPI(title="Financial Analyst Agent", version="1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(analyses.router)
    app.include_router(rules.router)
    return app


def _load_knowledge_base(catalog: Catalog) -> KnowledgeBase | None:
    try:
        from app.knowledge.embeddings import FastEmbedEmbedder, FastEmbedReranker

        embedder, reranker = FastEmbedEmbedder(), FastEmbedReranker()
    except ImportError:
        embedder, reranker = None, None
    return KnowledgeBase.load(KNOWLEDGE_DIR, catalog, embedder=embedder, reranker=reranker)


async def _reconcile_interrupted_jobs(session_factory) -> None:
    """A process restart abandons any in-flight job; mark it failed instead of leaving it stuck."""
    async with session_factory() as session:
        stuck = await session.scalars(select(Analysis).where(Analysis.status.in_(["queued", "running"])))
        for analysis in stuck:
            analysis.status = "failed"
            analysis.error_message = "interrupted by restart"
        await session.commit()


app = build_app()
