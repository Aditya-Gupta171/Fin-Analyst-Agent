"""Schema-constrained generation into Pydantic models.

Groq's strict JSON-schema mode guarantees well-formed output that matches the schema, but only for a
restricted dialect: every property required, ``additionalProperties: false`` everywhere, optional values
expressed as nullable types and no ``$ref``. :func:`strict_schema` converts a Pydantic model's schema into
that dialect. The response is still validated with Pydantic (for constraints the dialect cannot express), with
one repair attempt that shows the model its own validation errors.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.types import (
    CallRecord,
    Effort,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMSchemaError,
    Message,
)

# Dropped from the schema sent to the provider; Pydantic still enforces them on the parsed output.
MAX_ATTEMPTS = 3  # the first answer plus up to two repairs
_UNSUPPORTED_KEYWORDS = {
    "title",
    "default",
    "examples",
    "maxLength",
    "minLength",
    "pattern",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(definitions[node["$ref"].split("/")[-1]]))
            if "anyOf" in node:
                options = [resolve(option) for option in node["anyOf"]]
                non_null = [option for option in options if option.get("type") != "null"]
                if len(non_null) == 1 and len(options) == 2:
                    merged = dict(non_null[0])
                    kind = merged.get("type")
                    merged["type"] = [kind, "null"] if isinstance(kind, str) else [*kind, "null"]
                    if "enum" in merged:
                        merged["enum"] = [*merged["enum"], None]
                    return {k: v for k, v in merged.items() if k not in _UNSUPPORTED_KEYWORDS}
                return {"anyOf": options}
            result = {}
            for key, value in node.items():
                if key == "properties":  # property names are data, not schema keywords: keep "title" fields
                    result[key] = {name: resolve(schema) for name, schema in value.items()}
                elif key not in _UNSUPPORTED_KEYWORDS:
                    result[key] = resolve(value)
            if result.get("type") == "object" and "properties" in result:
                result["required"] = list(result["properties"])
                result["additionalProperties"] = False
            return result
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def generate[T: BaseModel](
    client: LLMClient,
    output: type[T],
    *,
    node: str,
    model: str,
    system: str,
    user: str,
    max_completion_tokens: int,
    effort: Effort = "medium",
    record: Callable[[CallRecord], None] | None = None,
    validate: Callable[[T], list[str]] | None = None,
) -> T:
    """Generate an instance of ``output``; ``validate`` may add domain checks (e.g. unknown references),
    and its problems trigger the same repair attempts as schema validation errors."""
    messages = [Message(role="system", content=system), Message(role="user", content=user)]
    schema = strict_schema(output)
    attempts = 0
    usage_totals = [0, 0, 0]
    latency = 0
    responses = 0
    cached = True
    problems: list[str] = []

    def call(conversation: list[Message]) -> LLMResponse:
        nonlocal attempts, latency, cached, responses
        attempts += 1
        response = client.complete(
            LLMRequest(
                model=model,
                messages=conversation,
                max_completion_tokens=max_completion_tokens,
                reasoning_effort=effort,
                json_schema=schema,
                schema_name=output.__name__,
            )
        )
        usage_totals[0] += response.usage.prompt_tokens
        usage_totals[1] += response.usage.completion_tokens
        usage_totals[2] += response.usage.reasoning_tokens
        latency += response.latency_ms
        responses += 1
        cached = cached and response.cached
        return response

    def finish(outcome: str, error: str | None = None) -> None:
        if record is not None:
            record(
                CallRecord(
                    node=node,
                    model=model,
                    prompt_tokens=usage_totals[0],
                    completion_tokens=usage_totals[1],
                    reasoning_tokens=usage_totals[2],
                    latency_ms=latency,
                    cached=cached and responses > 0,
                    attempts=attempts,
                    outcome=outcome,  # type: ignore[arg-type]
                    error=error,
                )
            )

    conversation = list(messages)
    repairs: list[str] = []
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = call(conversation)
            content = response.content
        except LLMSchemaError as exc:
            if attempt == MAX_ATTEMPTS - 1:
                finish("failed", str(exc))
                raise
            content, parsed, problems = (
                exc.failed_generation,
                None,
                [exc.message],
            )  # repair like a local failure
        except LLMError as exc:
            finish("failed", str(exc))
            raise
        else:
            try:
                parsed = output.model_validate_json(content)
                problems = validate(parsed) if validate else []
            except ValidationError as exc:
                parsed = None
                problems = [f"{error['msg']} at {'.'.join(map(str, error['loc']))}" for error in exc.errors()]
        if not problems and parsed is not None:
            finish("ok" if attempt == 0 else "repaired", "; ".join(repairs[:6]) or None)
            return parsed
        repairs += problems
        conversation = [
            *messages,
            Message(role="assistant", content=content or "{}"),
            Message(
                role="user",
                content="Your previous answer has these problems. Return a corrected answer that fixes"
                " all of them:\n- " + "\n- ".join(problems[:12]),
            ),
        ]
    finish("failed", "; ".join(problems[:5]))
    raise LLMError(f"{node}: output still invalid after repair: {'; '.join(problems[:5])}")
