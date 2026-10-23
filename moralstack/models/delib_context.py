"""
DelibContext - Compact, shared representation of the deliberative context.

Used to reduce tokens and latency: built once per cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DelibContext:
    """
    Shared context for deliberation. Derived once per cycle.

    Attributes:
        request_id: Request identifier
        user_prompt: Original user prompt
        detected_language: Detected language (ISO 639-1) if available
        domain_overlay: Active overlay domain (e.g. financial, health)
        risk_score: Risk score [0, 1]
        risk_category: Risk category (BENIGN, SENSITIVE, etc.)
        operational_risk: NONE | LOW | MEDIUM | HIGH
        intent_operational: True if operational intent (how-to, steps)
        actionability_risk: LOW | MEDIUM | HIGH
        draft_id: Draft version identifier (e.g. cycle number)
        draft_text_full: Full draft text (always available)
        safety_caveats_present: True if the draft contains explicit caveats
        citations_or_disclaimer_present: True if disclaimers/citations are present
        change_log: For cycle>1, list of changes relative to the previous draft
    """

    request_id: str = ""
    user_prompt: str = ""
    detected_language: str = ""
    domain_overlay: str = ""
    risk_score: float = 0.5
    risk_category: str = ""
    operational_risk: str = ""
    intent_operational: bool = False
    actionability_risk: str = ""
    risk_policy_action: str = ""
    harm_type: str = ""
    misuse_plausibility: str = ""
    intent_to_harm: bool = False
    requested_instructions: bool = False
    draft_id: str = ""
    draft_text_full: str = ""
    safety_caveats_present: bool = False
    citations_or_disclaimer_present: bool = False
    change_log: list[str] = field(default_factory=list)
    critic_decision: str = ""
    critic_violated_hard: bool = False
    critic_violations_summary: str = ""
    simulator_domain_guidance: str = ""
    conversation_history_snippet: str = ""
    developer_contract_text: str = ""
    # Tier-1 enumerated output contract (e.g. answer exactly 'TRUE'/'FALSE').
    # Derived deterministically by the critic; used to suppress SOFT-only
    # revisions that could only flip the selected option.
    output_is_enumerated: bool = False
    output_enumerated_options: tuple[str, ...] = ()

    def get_risk_signals_str(self) -> str:
        """Compact string of risk signals for prompts."""
        parts: list[str] = []
        if self.risk_score >= 0:
            parts.append(f"risk_score={self.risk_score:.2f}")
        if self.risk_category:
            parts.append(f"risk_category={self.risk_category}")
        if self.operational_risk:
            parts.append(f"operational_risk={self.operational_risk}")
        if self.actionability_risk:
            parts.append(f"actionability_risk={self.actionability_risk}")
        if self.intent_operational:
            parts.append("intent_operational=true")
        if self.risk_policy_action:
            parts.append(f"risk_policy_action={self.risk_policy_action}")
        if self.harm_type:
            parts.append(f"harm_type={self.harm_type}")
        if self.misuse_plausibility:
            parts.append(f"misuse_plausibility={self.misuse_plausibility}")
        if self.intent_to_harm:
            parts.append("intent_to_harm=true")
        if self.requested_instructions:
            parts.append("requested_instructions=true")
        return "; ".join(parts) if parts else ""
