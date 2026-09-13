"""A tiny in-memory table of {analysis_id: (stage, message)}, updated from the worker thread that runs an
analysis and read by the API's status/SSE endpoints while it's in flight.

The analysis itself runs off the event loop via ``asyncio.to_thread`` (see app/jobs/runner.py), so its
``on_progress`` callback fires on a worker thread; a plain dict assignment is simple and, under CPython's
GIL, atomic enough for a best-effort progress display — it is not the source of truth (the ``analyses`` row
is, updated once the job finishes), just a live look at a run in progress.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Progress:
    stage: str
    message: str


class ProgressStore:
    def __init__(self) -> None:
        self._live: dict[str, Progress] = {}

    def set(self, analysis_id: str, stage: str, message: str) -> None:
        self._live[analysis_id] = Progress(stage, message)

    def get(self, analysis_id: str) -> Progress | None:
        return self._live.get(analysis_id)

    def clear(self, analysis_id: str) -> None:
        self._live.pop(analysis_id, None)
