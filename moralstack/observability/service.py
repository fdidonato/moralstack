"""
ObservabilityService: singleton entry point for all MoralStack telemetry.

Usage:
    from moralstack.observability import obs
    obs.emit(make_envelope(EVENT_LLM_CALL, run_id=..., payload={...}))
    obs.flush()
"""

from __future__ import annotations

import logging
import threading
from typing import Sequence

from moralstack.observability import router
from moralstack.observability.events import EventEnvelope
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.write_queue import ObservabilityWriteQueue

logger = logging.getLogger(__name__)


class ObservabilityService:
    """
    Central service for emitting observability events.

    emit() is non-blocking: it enqueues the envelope on a background thread
    that calls router.route() with the captured contextvars snapshot.
    emit_batch() similarly enqueues as a single unit.

    Use flush() at request boundary to wait for pending writes before
    reading results (e.g. before update_request_response or end_run).
    """

    def __init__(self) -> None:
        self._queue = ObservabilityWriteQueue()
        self._read_store = SqliteReadStore()

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def emit(self, envelope: EventEnvelope) -> None:
        """Fire-and-forget: enqueue envelope for async dispatch. Never raises."""
        self._queue.submit(router.route, envelope)

    def emit_batch(self, envelopes: Sequence[EventEnvelope]) -> None:
        """Fire-and-forget batch emit. Never raises."""
        if not envelopes:
            return
        self._queue.submit(router.route_batch, list(envelopes))

    def flush(self, timeout: float = 30.0) -> None:
        """Block until all pending writes are flushed. Call at request boundary."""
        self._queue.flush(timeout=timeout)

    def shutdown(self, timeout: float = 30.0) -> None:
        """Drain the queue and stop the worker thread. Call at process shutdown."""
        self._queue.shutdown(timeout=timeout)

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    @property
    def read_store(self) -> SqliteReadStore:
        """The unique read contract for all observability data."""
        return self._read_store


# ---------------------------------------------------------------------------
# Module-level singleton (lazy, thread-safe)
# ---------------------------------------------------------------------------

_obs_instance: ObservabilityService | None = None
_obs_lock = threading.Lock()


def get_obs() -> ObservabilityService:
    """Returns the process-wide ObservabilityService singleton."""
    global _obs_instance
    if _obs_instance is None:
        with _obs_lock:
            if _obs_instance is None:
                _obs_instance = ObservabilityService()
    return _obs_instance
