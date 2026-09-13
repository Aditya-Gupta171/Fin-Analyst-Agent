"""Provider-neutral request, response and trace types for the LLM gateway."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Effort = Literal["low", "medium", "high"]


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    model: str
    messages: list[Message]
    max_completion_tokens: int
    reasoning_effort: Effort = "medium"
    json_schema: dict[str, Any] | None = None
    schema_name: str = "output"
    temperature: float | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMResponse(BaseModel):
    content: str
    usage: Usage = Field(default_factory=Usage)
    model: str
    cached: bool = False
    latency_ms: int = 0


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


class LLMError(RuntimeError):
    """The provider could not produce a usable response after retries."""


class LLMSchemaError(LLMError):
    """The provider rejected its own generation as not matching the schema; the attempt can be repaired."""

    def __init__(self, message: str, failed_generation: str) -> None:
        super().__init__(message)
        self.message = message
        self.failed_generation = failed_generation


class CallRecord(BaseModel):
    """One model call, kept in the analysis trace."""

    node: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    latency_ms: int
    cached: bool
    attempts: int
    outcome: Literal["ok", "repaired", "failed"]
    error: str | None = None  # the failure, or the problems a repair fixed
