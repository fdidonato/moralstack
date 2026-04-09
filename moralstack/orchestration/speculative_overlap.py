"""
Lazy speculative overlap: risk completes first; speculative generation is joined only when consumed.

Observability: orchestration_events for speculative lifecycle (see orchestration_event_taxonomy).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from moralstack.orchestration.orchestration_event_taxonomy import (
    SPECULATIVE_JOIN_REQUIRED,
    SPECULATIVE_JOIN_SKIPPED,
    SPECULATIVE_RESULT_DISCARDED,
    SPECULATIVE_RESULT_USED,
)
from moralstack.persistence.sink import persist_orchestration_event
from moralstack.persistence.write_queue import async_persist_llm_call

_LOG = logging.getLogger(__name__)


class SpeculativeOverlapHandle:
    """
    Holds the speculative Future and executor after risk has completed.

    Call ``join_for_consumer`` only on routes that use the draft; otherwise ``abandon``.
    """

    def __init__(
        self,
        *,
        risk_estimation: Any,
        spec_future: Future[tuple[str | None, dict[str, Any] | None]],
        executor: ThreadPoolExecutor,
        spec_started_at_ms: int,
    ) -> None:
        self.risk_estimation = risk_estimation
        self._spec_future = spec_future
        self._executor = executor
        self._spec_started_at_ms = spec_started_at_ms
        self._joined = False
        self._abandoned = False

    def shutdown_executor(self) -> None:
        # If process() exited without join/abandon (e.g. exception), still persist discarded outcome.
        if not self._joined and not self._abandoned:
            self.abandon("process_exit_without_consume", "unknown")
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            _LOG.debug("speculative executor shutdown failed", exc_info=True)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            persist_orchestration_event(
                stage="orchestration",
                component="speculative",
                event_type=event_type,
                decision=str(payload.get("decision") or payload.get("route") or "")[:512],
                status="ok",
                payload=payload,
            )
        except Exception:
            _LOG.debug("speculative orchestration event failed", exc_info=True)

    def join_for_consumer(self, route: str, consumer: str) -> str | None:
        """Await speculative output for a consuming route. Persists llm_call with call_outcome=used."""
        if self._abandoned:
            return None
        if self._joined:
            return None
        self._joined = True
        t_join = time.time()
        self._emit(
            SPECULATIVE_JOIN_REQUIRED,
            {
                "route": route,
                "consumer": consumer,
                "reason": "route_consumes_speculative",
                "elapsed_since_spec_start_ms": round(max(0.0, t_join * 1000 - self._spec_started_at_ms), 1),
            },
        )
        try:
            draft, meta = self._spec_future.result()
        except Exception as e:
            self._emit(
                SPECULATIVE_RESULT_DISCARDED,
                {
                    "reason": "speculative_failed",
                    "route": route,
                    "error": str(e)[:300],
                },
            )
            return None
        wait_ms = (time.time() - t_join) * 1000
        if meta:
            try:
                merged = dict(meta)
                merged["call_outcome"] = "used"
                async_persist_llm_call(**merged)
            except Exception:
                _LOG.debug("persist speculative used failed", exc_info=True)
        if draft:
            self._emit(
                SPECULATIVE_RESULT_USED,
                {
                    "route": route,
                    "consumer": consumer,
                    "join_wait_ms": round(wait_ms, 1),
                    "draft_nonempty": True,
                },
            )
        else:
            self._emit(
                SPECULATIVE_RESULT_DISCARDED,
                {"reason": "speculative_empty_or_failed", "route": route},
            )
        return draft

    def abandon(self, discard_reason: str, final_route: str) -> None:
        """
        Do not block on speculative completion; schedule background join to persist discarded outcome.

        discard_reason examples: domain_excluded, refuse_path, safe_complete_path,
        constrained_generation_incompatible.
        """
        if self._joined:
            return
        if self._abandoned:
            return
        self._abandoned = True
        now_ms = time.time() * 1000
        self._emit(
            SPECULATIVE_JOIN_SKIPPED,
            {
                "final_route": final_route,
                "reason": discard_reason,
                "elapsed_since_spec_start_ms": round(max(0.0, now_ms - self._spec_started_at_ms), 1),
            },
        )
        self._emit(
            SPECULATIVE_RESULT_DISCARDED,
            {
                "reason": discard_reason,
                "final_route": final_route,
            },
        )

        def _bg() -> None:
            try:
                draft, meta = self._spec_future.result()
                if meta:
                    try:
                        merged = dict(meta)
                        merged["call_outcome"] = "discarded"
                        async_persist_llm_call(**merged)
                    except Exception:
                        _LOG.debug("persist speculative discarded failed", exc_info=True)
                _ = draft
            except Exception as e:
                _LOG.debug("abandon speculative future: %s", e)

        threading.Thread(target=_bg, daemon=True, name="speculative-abandon").start()
