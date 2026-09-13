"""Runtime configuration, read from environment variables or the repository's ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.paths import REPO_ROOT


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

    @property
    def llm_enabled(self) -> bool:
        return self.groq_api_key is not None and bool(self.groq_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
