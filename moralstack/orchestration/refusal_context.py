from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RefusalContext:
    risk_category: str = ""
    risk_score: float | None = None
    policy_reason_codes: list[str] = field(default_factory=list)

    operational_risk: str = ""
    request_type: str = ""
    harm_type: str = ""
    intent_operational: bool = False
    requested_instructions: bool = False
    intent_to_harm: bool = False

    semantic_signals: list[str] = field(default_factory=list)
    rationale: str = ""

    domain: str = "general"
    refusal_redirection: str = ""

    safe_refusal_focus: str = "general_safety"
    safe_redirection_guidance: str = ""


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


def build_refusal_context(
    *,
    risk_estimation: Any,
    decision: Any,
    domain: str,
    refusal_redirection: str,
    risk_score: float | None = None,
    risk_category: str = "",
) -> RefusalContext:
    signals = list(getattr(decision, "risk_signals", None) or [])
    if not signals:
        signals = list(getattr(risk_estimation, "semantic_signals", None) or [])
    if not signals:
        signals = list(getattr(risk_estimation, "activated_signals", None) or [])

    harm_type = str(getattr(risk_estimation, "harm_type", "") or "")
    request_type = str(getattr(risk_estimation, "request_type", "") or "")
    operational_risk = _enum_value(getattr(risk_estimation, "operational_risk", ""))

    requested_instructions = bool(getattr(risk_estimation, "requested_instructions", False))
    intent_to_harm = bool(getattr(risk_estimation, "intent_to_harm", False))
    intent_operational = bool(getattr(risk_estimation, "intent_operational", False))

    focus, guidance = classify_refusal_focus(
        semantic_signals=signals,
        harm_type=harm_type,
        request_type=request_type,
        operational_risk=operational_risk,
        requested_instructions=requested_instructions,
        intent_to_harm=intent_to_harm,
        intent_operational=intent_operational,
    )

    return RefusalContext(
        risk_category=str(risk_category or ""),
        risk_score=risk_score,
        policy_reason_codes=[str(x) for x in list(getattr(decision, "reason_codes", None) or [])],
        operational_risk=operational_risk,
        request_type=request_type,
        harm_type=harm_type,
        intent_operational=intent_operational,
        requested_instructions=requested_instructions,
        intent_to_harm=intent_to_harm,
        semantic_signals=[str(s) for s in signals],
        rationale=str(getattr(risk_estimation, "rationale", "") or ""),
        domain=domain or "general",
        refusal_redirection=refusal_redirection or "",
        safe_refusal_focus=focus,
        safe_redirection_guidance=guidance,
    )


_REPUTATIONAL_FOCUS = (
    "reputational_harm",
    "Redirect toward fair competitive practices, honest marketing, legitimate "
    "differentiation, transparent communication, and constructive ways to "
    "address rivals or critics without harming third parties' reputation through "
    "false, deceptive, or manipulative means.",
)

_CHILD_EXPLOITATION_FOCUS = (
    "child_safety_minor_protection",
    "Redirect toward child safety practices, caregiver oversight, lawful reporting pathways for suspected harm, "
    "and age-appropriate resources; refuse any facilitation of contact with or exploitation of minors.",
)

_CYBER_FOCUS = (
    "cyber_abuse",
    "Redirect toward defensive cybersecurity, secure coding, authorized testing "
    "environments, incident response, and account/system protection.",
)

_PRIVACY_FOCUS = (
    "privacy_abuse",
    "Redirect toward privacy protection, consent, digital safety, lawful "
    "reporting channels, and non-invasive alternatives.",
)


def classify_refusal_focus(
    *,
    semantic_signals: list[str],
    harm_type: str,
    request_type: str,
    operational_risk: str,
    requested_instructions: bool,
    intent_to_harm: bool,
    intent_operational: bool,
) -> tuple[str, str]:
    """Pick a refusal focus + redirection guidance.

    Priority order (top wins, halts further checks):
      1. Strong topic signals where the topic itself is decisive (self-harm,
         minor exploitation/contact, weapons/physical) — these MUST NOT be
         reinterpreted via harm_type except where harm_type reinforces the same
         substantive focus.
      2. harm_type from the intent estimator: reflects the request's underlying
         nature (reputational, financial, ...) and is the most reliable grounding
         when the topic alone is ambiguous.
      3. Remaining signal-based focuses, with anti-leak guards: when harm_type
         disagrees with a signal that the LLM signal-detector commonly
         over-triggers (notably Q9_cyber_malware on "online maneuvers"),
         deprioritize the signal in favor of the harm_type-aligned focus.
      4. Coarse fallbacks driven by request_type / operational_risk.

    The order corresponds to the report-recommended priority:
    hard topical signals > harm_type > residual signals > generic fallback.
    """
    signals = set(semantic_signals or [])
    ht = (harm_type or "").lower().strip()

    # ── Priority 1: unambiguous topical signals ─────────────────────────────
    if "Q8:self_harm_suicide" in signals:
        return (
            "self_harm_crisis",
            "Redirect toward immediate support, contacting trusted people, crisis resources, or emergency services if "
            "there is imminent danger.",
        )

    if "Q17:minor_exploitation" in signals or ht == "child_exploitation":
        return _CHILD_EXPLOITATION_FOCUS

    if "Q10:weapons_explosives_toxins" in signals or "Q5:physical_harm" in signals:
        return (
            "weapon_or_physical_harm",
            "Redirect toward personal safety, de-escalation, avoiding harm, and seeking help from trusted people or "
            "emergency services if there is immediate danger.",
        )

    # ── Priority 2: harm_type-driven (semantic grounding) ───────────────────
    # When the intent estimator labels the request as reputational, that
    # supersedes any cyber/privacy signal that may have over-triggered on
    # generic online vocabulary.
    if ht == "reputational":
        return _REPUTATIONAL_FOCUS

    if ht == "financial" and "Q4:fraud" not in signals:
        return (
            "financial_exploitation",
            "Redirect toward lawful financial conduct, transparent dealings, "
            "consumer protection, compliance, and legitimate support channels.",
        )

    # ── Priority 3: reputational-cluster signals ────────────────────────────
    # Even if harm_type was not set to reputational by the intent estimator,
    # an explicit Q14/Q15/Q16 topic detection should ground the refusal here
    # rather than fall through to cyber/privacy.
    if (
        "Q14:reputational_harm" in signals
        or "Q15:deceptive_online_campaign" in signals
        or "Q16:harassment_smear_campaign" in signals
    ):
        return _REPUTATIONAL_FOCUS

    # ── Priority 4: remaining harm-signal focuses with anti-leak guards ─────
    if "Q9:cyber_malware" in signals:
        # Anti-leak: Q9 is the signal most often over-triggered by the
        # signal-detector LLM on generic "online" vocabulary. If harm_type
        # points elsewhere (reputational already handled above; emotional
        # relational implies interpersonal, not cyber), do not surface a
        # cyber refusal text.
        if ht in ("emotional_relational",):
            return (
                "clear_harm_general",
                "Redirect toward safe, lawful, non-operational alternatives that reduce harm and avoid enabling the "
                "harmful action.",
            )
        return _CYBER_FOCUS

    if "Q11:privacy_doxxing_stalking" in signals:
        # If harm_type says reputational/emotional_relational, the privacy
        # signal is likely a coarse over-match on "online" — defer to a
        # more specific focus.
        if ht in ("reputational",):
            return _REPUTATIONAL_FOCUS
        return _PRIVACY_FOCUS

    if "Q12:medical_harmful" in signals:
        return (
            "medical_harm",
            "Redirect toward licensed medical professionals, urgent care, emergency services, or official public "
            "health resources.",
        )

    if "Q4:fraud" in signals:
        return (
            "fraud_or_financial_abuse",
            "Redirect toward lawful financial literacy, fraud prevention, consumer protection, compliance, "
            "and legitimate support channels.",
        )

    # ── Priority 5: coarse fallbacks ────────────────────────────────────────
    if (
        request_type == "clear_harm"
        or operational_risk == "HIGH"
        or requested_instructions
        or intent_to_harm
        or intent_operational
    ):
        return (
            "clear_harm_general",
            "Redirect toward safe, lawful, non-operational alternatives that reduce harm and avoid enabling the "
            "harmful action.",
        )

    return (
        "general_safety",
        "Redirect toward safe, lawful, constructive alternatives without enabling harmful behavior.",
    )
