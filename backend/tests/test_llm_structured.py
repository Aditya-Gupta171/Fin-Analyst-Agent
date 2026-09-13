"""Strict-schema conversion and the generate/validate/repair loop, against a scripted fake client."""

from typing import Literal

import pytest
from pydantic import BaseModel

from app.llm.structured import generate, strict_schema
from app.llm.types import CallRecord, LLMError, LLMRequest, LLMResponse, LLMSchemaError, Usage


class Nested(BaseModel):
    tag: str
    weight: float | None = None


class Sample(BaseModel):
    name: str
    status: Literal["a", "b"]
    tags: list[str]
    detail: Nested | None = None


def test_strict_schema_marks_every_property_required_and_closed() -> None:
    schema = strict_schema(Sample)
    assert schema["required"] == ["name", "status", "tags", "detail"]
    assert schema["additionalProperties"] is False
    assert "$defs" not in schema


def test_strict_schema_makes_optional_fields_nullable_instead_of_absent() -> None:
    schema = strict_schema(Sample)
    detail_type = schema["properties"]["detail"]["type"]
    assert set(detail_type) == {"object", "null"}


def test_strict_schema_resolves_nested_refs_and_closes_them_too() -> None:
    schema = strict_schema(Sample)
    detail = schema["properties"]["detail"]
    assert detail["additionalProperties"] is False
    assert set(detail["required"]) == {"tag", "weight"}


def test_strict_schema_drops_keywords_the_provider_does_not_support() -> None:
    class Bounded(BaseModel):
        count: int = 0

    schema = strict_schema(Bounded)
    assert "default" not in schema["properties"]["count"]


class Answer(BaseModel):
    value: str


class FakeClient:
    """Replays a scripted sequence of responses or exceptions, one per call."""

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self.script = list(script)
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def response(content: str, **usage: int) -> LLMResponse:
    return LLMResponse(content=content, model="m", usage=Usage(**usage))


def generate_answer(client: FakeClient, *, validate=None, records: list[CallRecord] | None = None) -> Answer:
    return generate(
        client,
        Answer,
        node="n",
        model="m",
        system="s",
        user="u",
        max_completion_tokens=10,
        record=records.append if records is not None else None,
        validate=validate,
    )


def test_first_try_success_is_recorded_as_ok() -> None:
    records: list[CallRecord] = []
    client = FakeClient([response('{"value": "x"}', prompt_tokens=3, completion_tokens=1)])
    result = generate_answer(client, records=records)
    assert result.value == "x"
    assert len(client.calls) == 1
    assert records[0].outcome == "ok"
    assert records[0].attempts == 1


def test_schema_error_from_provider_triggers_one_repair_and_succeeds() -> None:
    records: list[CallRecord] = []
    client = FakeClient([LLMSchemaError("bad json", "{not json"), response('{"value": "fixed"}')])
    result = generate_answer(client, records=records)
    assert result.value == "fixed"
    assert len(client.calls) == 2
    # the repair turn shows the model its own broken output plus the problem
    assert client.calls[1].messages[-2].content == "{not json"
    assert "bad json" in client.calls[1].messages[-1].content
    assert records[0].outcome == "repaired"


def test_pydantic_validation_failure_triggers_repair() -> None:
    client = FakeClient([response('{"value": 5}'), response('{"value": "ok"}')])
    result = generate_answer(client)
    assert result.value == "ok"


def test_custom_validate_hook_can_force_a_repair() -> None:
    seen = []

    def validate(answer: Answer) -> list[str]:
        seen.append(answer.value)
        return ["must not be 'bad'"] if answer.value == "bad" else []

    client = FakeClient([response('{"value": "bad"}'), response('{"value": "good"}')])
    result = generate_answer(client, validate=validate)
    assert result.value == "good"
    assert seen == ["bad", "good"]


def test_gives_up_after_max_attempts_and_raises() -> None:
    records: list[CallRecord] = []
    client = FakeClient([response('{"value": "bad"}')] * 3)
    with pytest.raises(LLMError, match="still invalid after repair"):
        generate_answer(client, validate=lambda a: ["always wrong"], records=records)
    assert len(client.calls) == 3
    assert records[0].outcome == "failed"


def test_transport_error_is_not_retried_by_generate() -> None:
    client = FakeClient([LLMError("boom")])
    with pytest.raises(LLMError, match="boom"):
        generate_answer(client)
    assert len(client.calls) == 1


def test_final_schema_error_after_last_attempt_is_raised_not_repaired() -> None:
    # MAX_ATTEMPTS is 3: two repairable schema errors, then a third that exhausts the budget and is raised
    # as-is.
    client = FakeClient(
        [LLMSchemaError("bad", "{"), LLMSchemaError("still bad", "{"), LLMSchemaError("final", "{")]
    )
    with pytest.raises(LLMSchemaError, match="final"):
        generate(client, Answer, node="n", model="m", system="s", user="u", max_completion_tokens=10)
    assert len(client.calls) == 3
