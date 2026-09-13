"""Client-side rate limiting driven by provider headers, without any network calls."""

from app.llm.budget import TokenBudget, estimate_tokens, parse_duration


def test_parse_duration_formats() -> None:
    assert parse_duration("7") == 7.0
    assert parse_duration("3.074s") == 3.074
    assert parse_duration("1m26.4s") == 86.4
    assert parse_duration("450ms") == 0.45
    assert parse_duration(None) is None
    assert parse_duration("garbage") is None


def test_estimate_tokens_is_a_positive_lower_bound() -> None:
    assert estimate_tokens("") > 0
    assert estimate_tokens("word " * 100) > estimate_tokens("word " * 10)


def test_acquire_does_not_block_before_any_response_seen() -> None:
    budget = TokenBudget()
    waited = budget.acquire("model-a", 5000)
    assert waited == 0.0


def test_acquire_waits_for_the_window_to_reset_when_tokens_are_short() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []
    budget = TokenBudget(
        clock=lambda: clock["t"], sleep=lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s))
    )
    budget.update("model-a", {"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "5s"})

    waited = budget.acquire("model-a", 1000)
    assert waited == 5.0
    assert slept == [5.0]


def test_acquire_proceeds_immediately_when_enough_tokens_remain() -> None:
    clock = {"t": 0.0}
    budget = TokenBudget(
        clock=lambda: clock["t"], sleep=lambda s: (_ for _ in ()).throw(AssertionError("should not sleep"))
    )
    budget.update("model-a", {"x-ratelimit-remaining-tokens": "8000", "x-ratelimit-reset-tokens": "60s"})
    assert budget.acquire("model-a", 500) == 0.0


def test_window_resets_after_its_deadline_passes() -> None:
    clock = {"t": 0.0}
    budget = TokenBudget(clock=lambda: clock["t"], sleep=lambda s: None)
    budget.update("model-a", {"x-ratelimit-remaining-tokens": "10", "x-ratelimit-reset-tokens": "1s"})
    clock["t"] = 2.0  # past the reset deadline
    assert budget.acquire("model-a", 5000) == 0.0


def test_exhaust_forces_a_wait_until_retry_after() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []
    budget = TokenBudget(
        clock=lambda: clock["t"], sleep=lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s))
    )
    budget.exhaust("model-a", 12.0)
    assert budget.acquire("model-a", 100) == 12.0
    assert slept == [12.0]


def test_max_wait_gives_up_and_lets_the_client_retry() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []
    budget = TokenBudget(
        clock=lambda: clock["t"],
        sleep=lambda s: (slept.append(s), clock.__setitem__("t", clock["t"] + s)),
        max_wait_seconds=3.0,
    )
    budget.update("model-a", {"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "999s"})
    waited = budget.acquire("model-a", 100)
    assert waited == 0.0  # the wait would exceed the cap, so it gives up immediately rather than sleeping
    assert slept == []


def test_different_models_have_independent_windows() -> None:
    budget = TokenBudget()
    budget.exhaust("model-a", 999.0)
    assert budget.acquire("model-b", 100) == 0.0
