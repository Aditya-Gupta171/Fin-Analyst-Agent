"""A scripted stand-in for ``app.llm.gateway.Gateway`` so agent tests never make a network call.

Shared by the agent-graph tests and the API tests that exercise a full (agent-mode) analysis through the
job runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.llm.types import CallRecord, LLMError


@dataclass
class FakeGateway:
    """Replays scripted outputs per node prefix; supports repair sequences and forced errors."""

    script: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)

    def generate(
        self,
        output: type[BaseModel],
        *,
        node: str,
        tier: str,
        system: str,
        user: str,
        max_completion_tokens: int,
        effort: str = "medium",
        validate=None,
    ) -> BaseModel:
        prefix = node.split(":")[0]
        queue = self.script.get(prefix, self.script.get(node, []))
        problems: list[str] = []
        for attempt, item in enumerate(list(queue)):
            self.calls.append(
                CallRecord(
                    node=node,
                    model="fake",
                    prompt_tokens=1,
                    completion_tokens=1,
                    reasoning_tokens=0,
                    latency_ms=0,
                    cached=False,
                    attempts=attempt + 1,
                    outcome="ok",
                )
            )
            if isinstance(item, Exception):
                raise item
            problems = validate(item) if validate else []
            if not problems:
                return item
        raise LLMError(f"{node}: fake gateway exhausted its script; last problems {problems}")
