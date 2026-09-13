"""Runtime configuration, read from environment variables or the repository's ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.paths import REPO_ROOT, RULES_DIR


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8-sig", extra="ignore"
    )

    groq_api_key: SecretStr | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # The reasoning model plans, investigates and critiques; the fast model handles lighter structured tasks.
    reasoning_model: str = "openai/gpt-oss-120b"
    fast_model: str = "openai/gpt-oss-20b"
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 4
    llm_cache_dir: Path | None = REPO_ROOT / "data" / "llm_cache"

    # Defaults to a local SQLite file so the API runs with no setup; point it at Supabase (or any Postgres)
    # with e.g. postgresql+asyncpg://user:pass@host:5432/postgres to persist there instead.
    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'data' / 'app.db'}"
    cors_origins: list[str] = ["*"]
    api_key: SecretStr | None = None  # when set, POST/DELETE endpoints require `Authorization: Bearer <key>`
    max_concurrent_analyses: int = 2
    # Where an approved candidate rule is written (app/engine/promotion.py). A parameter, not a bare
    # import of RULES_DIR, purely so tests can redirect it at a throwaway copy of the rules tree.
    rules_dir: Path = RULES_DIR

    @field_validator("database_url")
    @classmethod
    def _use_the_async_postgres_driver(cls, url: str) -> str:
        """Supabase's dashboard hands out a plain ``postgresql://`` URL; upgrade it to the async driver
        (``asyncpg``) this app's engine needs, so pasting it into ``.env`` as-is just works."""
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url.removeprefix(prefix)
        return url

    @property
    def llm_enabled(self) -> bool:
        return self.groq_api_key is not None and bool(self.groq_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
