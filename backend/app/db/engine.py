"""Async SQLAlchemy engine and session factory, built from ``Settings.database_url``.

The same models run against SQLite (tests, first-run convenience) and Postgres (Supabase in production) —
see ``app/db/models.py`` for why the schema avoids Postgres-only column types.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base


def make_engine(database_url: str) -> AsyncEngine:
    if "+asyncpg" not in database_url:
        return create_async_engine(database_url)
    return create_async_engine(
        database_url,
        # Supabase's connection pooler (Supavisor/PgBouncer, transaction mode) multiplexes many client
        # connections onto few server ones, so asyncpg's per-connection prepared-statement cache goes
        # stale and raises DuplicatePreparedStatementError. Disabling it is the documented fix.
        connect_args={"statement_cache_size": 0},
        # The pooler also closes connections it considers idle without telling this side, which
        # otherwise surfaces mid-request as `asyncpg.exceptions.InterfaceError: connection is closed`.
        # pre_ping tests a connection before handing it out and transparently replaces a dead one;
        # recycle drops connections proactively before the pooler's own idle timeout gets there first.
        pool_pre_ping=True,
        pool_recycle=280,
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create tables directly, bypassing Alembic — used by tests and by SQLite's zero-setup default.

    A real Postgres deployment should run ``alembic upgrade head`` instead (see ``backend/alembic/``).
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
