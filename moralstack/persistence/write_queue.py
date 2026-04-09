"""
Persistence write queue — async wrappers over moralstack.observability.

Deprecated: use moralstack.observability.obs.emit() directly.
The async_persist_* helpers construct an EventEnvelope and submit router.route()
to the observability write queue (non-blocking, fire-and-forget).
"""

from __future__ import annotations

import time
from typing import Any

from moralstack.observability import router
from moralstack.observability.context import (
    get_current_cycle,
    get_current_request_id,
    get_current_run_id,
)
from moralstack.observability.events import (
    EVENT_DEBUG_EVENT,
    EVENT_DECISION_TRACE,
    EVENT_LLM_CALL,
    make_envelope,
)
from moralstack.observability.service import get_obs
from moralstack.observability.write_queue import ObservabilityWriteQueue as PersistenceWriteQueue  # noqa: F401


def get_write_queue() -> PersistenceWriteQueue:
    """Returns the observability write queue (backwards-compat)."""
    return get_obs()._queue


def async_persist_llm_call(**kwargs: Any) -> None:
    """Fire-and-forget LLM call persist via observability write queue."""
    run_id = kwargs.get("run_id") or get_current_run_id()
    request_id = kwargs.get("request_id") or get_current_request_id()
    if not run_id or not request_id:
        return
    cycle_val = kwargs.get("cycle") if "cycle" in kwargs else get_current_cycle()
    now = int(time.time() * 1000)
    envelope = make_envelope(
        EVENT_LLM_CALL,
        run_id=run_id,
        request_id=request_id,
        cycle=cycle_val,
        payload={
            "phase": kwargs.get("phase", ""),
            "module": kwargs.get("module", ""),
            "action": kwargs.get("action", ""),
            "model": kwargs.get("model", ""),
            "started_at": kwargs.get("started_at", now),
            "duration_ms": kwargs.get("duration_ms"),
            "prompt": kwargs.get("prompt", ""),
            "system_prompt": kwargs.get("system_prompt", ""),
            "raw_response": kwargs.get("raw_response", ""),
            "parsed_json": kwargs.get("parsed_json"),
            "parsed_summary_json": kwargs.get("parsed_summary_json"),
            "token_usage_json": kwargs.get("token_usage_json"),
            "attempts": kwargs.get("attempts"),
            "error": kwargs.get("error"),
            "sequence_in_cycle": kwargs.get("sequence_in_cycle"),
            "call_kind": kwargs.get("call_kind"),
            "call_outcome": kwargs.get("call_outcome"),
            "cache_status": kwargs.get("cache_status"),
            "related_event_id": kwargs.get("related_event_id"),
        },
    )
    get_obs()._queue.submit(router.route, envelope)


def async_persist_decision_trace(**kwargs: Any) -> None:
    """Fire-and-forget decision trace persist via observability write queue."""
    run_id = kwargs.get("run_id") or get_current_run_id()
    request_id = kwargs.get("request_id") or get_current_request_id()
    if not run_id or not request_id:
        return
    envelope = make_envelope(
        EVENT_DECISION_TRACE,
        run_id=run_id,
        request_id=request_id,
        payload={
            "stage": kwargs.get("stage", ""),
            "sequence": kwargs.get("sequence", 0),
            "trace_json": kwargs.get("trace_json", ""),
            "created_at": kwargs.get("created_at", int(time.time() * 1000)),
        },
    )
    get_obs()._queue.submit(router.route, envelope)


def async_persist_debug_event(**kwargs: Any) -> None:
    """Fire-and-forget debug event persist via observability write queue."""
    run_id = kwargs.get("run_id") or get_current_run_id()
    if not run_id:
        return
    payload = kwargs.get("payload") or {}
    envelope = make_envelope(
        EVENT_DEBUG_EVENT,
        run_id=run_id,
        request_id=kwargs.get("request_id") or get_current_request_id(),
        payload=payload if isinstance(payload, dict) else {"payload": payload},
    )
    get_obs()._queue.submit(router.route, envelope)
