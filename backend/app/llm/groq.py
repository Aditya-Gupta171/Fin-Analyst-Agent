"""Groq chat completions over its OpenAI-compatible HTTP API."""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from app.llm.budget import TokenBudget, estimate_tokens, parse_duration
from app.llm.cache import ResponseCache
from app.llm.types import LLMError, LLMRequest, LLMResponse, LLMSchemaError, Usage

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class GroqClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 90.0,
        max_retries: int = 4,
        budget: TokenBudget | None = None,
        cache: ResponseCache | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )
        self.max_retries = max_retries
        self.budget = budget or TokenBudget()
        self.cache = cache
        self._sleep = sleep

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.cache is not None and (hit := self.cache.get(request)) is not None:
            return hit

        payload = _payload(request)
        reserve = (
            estimate_tokens("".join(m.content for m in request.messages)) + request.max_completion_tokens
        )
        last_error = "no attempt made"
        for attempt in range(self.max_retries + 1):
            self.budget.acquire(request.model, reserve)
            started = time.perf_counter()
            try:
                response = self._http.post("/chat/completions", json=payload)
            except httpx.TransportError as exc:
                last_error = f"transport error: {exc}"
                self._backoff(attempt, None)
                continue
            self.budget.update(request.model, response.headers)

            if response.is_success:
                result = _parse(response.json(), request.model, int((time.perf_counter() - started) * 1000))
                if self.cache is not None:
                    self.cache.put(request, result)
                return result

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            error = _error_body(response)
            if error.get("code") == "json_validate_failed":
                raise LLMSchemaError(error.get("message", last_error), error.get("failed_generation", ""))
            if response.status_code not in _RETRYABLE:
                raise LLMError(last_error)
            retry_after = parse_duration(response.headers.get("retry-after"))
            if response.status_code == 429:
                self.budget.exhaust(request.model, retry_after or 10.0)
            self._backoff(attempt, retry_after)
        raise LLMError(f"gave up after {self.max_retries + 1} attempts; last error {last_error}")

    def _backoff(self, attempt: int, retry_after: float | None) -> None:
        if attempt >= self.max_retries:
            return
        delay = retry_after if retry_after is not None else min(30.0, 2**attempt + random.random())
        self._sleep(delay)

    def close(self) -> None:
        self._http.close()


def _payload(request: LLMRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": [message.model_dump() for message in request.messages],
        "max_completion_tokens": request.max_completion_tokens,
        "reasoning_effort": request.reasoning_effort,
        "include_reasoning": False,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.json_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "strict": True, "schema": request.json_schema},
        }
    return payload


def _error_body(response: httpx.Response) -> dict[str, Any]:
    try:
        error = response.json().get("error")
    except ValueError:
        return {}
    return error if isinstance(error, dict) else {}


def _parse(body: dict[str, Any], model: str, latency_ms: int) -> LLMResponse:
    try:
        choice = body["choices"][0]
        content = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected response shape: {str(body)[:300]}") from exc
    if choice.get("finish_reason") == "length":
        raise LLMError("response truncated at max_completion_tokens")
    usage = body.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    return LLMResponse(
        content=content,
        model=body.get("model", model),
        latency_ms=latency_ms,
        usage=Usage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning_tokens=details.get("reasoning_tokens", 0),
        ),
    )
