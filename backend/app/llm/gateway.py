"""The single object agent code talks to: model tiers, structured generation and the call trace."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.llm.budget import TokenBudget
from app.llm.cache import ResponseCache
from app.llm.groq import GroqClient
from app.llm.structured import generate
from app.llm.types import CallRecord, Effort, LLMClient
from app.settings import Settings

Tier = Literal["reasoning", "fast"]


@dataclass
class Gateway:
    client: LLMClient
    reasoning_model: str
    fast_model: str
    calls: list[CallRecord] = field(default_factory=list)
    on_call: Callable[[CallRecord], None] | None = None

    def model_for(self, tier: Tier) -> str:
        return self.reasoning_model if tier == "reasoning" else self.fast_model

    def generate[T: BaseModel](
        self,
        output: type[T],
        *,
        node: str,
        tier: Tier,
        system: str,
        user: str,
        max_completion_tokens: int,
        effort: Effort = "medium",
        validate: Callable[[T], list[str]] | None = None,
    ) -> T:
        return generate(
            self.client,
            output,
            node=node,
            model=self.model_for(tier),
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
            effort=effort,
            record=self._record,
            validate=validate,
        )

    def _record(self, call: CallRecord) -> None:
        self.calls.append(call)
        if self.on_call is not None:
            self.on_call(call)


def build_gateway(settings: Settings) -> Gateway | None:
    """A gateway backed by Groq, or ``None`` when no API key is configured (rules-only analysis)."""
    if not settings.llm_enabled or settings.groq_api_key is None:
        return None
    client = GroqClient(
        settings.groq_api_key.get_secret_value(),
        base_url=settings.groq_base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        budget=TokenBudget(),
        cache=ResponseCache(settings.llm_cache_dir) if settings.llm_cache_dir else None,
    )
    return Gateway(client, settings.reasoning_model, settings.fast_model)
