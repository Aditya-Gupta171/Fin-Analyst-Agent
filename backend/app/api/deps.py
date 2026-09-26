"""FastAPI dependencies: the shared ``AppState`` and a request-scoped DB session."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.state import AppState


def get_app_state(request: Request) -> AppState:
    return request.app.state.app_state


async def get_session(
    state: Annotated[AppState, Depends(get_app_state)],
) -> AsyncIterator[AsyncSession]:
    async with state.session_factory() as session:
        yield session


AppStateDep = Annotated[AppState, Depends(get_app_state)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def require_api_key(
    request: Request, state: AppStateDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    """No-op when ``settings.api_key`` is unset; otherwise mutating requests (POST, ...) need
    ``Authorization: Bearer <key>``. Reads stay open: a browser's ``EventSource`` (live progress) and the
    PDF evidence viewer can't attach an Authorization header, and nothing a read returns spends LLM budget
    or changes what the system has learned."""
    expected = state.settings.api_key
    if expected is None or request.method in _READ_ONLY_METHODS:
        return
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not secrets.compare_digest(token.encode(), expected.get_secret_value().encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")


RequireApiKey = Depends(require_api_key)
