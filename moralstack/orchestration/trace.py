"""
Trace: request-scoped object for end-to-end diagnostics.
Lives for the entire request; updated after each step in controller.process.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Trace:
    """
    Trace of a single request: observability and diagnostics.
    Minimal fields for audit and decision traceability.
    """

    request_id: str = ""
    trace_id: str = ""

    # Risk
    risk_score: float = 0.0
    risk_category: str = ""
    op_risk: str = ""

    # Decision
    decision_path: str = ""
    final_action: str = ""
    response_type: str = ""

    # Deliberation
    deliberation_cycles_planned: int = 0
    deliberation_cycles_actual: int = 0
    modules_called: set[str] = field(default_factory=set)
    converged: bool = False

    # Flags
    used_fallback_parse: bool = False
    domain_excluded: bool = False

    # Raw risk estimator (primi 200 chars)
    raw_risk_estimator_output_snippet: str = ""
