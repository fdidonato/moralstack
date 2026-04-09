"""
Async write queue for observability: fire-and-forget emit calls.

A single background thread drains the FIFO queue.
The contextvars snapshot is captured at submit() time so run_id/request_id
remain correct even if the caller's context changes before the worker runs.
"""

from __future__ import annotations

import contextvars
import logging
import queue
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SENTINEL = object()


class ObservabilityWriteQueue:
    """
    Background FIFO queue for ObservabilityService.emit() calls.
    - submit() is O(1) non-blocking
    - Worker thread processes in FIFO order (preserves FK ordering)
    - daemon=True: does not block process shutdown
    """

    def __init__(self, maxsize: int = 100_000) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="moralstack-obs-worker",
        )
        self._thread.start()

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Enqueue func(*args, **kwargs) with a snapshot of the current context."""
        ctx = contextvars.copy_context()
        try:
            self._queue.put_nowait((func, args, kwargs, ctx))
        except queue.Full:
            logger.warning(
                "observability: write_queue full (%d items), dropping %s",
                self._queue.maxsize,
                getattr(func, "__name__", str(func)),
            )

    def flush(self, timeout: float = 30.0) -> None:
        """Block until queue is empty or timeout expires."""
        self._queue.join()

    def shutdown(self, timeout: float = 30.0) -> None:
        """Drain the queue and stop the worker thread."""
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=timeout)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                self._queue.task_done()
                break
            func, args, kwargs, ctx = item
            try:
                ctx.run(func, *args, **kwargs)
            except Exception as e:
                logger.warning(
                    "observability: async write failed [%s]: %s",
                    getattr(func, "__name__", "?"),
                    e,
                )
            finally:
                self._queue.task_done()
