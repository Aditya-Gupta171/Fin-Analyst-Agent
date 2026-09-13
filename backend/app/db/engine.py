"""Async SQLAlchemy engine and session factory, built from ``Settings.database_url``.

The same models run against SQLite (tests, first-run convenience) and Postgres (Supabase in production) —
see ``app/db/models.py`` for why the schema avoids Postgres-only column types.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create tables directly, bypassing Alembic — used by tests and by SQLite's zero-setup default.

    A real Postgres deployment should run ``alembic upgrade head`` instead (see ``backend/alembic/``).
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
