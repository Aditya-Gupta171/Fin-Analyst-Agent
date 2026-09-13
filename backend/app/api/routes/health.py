"""Liveness/readiness: confirms the catalog/knowledge base loaded and the database is reachable."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import AppStateDep, SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(state: AppStateDep, session: SessionDep) -> dict:
    await session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "catalog_version": state.catalog.version,
        "kb_version": state.kb.version if state.kb else None,
        "llm_enabled": state.gateway is not None,
    }
