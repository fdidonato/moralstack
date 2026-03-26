"""
Calibration, parsing e mapping score/categoria per il risk estimator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from moralstack.utils.json_utils import extract_json

from .action import coerce_risk_policy_action
from .categories import (
    ActionabilityRisk,
    DomainSensitivity,
    IntentClarity,
    MisusePlausibility,
    OperationalRisk,
    RiskCategory,
    RiskPolicyAction,
)
from .parse_result import RiskParseResult

_CALIBRATION_LOG = logging.getLogger(__name__)

# Request types that indicate benign/non-operational framing.
# When the intent estimator reports one of these types AND no harm signals,
# the calibration guard caps the operational estimator's risk outputs.
_CALIBRATION_GUARD_REQUEST_TYPES = frozenset(
    {
        "factual_query",
        "sensitive_topic",
        "ethical_dilemma",
        "support_request",
        "crisis_support",
    }
)


def _is_yes(value: Any) -> bool:
    """Return True if value indicates yes (yes/true/1/sì/si)."""
    if isinstance(value, bool):
        return value
    return str(value).lower().strip() in ("yes", "true", "1", "sì", "si")


def _parse_core_fields(data: dict[str, Any]) -> tuple[float, float, str, str, str]:
    """Parse score, confidence, rationale, request_type, harm_type.
    Returns (score, confidence, rationale, request_type, harm_type)."""
    try:
        score = float(data.get("risk_score", 0.5))
    except (ValueError, TypeError):
        score = 0.5
    score = max(0.0, min(1.0, score))
    try:
        confidence = float(data.get("confidence", 0.7))
    except (ValueError, TypeError):
        confidence = 0.7
    confidence = max(0.0, min(1.0, confidence))
    rationale = str(data.get("rationale", ""))
    request_type = str(data.get("request_type", "")).lower().strip()
    harm_type = str(data.get("harm_type", "")).lower().strip()
    return score, confidence, rationale, request_type, harm_type


def _parse_enum_dimensions(data: dict[str, Any]) -> tuple[DomainSensitivity, OperationalRisk]:
    """Parse domain_sensitivity and operational_risk. Returns (domain_sensitivity, operational_risk)."""
    ds_str = str(data.get("domain_sensitivity", "LOW")).upper().strip()
    try:
        domain_sensitivity = DomainSensitivity(ds_str)
    except ValueError:
        domain_sensitivity = DomainSensitivity.LOW
    or_str = str(data.get("operational_risk", "NONE")).upper().strip()
    try:
        operational_risk = OperationalRisk(or_str)
    except ValueError:
        operational_risk = OperationalRisk.NONE
    return domain_sensitivity, operational_risk


@dataclass(frozen=True)
class _VerificationQuestions:
    """Parsed Q1-Q12 and intent flags plus derived counts."""

    q1_confidential: bool
    q2_unauthorized: bool
    q3_breach: bool
    q4_fraud: bool
    q5_physical: bool
    q6_illegal_items: bool
    q7_only_emotional: bool
    q8_self_harm: bool
    q9_cyber_malware: bool
    q10_weapons: bool
    q11_privacy_doxxing: bool
    q12_medical_harmful: bool
    requested_instructions: bool
    intent_to_harm: bool
    intent_operational: bool
    harmful_count: int
    priority_harm_count: int


def _parse_verification_questions(data: dict[str, Any]) -> _VerificationQuestions:
    """Parse Q1-Q12 and intent flags; compute harmful_count and priority_harm_count."""
    q1_confidential = _is_yes(data.get("q1_confidential", "no"))
    q2_unauthorized = _is_yes(data.get("q2_unauthorized", "no"))
    q3_breach = _is_yes(data.get("q3_breach", "no"))
    q4_fraud = _is_yes(data.get("q4_fraud", "no"))
    q5_physical = _is_yes(data.get("q5_physical", "no"))
    q6_illegal_items = _is_yes(data.get("q6_illegal_items", "no"))
    q7_only_emotional = _is_yes(data.get("q7_only_emotional", "no"))
    q8_self_harm = _is_yes(data.get("q8_self_harm_suicide", "no"))
    q9_cyber_malware = _is_yes(data.get("q9_cyber_malware", "no"))
    q10_weapons = _is_yes(data.get("q10_weapons_explosives_toxins", "no"))
    q11_privacy_doxxing = _is_yes(data.get("q11_privacy_doxxing_stalking", "no"))
    q12_medical_harmful = _is_yes(data.get("q12_medical_harmful", "no"))
    priority_harmful = [q8_self_harm, q9_cyber_malware, q10_weapons, q11_privacy_doxxing, q12_medical_harmful]
    harmful_questions = [q1_confidential, q2_unauthorized, q3_breach, q4_fraud, q5_physical, q6_illegal_items]
    harmful_questions.extend(priority_harmful)
    harmful_count = sum(harmful_questions)
    priority_harm_count = sum(priority_harmful)
    requested_instructions = _is_yes(data.get("requested_instructions", "no"))
    intent_to_harm = _is_yes(data.get("intent_to_harm", "no"))
    intent_operational = _is_yes(data.get("intent_operational", "no"))
    return _VerificationQuestions(
        q1_confidential=q1_confidential,
        q2_unauthorized=q2_unauthorized,
        q3_breach=q3_breach,
        q4_fraud=q4_fraud,
        q5_physical=q5_physical,
        q6_illegal_items=q6_illegal_items,
        q7_only_emotional=q7_only_emotional,
        q8_self_harm=q8_self_harm,
        q9_cyber_malware=q9_cyber_malware,
        q10_weapons=q10_weapons,
        q11_privacy_doxxing=q11_privacy_doxxing,
        q12_medical_harmful=q12_medical_harmful,
        requested_instructions=requested_instructions,
        intent_to_harm=intent_to_harm,
        intent_operational=intent_operational,
        harmful_count=harmful_count,
        priority_harm_count=priority_harm_count,
    )


def _calibrate_harm_signals_present(
    q: _VerificationQuestions,
    score: float,
    request_type: str,
    harm_type: str,
    operational_risk: OperationalRisk,
) -> tuple[float, RiskCategory, list[str], str]:
    """Caso: harmful_count > 0. Gestisce CLEARLY_HARMFUL, POTENTIALLY_HARMFUL, defensive override."""
    signals: list[str] = []
    category: RiskCategory = RiskCategory.SENSITIVE

    # All TRUE signals collected independently; harm_type assigned by priority
    if q.q5_physical:
        signals.append("Q5:physical_harm")
    if q.q8_self_harm:
        signals.append("Q8:self_harm_suicide")
    if q.q9_cyber_malware:
        signals.append("Q9:cyber_malware")
    if q.q10_weapons:
        signals.append("Q10:weapons_explosives_toxins")
    if q.q11_privacy_doxxing:
        signals.append("Q11:privacy_doxxing_stalking")
    if q.q12_medical_harmful:
        signals.append("Q12:medical_harmful")
    if q.q4_fraud:
        signals.append("Q4:fraud")

    # harm_type: assign by priority (weapons > physical > financial > other)
    if q.q10_weapons:
        harm_type = "weapons"
    elif q.q5_physical:
        harm_type = "physical"
    elif q.q4_fraud:
        harm_type = "financial"
    elif q.q8_self_harm or q.q9_cyber_malware or q.q11_privacy_doxxing or q.q12_medical_harmful:
        harm_type = "illegal"
    else:
        harm_type = "illegal"
        if q.q1_confidential:
            signals.append("Q1:confidential_data")
        if q.q2_unauthorized:
            signals.append("Q2:unauthorized_access")
        if q.q3_breach:
            signals.append("Q3:breach_of_contract")
        if q.q6_illegal_items:
            signals.append("Q6:illegal_items")

    priority_operational_signal = (
        q.requested_instructions or q.intent_to_harm or operational_risk == OperationalRisk.HIGH or q.intent_operational
    )
    is_operational = (
        q.intent_operational
        or q.requested_instructions
        or q.intent_to_harm
        or operational_risk in (OperationalRisk.LOW, OperationalRisk.HIGH)
    )

    # ── DEFENSIVE FRAMING OVERRIDE (domain-agnostic) ──────────────────────────
    # If the LLM reported a harm signal (q9, q12, etc.) BUT also explicitly said:
    #   - requested_instructions=False  AND
    #   - intent_to_harm=False          AND
    #   - intent_operational=False      AND
    #   - operational_risk=NONE or LOW
    # then the request is DEFENSIVE/EDUCATIONAL, not offensive.
    # Cap the score and set SENSITIVE category regardless of request_type.
    # Defensive override must NOT apply to weapons/explosives/toxins style content
    # WHEN there is operational intent. q10_weapons alone (e.g. fiction mentioning
    # toxins, conceptual security discussions) should NOT block the override if
    # all three intent signals (intent_operational, requested_instructions,
    # intent_to_harm) are explicitly False — this means the LLM has confirmed
    # the request is non-operational despite the topic.
    _has_any_intent = q.requested_instructions or q.intent_to_harm or q.intent_operational
    _defensive_override_blocked = (q.q10_weapons and _has_any_intent) or request_type == "clear_harm"

    _is_defensive = (
        q.harmful_count >= 1
        and not _defensive_override_blocked
        and not q.requested_instructions
        and not q.intent_to_harm
        and not q.intent_operational
        and operational_risk in (OperationalRisk.NONE, OperationalRisk.LOW)
    )
    if _is_defensive:
        category = RiskCategory.SENSITIVE
        score = min(score, 0.30)
        # Re-align harm_type for defensive queries
        if harm_type in ("illegal", "physical") and not q.intent_to_harm:
            harm_type = "none"
            # Continue to signal assignment below (don't return early)
            # Fall through to existing signal assignment for diagnostics

    # ── HARM ESCALATION (only when NOT defensive) ─────────────────────────────
    # If _is_defensive fired above, the request has been confirmed as
    # non-operational by all 3 intent signals + low op_risk. Do NOT
    # re-escalate to CLEARLY_HARMFUL based on topic signals alone.
    if not _is_defensive and (
        q.harmful_count >= 3
        or q.q5_physical
        or (q.priority_harm_count >= 1 and priority_operational_signal)
        or score >= 0.85
        or request_type == "clear_harm"
    ):
        category = RiskCategory.CLEARLY_HARMFUL
        score = max(score, 0.85)
    elif not _is_defensive:
        category = RiskCategory.POTENTIALLY_HARMFUL
        if is_operational:
            score = max(score, 0.5)
            if q.harmful_count >= 2:
                score = max(score, 0.70)
        else:
            score = min(max(score, 0.25), 0.30)

    return score, category, signals, harm_type


def _calibrate_emotional_only(
    request_type: str,
    score: float,
    harm_type: str,
) -> tuple[float, RiskCategory, list[str], str]:
    """Caso: solo q7_only_emotional=True."""
    signals: list[str] = []
    category: RiskCategory = RiskCategory.SENSITIVE

    if request_type == "clear_harm":
        category = RiskCategory.CLEARLY_HARMFUL
        signals = [f"request_type:{request_type}", "harm_type:emotional_relational"]
        score = max(score, 0.85)
    elif request_type == "potential_harm" or score >= 0.55:
        category = RiskCategory.POTENTIALLY_HARMFUL
        signals = [f"request_type:{request_type}", "harm_type:emotional_relational"]
        if score < 0.55:
            score = 0.60
    elif request_type in ("factual_query", "creative_request"):
        category = RiskCategory.BENIGN
        signals = [f"request_type:{request_type}", "corrected_from_q7"]
        if score > 0.25:
            score = 0.15
    elif request_type == "sensitive_topic":
        category = RiskCategory.SENSITIVE
        signals = [f"request_type:{request_type}", "harm_type:emotional_relational"]
        if score > 0.55:
            score = 0.45
        elif score < 0.30:
            score = 0.35
    else:
        category = RiskCategory.MORALLY_NUANCED
        harm_type = "emotional_relational"
        signals.append("Q7:ethical_dilemma")
        if score < 0.30:
            score = 0.35
        elif score > 0.50:
            score = 0.45

    return score, category, signals, harm_type


def _calibrate_no_harm(
    request_type: str,
    score: float,
    harm_type: str,
) -> tuple[float, RiskCategory, list[str], str]:
    """Caso: nessun segnale di harm. Routing basato su request_type/harm_type."""
    signals = [f"request_type:{request_type}", f"harm_type:{harm_type}"]
    category: RiskCategory = RiskCategory.SENSITIVE

    if request_type == "ethical_dilemma":
        category = RiskCategory.MORALLY_NUANCED
        if score < 0.30:
            score = 0.35
        elif score > 0.50:
            score = 0.45
    elif request_type in ("factual_query", "creative_request"):
        category = RiskCategory.BENIGN
        if score > 0.25:
            score = 0.15
    elif request_type == "sensitive_topic":
        category = RiskCategory.SENSITIVE
        if score > 0.55:
            score = 0.45
        elif score < 0.30:
            score = 0.35
    elif request_type == "potential_harm":
        if harm_type in ("none", "emotional_relational"):
            category = RiskCategory.MORALLY_NUANCED
            score = min(score, 0.45)
        elif harm_type == "reputational":
            category = RiskCategory.SENSITIVE
            score = min(score, 0.50)
        else:
            category = RiskCategory.POTENTIALLY_HARMFUL
            if score < 0.55:
                score = 0.60
    elif request_type == "clear_harm":
        if harm_type == "none":
            category = RiskCategory.MORALLY_NUANCED
            score = 0.45
        elif harm_type == "emotional_relational":
            category = RiskCategory.POTENTIALLY_HARMFUL
            score = max(score, 0.60)
        elif harm_type == "reputational":
            category = RiskCategory.POTENTIALLY_HARMFUL
            score = max(score, 0.65)
        else:
            category = RiskCategory.CLEARLY_HARMFUL
            if score < 0.85:
                score = 0.85
    else:
        if harm_type in ("physical", "illegal"):
            category = RiskCategory.POTENTIALLY_HARMFUL
            score = max(score, 0.60)
        elif harm_type in ("none", "emotional_relational"):
            category = RiskCategory.BENIGN if harm_type == "none" else RiskCategory.MORALLY_NUANCED
            score = min(score, 0.40)
        else:
            category = RiskCategory.SENSITIVE
            score = min(max(score, 0.25), 0.50)

    return score, category, signals, harm_type


def _apply_calibration(
    data: dict[str, Any],
    score: float,
    request_type: str,
    harm_type: str,
    operational_risk: OperationalRisk,
    questions: _VerificationQuestions,
) -> tuple[float, RiskCategory, list[str], str]:
    """
    Apply calibration logic: category, signals, score, harm_type.
    Dispatches to focused helpers and applies legacy fallback.
    Returns (score, category, signals, harm_type).
    """
    q = questions

    if q.harmful_count > 0:
        score, category, signals, harm_type = _calibrate_harm_signals_present(
            q, score, request_type, harm_type, operational_risk
        )
    elif q.q7_only_emotional:
        score, category, signals, harm_type = _calibrate_emotional_only(request_type, score, harm_type)
    else:
        score, category, signals, harm_type = _calibrate_no_harm(request_type, score, harm_type)

    # Legacy fallback when request_type and harm_type are empty
    if not request_type and not harm_type:
        category_str = str(data.get("category", "sensitive")).lower()
        category_map = {
            "benign": RiskCategory.BENIGN,
            "morally_nuanced": RiskCategory.MORALLY_NUANCED,
            "sensitive": RiskCategory.SENSITIVE,
            "potentially_harmful": RiskCategory.POTENTIALLY_HARMFUL,
            "clearly_harmful": RiskCategory.CLEARLY_HARMFUL,
        }
        category = category_map.get(category_str, RiskCategory.SENSITIVE)
        signals_raw = data.get("signals", [])
        if not isinstance(signals_raw, list):
            signals = [str(signals_raw)] if signals_raw else []
        else:
            signals = [str(s) for s in signals_raw]

    return score, category, signals, harm_type


def _parse_action(
    data: dict[str, Any],
    category: RiskCategory,
    score: float,
    operational_risk: OperationalRisk,
    questions: _VerificationQuestions,
) -> tuple[RiskPolicyAction, RiskCategory, float]:
    """
    Parse risk_policy_action and apply non-operational clamp.
    Returns (risk_policy_action, category, score).
    """
    action_raw = str(data.get("risk_policy_action", "")).strip().upper()
    valid_actions = (
        RiskPolicyAction.ALLOW,
        RiskPolicyAction.ALLOW_WITH_CAVEAT,
        RiskPolicyAction.DELIBERATE,
        RiskPolicyAction.DENY,
    )
    if action_raw in valid_actions:
        risk_policy_action = coerce_risk_policy_action(action_raw)
    else:
        if category == RiskCategory.CLEARLY_HARMFUL or score >= 0.85:
            risk_policy_action = RiskPolicyAction.DENY
        elif category == RiskCategory.POTENTIALLY_HARMFUL or score >= 0.6:
            risk_policy_action = RiskPolicyAction.DELIBERATE
        elif category == RiskCategory.SENSITIVE or score >= 0.35:
            risk_policy_action = RiskPolicyAction.ALLOW_WITH_CAVEAT
        else:
            risk_policy_action = RiskPolicyAction.ALLOW

    if operational_risk == OperationalRisk.HIGH:
        risk_policy_action = RiskPolicyAction.DENY
    elif operational_risk == OperationalRisk.NONE:
        risk_policy_action = RiskPolicyAction.ALLOW
    elif operational_risk == OperationalRisk.LOW and risk_policy_action != RiskPolicyAction.DENY:
        risk_policy_action = RiskPolicyAction.DELIBERATE

    # if op_risk is HIGH but ALL intent signals are False, the LLM is
    # self-contradicting. Apply a conservative downgrade: DENY→DELIBERATE,
    # CLEARLY_HARMFUL→SENSITIVE, score capped at 0.55.
    # This does NOT block REFUSE — it forces deliberation instead of fast-path.
    # Weapons/explosives: dual-use by inversion makes intent_operational=False
    # unreliable — do NOT apply intent_contradiction downgrade.
    _is_weapons_domain = questions.q10_weapons or (questions.q5_physical and questions.q6_illegal_items)
    _is_priority_harm_domain = _is_weapons_domain

    _intent_contradiction = (
        operational_risk == OperationalRisk.HIGH
        and not questions.intent_operational
        and not questions.requested_instructions
        and not questions.intent_to_harm
        and not _is_priority_harm_domain
    )
    if _intent_contradiction:
        if category == RiskCategory.CLEARLY_HARMFUL:
            category = RiskCategory.SENSITIVE
        score = min(score, 0.55)
        if risk_policy_action == RiskPolicyAction.DENY:
            risk_policy_action = RiskPolicyAction.DELIBERATE

    # Non-operational clamp: op_risk=NONE + all 3 intent flags False.
    # Four independent signals confirm the request is non-operational.
    # No _is_priority_harm_domain guard here: if all 4 signals agree
    # "not operational", the topic alone should not force CLEARLY_HARMFUL.
    if (
        operational_risk == OperationalRisk.NONE
        and not questions.intent_operational
        and not questions.requested_instructions
        and not questions.intent_to_harm
    ):
        if category == RiskCategory.CLEARLY_HARMFUL:
            category = RiskCategory.SENSITIVE
        score = min(score, 0.30)
        if risk_policy_action == RiskPolicyAction.DENY:
            risk_policy_action = RiskPolicyAction.DELIBERATE

    return risk_policy_action, category, score


def _parse_semantic_signals(
    data: dict[str, Any],
) -> tuple[IntentClarity, MisusePlausibility, ActionabilityRisk]:
    """Parse intent_clarity, misuse_plausibility, actionability_risk."""
    ic_str = str(data.get("intent_clarity", "CLEAR")).upper().strip()
    if ic_str in ("CLEAR", "HIGH"):
        intent_clarity = IntentClarity.HIGH
    elif ic_str in ("AMBIGUOUS", "LOW"):
        intent_clarity = IntentClarity.LOW
    elif ic_str == "MEDIUM":
        intent_clarity = IntentClarity.MEDIUM
    else:
        try:
            intent_clarity = IntentClarity(ic_str)
        except ValueError:
            intent_clarity = IntentClarity.HIGH
    mp_str = str(data.get("misuse_plausibility", "LOW")).upper().strip()
    try:
        misuse_plausibility = MisusePlausibility(mp_str)
    except ValueError:
        misuse_plausibility = MisusePlausibility.LOW
    ar_str = str(data.get("actionability_risk", "LOW")).upper().strip()
    try:
        actionability_risk = ActionabilityRisk(ar_str)
    except ValueError:
        actionability_risk = ActionabilityRisk.LOW
    return intent_clarity, misuse_plausibility, actionability_risk


def _parse_detected_language(data: dict[str, Any]) -> str:
    """Parse detected_language (ISO 639-1), max 10 chars."""
    return str(data.get("detected_language", "")).strip().lower()[:10]


def parse_risk_dict(data: dict[str, Any]) -> RiskParseResult:
    """
    Parse an already-decoded dict into a structured RiskParseResult.

    Accepts a dict produced by extract_json() or by merge_mini_estimator_results().
    Applies the full calibration pipeline (same logic as parse_risk_response).

    Backward compatibility: unknown keys are IGNORED (via .get()).
    Must NOT raise on new/unknown keys; must NOT default to FAIL_SAFE.

    Returns:
        RiskParseResult with all parsed and calibrated fields.
    """
    score, confidence, rationale, request_type, harm_type = _parse_core_fields(data)
    domain_sensitivity, operational_risk = _parse_enum_dimensions(data)
    questions = _parse_verification_questions(data)
    score, category, signals, harm_type = _apply_calibration(
        data, score, request_type, harm_type, operational_risk, questions
    )
    risk_policy_action, category, score = _parse_action(data, category, score, operational_risk, questions)
    intent_clarity, misuse_plausibility, actionability_risk = _parse_semantic_signals(data)
    self_harm_language = questions.q8_self_harm
    detected_language = _parse_detected_language(data)
    return RiskParseResult(
        score=score,
        confidence=confidence,
        category=category,
        signals=signals,
        rationale=rationale,
        risk_policy_action=risk_policy_action,
        domain_sensitivity=domain_sensitivity,
        operational_risk=operational_risk,
        intent_clarity=intent_clarity,
        misuse_plausibility=misuse_plausibility,
        actionability_risk=actionability_risk,
        harm_type=harm_type,
        self_harm_language=self_harm_language,
        requested_instructions=questions.requested_instructions,
        intent_to_harm=questions.intent_to_harm,
        request_type=request_type,
        intent_operational=questions.intent_operational,
        detected_language=detected_language,
    )


def _apply_calibration_guard(merged: dict[str, Any]) -> dict[str, Any]:
    """
    Calibration guard: when the intent estimator confirms a benign/non-operational
    request type with no harm intent and no requested instructions, cap risk metrics
    produced by the operational estimator that may have over-triggered on topic signals.

    Trigger conditions (ALL must be true):
      - request_type ∈ {factual_query, sensitive_topic, ethical_dilemma,
                        support_request, crisis_support}
      - intent_to_harm = no
      - requested_instructions = no

    Caps applied when triggered:
      - risk_score  → max 0.45
      - operational_risk HIGH → LOW
      - risk_policy_action DENY → DELIBERATE

    All changes are logged at WARNING level for trace visibility.
    The guard appends a [calibration_guard] tag to the rationale field and
    stores metadata in _calibration_guard_* keys for downstream consumers.
    """
    request_type = str(merged.get("request_type", "")).lower().strip()
    intent_to_harm = merged.get("intent_to_harm", "no")
    requested_instructions = merged.get("requested_instructions", "no")

    # Guard only applies to benign request types with no harm signals
    if request_type not in _CALIBRATION_GUARD_REQUEST_TYPES or _is_yes(intent_to_harm) or _is_yes(requested_instructions):
        return merged

    guard_notes: list[str] = []

    raw_score = merged.get("risk_score")
    numeric_score: float | None = None
    if raw_score is not None:
        try:
            numeric_score = float(raw_score)
        except (ValueError, TypeError):
            pass

    # Cap operational_risk: HIGH → LOW
    op_risk = str(merged.get("operational_risk", "")).upper().strip()
    if op_risk == "HIGH":
        merged["operational_risk"] = "LOW"
        guard_notes.append("operational_risk HIGH→LOW")

    # Cap risk_score — two independent rules evaluated against the original value:
    #   1. op_risk NONE/LOW with score >= 0.55 → inconsistency guard
    #   2. generic cap: score > 0.45
    if numeric_score is not None:
        if op_risk in ("NONE", "LOW") and numeric_score >= 0.55:
            merged["risk_score"] = 0.45
            guard_notes.append(f"risk_score {numeric_score:.2f}→0.45" f" (op_risk {op_risk} inconsistency)")
        elif numeric_score > 0.45:
            merged["risk_score"] = 0.45
            guard_notes.append(f"risk_score {numeric_score:.2f}→0.45")

    # Cap risk_policy_action: DENY → DELIBERATE
    action_raw = str(merged.get("risk_policy_action", "")).upper().strip()
    if action_raw == "DENY":
        merged["risk_policy_action"] = "DELIBERATE"
        guard_notes.append("risk_policy_action DENY→DELIBERATE")

    if not guard_notes:
        # No actual capping needed — metrics were already within bounds
        return merged

    guard_summary = "; ".join(guard_notes)
    _CALIBRATION_LOG.warning(
        "CALIBRATION_GUARD applied " "[request_type=%s intent_to_harm=%s requested_instructions=%s]: %s",
        request_type,
        intent_to_harm,
        requested_instructions,
        guard_summary,
    )

    # Append guard note to rationale for downstream visibility
    existing_rationale = str(merged.get("rationale", "")).strip()
    guard_tag = f"[calibration_guard] {guard_summary}"
    merged["rationale"] = f"{existing_rationale} | {guard_tag}" if existing_rationale else guard_tag

    # Store guard metadata for trace/report surfacing
    merged["_calibration_guard_applied"] = True
    merged["_calibration_guard_notes"] = guard_notes
    merged["_calibration_guard_request_type"] = request_type

    return merged


def merge_mini_estimator_results(
    intent_data: dict[str, Any],
    signal_data: dict[str, Any],
    operational_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Unifica i 3 JSON dict dei mini-estimator in un unico dict con priorità per fonte.

    - LLM 1 (intent_data): authoritative per detected_language, intent_*, request_type,
      harm_type, intent_clarity, rationale (intent framing)
    - LLM 2 (signal_data): authoritative per q1..q12, domain_sensitivity
    - LLM 3 (operational_data): authoritative per operational_risk, risk_score, confidence,
      misuse_plausibility, actionability_risk, risk_policy_action, rationale (risk assessment)

    rationale: merged from LLM 1 (intent) + LLM 3 (operational) with source labels.
    If only one source provides a rationale, it is used as-is without labels.
    """
    merged: dict[str, Any] = {}

    # LLM 3: operational fields
    for key in (
        "operational_risk",
        "risk_score",
        "confidence",
        "misuse_plausibility",
        "actionability_risk",
        "risk_policy_action",
    ):
        if key in operational_data:
            merged[key] = operational_data[key]

    # LLM 2: harm signal fields and domain sensitivity
    for key in (
        "q1_confidential",
        "q2_unauthorized",
        "q3_breach",
        "q4_fraud",
        "q5_physical",
        "q6_illegal_items",
        "q7_only_emotional",
        "q8_self_harm_suicide",
        "q9_cyber_malware",
        "q10_weapons_explosives_toxins",
        "q11_privacy_doxxing_stalking",
        "q12_medical_harmful",
        "domain_sensitivity",
    ):
        if key in signal_data:
            merged[key] = signal_data[key]

    # LLM 1: intent and language fields (authoritative, override last)
    for key in (
        "detected_language",
        "intent_to_harm",
        "requested_instructions",
        "intent_operational",
        "request_type",
        "harm_type",
        "intent_clarity",
    ):
        if key in intent_data:
            merged[key] = intent_data[key]

    # ── rationale: merge from intent (LLM 1) + operational (LLM 3) ────────
    # Both mini-estimators now produce a rationale field. Combine them
    # with source labels so that downstream consumers (safe_refusal_generator,
    # reports, debug) see both the intent analysis and the risk assessment.
    intent_rationale = str(intent_data.get("rationale", "")).strip()
    op_rationale = str(operational_data.get("rationale", "")).strip()
    if intent_rationale and op_rationale:
        merged["rationale"] = f"[intent] {intent_rationale} | [op_risk] {op_rationale}"
    elif intent_rationale:
        merged["rationale"] = intent_rationale
    elif op_rationale:
        merged["rationale"] = op_rationale
    # else: no rationale from either source → key absent → _parse_core_fields defaults to ""

    # ── Calibration guard: cap metrics when intent signals benign framing ──
    merged = _apply_calibration_guard(merged)

    return merged


def parse_risk_response(text: str) -> RiskParseResult:
    """
    Parse the risk estimator LLM response into a structured result.

    Uses request_type, harm_type, Q1-Q12 and language-agnostic signals
    (intent_clarity, misuse_plausibility, actionability_risk, intent_operational) for routing.

    Backward compatibility: unknown JSON keys are IGNORED (via .get()).
    Must NOT raise on new/unknown keys; must NOT default to FAIL_SAFE.

    Returns:
        RiskParseResult with all parsed and calibrated fields.
    """
    return parse_risk_dict(extract_json(text))
