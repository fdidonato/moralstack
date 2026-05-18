"""
Step 13 multi-turn observability emit helpers.

Each helper builds an :class:`EventEnvelope` for a canonical multi-turn
observability event and forwards it through the process-wide observability
service (``obs.emit``). All helpers are best-effort: errors during payload
construction or emission are logged at DEBUG level and silently swallowed so
that governance behaviour is never impacted by telemetry failures.

Why these helpers exist:
    Step 13 introduces several new canonical events (``conversation.state_updated``,
    ``ledger.lookup``, ``ledger.store``, ``session_store.get``, ``session_store.put``,
    ``proxy.request_finalized``, ``request.meta_updated``). They share the same
    pattern (build a JSON-safe payload, fill in context values, emit). Centralising
    that pattern here avoids per-callsite duplication and keeps the emission
    contract uniform across the proxy, the controller, the ledger, and the
    session store.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from typing import Any, Mapping

from moralstack.observability.context import get_current_request_id, get_current_run_id
from moralstack.observability.events import (
    EVENT_CONVERSATION_STATE_UPDATED,
    EVENT_LEDGER_LOOKUP,
    EVENT_LEDGER_STORE,
    EVENT_PROXY_REQUEST_FINALIZED,
    EVENT_REQUEST_META_UPDATED,
    EVENT_SESSION_STORE_GET,
    EVENT_SESSION_STORE_PUT,
    make_envelope,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON-safe conversion
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """
    Convert a value into a JSON-serialisable representation.

    Handles:
        - ``None`` and primitive scalars (str, int, float, bool)
        - dataclass instances (recursively via ``dataclasses.asdict``)
        - tuples / sets / frozensets (converted to lists)
        - enums (``.value`` extracted)
        - objects exposing ``to_dict()`` or ``to_summary_dict()``
        - dict / list (recursively walked)

    Any unconvertible value is coerced to ``str(value)`` as a last resort so
    that the emit pipeline never fails on a sneaky non-JSON type.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return getattr(value, "value", str(value))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return _json_safe(dataclasses.asdict(value))
        except Exception:
            pass
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _json_safe(to_dict())
        except Exception:
            pass
    to_summary_dict = getattr(value, "to_summary_dict", None)
    if callable(to_summary_dict):
        try:
            return _json_safe(to_summary_dict())
        except Exception:
            pass
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    try:
        return str(value)
    except Exception:
        return None


def _state_summary(state: Any) -> dict[str, Any] | None:
    """Return ``state.to_summary_dict()`` when available, JSON-safe."""
    if state is None:
        return None
    summary_fn = getattr(state, "to_summary_dict", None)
    if not callable(summary_fn):
        return None
    try:
        summary = summary_fn()
    except Exception:
        return None
    safe = _json_safe(summary)
    return safe if isinstance(safe, dict) else None


def _emit_safe(envelope: Any) -> None:
    """Forward to ``obs.emit``; swallow all exceptions."""
    try:
        from moralstack.observability import obs

        obs.emit(envelope)
    except Exception:
        logger.debug("conversation_events: emit failed", exc_info=True)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def emit_request_meta_updated(
    *,
    run_id: str | None,
    request_id: str | None,
    meta: dict[str, Any],
    merge: bool = True,
) -> None:
    """
    Emit ``request.meta_updated`` so the SQLite sink merges (or replaces) the
    ``requests.meta_json`` column for the given request, while the file/JSONL
    sink captures the full payload too.
    """
    try:
        rid = run_id or get_current_run_id()
        req_id = request_id or get_current_request_id()
        if not rid or not req_id:
            return
        if not isinstance(meta, dict):
            return
        safe_meta = _json_safe(meta)
        if not isinstance(safe_meta, dict):
            return
        envelope = make_envelope(
            EVENT_REQUEST_META_UPDATED,
            run_id=rid,
            request_id=req_id,
            payload={"meta": safe_meta, "merge": bool(merge)},
        )
        _emit_safe(envelope)
    except Exception:
        logger.debug("emit_request_meta_updated failed", exc_info=True)


def emit_conversation_state_updated(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    conversation_id: str | None,
    turn_index: int | None,
    state_in: Any | None,
    state_out: Any,
    final_action: str | None = None,
    risk_score: float | None = None,
    posture: str | None = None,
    was_cached: bool | None = None,
    cached_from_turn: int | None = None,
    refresh_required: bool | None = None,
    refresh_reason: str | None = None,
) -> None:
    """Emit ``conversation.state_updated`` with the full state transition."""
    try:
        rid = run_id or get_current_run_id()
        req_id = request_id or get_current_request_id()
        conv_id = (conversation_id or "").strip() or None
        if not rid or not req_id or not conv_id:
            return
        payload: dict[str, Any] = {
            "conversation_id": conv_id,
            "turn_index": turn_index,
            "state_in": _json_safe(state_in),
            "state_out": _json_safe(state_out),
            "state_summary": _state_summary(state_out),
            "final_action": final_action,
            "risk_score": risk_score,
            "posture": posture,
            "was_cached": was_cached,
            "cached_from_turn": cached_from_turn,
            "refresh_required": refresh_required,
            "refresh_reason": refresh_reason,
        }
        envelope = make_envelope(
            EVENT_CONVERSATION_STATE_UPDATED,
            run_id=rid,
            request_id=req_id,
            session_id=conv_id,
            turn_number=turn_index,
            payload=payload,
        )
        _emit_safe(envelope)
    except Exception:
        logger.debug("emit_conversation_state_updated failed", exc_info=True)


def emit_ledger_lookup(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    conversation_id: str | None = None,
    turn_index: int | None = None,
    outcome: str,
    reason: str | None = None,
    similarity: float | None = None,
    from_turn: int | None = None,
    contract_hash: str | None = None,
    posture: str | None = None,
    domain: str | None = None,
    intent_clarity: str | None = None,
    request_type: str | None = None,
    final_action: str | None = None,
    risk_score: float | None = None,
    candidate_count: int | None = None,
    threshold: float | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit ``ledger.lookup`` for every lookup path (hit/miss)."""
    try:
        rid = run_id or get_current_run_id()
        req_id = request_id or get_current_request_id()
        if not rid:
            return
        payload: dict[str, Any] = {
            "operation": "lookup",
            "outcome": outcome,
            "reason": reason,
            "similarity": similarity,
            "from_turn": from_turn,
            "contract_hash": contract_hash,
            "posture": posture,
            "domain": domain,
            "intent_clarity": intent_clarity,
            "request_type": request_type,
            "final_action": final_action,
            "risk_score": risk_score,
            "candidate_count": candidate_count,
            "threshold": threshold,
        }
        if extra:
            payload["payload"] = _json_safe(extra)
        envelope = make_envelope(
            EVENT_LEDGER_LOOKUP,
            run_id=rid,
            request_id=req_id,
            session_id=(conversation_id or None),
            turn_number=turn_index,
            payload=payload,
        )
        _emit_safe(envelope)
    except Exception:
        logger.debug("emit_ledger_lookup failed", exc_info=True)


def emit_ledger_store(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    conversation_id: str | None = None,
    turn_index: int | None = None,
    outcome: str,
    reason: str | None = None,
    contract_hash: str | None = None,
    posture: str | None = None,
    domain: str | None = None,
    intent_clarity: str | None = None,
    request_type: str | None = None,
    final_action: str | None = None,
    risk_score: float | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit ``ledger.store`` for every store path (stored/skipped)."""
    try:
        rid = run_id or get_current_run_id()
        req_id = request_id or get_current_request_id()
        if not rid:
            return
        payload: dict[str, Any] = {
            "operation": "store",
            "outcome": outcome,
            "reason": reason,
            "contract_hash": contract_hash,
            "posture": posture,
            "domain": domain,
            "intent_clarity": intent_clarity,
            "request_type": request_type,
            "final_action": final_action,
            "risk_score": risk_score,
        }
        if extra:
            payload["payload"] = _json_safe(extra)
        envelope = make_envelope(
            EVENT_LEDGER_STORE,
            run_id=rid,
            request_id=req_id,
            session_id=(conversation_id or None),
            turn_number=turn_index,
            payload=payload,
        )
        _emit_safe(envelope)
    except Exception:
        logger.debug("emit_ledger_store failed", exc_info=True)


def emit_session_store_get(
    *,
    conversation_id: str,
    outcome: str,
    state: Any | None = None,
    ttl_age_seconds: float | None = None,
    extra: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    turn_index: int | None = None,
) -> None:
    """Emit ``session_store.get`` (hit/miss/expired)."""
    try:
        cid = (conversation_id or "").strip()
        if not cid:
            return
        rid = run_id or get_current_run_id()
        req_id = request_id or get_current_request_id()
        # Bundle auxiliary scalars into the `payload` sub-dict so the SQLite
        # `payload_json` column captures them (state_summary is a separate column).
        nested: dict[str, Any] = {}
        if ttl_age_seconds is not None:
            nested["ttl_age_seconds"] = ttl_age_seconds
        if extra:
            safe_extra = _json_safe(extra)
            if isinstance(safe_extra, dict):
                nested.update(safe_extra)
            else:
                nested["extra"] = safe_extra
        payload: dict[str, Any] = {
            "operation": "get",
            "outcome": outcome,
            "state_summary": _state_summary(state),
            "ttl_age_seconds": ttl_age_seconds,
        }
        if nested:
            payload["payload"] = nested
        envelope = make_envelope(
            EVENT_SESSION_STORE_GET,
            run_id=rid,
            request_id=req_id,
            session_id=cid,
            turn_number=turn_index,
            payload=payload,
        )
        _emit_safe(envelope)
    except Exception:
        logger.debug("emit_session_store_get failed", exc_info=True)


def emit_session_store_put(
    *,
    conversation_id: str,
    outcome: str,
    state: Any | None = None,
    evicted_ids: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    turn_index: int | None = None,
) -> None:
    """Emit ``session_store.put`` (stored, with optional eviction info)."""
    try:
        cid = (conversation_id or "").strip()
        if not cid:
            return
        rid = run_id or get_current_run_id()
        req_id = request_id or get_current_request_id()
        evicted_list = list(evicted_ids or []) or None
        # Bundle auxiliary scalars into the `payload` sub-dict so the SQLite
        # `payload_json` column captures them (state_summary is a separate column).
        nested: dict[str, Any] = {}
        if evicted_list is not None:
            nested["evicted_ids"] = evicted_list
        if extra:
            safe_extra = _json_safe(extra)
            if isinstance(safe_extra, dict):
                nested.update(safe_extra)
            else:
                nested["extra"] = safe_extra
        payload: dict[str, Any] = {
            "operation": "put",
            "outcome": outcome,
            "state_summary": _state_summary(state),
            "evicted_ids": evicted_list,
        }
        if nested:
            payload["payload"] = nested
        envelope = make_envelope(
            EVENT_SESSION_STORE_PUT,
            run_id=rid,
            request_id=req_id,
            session_id=cid,
            turn_number=turn_index,
            payload=payload,
        )
        _emit_safe(envelope)
    except Exception:
        logger.debug("emit_session_store_put failed", exc_info=True)


def emit_proxy_request_finalized(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    conversation_id: str | None = None,
    turn_index: int | None = None,
    final_action: str | None = None,
    risk_score: float | None = None,
    path: str | None = None,
    domain: str | None = None,
    posture_in: str | None = None,
    posture_out: str | None = None,
    state_provided: bool | None = None,
    state_updated: bool | None = None,
    was_cached: bool | None = None,
    cached_from_turn: int | None = None,
    final_response_length: int | None = None,
    headers: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
    state_in: Any | None = None,
    state_out: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit ``proxy.request_finalized`` summarising one HTTP proxy request."""
    try:
        rid = run_id or get_current_run_id()
        req_id = request_id or get_current_request_id()
        if not rid or not req_id:
            return
        payload: dict[str, Any] = {
            "final_action": final_action,
            "risk_score": risk_score,
            "path": path,
            "domain": domain,
            "posture_in": posture_in,
            "posture_out": posture_out,
            "state_provided": state_provided,
            "state_updated": state_updated,
            "was_cached": was_cached,
            "cached_from_turn": cached_from_turn,
            "final_response_length": final_response_length,
            "headers": _json_safe(headers) if headers else None,
            "metadata": _json_safe(metadata) if metadata else None,
            "state_in": _json_safe(state_in),
            "state_out": _json_safe(state_out),
        }
        if extra:
            payload["payload"] = _json_safe(extra)
        envelope = make_envelope(
            EVENT_PROXY_REQUEST_FINALIZED,
            run_id=rid,
            request_id=req_id,
            session_id=(conversation_id or None),
            turn_number=turn_index,
            payload=payload,
        )
        _emit_safe(envelope)
    except Exception:
        logger.debug("emit_proxy_request_finalized failed", exc_info=True)


__all__ = [
    "emit_request_meta_updated",
    "emit_conversation_state_updated",
    "emit_ledger_lookup",
    "emit_ledger_store",
    "emit_session_store_get",
    "emit_session_store_put",
    "emit_proxy_request_finalized",
]
