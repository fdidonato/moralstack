"""
Deliberative convergence: explicit state model and invariants.
Central enforcement: "continue" can never survive when cycles are exhausted or converged.
"""

from __future__ import annotations

import logging
from typing import Any

from moralstack.orchestration.types import (
    ConvergenceOutcome,
    DecisionType,
)

_LOG = logging.getLogger(__name__)


def enforce_convergence_invariants(
    cycle: int,
    max_cycles: int,
    decision_type: DecisionType | None,
) -> ConvergenceOutcome:
    """
    Hard invariants: sole authority over the loop.
    - should_continue=False when cycle >= max_cycles or the decision is convergent/refuse.
    - If the decision is convergent (CONVERGED / CONVERGED_WITH_SUGGESTIONS): converged=True even
      when cycle >= max_cycles (the decision takes priority over the stop for exhausted cycles).
    - If cycle >= max_cycles and the decision is not convergent: converged=False, stop_reason=CYCLES_EXHAUSTED.
    - should_continue=True allowed ONLY if cycle < max_cycles AND not converged AND no REFUSE.
    """
    decision = decision_type or DecisionType.CONTINUE

    if decision in (DecisionType.CONVERGED, DecisionType.CONVERGED_WITH_SUGGESTIONS):
        return ConvergenceOutcome(
            should_continue=False,
            converged=True,
            stop_reason="CONVERGED",
            cycle=cycle,
            max_cycles=max_cycles,
        )
    if decision == DecisionType.REFUSE:
        return ConvergenceOutcome(
            should_continue=False,
            converged=False,
            stop_reason="HARD_VIOLATION_STOP",
            cycle=cycle,
            max_cycles=max_cycles,
        )
    if cycle >= max_cycles:
        return ConvergenceOutcome(
            should_continue=False,
            converged=False,
            stop_reason="CYCLES_EXHAUSTED",
            cycle=cycle,
            max_cycles=max_cycles,
        )
    # CONTINUE or REVISE: can continue only if cycles remain
    return ConvergenceOutcome(
        should_continue=cycle < max_cycles,
        converged=False,
        stop_reason="NONE",
        cycle=cycle,
        max_cycles=max_cycles,
    )


def build_raw_outcome_for_log(
    cycle: int,
    max_cycles: int,
    decision_type: DecisionType | None,
) -> dict[str, Any]:
    """Builds the 'raw' payload for logging (before enforcement)."""
    decision = decision_type or DecisionType.CONTINUE
    raw_continue = decision in (DecisionType.CONTINUE, DecisionType.REVISE)
    raw_converged = decision in (DecisionType.CONVERGED, DecisionType.CONVERGED_WITH_SUGGESTIONS)
    return {
        "cycle": cycle,
        "max_cycles": max_cycles,
        "raw_should_continue": raw_continue,
        "raw_converged": raw_converged,
        "decision_type": getattr(decision, "value", str(decision)),
    }


def log_convergence_event(
    event: str,
    request_id: str = "",
    question_id: str | None = None,
    **payload: Any,
) -> None:
    """Structured JSON log for convergence (thread-safe via logging)."""
    try:
        import json

        data = {"event": event, "request_id": request_id or "", **payload}
        if question_id is not None:
            data["question_id"] = question_id
        _LOG.info("%s", json.dumps(data, ensure_ascii=False))
    except Exception as e:
        _LOG.warning(
            "log_convergence_event failed event=%s request_id=%s error_type=%s error=%s",
            event,
            request_id or "",
            type(e).__name__,
            e,
        )
