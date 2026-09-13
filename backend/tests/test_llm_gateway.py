"""Gateway: model-tier selection, call recording, and construction from settings."""

from pydantic import BaseModel

from app.llm.gateway import Gateway, build_gateway
from app.llm.types import CallRecord, LLMRequest, LLMResponse, Usage
from app.settings import Settings


class Answer(BaseModel):
    value: str


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(content='{"value": "ok"}', model=request.model, usage=Usage(prompt_tokens=1))


def test_model_for_selects_the_configured_model_per_tier() -> None:
    gateway = Gateway(FakeClient(), reasoning_model="big", fast_model="small")
    assert gateway.model_for("reasoning") == "big"
    assert gateway.model_for("fast") == "small"


def test_generate_uses_the_right_model_and_records_the_call() -> None:
    client = FakeClient()
    gateway = Gateway(client, reasoning_model="big", fast_model="small")
    result = gateway.generate(Answer, node="n", tier="fast", system="s", user="u", max_completion_tokens=5)
    assert result.value == "ok"
    assert client.requests[0].model == "small"
    assert gateway.calls[0].node == "n"


def test_on_call_hook_fires_alongside_the_trace() -> None:
    seen: list[CallRecord] = []
    gateway = Gateway(FakeClient(), reasoning_model="big", fast_model="small", on_call=seen.append)
    gateway.generate(Answer, node="n", tier="reasoning", system="s", user="u", max_completion_tokens=5)
    assert len(seen) == 1
    assert seen[0] is gateway.calls[0]


def test_build_gateway_returns_none_without_an_api_key() -> None:
    assert build_gateway(Settings(groq_api_key=None)) is None


def test_build_gateway_wires_the_configured_models(tmp_path) -> None:
    settings = Settings(
        groq_api_key="gsk_test", reasoning_model="r-model", fast_model="f-model", llm_cache_dir=tmp_path
    )
    gateway = build_gateway(settings)
    assert gateway is not None
    assert (gateway.reasoning_model, gateway.fast_model) == ("r-model", "f-model")
