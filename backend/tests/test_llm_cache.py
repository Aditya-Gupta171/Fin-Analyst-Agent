"""Content-addressed response cache."""

from pathlib import Path

from app.llm.cache import ResponseCache
from app.llm.types import LLMRequest, LLMResponse, Message, Usage


def make_request(model: str = "m", content: str = "hello") -> LLMRequest:
    return LLMRequest(
        model=model, messages=[Message(role="user", content=content)], max_completion_tokens=100
    )


def test_miss_on_empty_cache(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    assert cache.get(make_request()) is None


def test_put_then_get_round_trips_and_marks_cached(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    request = make_request()
    response = LLMResponse(content='{"a": 1}', model="m", usage=Usage(prompt_tokens=5, completion_tokens=2))
    cache.put(request, response)

    hit = cache.get(request)
    assert hit is not None
    assert hit.content == response.content
    assert hit.cached is True
    assert hit.latency_ms == 0


def test_different_requests_get_different_keys(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put(make_request(content="a"), LLMResponse(content="A", model="m"))
    assert cache.get(make_request(content="b")) is None


def test_schema_differences_change_the_key(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    plain = make_request()
    with_schema = plain.model_copy(update={"json_schema": {"type": "object"}})
    cache.put(plain, LLMResponse(content="plain", model="m"))
    assert cache.get(with_schema) is None


def test_corrupted_cache_file_is_treated_as_a_miss(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    request = make_request()
    path = cache._path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert cache.get(request) is None
