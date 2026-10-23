"""
Data models for the risk classification system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    Result of the semantic risk estimation.
    Immutable value object: any variation requires a new instance.

    Attributes:
        score: Risk score [0, 1], higher = riskier
        confidence: Confidence in the estimate [0, 1]
        risk_category: Risk category
        domain_sensitivity: regulated/ethical domains dimension
        operational_risk: ability to reconstruct/carry out harmful actions
        semantic_signals: Semantic signals identified by the LLM analysis
        risk_policy_action: ALLOW | ALLOW_WITH_CAVEAT | DELIBERATE | DENY (for gating)
        rationale: Risk explanation from the semantic judge
        raw_response: Raw LLM response (for debugging)
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
    harm_type: str = "none"
    # none|emotional_relational|reputational|financial|physical|illegal|child_exploitation
    request_type: str = ""  # e.g. crisis_support for help-seeking
    intent_type: str = ""  # support|advice|factual|explanation (derivable from request_type)
    intent_operational: bool = False  # true=HOW-TO/steps; false=informational/descriptive
    requested_instructions: bool = False
    intent_to_harm: bool = False
    used_fallback_parse: bool = False  # True for from_error / parse-fallback estimates
    detected_language: str = ""  # ISO 639-1 (en, it, es) from user request; for refusal
    detected_domain: str | None = None  # Domain from _detect_domain() in risk estimator
    estimation_mode: str = ""  # "parallel" | "" (fallback/error estimates)

    # ── Semantic flags (LLM-judged, language-agnostic) ─────────────────────
    # Surfaced from the intent estimator's EXECUTION-OF-STATED-INTENT framing
    # detection. Both default to False so older traces / fallback paths are
    # unaffected. Downstream consumers (decision_service, reports) read these
    # to falsify ethical_dilemma classifications when the requester has
    # already decided and is asking only how to execute (see prompts.py).
    stated_personal_bias: bool = False
    seeks_norm_circumvention: bool = False
    # Topic signal: differential treatment of identifiable individuals based
    # on protected characteristics. Pure topic detection, language-agnostic.
    q13_protected_class_targeting: bool = False
    # Reputational-harm cluster (q14-q16): defamation, deceptive online
    # campaigns, coordinated harassment/smear. Distinct from q11 which targets
    # private data; these target public reputation/social standing.
    q14_reputational_harm: bool = False
    q15_deceptive_online_campaign: bool = False
    q16_harassment_smear_campaign: bool = False
    q17_minor_exploitation: bool = False

    # ── Constitution retrieval carrier (unified single-wave retrieval) ─────
    # In-memory Principle objects from constitution_store.get_relevant_principles,
    # retrieved once at risk-estimation time and reused by deliberation/critic/
    # fast-path consumers (see RequestAnalysisContext in orchestration/types.py).
    # MUST NEVER be serialized into persisted llm_calls payloads (see
    # _LOCAL_LLM_CALL_PAYLOAD_KEYS in models/risk/estimator.py).
    relevant_principles: tuple[Any, ...] = ()
    retrieval_metadata: dict[str, Any] = field(default_factory=dict)
    retrieval_count: int = 0
    retrieval_duration_ms: float = 0.0
    retrieval_started_at_ms: int = 0
    retrieval_top_k: int = 0
    # Status flags — the authoritative signal for reuse-vs-fallback decisions
    # downstream (never the emptiness of relevant_principles: an empty
    # successful retrieval is a valid result, not a degraded one).
    retrieval_attempted: bool = False
    retrieval_succeeded: bool = False
    retrieval_error: str | None = None

    def __post_init__(self) -> None:
        # Clamp values to the valid range (object.__setattr__ for frozen)
        object.__setattr__(self, "score", max(0.0, min(1.0, self.score)))
        object.__setattr__(self, "confidence", max(0.0, min(1.0, self.confidence)))
        # Coerce legacy str input to RiskPolicyAction
        rpa = getattr(self, "risk_policy_action", RiskPolicyAction.DELIBERATE)
        object.__setattr__(self, "risk_policy_action", coerce_risk_policy_action(rpa))

    @classmethod
    def benign(cls, confidence: float = 0.95, rationale: str = "") -> RiskEstimation:
        """Factory for benign requests."""
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
        """Factory for clearly harmful requests."""
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
        """Factory for error fallback (assumes moderate risk,
        requires deliberation)."""
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
    Configuration for LLMBasedRiskEstimator (Semantic Judge).
    Immutable value object: any variation requires a new instance.

    The system does NOT use keyword matching. All decisions are based
    on the LLM's semantic analysis, which evaluates intent, context and meaning.
    """

    # LLM settings for the semantic analysis
    max_retries: int = 2
    max_tokens: int = 512  # Tokens per judge response (512 to avoid JSON truncation)
    temperature: float = 0.1  # Low for consistent decisions

    # Focused mini-estimator strategy (3 focused LLM calls).
    # None = inherit the main risk policy model (see resolve_risk_mini_model).
    intent_model: str | None = None
    signals_model: str | None = None
    operational_model: str | None = None
