"""
Persistence sink: safe insert functions that do not raise.

All functions check persist_mode; if file_only, they are no-op for DB.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from moralstack.persistence.config import get_db_path, get_persist_mode
from moralstack.persistence.context import (
    get_current_cycle,
    get_current_request_id,
    get_current_run_id,
)
from moralstack.persistence.db import (
    SqliteUnitOfWork,
    _get_connection,
    insert_debug_events_batch,
    insert_decision_traces_batch,
    insert_llm_calls_batch,
    insert_orchestration_events_batch,
)

logger = logging.getLogger(__name__)


def _should_persist() -> bool:
    """Returns True if we should write to DB (db_only or dual)."""
    return get_persist_mode() in ("db_only", "dual")


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
    uow: SqliteUnitOfWork | None = None,
) -> bool:
    """
    Persists an LLM call. Does not raise; logs warning on error.

    Uses context run_id/request_id/cycle if not provided.
    If uow is provided and has an active connection, uses it (no commit/close here).
    """
    if not _should_persist():
        return False
    path = get_db_path()
    if not path:
        return False
    run_id = run_id or get_current_run_id()
    request_id = request_id or get_current_request_id()
    if not run_id or not request_id:
        return False
    cycle_val = cycle if cycle is not None else get_current_cycle()
    started = started_at if started_at is not None else int(time.time() * 1000)
    owned = uow is None or uow.conn is None
    conn = None
    try:
        conn = uow.conn if not owned else _get_connection(path)
        conn.execute(
            """
            INSERT INTO llm_calls (run_id, request_id, cycle, phase, module, action, model,
                                   started_at, duration_ms, prompt, system_prompt, raw_response,
                                   parsed_json, parsed_summary_json, token_usage_json,
                                   attempts, error, sequence_in_cycle,
                                   call_kind, call_outcome, cache_status, related_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request_id,
                cycle_val,
                phase,
                module,
                action,
                model or "",
                started,
                duration_ms,
                prompt,
                system_prompt,
                raw_response,
                parsed_json,
                parsed_summary_json,
                token_usage_json,
                attempts,
                error,
                sequence_in_cycle,
                call_kind,
                call_outcome,
                cache_status,
                related_event_id,
            ),
        )
        if owned:
            conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: persist_llm_call failed: %s", e)
        if owned and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owned and conn is not None:
            conn.close()


def persist_decision_trace(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    stage: str,
    sequence: int,
    trace_json: str,
    uow: SqliteUnitOfWork | None = None,
) -> bool:
    """Persists a decision trace. Does not raise. If uow provided, uses it (no commit/close)."""
    if not _should_persist():
        return False
    path = get_db_path()
    if not path:
        return False
    run_id = run_id or get_current_run_id()
    request_id = request_id or get_current_request_id()
    if not run_id or not request_id:
        return False
    owned = uow is None or uow.conn is None
    conn = None
    try:
        conn = uow.conn if not owned else _get_connection(path)
        conn.execute(
            """
            INSERT INTO decision_traces (run_id, request_id, stage, sequence, trace_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request_id,
                stage,
                sequence,
                trace_json,
                int(time.time() * 1000),
            ),
        )
        if owned:
            conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: persist_decision_trace failed: %s", e)
        if owned and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owned and conn is not None:
            conn.close()


def persist_debug_event(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    payload: dict[str, Any],
    uow: SqliteUnitOfWork | None = None,
) -> bool:
    """Persists a debug event. Does not raise. If uow provided, uses it (no commit/close)."""
    if not _should_persist():
        return False
    path = get_db_path()
    if not path:
        return False
    run_id = run_id or get_current_run_id()
    if not run_id:
        return False
    owned = uow is None or uow.conn is None
    conn = None
    try:
        conn = uow.conn if not owned else _get_connection(path)
        conn.execute(
            """
            INSERT INTO debug_events (run_id, request_id, created_at, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                run_id,
                request_id or "",
                int(time.time() * 1000),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        if owned:
            conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: persist_debug_event failed: %s", e)
        if owned and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owned and conn is not None:
            conn.close()


def _llm_call_row(
    run_id: str,
    request_id: str,
    cycle: int,
    phase: str,
    module: str,
    action: str,
    model: str,
    started_at: int,
    duration_ms: float | None,
    prompt: str,
    system_prompt: str,
    raw_response: str,
    parsed_json: str | None,
    parsed_summary_json: str | None,
    token_usage_json: str | None,
    attempts: int | None,
    error: str | None,
    sequence_in_cycle: int | None,
    call_kind: str | None,
    call_outcome: str | None,
    cache_status: str | None,
    related_event_id: int | None,
) -> tuple[Any, ...]:
    return (
        run_id,
        request_id,
        cycle,
        phase,
        module,
        action,
        model,
        started_at,
        duration_ms,
        prompt,
        system_prompt,
        raw_response,
        parsed_json,
        parsed_summary_json,
        token_usage_json,
        attempts,
        error,
        sequence_in_cycle,
        call_kind,
        call_outcome,
        cache_status,
        related_event_id,
    )


def persist_llm_calls_batch(
    entries: list[dict[str, Any]],
    uow: SqliteUnitOfWork | None = None,
) -> bool:
    """
    Batch persist LLM calls. Each entry dict has same keys as persist_llm_call kwargs.
    Uses context for run_id/request_id/cycle when missing. Does not raise; returns False on error.
    """
    if not _should_persist() or not entries:
        return True
    path = get_db_path()
    if not path:
        return False
    default_run = get_current_run_id()
    default_req = get_current_request_id()
    default_cycle = get_current_cycle()
    now_ms = int(time.time() * 1000)
    rows: list[tuple[Any, ...]] = []
    for e in entries:
        run_id = e.get("run_id") or default_run
        request_id = e.get("request_id") or default_req
        if not run_id or not request_id:
            continue
        cycle = e.get("cycle") if "cycle" in e else default_cycle
        started = e.get("started_at") if "started_at" in e else now_ms
        seq = e.get("sequence_in_cycle") if "sequence_in_cycle" in e else None
        rows.append(
            _llm_call_row(
                run_id=run_id,
                request_id=request_id,
                cycle=cycle,
                phase=e.get("phase", ""),
                module=e.get("module", ""),
                action=e.get("action", ""),
                model=e.get("model", ""),
                started_at=started,
                duration_ms=e.get("duration_ms"),
                prompt=e.get("prompt", ""),
                system_prompt=e.get("system_prompt", ""),
                raw_response=e.get("raw_response", ""),
                parsed_json=e.get("parsed_json"),
                parsed_summary_json=e.get("parsed_summary_json"),
                token_usage_json=e.get("token_usage_json"),
                attempts=e.get("attempts"),
                error=e.get("error"),
                sequence_in_cycle=seq,
                call_kind=e.get("call_kind"),
                call_outcome=e.get("call_outcome"),
                cache_status=e.get("cache_status"),
                related_event_id=e.get("related_event_id"),
            )
        )
    if not rows:
        return True
    owned = uow is None or uow.conn is None
    conn = None
    try:
        conn = uow.conn if not owned else _get_connection(path)
        insert_llm_calls_batch(conn, rows)
        if owned:
            conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: persist_llm_calls_batch failed: %s", e)
        if owned and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owned and conn is not None:
            conn.close()


def persist_decision_traces_batch(
    entries: list[dict[str, Any]],
    uow: SqliteUnitOfWork | None = None,
) -> bool:
    """
    Batch persist decision traces. Each entry: run_id, request_id, stage, sequence,
    trace_json; created_at optional (default now). Does not raise.
    """
    if not _should_persist() or not entries:
        return True
    path = get_db_path()
    if not path:
        return False
    default_run = get_current_run_id()
    default_req = get_current_request_id()
    now_ms = int(time.time() * 1000)
    rows: list[tuple[Any, ...]] = []
    for e in entries:
        run_id = e.get("run_id") or default_run
        request_id = e.get("request_id") or default_req
        if not run_id or not request_id:
            continue
        rows.append(
            (
                run_id,
                request_id,
                e.get("stage", ""),
                e.get("sequence", 0),
                e.get("trace_json", ""),
                e.get("created_at", now_ms),
            )
        )
    if not rows:
        return True
    owned = uow is None or uow.conn is None
    conn = None
    try:
        conn = uow.conn if not owned else _get_connection(path)
        insert_decision_traces_batch(conn, rows)
        if owned:
            conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: persist_decision_traces_batch failed: %s", e)
        if owned and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owned and conn is not None:
            conn.close()


def persist_debug_events_batch(
    entries: list[dict[str, Any]],
    uow: SqliteUnitOfWork | None = None,
) -> bool:
    """
    Batch persist debug events. Each entry: run_id, request_id (optional), payload;
    created_at optional. Does not raise.
    """
    if not _should_persist() or not entries:
        return True
    path = get_db_path()
    if not path:
        return False
    default_run = get_current_run_id()
    now_ms = int(time.time() * 1000)
    rows: list[tuple[Any, ...]] = []
    for e in entries:
        run_id = e.get("run_id") or default_run
        if not run_id:
            continue
        payload = e.get("payload", e) if "payload" in e else e
        if isinstance(payload, dict):
            payload_json = json.dumps(payload, ensure_ascii=False)
        else:
            payload_json = json.dumps({"payload": payload}, ensure_ascii=False)
        rows.append((run_id, e.get("request_id", ""), e.get("created_at", now_ms), payload_json))
    if not rows:
        return True
    owned = uow is None or uow.conn is None
    conn = None
    try:
        conn = uow.conn if not owned else _get_connection(path)
        insert_debug_events_batch(conn, rows)
        if owned:
            conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: persist_debug_events_batch failed: %s", e)
        if owned and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owned and conn is not None:
            conn.close()


def _json_or_none(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None


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
    uow: SqliteUnitOfWork | None = None,
) -> int | None:
    """
    Persist a single orchestration_events row. Returns last inserted row id, or None on skip/error.
    """
    if not _should_persist():
        return None
    path = get_db_path()
    if not path:
        return None
    run_id = run_id or get_current_run_id()
    request_id = request_id or get_current_request_id()
    if not run_id or not request_id:
        return None
    owned = uow is None or uow.conn is None
    conn = None
    try:
        conn = uow.conn if not owned else _get_connection(path)
        cur = conn.execute(
            """
            INSERT INTO orchestration_events (
                run_id, request_id, cycle, stage, component, event_type,
                decision, status, sequence, started_at, duration_ms,
                reason_codes_json, inputs_json, outputs_json, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request_id,
                cycle,
                stage,
                component,
                event_type,
                decision,
                status,
                sequence,
                started_at if started_at is not None else int(time.time() * 1000),
                duration_ms,
                _json_or_none(reason_codes),
                _json_or_none(inputs),
                _json_or_none(outputs),
                _json_or_none(payload),
            ),
        )
        if owned:
            conn.commit()
        return int(cur.lastrowid) if cur.lastrowid is not None else None
    except Exception as e:
        logger.warning("persistence: persist_orchestration_event failed: %s", e)
        if owned and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return None
    finally:
        if owned and conn is not None:
            conn.close()


def persist_orchestration_events_batch(
    entries: list[dict[str, Any]],
    uow: SqliteUnitOfWork | None = None,
) -> bool:
    """
    Batch persist orchestration_events. Each row dict uses keys aligned with persist_orchestration_event.
    Optional JSON fields: reason_codes, inputs, outputs, payload (serialized like persist_llm_call).
    """
    if not _should_persist() or not entries:
        return True
    path = get_db_path()
    if not path:
        return False
    default_run = get_current_run_id()
    default_req = get_current_request_id()
    now_ms = int(time.time() * 1000)
    rows: list[tuple[Any, ...]] = []
    for e in entries:
        run_id = e.get("run_id") or default_run
        request_id = e.get("request_id") or default_req
        if not run_id or not request_id:
            continue
        rows.append(
            (
                run_id,
                request_id,
                e.get("cycle"),
                e.get("stage", ""),
                e.get("component", ""),
                e.get("event_type", ""),
                e.get("decision"),
                e.get("status"),
                e.get("sequence"),
                e.get("started_at", now_ms),
                e.get("duration_ms"),
                _json_or_none(e.get("reason_codes")),
                _json_or_none(e.get("inputs")),
                _json_or_none(e.get("outputs")),
                _json_or_none(e.get("payload")),
            )
        )
    if not rows:
        return True
    owned = uow is None or uow.conn is None
    conn = None
    try:
        conn = uow.conn if not owned else _get_connection(path)
        insert_orchestration_events_batch(conn, rows)
        if owned:
            conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: persist_orchestration_events_batch failed: %s", e)
        if owned and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owned and conn is not None:
            conn.close()
