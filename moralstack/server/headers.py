"""
Build governance headers from an OrchestratorResult.

Per design v1.3 §4.2, the proxy attaches X-Moralstack-* headers to every
response. Clients can read these for audit/logging without parsing the body.
"""

from __future__ import annotations

from typing import Any


def build_governance_headers(
    result: Any,
    *,
    conversation_id: str = "",
) -> dict[str, str]:
    """
    Build X-Moralstack-* headers from an OrchestratorResult.

    Args:
        result: an OrchestratorResult instance (the output of controller.process).
        conversation_id: the resolved conversation_id (may be empty for single-turn).

    Returns:
        A dict {header_name: header_value} ready to be passed to FastAPI's
        Response(headers=...). All values are strings.
    """
    metadata = result.response.metadata
    decision = str(getattr(metadata, "final_action", "") or "")
    risk_score_raw = getattr(metadata, "risk_score", None)
    risk_score = f"{risk_score_raw:.4f}" if isinstance(risk_score_raw, (int, float)) else ""
    path = str(getattr(result, "path", "") or getattr(result, "path_taken", "") or "")
    posture = ""
    if hasattr(result, "conversation_governance_state_out") and result.conversation_governance_state_out is not None:
        posture = str(getattr(result.conversation_governance_state_out, "posture", "") or "")
    cached_from = str(getattr(metadata, "cached_from_decision_id", "") or "")
    internal_draft_reused = bool(getattr(metadata, "internal_draft_reused", False))

    headers: dict[str, str] = {
        "X-Moralstack-Decision": decision or "UNKNOWN",
        "X-Moralstack-Risk-Score": risk_score,
        "X-Moralstack-Posture": posture or "NORMAL",
        "X-Moralstack-Path": path or "UNKNOWN",
        "X-Moralstack-Conversation-Id": conversation_id or "",
        "X-Moralstack-Internal-Draft-Reused": "true" if internal_draft_reused else "false",
    }
    if cached_from:
        headers["X-Moralstack-Cached-From"] = cached_from
    return headers
