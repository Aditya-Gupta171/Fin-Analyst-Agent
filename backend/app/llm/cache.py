"""Content-addressed cache of model responses.

Re-running an analysis of the same document with the same prompts, schemas and models costs nothing and
returns the same result, which keeps demos fast and tests reproducible under tight provider rate limits.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.llm.types import LLMRequest, LLMResponse


class ResponseCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @staticmethod
    def key(request: LLMRequest) -> str:
        canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _path(self, request: LLMRequest) -> Path:
        key = self.key(request)
        return self.directory / key[:2] / f"{key}.json"

    def get(self, request: LLMRequest) -> LLMResponse | None:
        path = self._path(request)
        if not path.exists():
            return None
        try:
            response = LLMResponse.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        return response.model_copy(update={"cached": True, "latency_ms": 0})

    def put(self, request: LLMRequest, response: LLMResponse) -> None:
        path = self._path(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response.model_dump_json(), encoding="utf-8")
