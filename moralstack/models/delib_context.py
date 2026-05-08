"""
DelibContext - Rappresentazione compatta e condivisa del contesto deliberativo.

Usato per ridurre token e latenza: costruito una volta per ciclo.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DelibContext:
    """
    Contesto condiviso per la deliberazione. Deriva una volta per ciclo.

    Attributes:
        request_id: Identificatore richiesta
        user_prompt: Prompt originale utente
        detected_language: Lingua rilevata (ISO 639-1) se disponibile
        domain_overlay: Dominio overlay attivo (es. financial, health)
        risk_score: Score rischio [0, 1]
        risk_category: Categoria rischio (BENIGN, SENSITIVE, ecc.)
        operational_risk: NONE | LOW | MEDIUM | HIGH
        intent_operational: True se intento operativo (how-to, steps)
        actionability_risk: LOW | MEDIUM | HIGH
        draft_id: Identificatore versione draft (es. cycle number)
        draft_text_full: Testo completo del draft (sempre disponibile)
        safety_caveats_present: True se draft contiene caveat espliciti
        citations_or_disclaimer_present: True se disclaimer/citazioni presenti
        change_log: Per cycle>1, lista modifiche rispetto a draft precedente
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
