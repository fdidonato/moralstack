"""
Data models per il sistema di classificazione del rischio.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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


@dataclass(frozen=True)
class RiskEstimation:
    """
    Risultato della stima del rischio semantico.
    Value object immutabile: ogni variazione richiede una nuova istanza.

    Attributes:
        score: Score di rischio [0, 1], più alto = più rischioso
        confidence: Confidenza nella stima [0, 1]
        risk_category: Categoria di rischio
        domain_sensitivity: dimensione domini regolati/etici
        operational_risk: capacità di ricostruire/eseguire azioni dannose
        semantic_signals: Segnali semantici identificati dall'analisi LLM
        risk_policy_action: ALLOW | ALLOW_WITH_CAVEAT | DELIBERATE | DENY (per gating)
        rationale: Spiegazione del rischio dal giudice semantico
        raw_response: Risposta grezza dell'LLM (per debug)
        intent_clarity: Language-agnostic; LOW | MEDIUM | HIGH (for SAFE_COMPLETE routing)
        misuse_plausibility: Language-agnostic; LOW | MEDIUM | HIGH (for SAFE_COMPLETE routing)
        actionability_risk: Language-agnostic; LOW | MEDIUM | HIGH (instrumentation only)
    """

    score: float  # [0, 1]
    confidence: float  # [0, 1]
    risk_category: RiskCategory
    domain_sensitivity: DomainSensitivity = DomainSensitivity.LOW
    operational_risk: OperationalRisk = OperationalRisk.NONE
    semantic_signals: list[str] = field(default_factory=list)
    risk_policy_action: RiskPolicyAction = RiskPolicyAction.DELIBERATE
    rationale: str = ""
    raw_response: str = ""
    intent_clarity: IntentClarity = IntentClarity.HIGH
    misuse_plausibility: MisusePlausibility = MisusePlausibility.LOW
    actionability_risk: ActionabilityRisk = ActionabilityRisk.LOW
    harm_type: str = "none"  # none|emotional_relational|reputational|financial|physical|illegal
    request_type: str = ""  # e.g. crisis_support for help-seeking
    intent_type: str = ""  # support|advice|factual|explanation (derivable from request_type)
    intent_operational: bool = False  # true=HOW-TO/steps; false=informational/descriptive
    requested_instructions: bool = False
    intent_to_harm: bool = False
    used_fallback_parse: bool = False  # True when using fallback/default (no LLM)
    detected_language: str = ""  # ISO 639-1 (en, it, es) from user request; for refusal
    detected_domain: str | None = None  # Domain from _detect_domain() in risk estimator
    estimation_mode: str = ""  # "parallel" (3 mini-estimators) | "monolithic" | "" (fallback/no LLM)

    # Alias per backward compatibility
    @property
    def triggered_signals(self) -> list[str]:
        """Alias per semantic_signals (backward compatibility)."""
        return self.semantic_signals

    def __post_init__(self) -> None:
        # Clamp values in range valido (object.__setattr__ per frozen)
        object.__setattr__(self, "score", max(0.0, min(1.0, self.score)))
        object.__setattr__(self, "confidence", max(0.0, min(1.0, self.confidence)))
        # Coerce legacy str input to RiskPolicyAction
        rpa = getattr(self, "risk_policy_action", RiskPolicyAction.DELIBERATE)
        object.__setattr__(self, "risk_policy_action", coerce_risk_policy_action(rpa))

    @classmethod
    def benign(cls, confidence: float = 0.95, rationale: str = "") -> RiskEstimation:
        """Factory per richieste benigne."""
        return cls(
            score=0.1,
            confidence=confidence,
            risk_category=RiskCategory.BENIGN,
            domain_sensitivity=DomainSensitivity.LOW,
            operational_risk=OperationalRisk.NONE,
            semantic_signals=[],
            risk_policy_action=RiskPolicyAction.ALLOW,
            rationale=rationale or "Semantic analysis: no harmful intent detected",
        )

    @classmethod
    def clearly_harmful(cls, semantic_signals: list[str], rationale: str = "", confidence: float = 0.95) -> RiskEstimation:
        """Factory per richieste chiaramente dannose."""
        return cls(
            score=0.95,
            confidence=confidence,
            risk_category=RiskCategory.CLEARLY_HARMFUL,
            domain_sensitivity=DomainSensitivity.LOW,
            operational_risk=OperationalRisk.HIGH,
            semantic_signals=semantic_signals,
            risk_policy_action=RiskPolicyAction.DENY,
            rationale=rationale or f"Semantic analysis detected harmful intent: {', '.join(semantic_signals)}",
        )

    @classmethod
    def from_error(cls, error_msg: str) -> RiskEstimation:
        """Factory per fallback in caso di errore (assume rischio moderato,
        richiede deliberazione)."""
        return cls(
            score=0.6,
            confidence=0.4,
            risk_category=RiskCategory.SENSITIVE,
            semantic_signals=["SYSTEM.REQUIRES_DELIBERATION"],
            risk_policy_action=RiskPolicyAction.DELIBERATE,
            rationale=f"Semantic analysis failed: {error_msg}. Requiring deliberation.",
            raw_response=error_msg,
            used_fallback_parse=True,
        )


@dataclass(frozen=True)
class RiskEstimatorConfig:
    """
    Configurazione per LLMBasedRiskEstimator (Giudice Semantico).
    Value object immutabile: ogni variazione richiede una nuova istanza.

    Il sistema NON usa keyword matching. Tutte le decisioni sono basate
    sull'analisi semantica dell'LLM che valuta intento, contesto e significato.
    """

    # Soglie di rischio per decisioni di deliberazione
    low_threshold: float = 0.3  # Sotto: minimal deliberation
    medium_threshold: float = 0.7  # Tra low e medium: standard deliberation
    # Sopra medium: full deliberation con multiple prospettive

    # LLM settings per l'analisi semantica
    max_retries: int = 2
    max_tokens: int = 512  # Token per risposta del giudice (512 per evitare troncamento JSON)
    temperature: float = 0.1  # Bassa per decisioni consistenti

    # Fallback quando LLM non disponibile
    fallback_risk_score: float = 0.5  # Score conservativo
    fallback_confidence: float = 0.3  # Bassa confidenza senza LLM
    require_deliberation_on_fallback: bool = True  # Sempre deliberare senza LLM

    # Parallel mini-estimator strategy (3 focused LLM calls instead of 1 monolithic).
    # Default False for backward compatibility; set True to enable parallel mode.
    use_parallel_estimators: bool = False
    intent_model: str = "gpt-4o"
    signals_model: str = "gpt-4o"
    operational_model: str = "gpt-4o"
