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

from moralstack.observability.events import EVENT_LLM_CALL, EventEnvelope
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.request_token_accumulator import record_llm_call_usage
from moralstack.observability.write_queue import ObservabilityWriteQueue

logger = logging.getLogger(__name__)


class ObservabilityService:
    """
    Central service for emitting observability events.

    emit() is non-blocking: it enqueues the envelope on a background thread
    that persists envelope windows through the observability router.
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
        if envelope.event_type == EVENT_LLM_CALL and envelope.run_id and envelope.request_id:
            if envelope.payload.get("billable_provider_call", True):
                try:
                    token_json = envelope.payload.get("token_usage_json")
                    if token_json is not None and not isinstance(token_json, str):
                        import json as _json

                        token_json = _json.dumps(token_json)
                    record_llm_call_usage(
                        envelope.run_id,
                        envelope.request_id,
                        token_json if isinstance(token_json, str) else None,
                    )
                except Exception:
                    logger.debug("token usage accumulation failed", exc_info=True)
        try:
            self._queue.submit_envelope(envelope)
        except Exception as exc:
            logger.warning("observability: emit failed: %s", exc)

    def emit_batch(self, envelopes: Sequence[EventEnvelope]) -> None:
        """Fire-and-forget batch emit. Never raises."""
        for envelope in envelopes:
            if envelope.event_type == EVENT_LLM_CALL and envelope.run_id and envelope.request_id:
                if envelope.payload.get("billable_provider_call", True):
                    try:
                        token_json = envelope.payload.get("token_usage_json")
                        if token_json is not None and not isinstance(token_json, str):
                            import json as _json

                            token_json = _json.dumps(token_json)
                        record_llm_call_usage(
                            envelope.run_id,
                            envelope.request_id,
                            token_json if isinstance(token_json, str) else None,
                        )
                    except Exception:
                        logger.debug("token usage accumulation failed", exc_info=True)
        try:
            if not envelopes:
                return
            self._queue.submit_batch(list(envelopes))
        except Exception as exc:
            logger.warning("observability: emit_batch failed: %s", exc)

    def flush(self, timeout: float = 30.0) -> None:
        """Block until all pending writes are flushed. Call at request boundary."""
        self._queue.flush(timeout=timeout)

    def shutdown(self, timeout: float = 30.0) -> None:
        """Drain the queue and stop the worker thread. Call at process shutdown."""
        self._queue.shutdown(timeout=timeout)

    def stats(self) -> dict[str, object]:
        """Return observability queue counters."""
        return self._queue.stats()

    def record_finalize_failure(self, count: int = 1, error: str | None = None) -> None:
        """Count synchronous finalization failures. Never raises."""
        try:
            self._queue.record_finalize_failure(count, error)
        except Exception:
            logger.debug("observability: record_finalize_failure failed", exc_info=True)

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
