"""
Decision Trace: modello strutturato e serializzabile per ogni richiesta MoralStack.
Persistito su file JSONL, indicizzato per request_id.
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TRACE_PATH = "logs/decision_trace.jsonl"
TRACE_PATH_ENV = "MORALSTACK_DECISION_TRACE_PATH"

_trace_lock = threading.Lock()
_trace_file: io.TextIOWrapper | None = None
_trace_file_path: str | None = None


def _get_trace_path(path_override: str | None = None) -> str:
    """Risolve il path: override > env > default. Ritorna path assoluto per consistency."""
    raw = path_override if path_override is not None else os.getenv(TRACE_PATH_ENV, DEFAULT_TRACE_PATH)
    return os.path.abspath(raw)


def _ensure_trace_file(path: str) -> io.TextIOWrapper | None:
    """Apre (lazy) il file di trace e lo restituisce. None in caso di errore."""
    global _trace_file, _trace_file_path
    if _trace_file is not None and _trace_file_path == path:
        return _trace_file
    if _trace_file is not None:
        try:
            _trace_file.close()
        except OSError:
            pass
        _trace_file = None
        _trace_file_path = None
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        _trace_file = open(path, "a", encoding="utf-8")
        _trace_file_path = path
        return _trace_file
    except OSError as e:
        logger.warning("decision_trace: impossibile aprire %s: %s", path, e)
        return None


@dataclass
class DecisionTrace:
    request_id: str

    # Stage audit (PRE_POLICY | FINAL)
    stage: str = ""
    sequence: int = 0  # 1 = PRE, 2 = FINAL

    # Risk estimator
    risk_raw: dict | None = None
    risk_category: str = ""
    risk_score: float = 0.0
    operational_risk: str = ""
    intent_operational: bool = False
    requested_instructions: bool = False
    intent_to_harm: bool = False
    estimation_mode: str = ""  # "parallel" (3 mini-estimators) | "monolithic" | ""

    # Domain & policy
    domain_overlay: str = ""
    excluded_domain: str = ""
    domain_excluded: bool = False
    policy_min_action: str = ""
    policy_max_action: str = ""
    policy_reason_codes: list[str] = field(default_factory=list)

    # Decision
    path: str = ""
    final_action: str = ""
    decision_reason: str = ""

    # Decision explainability (always populated)
    activated_signals: list[str] = field(default_factory=list)
    overlay_applied: str = ""
    winning_rule: str = ""
    reason_codes: list[str] = field(default_factory=list)
    why_not_refuse: str = ""
    why_not_safe_complete: str = ""
    why_not_normal_complete: str = ""

    # Hard violations (post-policy override)
    hard_violation_codes: list[str] = field(default_factory=list)
    hard_violation_source: str = ""

    # Simulator semantic metrics (populated when sim_result available)
    sim_expected_valence: float = 0.0
    sim_semantic_expected_harm: float = 0.0
    sim_dominant_harm_types: list[str] = field(default_factory=list)
    sim_worst_harm: dict | None = None

    # Token optimization (optional, for reporting)
    context_mode_by_module: dict[str, str] = field(default_factory=dict)  # e.g. {"critic":"thin","simulator":"full"}
    modules_skipped: dict[str, str] = field(default_factory=dict)  # e.g. {"simulator":"carried_forward"}

    # Closure state (populated when available from deliberation outcome)
    stop_reason: str = ""  # CONVERGED | CYCLES_EXHAUSTED | HARD_VIOLATION_STOP | NONE
    total_cycles: int = 0  # total deliberative cycles executed (populated post-deliberation)
    policy_principle_ids: list[str] = field(default_factory=list)  # from policy_overlay for audit

    # Extensible JSON-safe payload for audit stages (RISK_ASSESSMENT, REQUEST_ANALYSIS_CONTEXT, CYCLE_SUMMARY, …)
    stage_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_trace_fields(trace: DecisionTrace) -> DecisionTrace:
    """Assicura che nessun campo critico sia None (audit-grade)."""
    trace.risk_raw = trace.risk_raw or None
    trace.estimation_mode = getattr(trace, "estimation_mode", "") or ""
    trace.domain_overlay = trace.domain_overlay or ""
    trace.excluded_domain = getattr(trace, "excluded_domain", "") or ""
    trace.policy_min_action = trace.policy_min_action or ""
    trace.policy_max_action = trace.policy_max_action or ""
    trace.policy_reason_codes = list(trace.policy_reason_codes or [])
    trace.hard_violation_codes = list(trace.hard_violation_codes or [])
    trace.decision_reason = trace.decision_reason or ""
    trace.sim_dominant_harm_types = list(trace.sim_dominant_harm_types or [])
    trace.activated_signals = list(trace.activated_signals or [])
    trace.overlay_applied = trace.overlay_applied or ""
    trace.winning_rule = trace.winning_rule or ""
    trace.reason_codes = list(trace.reason_codes or [])
    trace.why_not_refuse = trace.why_not_refuse or ""
    trace.why_not_safe_complete = trace.why_not_safe_complete or ""
    trace.why_not_normal_complete = getattr(trace, "why_not_normal_complete", "") or ""
    trace.stage_payload = dict(trace.stage_payload or {})
    return trace


def append_decision_trace(trace: DecisionTrace, path: str | None = None) -> None:
    """
    Emits a decision trace via observability (fire-and-forget).

    Routing is controlled by MORALSTACK_OBSERVABILITY_MODE:
      db_only   -> SQLite only
      dual      -> SQLite + logs/observability/decision.trace.jsonl
      file_only -> logs/observability/decision.trace.jsonl only

    The path= parameter is accepted for backwards compatibility but ignored;
    output location is controlled by MORALSTACK_OBSERVABILITY_JSONL_DIR.
    """
    from moralstack.observability.context import get_current_run_id
    from moralstack.observability.events import EVENT_DECISION_TRACE, make_envelope
    from moralstack.observability.service import get_obs

    run_id = get_current_run_id()
    request_id = trace.request_id or ""
    if not run_id or not request_id:
        return
    try:
        envelope = make_envelope(
            EVENT_DECISION_TRACE,
            run_id=run_id,
            request_id=request_id,
            payload={
                "stage": trace.stage or "",
                "sequence": trace.sequence or 0,
                "trace_json": json.dumps(trace.to_dict(), ensure_ascii=False),
            },
        )
        get_obs().emit(envelope)
    except Exception as e:
        logger.warning("decision_trace: persist failed: %s", e)
