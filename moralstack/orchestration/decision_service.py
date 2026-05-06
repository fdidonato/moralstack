"""
DecisionService: encapsulates decide_action (policy-driven, language-agnostic).
Single source of truth: moralstack.runtime.decision.safe_complete_policy.

The decision is deterministic with respect to the provided inputs.
In normal operation, the module may emit audit logs and decision traces
(e.g. for debug, evaluation or compliance).
These side effects are observational only and do not affect the decision outcome.
No inference from text or disclaimer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, cast

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.reason_codes import policy_reason_codes_to_reason_codes
from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.orchestration.types import (
    CriticReportProtocol,
    Decision,
    FinalActionStr,
    HindsightResultProtocol,
    PathStr,
    ProcessedRequest,
    RiskCategoryLikeProtocol,
    RiskEstimationProtocol,
    RiskThresholds,
    SimulationResultProtocol,
)
from moralstack.runtime.decision.safe_complete_policy import (
    REASON_HARD_VIOLATIONS,
    REASON_RISK_CLEARLY_HARMFUL,
    PolicyContext,
    decide_final_action,
)
from moralstack.runtime.trace import (
    DecisionTrace,
    append_decision_trace,
    normalize_trace_fields,
)

logger = logging.getLogger(__name__)

# Reason codes for diagnostics (recovery rule only; policy reason_codes from safe_complete_policy).
REASON_REGULATED_BUT_INFORMATIONAL = "regulated_but_informational"

# Soft principles promotable to hard only with severity >= 0.85.
# Initially empty: no soft principle is auto-promoted.
# Add explicit IDs only after full benchmark review.
PROMOTABLE_SOFT_IDS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _DecisionInputs:
    """Common parameters for building Decision; reduces duplication."""

    intent_clarity: str
    misuse_plausibility: str
    actionability_risk: str
    triggered_principles: list[str]
    hard_violations: list[str]
    risk_signals: list[str]


def _make_decision(
    inputs: _DecisionInputs,
    final_action: str,
    path: str,
    reason_codes: list[str],
) -> Decision:
    """Builds a Decision from common + specific parameters."""
    return Decision(
        final_action=cast(FinalActionStr, final_action),
        path=cast(PathStr, path),
        intent_clarity=inputs.intent_clarity,
        misuse_plausibility=inputs.misuse_plausibility,
        actionability_risk=inputs.actionability_risk,
        triggered_principles=inputs.triggered_principles,
        hard_violations=inputs.hard_violations,
        risk_signals=inputs.risk_signals,
        reason_codes=reason_codes,
    )


def _build_why_not(
    final_action: str,
    risk_score: float,
    *,
    risk_thresholds: RiskThresholds | None = None,
    decisive_signals: list[str] | None = None,
    hard_violations: list[str] | None = None,
    reason_codes: list[str] | None = None,
) -> tuple[str, str, str]:
    """Build why_not_refuse, why_not_safe_complete, why_not_normal_complete. Never empty.

    When `risk_thresholds` is None (legacy callers), falls back to canned strings.
    When provided together with decisive_signals/hard_violations/reason_codes,
    produces qualitative reasoning enriched with the relevant numeric thresholds
    (qualitative + numeric color).
    """
    fa = (final_action or "").strip().upper()

    if risk_thresholds is None:
        # Back-compat: legacy callers (no thresholds plumbed) → canned strings.
        if fa == "REFUSE":
            return (
                "Action was REFUSE; alternative actions not applicable.",
                "Risk score exceeded refuse threshold.",
                "Risk/refuse threshold or hard violations required refusal.",
            )
        if fa == "SAFE_COMPLETE":
            return (
                "Risk below refuse threshold or non-operational.",
                "N/A; current action is SAFE_COMPLETE.",
                "Risk/domain/guardrail required safe framing (not full NORMAL_COMPLETE).",
            )
        return (
            "No operational harmful intent detected.",
            "Risk below sensitive guardrail threshold.",
            "Action was NORMAL_COMPLETE; alternative not applicable.",
        )

    # Enriched mode: thresholds plumbed → qualitative + numeric color.
    score = float(risk_score or 0.0)
    low = float(risk_thresholds.low)
    medium = float(risk_thresholds.medium)
    sigs = list(decisive_signals or [])
    hv = list(hard_violations or [])
    rc = list(reason_codes or [])
    sig_str = ", ".join(sigs) if sigs else "no decisive signals"
    hv_str = ", ".join(hv) if hv else "none"
    rc_str = ", ".join(rc) if rc else "none"

    if fa == "REFUSE":
        return (
            (
                f"REFUSE chosen: risk_score={score:.2f} (medium threshold={medium:.2f}); "
                f"hard_violations=[{hv_str}]; decisive_signals=[{sig_str}]; "
                f"reason_codes=[{rc_str}]."
            ),
            (
                f"SAFE_COMPLETE not selected: hard_violations or normative refuse guards triggered "
                f"(see hard_violations=[{hv_str}], reason_codes=[{rc_str}])."
            ),
            (
                f"NORMAL_COMPLETE not selected: decisive_signals=[{sig_str}] and/or "
                f"hard_violations=[{hv_str}] required at least SAFE_COMPLETE; risk_score={score:.2f}."
            ),
        )
    if fa == "SAFE_COMPLETE":
        return (
            (
                f"REFUSE not selected: risk_score={score:.2f} below medium threshold ({medium:.2f}) "
                f"and no hard_violations triggered (hard_violations=[{hv_str}]); "
                f"decisive_signals=[{sig_str}]."
            ),
            (
                f"SAFE_COMPLETE chosen: risk_score={score:.2f} between low={low:.2f} and "
                f"medium={medium:.2f}, or domain/guardrail required safe framing; "
                f"signals=[{sig_str}]."
            ),
            (
                f"NORMAL_COMPLETE not selected: signals=[{sig_str}] and/or domain overlay "
                f"required caveat-framed response (reason_codes=[{rc_str}])."
            ),
        )
    # NORMAL_COMPLETE
    return (
        (
            f"REFUSE not selected: risk_score={score:.2f} below medium threshold ({medium:.2f}); "
            f"no hard_violations (hard_violations=[{hv_str}]); decisive_signals=[{sig_str}]."
        ),
        (
            f"SAFE_COMPLETE not selected: risk_score={score:.2f} below low threshold ({low:.2f}) "
            f"or no sensitive-domain overlay; signals=[{sig_str}]."
        ),
        (f"NORMAL_COMPLETE chosen: risk_score={score:.2f} below low threshold ({low:.2f}); " f"reason_codes=[{rc_str}]."),
    )


def _build_decision_explanation(
    trace: DecisionTrace,
    decision: Decision,
    winning_rule: str,
    *,
    risk_thresholds: RiskThresholds | None = None,
) -> DecisionExplanation:
    """Build decision explanation from trace, decision, and winning rule.

    When risk_thresholds is provided (or stashed on trace by decide_action),
    why_not_* strings are enriched with numeric thresholds + decisive signals
    + reason codes (qualitative + numeric). Otherwise falls back to canned
    strings (back-compat).
    """
    if risk_thresholds is None:
        risk_thresholds = getattr(trace, "_risk_thresholds", None)
    risk_score = getattr(trace, "risk_score", 0.0) or 0.0
    risk_category = (getattr(trace, "risk_category", "") or "").strip() or "unknown"
    activated_signals = list(getattr(decision, "risk_signals", []) or [])
    overlay = (getattr(trace, "domain_overlay", "") or "").strip() or None
    if not overlay:
        overlay = None
    policy_codes = getattr(decision, "reason_codes", []) or []
    reason_codes = policy_reason_codes_to_reason_codes(policy_codes)
    if not reason_codes:
        reason_codes = ["DEFAULT_NORMAL_COMPLETE"]
    why_not_refuse, why_not_safe_complete, why_not_normal_complete = _build_why_not(
        decision.final_action,
        risk_score,
        risk_thresholds=risk_thresholds,
        decisive_signals=activated_signals,
        hard_violations=list(getattr(decision, "hard_violations", []) or []),
        reason_codes=reason_codes,
    )
    return DecisionExplanation(
        request_id=trace.request_id,
        final_action=decision.final_action,
        risk_score=risk_score,
        risk_category=risk_category,
        activated_signals=activated_signals,
        overlay_applied=overlay or "",
        winning_rule=winning_rule or "policy_bounds_fallback",
        reason_codes=reason_codes,
        why_not_refuse=why_not_refuse,
        why_not_safe_complete=why_not_safe_complete,
        why_not_normal_complete=why_not_normal_complete,
        timestamp=time.time(),
    )


def _log_final_trace(
    trace: DecisionTrace,
    decision: Decision,
    winning_rule: str = "policy_bounds_fallback",
    hard_violation_codes: list[str] | None = None,
    hard_violation_source: str = "",
    *,
    risk_thresholds: RiskThresholds | None = None,
) -> DecisionExplanation:
    """Creates a FINAL DecisionTrace (stage=FINAL, sequence=2), normalizes and appends to JSONL.
    Returns DecisionExplanation for metadata propagation."""
    # Fall back to thresholds stashed on trace by decide_action when not passed explicitly.
    rt = risk_thresholds if risk_thresholds is not None else getattr(trace, "_risk_thresholds", None)
    explanation = _build_decision_explanation(trace, decision, winning_rule, risk_thresholds=rt)
    assert (
        explanation.request_id == trace.request_id
    ), f"request_id mismatch: explanation={explanation.request_id!r} vs trace={trace.request_id!r}"

    final_trace = DecisionTrace(request_id=trace.request_id)
    final_trace.stage = "FINAL"
    final_trace.sequence = 2
    final_trace.risk_raw = trace.risk_raw
    final_trace.risk_category = trace.risk_category
    final_trace.risk_score = trace.risk_score
    final_trace.operational_risk = trace.operational_risk
    final_trace.intent_operational = trace.intent_operational
    final_trace.requested_instructions = trace.requested_instructions
    final_trace.intent_to_harm = trace.intent_to_harm
    final_trace.domain_overlay = trace.domain_overlay or ""
    # Ensure overlay_applied is never empty when domain is known (audit consistency).
    final_trace.overlay_applied = (explanation.overlay_applied or "").strip() or (trace.domain_overlay or "").strip() or ""
    final_trace.policy_max_action = trace.policy_max_action or ""
    final_trace.policy_reason_codes = list(trace.policy_reason_codes or [])
    # When deliberation actually ran (total_cycles > 0 or stop_reason set), the execution path
    # was DELIBERATIVE_PATH even if policy pre-decision was FAST_PATH (e.g. REFUSE borderline).
    _total_cycles = getattr(trace, "total_cycles", 0) or 0
    _stop_reason = (getattr(trace, "stop_reason", "") or "").strip()
    if _total_cycles > 0 or _stop_reason:
        final_trace.path = "DELIBERATIVE_PATH"
    else:
        final_trace.path = decision.path
    final_trace.final_action = decision.final_action
    final_trace.decision_reason = ", ".join(explanation.reason_codes) or "policy_bounds_decision"
    final_trace.activated_signals = list(explanation.activated_signals)
    final_trace.winning_rule = explanation.winning_rule or "policy_bounds_fallback"
    final_trace.reason_codes = list(explanation.reason_codes)
    final_trace.why_not_refuse = explanation.why_not_refuse or ""
    final_trace.why_not_safe_complete = explanation.why_not_safe_complete or ""
    final_trace.why_not_normal_complete = getattr(explanation, "why_not_normal_complete", "") or ""
    final_trace.hard_violation_codes = list(hard_violation_codes or [])
    final_trace.hard_violation_source = hard_violation_source or ""
    final_trace.sim_expected_valence = getattr(trace, "sim_expected_valence", 0.0)
    final_trace.sim_semantic_expected_harm = getattr(trace, "sim_semantic_expected_harm", 0.0)
    final_trace.sim_dominant_harm_types = list(getattr(trace, "sim_dominant_harm_types", []) or [])
    final_trace.sim_worst_harm = getattr(trace, "sim_worst_harm", None)
    final_trace.policy_principle_ids = list(getattr(decision, "triggered_principles", []) or [])
    final_trace.total_cycles = getattr(trace, "total_cycles", 0)
    final_trace.stop_reason = getattr(trace, "stop_reason", "")
    # Fast path: no deliberation cycles and non-REFUSE outcome => treat as converged for reporting.
    if final_trace.total_cycles == 0 and (decision.final_action or "").strip().upper() != "REFUSE":
        final_trace.stop_reason = "CONVERGED"
    normalize_trace_fields(final_trace)
    append_decision_trace(final_trace)

    logger.info(
        "[DECISION_EXPLANATION] final_action=%s risk_score=%s overlay=%s "
        "winning_rule=%s reason_codes=%s why_not_refuse=%s why_not_safe_complete=%s",
        explanation.final_action,
        explanation.risk_score,
        explanation.overlay_applied or "",
        explanation.winning_rule,
        explanation.reason_codes,
        explanation.why_not_refuse,
        explanation.why_not_safe_complete,
    )
    return explanation


# Explicit mapping: categorical/legacy values -> decision levels (LOW|MEDIUM|HIGH).
# Used by intent_clarity, misuse_plausibility, actionability_risk.
# NOTE: The mapping reflects intentional policy decisions; some associations
# (e.g. NONE->HIGH) may seem counter-intuitive but are kept for
# backward compatibility. Do NOT modify without full benchmark review.
AXIS_VALUE_MAPPING: dict[str, str] = {
    "LOW": "LOW",
    "MEDIUM": "MEDIUM",
    "HIGH": "HIGH",
    "CLEAR": "HIGH",
    "NONE": "HIGH",
    "AMBIGUOUS": "LOW",
}
_AXIS_FALLBACK_LEVEL = "LOW"


def _axis_val(e: RiskCategoryLikeProtocol | None) -> str:
    """
    Normalizza valori degli assi decisionali in livelli LOW|MEDIUM|HIGH.

    Riceve enum o valori categoriali (es. IntentClarity, MisusePlausibility,
    ActionabilityRisk) e li mappa in livelli decisionali per la policy.
    Usa AXIS_VALUE_MAPPING; valori non mappati producono _AXIS_FALLBACK_LEVEL.

    NOTE:
    Il mapping riflette decisioni di policy intenzionali.
    Alcune associazioni categoria->livello possono sembrare non intuitive
    (es. NONE->HIGH) ma sono mantenute per backward compatibility.
    NON modificare senza full benchmark review.
    """
    if e is None:
        return _AXIS_FALLBACK_LEVEL
    if hasattr(e, "value"):
        v = str(e.value).upper()
        return AXIS_VALUE_MAPPING.get(v, _AXIS_FALLBACK_LEVEL)
    return _AXIS_FALLBACK_LEVEL


def _extract_critic_violations(critic_result: CriticReportProtocol | None) -> tuple[list[str], list[str]]:
    """
    Estrae hard_violations e triggered_principles dal critic_result.
    Ritorna (hard_violations, triggered_principles).
    """
    hard_violations: list[str] = []
    triggered_principles: list[str] = []
    if critic_result is None:
        return hard_violations, triggered_principles
    for v in getattr(critic_result, "violations", []) or []:
        constraint_type = getattr(v, "constraint_type", "")
        severity = getattr(v, "severity", 0)
        pid = getattr(v, "principle_id", "") or getattr(v, "id", "")
        if constraint_type == "hard" and pid:
            hard_violations.append(pid)
        elif severity >= 0.85 and pid in PROMOTABLE_SOFT_IDS and pid:
            hard_violations.append(pid)
        if pid:
            triggered_principles.append(pid)
    hard_violations = list(dict.fromkeys(hard_violations))
    if getattr(critic_result, "violated_hard", False) and not hard_violations:
        hard_violations = ["hard"]
    return hard_violations, triggered_principles


def _populate_trace_from_risk(trace: DecisionTrace, risk_assessment: RiskEstimationProtocol) -> None:
    """Populates the trace with fields derived from risk_assessment."""
    op_risk = getattr(risk_assessment, "operational_risk", OperationalRisk.NONE)
    risk_category = getattr(risk_assessment, "risk_category", None)
    trace.risk_raw = getattr(risk_assessment, "raw_json", None)
    trace.risk_category = (
        risk_category.value if risk_category and hasattr(risk_category, "value") else (str(risk_category or ""))
    )
    trace.operational_risk = op_risk.value if op_risk and hasattr(op_risk, "value") else (str(op_risk or ""))
    trace.intent_operational = getattr(risk_assessment, "intent_operational", False)
    trace.requested_instructions = getattr(risk_assessment, "requested_instructions", False)
    trace.intent_to_harm = getattr(risk_assessment, "intent_to_harm", False)
    trace.risk_score = getattr(risk_assessment, "score", 0.5)
    trace.estimation_mode = getattr(risk_assessment, "estimation_mode", "")


def _populate_trace_from_sim(trace: DecisionTrace, sim_result: SimulationResultProtocol | None) -> None:
    """Populates the trace with fields derived from sim_result."""
    if sim_result is None:
        return
    trace.sim_expected_valence = getattr(sim_result, "expected_valence", 0.0)
    trace.sim_semantic_expected_harm = getattr(sim_result, "semantic_expected_harm", 0.0)
    trace.sim_dominant_harm_types = list(getattr(sim_result, "dominant_harm_types", []) or [])
    trace.sim_worst_harm = getattr(sim_result, "worst_harm", None)


def _build_policy_context_pre(
    risk_assessment: RiskEstimationProtocol,
    domain: str | None,
    intent_clarity: str,
    misuse_plausibility: str,
    actionability_risk: str,
    overlay_sensitive: bool = False,
) -> PolicyContext:
    """Builds the PolicyContext for the PRE_POLICY decision (hard_violations_count=0)."""
    harm_type = str(getattr(risk_assessment, "harm_type", "none") or "none").strip().lower()
    intent_operational = getattr(risk_assessment, "intent_operational", False)
    requested_instructions = getattr(risk_assessment, "requested_instructions", False)
    """NORMAL_COMPLETE for fiscal frode benchmark question, previously SAFE_COMPLETE even
    if intent_operational=False if requested_instructions=True."""
    _has_operational_signal = intent_operational or requested_instructions
    _harm_type_present = harm_type not in ("", "none", "emotional_relational")

    has_ambiguity_or_dual_use = (
        # Both signals HIGH simultaneously — always active (independent of intent_operational)
        (misuse_plausibility == "HIGH" and actionability_risk == "HIGH")
        # Medium/high signals + explicit operational signal
        or (misuse_plausibility in ("MEDIUM", "HIGH") and _has_operational_signal)
        or (actionability_risk in ("MEDIUM", "HIGH") and _has_operational_signal)
        # Harm type + operational signal (existing logic)
        or (_harm_type_present and _has_operational_signal)
    )
    intent_type_val = (getattr(risk_assessment, "intent_type", "") or "").strip().lower()
    op_risk = getattr(risk_assessment, "operational_risk", OperationalRisk.NONE)
    risk_category = getattr(risk_assessment, "risk_category", None)
    return PolicyContext(
        domain=domain,
        risk_category=risk_category,
        op_risk=op_risk,
        hard_violations_count=0,
        misuse_plausibility=misuse_plausibility,
        actionability_risk=actionability_risk,
        intent_clarity=intent_clarity,
        has_ambiguity_or_dual_use=has_ambiguity_or_dual_use,
        intent_type=intent_type_val or None,
        risk_score=getattr(risk_assessment, "score", 0.5),
        intent_operational=getattr(risk_assessment, "intent_operational", False),
        overlay_sensitive=overlay_sensitive,
    )


def _run_pre_policy_audit(
    trace: DecisionTrace,
    risk_assessment: RiskEstimationProtocol,
    domain: str | None,
    policy_ctx: PolicyContext,
    *,
    append_pre_policy_trace: bool = True,
) -> tuple[str, Any, list[str], str]:
    """
    Executes the PRE_POLICY decision for audit, optionally appends trace.
    Returns (pre_final_action, pre_bounds, pre_reason_codes, pre_path).
    When append_pre_policy_trace is False (e.g. post-deliberation call), skips writing to JSONL.
    """
    pre_final_action, pre_bounds, pre_reason_codes = decide_final_action(policy_ctx)
    trace.domain_overlay = (domain or "").strip() if domain else ""
    trace.policy_min_action = getattr(pre_bounds.min_required, "value", str(pre_bounds.min_required))
    trace.policy_max_action = getattr(pre_bounds.max_allowed, "value", str(pre_bounds.max_allowed))
    trace.policy_reason_codes = list(pre_bounds.reason_codes)
    risk_category = getattr(risk_assessment, "risk_category", None)
    if pre_final_action == "REFUSE":
        pre_path = "FAST_PATH"
    elif pre_final_action == "SAFE_COMPLETE":
        pre_path = "DELIBERATIVE_PATH"
    elif pre_final_action == "NORMAL_COMPLETE" and risk_category == RiskCategory.BENIGN:
        pre_path = "FAST_PATH"
    else:
        pre_path = "DELIBERATIVE_PATH"
    trace.stage = "PRE_POLICY"
    trace.sequence = 1
    trace.path = pre_path
    trace.final_action = pre_final_action
    trace.decision_reason = ", ".join(pre_reason_codes) if pre_reason_codes else "policy_bounds_decision"
    trace.hard_violation_codes = []
    trace.hard_violation_source = ""
    # Populate explainability fields for PRE_POLICY so analytics get consistent reasoning.
    trace.reason_codes = list(policy_reason_codes_to_reason_codes(pre_bounds.reason_codes))
    if not trace.reason_codes:
        trace.reason_codes = ["DEFAULT_NORMAL_COMPLETE"]
    trace.winning_rule = "fast_path" if pre_path == "FAST_PATH" else "policy_bounds_fallback"
    trace.overlay_applied = (domain or "").strip() if domain else ""
    normalize_trace_fields(trace)
    if append_pre_policy_trace:
        append_decision_trace(trace)
    return pre_final_action, pre_bounds, pre_reason_codes, pre_path


def _handle_hard_violations(
    inputs: _DecisionInputs,
    trace: DecisionTrace,
    risk_assessment: RiskEstimationProtocol,
    pre_final_action: str,
    risk_score: float,
    intent_operational: bool,
    domain_regulated: bool,
    requested_instructions: bool,
    write_final: bool = True,
) -> tuple[Decision, DecisionExplanation] | None:
    """
    Gestisce il ramo con hard_violations presenti.
    Ritorna (Decision, DecisionExplanation) se gestito, None altrimenti
    (non dovrebbe mai essere None se hard_violations).
    """
    hard_violations = inputs.hard_violations
    if not hard_violations:
        return None

    # Crisis help-seeking: MH.CRISIS.1 + request_type crisis_support
    crisis_help_seeking = (
        "MH.CRISIS.1" in hard_violations
        and str(getattr(risk_assessment, "request_type", "") or "").strip().lower() == "crisis_support"
    )
    if crisis_help_seeking:
        d = _make_decision(
            inputs,
            final_action="SAFE_COMPLETE",
            path="DELIBERATIVE_PATH",
            reason_codes=[REASON_HARD_VIOLATIONS, "safe_complete_required"],
        )
        expl = (
            _log_final_trace(
                trace,
                d,
                winning_rule="hard_violations",
                hard_violation_codes=hard_violations,
                hard_violation_source="post_policy_guardrail",
            )
            if write_final
            else _build_decision_explanation(trace, d, "hard_violations")
        )
        return d, expl

    # Deroga: info-regolata (risk basso, non operativo, dominio regolato)
    op_risk = getattr(risk_assessment, "operational_risk", OperationalRisk.NONE)
    if risk_score < 0.5 and op_risk == OperationalRisk.NONE and not intent_operational and domain_regulated:
        d = _make_decision(
            inputs,
            final_action="SAFE_COMPLETE",
            path="DELIBERATIVE_PATH",
            reason_codes=[REASON_HARD_VIOLATIONS, "safe_complete_required"],
        )
        expl = (
            _log_final_trace(
                trace,
                d,
                winning_rule="hard_violations",
                hard_violation_codes=hard_violations,
                hard_violation_source="post_policy_guardrail",
            )
            if write_final
            else _build_decision_explanation(trace, d, "hard_violations")
        )
        return d, expl

    # SAFE_COMPLETE must not be overridden for non-operational, medium/low-risk cases
    if pre_final_action == "SAFE_COMPLETE" and risk_score < 0.5 and not intent_operational and not requested_instructions:
        d = _make_decision(
            inputs,
            final_action="SAFE_COMPLETE",
            path="DELIBERATIVE_PATH",
            reason_codes=["hard_violation_downgraded_to_safe_complete"],
        )
        expl = (
            _log_final_trace(
                trace,
                d,
                winning_rule="hard_violations",
                hard_violation_codes=hard_violations,
                hard_violation_source="post_policy_guardrail",
            )
            if write_final
            else _build_decision_explanation(trace, d, "hard_violations")
        )
        return d, expl

    # Otherwise, enforce REFUSE
    d = _make_decision(
        inputs,
        final_action="REFUSE",
        path="FAST_PATH",
        reason_codes=[REASON_HARD_VIOLATIONS],
    )
    expl = (
        _log_final_trace(
            trace,
            d,
            winning_rule="hard_violations",
            hard_violation_codes=hard_violations,
            hard_violation_source="post_policy_guardrail",
        )
        if write_final
        else _build_decision_explanation(trace, d, "hard_violations")
    )
    return d, expl


def _handle_hindsight_refuse(
    inputs: _DecisionInputs,
    trace: DecisionTrace,
    hindsight_result: HindsightResultProtocol | None,
    write_final: bool = True,
) -> tuple[Decision, DecisionExplanation] | None:
    """If hindsight recommends refuse, returns REFUSE. Otherwise None."""
    if hindsight_result is None:
        return None
    agg = getattr(hindsight_result, "aggregated", None)
    rec = getattr(agg, "recommendation", None) if agg else None
    rec = rec or getattr(hindsight_result, "recommendation", None)
    if rec is None:
        return None
    rec_val = getattr(rec, "value", rec)
    rec_val = str(rec_val).strip().lower() if rec_val else ""
    if rec_val != "refuse":
        return None
    d = _make_decision(
        inputs,
        final_action="REFUSE",
        path="DELIBERATIVE_PATH",
        reason_codes=[REASON_RISK_CLEARLY_HARMFUL],
    )
    expl = (
        _log_final_trace(trace, d, winning_rule="hindsight_refuse")
        if write_final
        else _build_decision_explanation(trace, d, "hindsight_refuse")
    )
    return d, expl


def _handle_normative_refuse_guards(
    inputs: _DecisionInputs,
    trace: DecisionTrace,
    op_risk: OperationalRisk,
    misuse_plausibility: str,
    actionability_risk: str,
    write_final: bool = True,
) -> tuple[Decision, DecisionExplanation] | None:
    # FONTE AUTORITATIVA per final_action quando op_risk == HIGH.
    # Produce la decisione effettiva e la scrive nel trace con winning_rule="normative_refuse_guards".
    # path_router.py contiene un blocco speculare che ri-deriva risk_policy_action
    # dallo stesso op_risk, ma solo per scopi di routing e logging.
    # Le due logiche devono rimanere allineate: se cambi le condizioni qui,
    # aggiorna anche path_router.get_route() e viceversa.
    """Normative REFUSE guards: op_risk HIGH, or misuse+actionability HIGH."""
    if op_risk == OperationalRisk.HIGH:
        d = _make_decision(
            inputs,
            final_action="REFUSE",
            path="FAST_PATH",
            reason_codes=[REASON_RISK_CLEARLY_HARMFUL],
        )
        expl = (
            _log_final_trace(trace, d, winning_rule="normative_refuse_guards")
            if write_final
            else _build_decision_explanation(trace, d, "normative_refuse_guards")
        )
        return d, expl
    if misuse_plausibility == "HIGH" and actionability_risk == "HIGH":
        d = _make_decision(
            inputs,
            final_action="REFUSE",
            path="FAST_PATH",
            reason_codes=[REASON_RISK_CLEARLY_HARMFUL],
        )
        expl = (
            _log_final_trace(trace, d, winning_rule="normative_refuse_guards")
            if write_final
            else _build_decision_explanation(trace, d, "normative_refuse_guards")
        )
        return d, expl
    return None


def _handle_epistemic_escalation(
    inputs: _DecisionInputs,
    trace: DecisionTrace,
    sim_result: SimulationResultProtocol | None,
    actionability_risk: str,
    write_final: bool = True,
) -> tuple[Decision, DecisionExplanation] | None:
    """Simulator negativity → SAFE_COMPLETE, never REFUSE."""
    if sim_result is None:
        return None
    exp_val = getattr(sim_result, "expected_valence", 0.0)
    if exp_val >= -0.5 or actionability_risk != "HIGH":
        return None
    d = _make_decision(
        inputs,
        final_action="SAFE_COMPLETE",
        path="DELIBERATIVE_PATH",
        reason_codes=["sim_negative_valence_safe_complete"],
    )
    expl = (
        _log_final_trace(trace, d, winning_rule="epistemic_escalation")
        if write_final
        else _build_decision_explanation(trace, d, "epistemic_escalation")
    )
    return d, expl


def _handle_informational_recovery(
    inputs: _DecisionInputs,
    trace: DecisionTrace,
    policy_ctx: PolicyContext,
    pre_final_action: str,
    has_overlay_soft_violations: bool = False,
) -> tuple[Decision, DecisionExplanation] | None:
    """
    Recovery: informational (not operational, low score).

    Correct narrowed behavior:
    - keep benign/no-overlay informational recovery to NORMAL_COMPLETE
    - if the active constitution overlay is sensitive, BENIGN informational recovery
      is promoted to SAFE_COMPLETE
    - SENSITIVE / MORALLY_NUANCED / POTENTIALLY_HARMFUL keep deferring to policy bounds
      so their native reason_codes remain intact
    """
    if policy_ctx.intent_operational or policy_ctx.risk_score >= 0.5:
        return None

    domain_regulated = bool(policy_ctx.overlay_sensitive)

    requested_instructions = bool(getattr(trace, "requested_instructions", False))
    intent_to_harm = bool(getattr(trace, "intent_to_harm", False))
    trace_intent_operational = bool(getattr(trace, "intent_operational", False))
    has_ambiguity = bool(getattr(policy_ctx, "has_ambiguity_or_dual_use", False))

    # Never use informational recovery for non-benign categories:
    # let policy_bounds preserve canonical reason_codes such as
    # domain_regulated / risk_sensitive_allowed / safe_complete_required.
    if policy_ctx.risk_category in (
        RiskCategory.SENSITIVE,
        RiskCategory.MORALLY_NUANCED,
        RiskCategory.POTENTIALLY_HARMFUL,
        RiskCategory.CLEARLY_HARMFUL,
    ):
        return None

    # BENIGN only from here on.

    if domain_regulated:
        # Benign + regulated: informational recovery must stay SAFE_COMPLETE.
        # NOTE: soft violations from the overlay critic (e.g. FIN.TAX.1, MED.DISCLAIMER.1)
        # are an additional signal that the domain's principles are relevant —
        # they reinforce the need for SAFE_COMPLETE, not undermine it.
        # Previously, has_overlay_soft_violations caused a `return None` here,
        # which fell through to the policy_bounds fallback that used pre_final_action
        # (NORMAL_COMPLETE for BENIGN), losing the regulated-domain escalation.
        d = _make_decision(
            inputs,
            final_action="SAFE_COMPLETE",
            path="DELIBERATIVE_PATH",
            reason_codes=[REASON_REGULATED_BUT_INFORMATIONAL, "safe_complete_required"],
        )
        expl = _log_final_trace(trace, d, winning_rule="informational_recovery")
        return d, expl

    # Unregulated benign informational recovery:
    # preserve the old useful behavior and keep FAST_PATH for benign.
    if (
        not has_ambiguity
        and not requested_instructions
        and not intent_to_harm
        and not trace_intent_operational
        and pre_final_action != "SAFE_COMPLETE"
    ):
        d = _make_decision(
            inputs,
            final_action="NORMAL_COMPLETE",
            path="FAST_PATH",
            reason_codes=[],
        )
        expl = _log_final_trace(trace, d, winning_rule="informational_recovery")
        return d, expl

    # Conservative fallback for benign ambiguous informational requests.
    if pre_final_action != "SAFE_COMPLETE":
        d = _make_decision(
            inputs,
            final_action="SAFE_COMPLETE",
            path="DELIBERATIVE_PATH",
            reason_codes=["safe_complete_required"],
        )
        expl = _log_final_trace(trace, d, winning_rule="informational_recovery")
        return d, expl

    return None


def decide_action(
    request: ProcessedRequest | None,
    risk_assessment: RiskEstimationProtocol,
    critic_result: CriticReportProtocol | None = None,
    sim_result: SimulationResultProtocol | None = None,
    hindsight_result: HindsightResultProtocol | None = None,
    *,
    append_pre_policy_trace: bool = True,
    total_cycles: int = 0,
    stop_reason: str = "",
    overlay_sensitive: bool = False,
    risk_thresholds: RiskThresholds | None = None,
) -> tuple[Decision, DecisionExplanation]:
    """
    Single policy-driven decision function (language-agnostic).
    Determines final_action and path from risk + optional critic/sim/hindsight only.
    No parsing of response text; no regex on language.
    Returns (Decision, DecisionExplanation) for metadata propagation.
    When append_pre_policy_trace is False (e.g. post-deliberation), only FINAL is written to trace.
    """
    request_id = (getattr(request, "request_id", "") or "").strip() or ""
    trace = DecisionTrace(request_id=request_id)
    trace.total_cycles = total_cycles
    trace.stop_reason = stop_reason
    # Stash thresholds on trace so internal _log_final_trace calls can enrich
    # why_not_* without threading the kwarg through every handler signature.
    if risk_thresholds is not None:
        trace._risk_thresholds = risk_thresholds  # type: ignore[attr-defined]

    intent_clarity = _axis_val(getattr(risk_assessment, "intent_clarity", None))
    misuse_plausibility = _axis_val(getattr(risk_assessment, "misuse_plausibility", None))
    actionability_risk = _axis_val(getattr(risk_assessment, "actionability_risk", None))

    hard_violations, triggered_principles = _extract_critic_violations(critic_result)
    semantic_signals = list(getattr(risk_assessment, "semantic_signals", []) or [])
    # Keep activated_signals coherent across PRE_POLICY, FINAL and DECISION_EXPLANATION.
    trace.activated_signals = list(semantic_signals)
    inputs = _DecisionInputs(
        intent_clarity=intent_clarity,
        misuse_plausibility=misuse_plausibility,
        actionability_risk=actionability_risk,
        triggered_principles=triggered_principles,
        hard_violations=hard_violations,
        risk_signals=semantic_signals,
    )

    _populate_trace_from_risk(trace, risk_assessment)
    _populate_trace_from_sim(trace, sim_result)

    domain = getattr(request, "get_domain", lambda: None)() if request is not None else None

    policy_ctx = _build_policy_context_pre(
        risk_assessment, domain, intent_clarity, misuse_plausibility, actionability_risk, overlay_sensitive=overlay_sensitive
    )
    pre_final_action, pre_bounds, pre_reason_codes, pre_path = _run_pre_policy_audit(
        trace,
        risk_assessment,
        domain,
        policy_ctx,
        append_pre_policy_trace=append_pre_policy_trace,
    )

    risk_score = getattr(risk_assessment, "score", 0.5)
    intent_operational = getattr(risk_assessment, "intent_operational", False)
    requested_instructions = getattr(risk_assessment, "requested_instructions", False)
    op_risk = getattr(risk_assessment, "operational_risk", OperationalRisk.NONE)

    write_final = not (append_pre_policy_trace and pre_path == "DELIBERATIVE_PATH")

    # Hard violations (early return)
    out = _handle_hard_violations(
        inputs,
        trace,
        risk_assessment,
        pre_final_action,
        risk_score,
        intent_operational,
        overlay_sensitive,
        requested_instructions,
        write_final=write_final,
    )
    if out is not None:
        d, expl = out
        return d, expl

    # Hindsight refuse
    out = _handle_hindsight_refuse(inputs, trace, hindsight_result, write_final=write_final)
    if out is not None:
        d, expl = out
        return d, expl

    # Normative REFUSE guards
    out = _handle_normative_refuse_guards(
        inputs, trace, op_risk, misuse_plausibility, actionability_risk, write_final=write_final
    )
    if out is not None:
        d, expl = out
        return d, expl

    # Epistemic escalation (simulator negativity)
    out = _handle_epistemic_escalation(inputs, trace, sim_result, actionability_risk, write_final=write_final)
    if out is not None:
        d, expl = out
        return d, expl

    # Informational recovery
    # Calcola se il critic ha trovato soft violations nell'overlay corrente.
    # Se True, _handle_informational_recovery cede il controllo alla policy bounds
    # invece di forzare NORMAL_COMPLETE, preservando i principi overlay (es. MED.DISCLAIMER.1).
    _overlay_soft_violations = bool(
        critic_result
        and any(getattr(v, "constraint_type", "") == "soft" for v in (getattr(critic_result, "violations", []) or []))
    )
    out = _handle_informational_recovery(
        inputs,
        trace,
        policy_ctx,
        pre_final_action,
        has_overlay_soft_violations=_overlay_soft_violations,
    )
    if out is not None:
        d, expl = out
        return d, expl

    # Fallback: policy bounds (single source of truth)
    decision = _make_decision(
        inputs,
        final_action=pre_final_action,
        path=pre_path,
        reason_codes=list(pre_reason_codes),
    )
    winning_rule = "fast_path" if pre_path == "FAST_PATH" else "policy_bounds_fallback"
    # When write_final is True (e.g. post-deliberation call with append_pre_policy_trace=False),
    # persist the FINAL trace; otherwise only build the explanation in memory.
    if write_final:
        explanation = _log_final_trace(trace, decision, winning_rule=winning_rule)
    else:
        explanation = _build_decision_explanation(trace, decision, winning_rule)
    return decision, explanation
