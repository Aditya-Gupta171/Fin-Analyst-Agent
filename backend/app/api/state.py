"""The objects every request needs, built once at startup and hung off ``app.state.app_state``."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.engine.catalog import Catalog
from app.jobs.progress import ProgressStore
from app.jobs.queue import JobQueue
from app.knowledge.base import KnowledgeBase
from app.llm.gateway import Gateway
from app.settings import Settings


@dataclass
class AppState:
    settings: Settings
    catalog: Catalog
    kb: KnowledgeBase | None
    gateway: Gateway | None
    engine: AsyncEngine
    session_factory: async_sessionmaker
    queue: JobQueue
    progress: ProgressStore
