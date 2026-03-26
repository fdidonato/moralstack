"""
Decision explanation logging: centralizes DECISION_EXPLANATION event payload and orch_debug_log.

Used by the controller after decide_action (process) and after deliberative path (_route_deliberative).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.orchestration.diagnostics import orch_debug_log

_LOG = logging.getLogger(__name__)


def log_decision_explanation(
    explanation: DecisionExplanation,
    request_id: str,
    hypothesis_id: str = "H-decision",
    risk_score: float | None = None,
    risk_category: str | None = None,
    **extra: Any,
) -> None:
    """
    Build DECISION_EXPLANATION payload from explanation, merge extra fields,
    log one JSON line to module logger and call orch_debug_log.
    """
    risk_score = risk_score if risk_score is not None else getattr(explanation, "risk_score", 0)
    risk_category = risk_category or getattr(explanation, "risk_category", "") or ""

    payload = {
        "event": "DECISION_EXPLANATION",
        "final_action": getattr(explanation, "final_action", "") or "",
        "risk_score": risk_score,
        "risk_category": risk_category,
        "overlay_applied": getattr(explanation, "overlay_applied", "") or "",
        "winning_rule": getattr(explanation, "winning_rule", "") or "",
        "reason_codes": list(getattr(explanation, "reason_codes", []) or []),
        "why_not_refuse": getattr(explanation, "why_not_refuse", "") or "",
        "why_not_safe_complete": getattr(explanation, "why_not_safe_complete", "") or "",
        "why_not_normal_complete": getattr(explanation, "why_not_normal_complete", "") or "",
        "activated_signals": list(getattr(explanation, "activated_signals", []) or []),
        "timestamp": getattr(explanation, "timestamp", 0),
        **extra,
    }
    _LOG.info("%s", json.dumps({"request_id": request_id, **payload}, ensure_ascii=False))
    orch_debug_log(
        "orchestrator.py:process",
        "DECISION_EXPLANATION",
        payload,
        hypothesis_id=hypothesis_id,
        request_id=request_id,
    )
