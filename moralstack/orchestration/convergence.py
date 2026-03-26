"""
Convergenza deliberativa: modello di stato esplicito e invarianti.
Enforcement centrale: "continue" non può mai sopravvivere quando cicli esauriti o converged.
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
    Invarianti dure: unica autorità sul loop.
    - should_continue=False quando cycle >= max_cycles oppure decisione convergente/refuse.
    - Se decisione convergente (CONVERGED / CONVERGED_WITH_SUGGESTIONS): converged=True anche
      quando cycle >= max_cycles (priorità alla decisione sullo stop per cicli esauriti).
    - Se cycle >= max_cycles e decisione non convergente: converged=False, stop_reason=CYCLES_EXHAUSTED.
    - should_continue=True permesso SOLO se cycle < max_cycles AND not converged AND no REFUSE.
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
    # CONTINUE o REVISE: si può continuare solo se ci sono ancora cicli
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
    """Costruisce il payload 'raw' per log (prima dell'enforcement)."""
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
    """Log strutturato JSON per convergenza (thread-safe via logging)."""
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
