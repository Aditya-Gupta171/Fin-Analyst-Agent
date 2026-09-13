"""FastAPI dependencies: the shared ``AppState`` and a request-scoped DB session."""

from __future__ import annotations

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


async def require_api_key(state: AppStateDep, authorization: Annotated[str | None, Header()] = None) -> None:
    """No-op when ``settings.api_key`` is unset; otherwise requires ``Authorization: Bearer <key>``."""
    expected = state.settings.api_key
    if expected is None:
        return
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    if token != expected.get_secret_value():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid API key")


RequireApiKey = Depends(require_api_key)
