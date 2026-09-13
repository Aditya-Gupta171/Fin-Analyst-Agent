"""Client-side rate limiting driven by the provider's own headers.

Groq's free tier allows 8,000 tokens a minute per model and reserves ``prompt + max_completion_tokens`` when a
request arrives. Rather than firing requests and handling 429s, the budget tracks what the last response said
is left and when the window resets, and waits before sending a request that would not fit.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

_DURATION = re.compile(r"(?:(?P<m>\d+(?:\.\d+)?)m)?(?:(?P<s>\d+(?:\.\d+)?)s)?(?:(?P<ms>\d+(?:\.\d+)?)ms)?$")


def parse_duration(text: str | None) -> float | None:
    """Seconds from Groq's reset headers ("3.074s", "1m26.4s", "450ms") or a Retry-After value ("7")."""
    if not text:
        return None
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    match = _DURATION.match(text)
    if not match or not any(match.groupdict().values()):
        return None
    minutes, seconds, millis = (float(match[g]) if match[g] else 0.0 for g in ("m", "s", "ms"))
    return minutes * 60 + seconds + millis / 1000


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for budgeting (about 3.5 characters per token for English and figures)."""
    return int(len(text) / 3.5) + 16


@dataclass
class _ModelWindow:
    remaining_tokens: int | None = None
    remaining_requests: int | None = None
    tokens_reset_at: float = 0.0
    requests_reset_at: float = 0.0


@dataclass
class TokenBudget:
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    safety_margin: int = 200
    max_wait_seconds: float = 120.0
    _windows: dict[str, _ModelWindow] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def acquire(self, model: str, tokens: int) -> float:
        """Block until a request of ``tokens`` is likely to fit; returns the seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                window = self._windows.get(model)
                now = self.clock()
                if window is None:
                    return waited
                if window.tokens_reset_at <= now:
                    window.remaining_tokens = None
                if window.requests_reset_at <= now:
                    window.remaining_requests = None
                tokens_ok = (
                    window.remaining_tokens is None or window.remaining_tokens >= tokens + self.safety_margin
                )
                requests_ok = window.remaining_requests is None or window.remaining_requests > 0
                if tokens_ok and requests_ok:
                    if window.remaining_tokens is not None:
                        window.remaining_tokens -= tokens
                    return waited
                resets = [
                    window.tokens_reset_at if not tokens_ok else now,
                    window.requests_reset_at if not requests_ok else now,
                ]
                delay = max(0.05, max(resets) - now)
            if waited + delay > self.max_wait_seconds:
                return waited  # give up waiting and let the provider decide; a 429 is retried by the client
            self.sleep(delay)
            waited += delay

    def update(self, model: str, headers: Mapping[str, str]) -> None:
        now = self.clock()
        with self._lock:
            window = self._windows.setdefault(model, _ModelWindow())
            if (tokens := headers.get("x-ratelimit-remaining-tokens")) is not None:
                window.remaining_tokens = int(float(tokens))
                window.tokens_reset_at = now + (
                    parse_duration(headers.get("x-ratelimit-reset-tokens")) or 60.0
                )
            if (requests := headers.get("x-ratelimit-remaining-requests")) is not None:
                window.remaining_requests = int(float(requests))
                window.requests_reset_at = now + (
                    parse_duration(headers.get("x-ratelimit-reset-requests")) or 60.0
                )

    def exhaust(self, model: str, retry_after: float) -> None:
        """Record a 429: nothing is available until ``retry_after`` seconds from now."""
        with self._lock:
            window = self._windows.setdefault(model, _ModelWindow())
            window.remaining_tokens = 0
            window.tokens_reset_at = self.clock() + retry_after
