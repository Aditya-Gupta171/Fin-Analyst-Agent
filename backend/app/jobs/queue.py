"""In-process async job queue that runs analyses with bounded concurrency.

No Redis/Celery: the deployment target is a single container, and Groq's own free-tier rate limit is the
real throughput ceiling anyway, so an ``asyncio`` queue is enough. A process restart loses in-flight jobs —
see ``reconcile_interrupted_jobs`` in ``app/api/main.py``, which marks any row left ``queued``/``running`` as
failed on startup rather than leaving it stuck forever.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

Job = Callable[[], Awaitable[None]]


class JobQueue:
    def __init__(self, max_concurrency: int = 2, *, eager: bool = False) -> None:
        """``eager=True`` runs a submitted job inline and awaits it before returning — used by tests so an
        analysis finishes deterministically within the request instead of racing a background task."""
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._eager = eager
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(self, job: Job) -> None:
        if self._eager:
            await job()
            return
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: Job) -> None:
        async with self._semaphore:
            await job()
