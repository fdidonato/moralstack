"""
Persistence sink — thin wrappers over moralstack.observability.

Deprecated: use moralstack.observability directly.
All persist_* functions enqueue high-frequency telemetry asynchronously:
they construct an EventEnvelope and call get_obs().emit*().
The uow= parameter is accepted but ignored (atomicity is handled by SqliteEventSink).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from moralstack.observability.context import (
    get_current_cycle,
    get_current_request_id,
    get_current_run_id,
    get_current_session_id,
    get_current_turn_number,
)
from moralstack.observability.events import (
    EVENT_DEBUG_EVENT,
    EVENT_DECISION_TRACE,
    EVENT_LLM_CALL,
    EVENT_ORCHESTRATION_EVENT,
    make_envelope,
)

logger = logging.getLogger(__name__)

_uow_warned = False


def _warn_uow_once() -> None:
    global _uow_warned
    if not _uow_warned:
        _uow_warned = True
        logger.warning(
            "persistence: uow= parameter is deprecated and ignored; " "atomicity is handled by SqliteEventSink internally."
        )


# ---------------------------------------------------------------------------
# Single-event persist functions (backwards-compatible, async telemetry)
# ---------------------------------------------------------------------------


def persist_llm_call(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    cycle: int | None = None,
    phase: str,
    module: str,
    action: str,
    model: str | None = None,
    started_at: int | None = None,
    duration_ms: float | None = None,
    prompt: str = "",
    system_prompt: str = "",
    raw_response: str = "",
    parsed_json: str | None = None,
    parsed_summary_json: str | None = None,
    token_usage_json: str | None = None,
    attempts: int | None = None,
    error: str | None = None,
    sequence_in_cycle: int | None = None,
    call_kind: str | None = None,
    call_outcome: str | None = None,
    cache_status: str | None = None,
    related_event_id: int | None = None,
    uow: Any = None,
) -> bool:
    """Enqueues an LLM call via get_obs().emit(). Does not raise."""
    if uow is not None:
        _warn_uow_once()
    run_id = run_id or get_current_run_id()
    request_id = request_id or get_current_request_id()
    if not run_id or not request_id:
        return False
    cycle_val = cycle if cycle is not None else get_current_cycle()
    envelope = make_envelope(
        EVENT_LLM_CALL,
        run_id=run_id,
        request_id=request_id,
        cycle=cycle_val,
        session_id=get_current_session_id(),
        turn_number=get_current_turn_number(),
        payload={
            "phase": phase,
            "module": module,
            "action": action,
            "model": model or "",
            "started_at": started_at if started_at is not None else int(time.time() * 1000),
            "duration_ms": duration_ms,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "raw_response": raw_response,
            "parsed_json": parsed_json,
            "parsed_summary_json": parsed_summary_json,
            "token_usage_json": token_usage_json,
            "attempts": attempts,
            "error": error,
            "sequence_in_cycle": sequence_in_cycle,
            "call_kind": call_kind,
            "call_outcome": call_outcome,
            "cache_status": cache_status,
            "related_event_id": related_event_id,
        },
    )
    try:
        from moralstack.observability.service import get_obs

        get_obs().emit(envelope)
        return True
    except Exception as e:
        logger.warning("persistence: persist_llm_call failed: %s", e)
        return False


def persist_decision_trace(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    stage: str,
    sequence: int,
    trace_json: str,
    uow: Any = None,
) -> bool:
    """Enqueues a decision trace. Does not raise."""
    if uow is not None:
        _warn_uow_once()
    run_id = run_id or get_current_run_id()
    request_id = request_id or get_current_request_id()
    if not run_id or not request_id:
        return False
    envelope = make_envelope(
        EVENT_DECISION_TRACE,
        run_id=run_id,
        request_id=request_id,
        session_id=get_current_session_id(),
        turn_number=get_current_turn_number(),
        payload={
            "stage": stage,
            "sequence": sequence,
            "trace_json": trace_json,
            "created_at": int(time.time() * 1000),
        },
    )
    try:
        from moralstack.observability.service import get_obs

        get_obs().emit(envelope)
        return True
    except Exception as e:
        logger.warning("persistence: persist_decision_trace failed: %s", e)
        return False


def persist_debug_event(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    payload: dict[str, Any],
    uow: Any = None,
) -> bool:
    """Enqueues a debug event. Does not raise."""
    if uow is not None:
        _warn_uow_once()
    run_id = run_id or get_current_run_id()
    if not run_id:
        return False
    envelope = make_envelope(
        EVENT_DEBUG_EVENT,
        run_id=run_id,
        request_id=request_id or get_current_request_id(),
        session_id=get_current_session_id(),
        turn_number=get_current_turn_number(),
        payload=payload,
    )
    try:
        from moralstack.observability.service import get_obs

        get_obs().emit(envelope)
        return True
    except Exception as e:
        logger.warning("persistence: persist_debug_event failed: %s", e)
        return False


def persist_orchestration_event(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    cycle: int | None = None,
    stage: str,
    component: str,
    event_type: str,
    decision: str | None = None,
    status: str | None = None,
    sequence: int | None = None,
    started_at: int | None = None,
    duration_ms: float | None = None,
    reason_codes: list[str] | None = None,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    uow: Any = None,
) -> int | None:
    """Enqueues a single orchestration_event. Does not raise. Returns None."""
    if uow is not None:
        _warn_uow_once()
    run_id = run_id or get_current_run_id()
    request_id = request_id or get_current_request_id()
    if not run_id or not request_id:
        return None
    cycle_val = cycle if cycle is not None else get_current_cycle()
    envelope = make_envelope(
        EVENT_ORCHESTRATION_EVENT,
        run_id=run_id,
        request_id=request_id,
        cycle=cycle_val,
        session_id=get_current_session_id(),
        turn_number=get_current_turn_number(),
        payload={
            "stage": stage,
            "component": component,
            "event_type": event_type,
            "decision": decision,
            "status": status,
            "sequence": sequence,
            "started_at": started_at if started_at is not None else int(time.time() * 1000),
            "duration_ms": duration_ms,
            "reason_codes": reason_codes,
            "inputs": inputs,
            "outputs": outputs,
            "payload": payload,
        },
    )
    try:
        from moralstack.observability.service import get_obs

        get_obs().emit(envelope)
        return None  # row id no longer available after routing
    except Exception as e:
        logger.warning("persistence: persist_orchestration_event failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Batch persist functions (async telemetry)
# ---------------------------------------------------------------------------


def persist_llm_calls_batch(
    entries: list[dict[str, Any]],
    uow: Any = None,
) -> bool:
    """Batch enqueue LLM calls via get_obs().emit_batch(). Does not raise."""
    if uow is not None:
        _warn_uow_once()
    if not entries:
        return True
    default_run = get_current_run_id()
    default_req = get_current_request_id()
    default_cycle = get_current_cycle()
    default_sess = get_current_session_id()
    default_turn = get_current_turn_number()
    now_ms = int(time.time() * 1000)
    envelopes = []
    for e in entries:
        run_id = e.get("run_id") or default_run
        request_id = e.get("request_id") or default_req
        if not run_id or not request_id:
            continue
        cycle_val = e.get("cycle") if "cycle" in e else default_cycle
        envelopes.append(
            make_envelope(
                EVENT_LLM_CALL,
                run_id=run_id,
                request_id=request_id,
                cycle=cycle_val,
                session_id=default_sess,
                turn_number=default_turn,
                payload={
                    "phase": e.get("phase", ""),
                    "module": e.get("module", ""),
                    "action": e.get("action", ""),
                    "model": e.get("model", ""),
                    "started_at": e.get("started_at", now_ms),
                    "duration_ms": e.get("duration_ms"),
                    "prompt": e.get("prompt", ""),
                    "system_prompt": e.get("system_prompt", ""),
                    "raw_response": e.get("raw_response", ""),
                    "parsed_json": e.get("parsed_json"),
                    "parsed_summary_json": e.get("parsed_summary_json"),
                    "token_usage_json": e.get("token_usage_json"),
                    "attempts": e.get("attempts"),
                    "error": e.get("error"),
                    "sequence_in_cycle": e.get("sequence_in_cycle"),
                    "call_kind": e.get("call_kind"),
                    "call_outcome": e.get("call_outcome"),
                    "cache_status": e.get("cache_status"),
                    "related_event_id": e.get("related_event_id"),
                },
            )
        )
    if not envelopes:
        return True
    try:
        from moralstack.observability.service import get_obs

        get_obs().emit_batch(envelopes)
        return True
    except Exception as e:
        logger.warning("persistence: persist_llm_calls_batch failed: %s", e)
        return False


def persist_decision_traces_batch(
    entries: list[dict[str, Any]],
    uow: Any = None,
) -> bool:
    """Batch enqueue decision traces. Does not raise."""
    if uow is not None:
        _warn_uow_once()
    if not entries:
        return True
    default_run = get_current_run_id()
    default_req = get_current_request_id()
    default_sess = get_current_session_id()
    default_turn = get_current_turn_number()
    now_ms = int(time.time() * 1000)
    envelopes = []
    for e in entries:
        run_id = e.get("run_id") or default_run
        request_id = e.get("request_id") or default_req
        if not run_id or not request_id:
            continue
        envelopes.append(
            make_envelope(
                EVENT_DECISION_TRACE,
                run_id=run_id,
                request_id=request_id,
                session_id=default_sess,
                turn_number=default_turn,
                payload={
                    "stage": e.get("stage", ""),
                    "sequence": e.get("sequence", 0),
                    "trace_json": e.get("trace_json", ""),
                    "created_at": e.get("created_at", now_ms),
                },
            )
        )
    if not envelopes:
        return True
    try:
        from moralstack.observability.service import get_obs

        get_obs().emit_batch(envelopes)
        return True
    except Exception as e:
        logger.warning("persistence: persist_decision_traces_batch failed: %s", e)
        return False


def persist_debug_events_batch(
    entries: list[dict[str, Any]],
    uow: Any = None,
) -> bool:
    """Batch enqueue debug events. Does not raise."""
    if uow is not None:
        _warn_uow_once()
    if not entries:
        return True
    default_run = get_current_run_id()
    default_sess = get_current_session_id()
    default_turn = get_current_turn_number()
    envelopes = []
    for e in entries:
        run_id = e.get("run_id") or default_run
        if not run_id:
            continue
        payload = e.get("payload", e) if "payload" in e else e
        envelopes.append(
            make_envelope(
                EVENT_DEBUG_EVENT,
                run_id=run_id,
                request_id=e.get("request_id") or "",
                session_id=default_sess,
                turn_number=default_turn,
                payload=payload if isinstance(payload, dict) else {"payload": payload},
            )
        )
    if not envelopes:
        return True
    try:
        from moralstack.observability.service import get_obs

        get_obs().emit_batch(envelopes)
        return True
    except Exception as e:
        logger.warning("persistence: persist_debug_events_batch failed: %s", e)
        return False


def persist_orchestration_events_batch(
    entries: list[dict[str, Any]],
    uow: Any = None,
) -> bool:
    """Batch enqueue orchestration events. Does not raise."""
    if uow is not None:
        _warn_uow_once()
    if not entries:
        return True
    default_run = get_current_run_id()
    default_req = get_current_request_id()
    default_sess = get_current_session_id()
    default_turn = get_current_turn_number()
    now_ms = int(time.time() * 1000)
    envelopes = []
    for e in entries:
        run_id = e.get("run_id") or default_run
        request_id = e.get("request_id") or default_req
        if not run_id or not request_id:
            continue
        envelopes.append(
            make_envelope(
                EVENT_ORCHESTRATION_EVENT,
                run_id=run_id,
                request_id=request_id,
                cycle=e.get("cycle"),
                session_id=default_sess,
                turn_number=default_turn,
                payload={
                    "stage": e.get("stage", ""),
                    "component": e.get("component", ""),
                    "event_type": e.get("event_type", ""),
                    "decision": e.get("decision"),
                    "status": e.get("status"),
                    "sequence": e.get("sequence"),
                    "started_at": e.get("started_at", now_ms),
                    "duration_ms": e.get("duration_ms"),
                    "reason_codes": e.get("reason_codes"),
                    "inputs": e.get("inputs"),
                    "outputs": e.get("outputs"),
                    "payload": e.get("payload"),
                },
            )
        )
    if not envelopes:
        return True
    try:
        from moralstack.observability.service import get_obs

        get_obs().emit_batch(envelopes)
        return True
    except Exception as e:
        logger.warning("persistence: persist_orchestration_events_batch failed: %s", e)
        return False
