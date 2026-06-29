"""
Step 13 — shared governance audit finalization.

This module centralises the per-request "finalisation" work that previously
lived only in the FastAPI server proxy. The same logic is now also invoked
from the Python SDK so that ``requests.final_response`` and
``requests.meta_json`` (plus the canonical ``request.meta_updated``
observability envelope) are populated regardless of the entry point.

Why this exists:
    * The proxy and the SDK both consume the same ``OrchestratorResult``.
    * Without a shared helper, only the proxy populated the governance audit
      surface, leaving SDK-driven traces with empty governance fields in the
      UI and Markdown export.
    * Keeping the extraction logic in one place avoids duplicated parsing of
      ``result.response.metadata`` and keeps the contract uniform.

Best-effort: every public function swallows exceptions internally and never
propagates failures into the hot governance path. Observability is a
side-effect, never the failure surface.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_request_meta_from_result(result: Any) -> dict[str, Any]:
    """
    Extract stable governance metadata for the ``requests.meta_json`` column.

    The returned dictionary contains only JSON-friendly scalars / lists / dicts
    so it can be safely merged into the persisted ``meta_json`` column. All
    fields are best-effort: missing attributes resolve to ``None`` and the
    function never raises.

    The keys mirror what the UI templates and Markdown export read so the
    audit surface is consistent for both proxy- and SDK-originated runs.
    """
    meta: dict[str, Any] = {}
    if result is None:
        return meta
    try:
        response = getattr(result, "response", None)
        metadata = getattr(response, "metadata", None) if response is not None else None
        if metadata is not None:
            meta["final_action"] = getattr(metadata, "final_action", None)
            risk_score = getattr(metadata, "risk_score", None)
            try:
                meta["risk_score"] = float(risk_score) if risk_score is not None else None
            except (ValueError, TypeError):
                meta["risk_score"] = None
            path_value = getattr(metadata, "path", None) or getattr(metadata, "decision_path", None)
            meta["path"] = path_value
            meta["path_taken"] = getattr(result, "path_taken", None) or path_value
            reason_codes = getattr(metadata, "reason_codes", None)
            meta["reason_codes"] = list(reason_codes) if reason_codes else []
            triggered = getattr(metadata, "triggered_principles", None)
            meta["triggered_principles"] = list(triggered) if triggered else []
            meta["decision_reason"] = (
                getattr(metadata, "winning_decision_reason", None)
                or getattr(metadata, "decision_reason", None)
                or getattr(metadata, "winning_rule", None)
            )
            explanation = getattr(metadata, "decision_explanation", None)
            if explanation is not None:
                to_dict = getattr(explanation, "to_dict", None)
                if callable(to_dict):
                    try:
                        meta["decision_explanation"] = to_dict()
                    except Exception:
                        meta["decision_explanation"] = str(explanation)
                else:
                    meta["decision_explanation"] = str(explanation)
            meta["domain_overlay"] = getattr(metadata, "domain_overlay", None)
            meta["governance_posture"] = getattr(metadata, "governance_posture", None) or getattr(metadata, "posture", None)
        # Conversation linkage
        meta["conversation_id"] = getattr(result, "conversation_id", None)
        meta["turn_index"] = getattr(result, "turn_index", None)
        meta["parent_request_id"] = getattr(result, "parent_request_id", None)
        meta["conversation_state_provided"] = bool(getattr(result, "conversation_state_provided", False))
        meta["conversation_state_updated"] = bool(getattr(result, "conversation_state_updated", False))
        # Ledger / cache hints (from controller context, when available)
        was_cached = getattr(result, "was_cached", None)
        if was_cached is None:
            was_cached = getattr(result, "ledger_hit_applied", None)
        cached_from_turn = getattr(result, "cached_from_turn", None)
        if cached_from_turn is None:
            cached_from_turn = getattr(result, "ledger_from_turn", None)
        if was_cached is not None:
            meta["was_cached"] = bool(was_cached)
        if cached_from_turn is not None:
            meta["cached_from_turn"] = cached_from_turn
        meta["delivery_context_broader_than_governance"] = bool(
            getattr(result, "delivery_context_broader_than_governance", False)
        )
        meta["mismatch_guard_action"] = getattr(result, "mismatch_guard_action", "none")
        meta["governance_context_mode"] = getattr(result, "governance_context_mode", "none")
        meta["candidate_context_mode"] = getattr(result, "candidate_context_mode", "none")
        meta["prior_turn_count"] = getattr(result, "prior_turn_count", 0)
        meta["history_source"] = getattr(result, "history_source", "none")
    except Exception:
        logger.debug("build_request_meta_from_result: extraction failed (non-fatal)", exc_info=True)
    # Keep empty lists for reason_codes/triggered_principles so consumers can rely on stable shape.
    return {k: v for k, v in meta.items() if v is not None or k in {"reason_codes", "triggered_principles"}}


def state_summary_or_none(state: Any) -> dict[str, Any] | None:
    """Return ``state.to_summary_dict()`` when available, else ``None``."""
    if state is None:
        return None
    summary_fn = getattr(state, "to_summary_dict", None)
    if not callable(summary_fn):
        return None
    try:
        summary = summary_fn()
    except Exception:
        return None
    return summary if isinstance(summary, dict) else None


def posture_of(state: Any) -> str | None:
    """Return ``state.last_governance_posture`` when available, else ``None``."""
    if state is None:
        return None
    posture = getattr(state, "last_governance_posture", None)
    return posture if isinstance(posture, str) and posture else None


def finalize_governance_audit(
    *,
    run_id: str,
    request_id: str,
    result: Any | None,
    final_response_text: str,
    conversation_id: str | None = None,
    turn_index: int | None = None,
    domain: str | None = None,
    final_action_override: str | None = None,
    update_response: bool = True,
    update_domain: bool = True,
    emit_meta: bool = True,
) -> dict[str, Any]:
    """
    Persist Step 13 governance audit fields on the ``requests`` row.

    Side effects (all best-effort):
        * Updates ``requests.final_response`` with ``final_response_text``.
        * Updates ``requests.domain`` when ``domain`` is provided.
        * Builds a governance meta dictionary from ``result`` and emits
          ``request.meta_updated`` (which merges into ``requests.meta_json``).

    Returns the constructed meta dictionary so callers (e.g. the proxy) can
    reuse it for additional emit calls (``proxy.request_finalized``).

    Notes:
        - ``run_id`` and ``request_id`` are required; an empty/missing value
          short-circuits the call and returns an empty meta dict.
        - When observability is disabled (no DB path, file_only mode without
          DB) the SQLite writes are no-ops; the JSONL sink still records the
          ``request.meta_updated`` envelope.
    """
    if not run_id or not request_id:
        return {}

    try:
        from moralstack.observability.conversation_events import finalize_audit_sync
        from moralstack.observability.sinks.sqlite_sink import (
            update_request_domain,
            update_request_response,
        )
    except Exception:
        logger.debug("finalize_governance_audit: import failure (non-fatal)", exc_info=True)
        return {}

    if update_response:
        try:
            update_request_response(
                run_id=run_id,
                request_id=request_id,
                final_response=final_response_text or "",
            )
        except Exception as exc:
            logger.debug("finalize_governance_audit: update_request_response failed: %s", exc)

    if update_domain and domain:
        try:
            update_request_domain(run_id=run_id, request_id=request_id, domain=domain)
        except Exception as exc:
            logger.debug("finalize_governance_audit: update_request_domain failed: %s", exc)

    meta = build_request_meta_from_result(result) if result is not None else {}
    if final_action_override:
        # Delivery may fail closed after orchestration (for example when a
        # NORMAL_COMPLETE result contains only whitespace). Persist the action
        # actually delivered, while the transport-specific event retains the
        # original action separately for audit.
        meta["final_action"] = final_action_override
    if meta and emit_meta:
        try:
            finalize_audit_sync(
                run_id=run_id,
                request_id=request_id,
                final_action=meta.get("final_action"),
                final_response=final_response_text or "",
                domain=domain,
                proxy_summary={"metadata": meta, "_emit_proxy_request_finalized": False},
            )
        except Exception as exc:
            logger.debug("finalize_governance_audit: finalize_audit_sync failed: %s", exc)
    # Stash linkage to help callers reuse without re-extracting.
    if conversation_id is not None and "conversation_id" not in meta:
        meta["conversation_id"] = conversation_id
    if turn_index is not None and "turn_index" not in meta:
        meta["turn_index"] = turn_index
    return meta


__all__ = [
    "build_request_meta_from_result",
    "state_summary_or_none",
    "posture_of",
    "finalize_governance_audit",
]
