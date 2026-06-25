"""
Tipi condivisi per l'orchestrazione: state, result, config.
Solo dataclass/enum/protocol; nessuna logica di flusso.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Literal, Protocol

from moralstack.compliance.types import ComplianceVerdict
from moralstack.core.types import (
    CriticProtocol,
    HindsightProtocol,
    PerspectiveEnsembleProtocol,
    PolicyLLMProtocol,
    SimulatorProtocol,
    Turn,
    UserContext,
)
from moralstack.models.base import GenerationOverrides
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.conversation_context import ConversationContext
from moralstack.utils.output_protection import ProtectionResult

# =============================================================================
# Exceptions
# =============================================================================


class MoralStackError(Exception):
    """Base exception per MoralStack."""

    pass


class RiskEstimationError(MoralStackError):
    """Errore nella stima del rischio."""

    pass


class GenerationError(MoralStackError):
    """Il policy LLM non ha potuto generare la risposta."""

    pass


class CritiqueError(MoralStackError):
    """Errore del critic costituzionale."""

    pass


class SimulationError(MoralStackError):
    """Errore nella simulazione delle conseguenze."""

    pass


class OrchestratorTimeoutError(MoralStackError):
    """Timeout superato durante l'elaborazione."""

    pass


class FailSafeException(MoralStackError):
    """
    Sollevata solo dai moduli safety-critical per richiedere FAIL_SAFE come ultima risorsa.
    FAIL_SAFE è riservato a effettivo guasto di sistema.
    """

    pass


# =============================================================================
# Enums
# =============================================================================


class FinalAction(Enum):
    """Stato finale esplicito della decisione."""

    NORMAL_COMPLETE = "normal_complete"
    SAFE_COMPLETE = "safe_complete"
    REFUSE = "refuse"


class ResponseType(Enum):
    """Tipi di risposta finale."""

    DIRECT = "direct"
    WITH_CAVEAT = "with_caveat"
    PARTIAL_REFUSAL = "partial_refusal"
    FULL_REFUSAL = "full_refusal"
    REDIRECT = "redirect"
    DOMAIN_EXCLUDED = "domain_excluded"


class DecisionType(Enum):
    """Tipi di decisione del ciclo deliberativo."""

    CONTINUE = "continue"
    CONVERGED = "converged"
    CONVERGED_WITH_SUGGESTIONS = "converged_with_suggestions"
    REFUSE = "refuse"
    REVISE = "revise"


StopReason = Literal[
    "CONVERGED",
    "CYCLES_EXHAUSTED",
    "HARD_VIOLATION_STOP",
    "TIMEOUT",
    "ERROR",
    "MANUAL_POLICY_STOP",
    "NONE",  # in-loop, non ancora usciti
]


@dataclass(frozen=True)
class ConvergenceOutcome:
    """
    Esito convergenza dopo enforcement: unica autorità per il loop deliberativo.
    should_continue=True è permesso SOLO se cycle < max_cycles AND converged=False
    AND no fatal condition.
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    should_continue: bool
    converged: bool
    stop_reason: StopReason
    cycle: int
    max_cycles: int


def response_type_to_final_action(response_type: ResponseType) -> FinalAction | None:
    """Mappa ResponseType -> FinalAction. FAIL_SAFE solo se final_action
    non è uno di questi tre."""
    if response_type == ResponseType.DIRECT:
        return FinalAction.NORMAL_COMPLETE
    if response_type == ResponseType.WITH_CAVEAT:
        return FinalAction.SAFE_COMPLETE
    if response_type in (
        ResponseType.FULL_REFUSAL,
        ResponseType.PARTIAL_REFUSAL,
        ResponseType.REDIRECT,
        ResponseType.DOMAIN_EXCLUDED,
    ):
        return FinalAction.REFUSE
    return None


# =============================================================================
# Decision result (output di decide_action)
# =============================================================================

FinalActionStr = Literal["REFUSE", "SAFE_COMPLETE", "NORMAL_COMPLETE"]
PathStr = Literal["FAST_PATH", "DELIBERATIVE_PATH", "COMPLIANCE_FAST_PATH"]
AxisStr = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass(frozen=True)
class Decision:
    """
    Output di decide_action(): final_action e path deterministici da soli segnali semantici.
    Tutti i campi sempre impostati (nessuna inferenza dal testo di risposta).
    reason_codes: codici diagnostici per routing
    (es. regulated_but_informational, informational_intent_override).
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    final_action: FinalActionStr
    path: PathStr
    intent_clarity: str
    misuse_plausibility: str
    actionability_risk: str
    triggered_principles: list[str]
    hard_violations: list[str]
    risk_signals: list[str]
    reason_codes: list[str] = field(default_factory=list)


# =============================================================================
# Request / Response
# =============================================================================


@dataclass
class ProcessedRequest:
    """Richiesta processata pronta per l'Orchestrator."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    prompt: str = ""
    conversation_history: list[Turn] = field(default_factory=list)
    user_context: UserContext = field(default_factory=UserContext)
    timestamp: float = field(default_factory=time.time)
    developer_contract: DeveloperContract | None = None  # NEW v0.4
    conversation_context: ConversationContext | None = None
    # Per-request sampling overrides from the client (proxy body / SDK kwargs).
    # Honored only by delivered-answer generators, never by REFUSE wording.
    generation_overrides: GenerationOverrides | None = None

    def get_domain(self) -> str | None:
        """Ottiene il dominio overlay dall'user context."""
        if isinstance(self.user_context, UserContext):
            return self.user_context.domain_overlay
        elif isinstance(self.user_context, dict):
            return self.user_context.get("domain")
        return None


@dataclass
class PolicyOverlay:
    """Policy overlay metadata (structure only, no user-facing text). Language-agnostic."""

    caveat_type: Literal["generic", "domain_specific", "none"]
    principle_ids: list[str]


@dataclass
class MetaAnalysis:
    """Meta-analysis for audit/debug. Never appears in content."""

    critic_rationales: list[str]
    hindsight_score: float
    stop_reason: str


@dataclass
class ResponseMetadata:
    """Metadata della risposta finale (audit/tracciabilità)."""

    risk_score: float = 0.0
    hindsight_score: float = 0.0
    triggered_principles: list[str] = field(default_factory=list)
    final_action: str = ""
    must_refuse: bool = False
    refusal_reason: str = ""
    decision_trace_id: str = ""
    # --- Extended fields (audit trail, DCF, metadata) ---
    deliberation_cycles: int = 0
    cycles: int = 0
    processing_time_ms: int = 0
    path: str = ""
    predicted_action: str | None = None
    constitution_loaded_ok: bool | None = None
    risk_category: str = ""
    hard_violations: list[str] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)
    intent_clarity: str = ""
    misuse_plausibility: str = ""
    actionability_risk: str = ""
    intent_type: str | None = None
    domain_overlay: str | None = None
    operational_risk: str = ""
    requested_instructions: bool = False
    intent_to_harm: bool = False
    intent_operational: bool = False
    routing_reason_codes: list[str] = field(default_factory=list)
    # Decision Correctness Framework payload (diagnostics.attach_decision_correctness).
    decision_correctness: dict[str, Any] | None = None
    reason_codes: list[str] = field(default_factory=list)
    decision_reason: str = ""
    overlay_applied: str = ""
    winning_rule: str = ""
    why_not_refuse: str = ""
    why_not_safe_complete: str = ""
    caveat_present: bool = True
    safe_alternative_present: bool = True
    no_prescriptive_language: bool = True
    excluded_domain: str | None = None
    refusal_domain: str | None = None
    refusal_redirection_source: str | None = None
    safe_refusal_focus: str | None = None
    # True when the delivered governed content is a reused internal speculative
    # draft (no second policy `generate` for delivery). Surfaced as the
    # X-Moralstack-Internal-Draft-Reused proxy header (server/headers.py).
    internal_draft_reused: bool = False

    @classmethod
    def from_decision(
        cls,
        *,
        decision: Decision,
        request_id: str,
        risk_score: float,
        processing_time_ms: int,
        risk_category: str,
        decision_explanation: DecisionExplanation | None = None,
        deliberation_cycles: int = 0,
        hindsight_score: float = 0.0,
        constitution_loaded_ok: bool | None = None,
        predicted_action: str | None = None,
        intent_type: str | None = None,
        domain_overlay: str | None = None,
        routing_reason_codes: list[str] | None = None,
        **overrides: Any,
    ) -> ResponseMetadata:
        """
        Build ResponseMetadata from Decision and optional DecisionExplanation.
        Centralizes mapping so all paths produce consistent metadata.
        """
        _reason_codes = (
            list(decision_explanation.reason_codes)
            if decision_explanation
            else list(getattr(decision, "reason_codes", None) or [])
        )
        _routing = list(routing_reason_codes) if routing_reason_codes is not None else list(decision.reason_codes)
        _reason_str = ", ".join(_reason_codes) if _reason_codes else "policy_bounds_decision"
        base: dict[str, Any] = {
            "risk_score": risk_score,
            "deliberation_cycles": deliberation_cycles,
            "hindsight_score": hindsight_score,
            "triggered_principles": list(decision.triggered_principles),
            "processing_time_ms": processing_time_ms,
            "predicted_action": predicted_action,
            "constitution_loaded_ok": constitution_loaded_ok,
            "hard_violations": list(decision.hard_violations),
            "risk_signals": list(decision.risk_signals),
            "final_action": decision.final_action,
            "path": decision.path,
            "cycles": deliberation_cycles,
            "intent_clarity": decision.intent_clarity,
            "misuse_plausibility": decision.misuse_plausibility,
            "actionability_risk": decision.actionability_risk,
            "risk_category": risk_category,
            "intent_type": intent_type,
            "domain_overlay": domain_overlay,
            "operational_risk": overrides.get("operational_risk", ""),
            "requested_instructions": bool(overrides.get("requested_instructions", False)),
            "intent_to_harm": bool(overrides.get("intent_to_harm", False)),
            "intent_operational": bool(overrides.get("intent_operational", False)),
            "routing_reason_codes": _routing,
            "decision_trace_id": request_id,
            "decision_reason": _reason_str,
            "reason_codes": list(_reason_codes),
            "overlay_applied": (decision_explanation.overlay_applied or "") if decision_explanation else "",
            "winning_rule": (decision_explanation.winning_rule or "") if decision_explanation else "",
            "why_not_refuse": (decision_explanation.why_not_refuse or "") if decision_explanation else "",
            "why_not_safe_complete": ((decision_explanation.why_not_safe_complete or "") if decision_explanation else ""),
        }
        base.update(overrides)
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in base.items() if k in valid})

    @classmethod
    def for_system_error(
        cls,
        processing_time_ms: int,
        request_id: str = "",
        principle: str = "SYSTEM.ERROR",
    ) -> ResponseMetadata:
        """Metadata for timeout or generic system error (REFUSE)."""
        return cls(
            risk_score=1.0,
            processing_time_ms=processing_time_ms,
            triggered_principles=[principle],
            decision_trace_id=request_id,
        )

    @classmethod
    def for_domain_excluded(
        cls,
        processing_time_ms: int,
        request_id: str,
        excluded_domain: str,
    ) -> ResponseMetadata:
        """Metadata for domain-excluded early exit (REFUSE)."""
        return cls(
            risk_score=0.0,
            deliberation_cycles=0,
            processing_time_ms=processing_time_ms,
            final_action="REFUSE",
            path="DOMAIN_EXCLUDED",
            excluded_domain=excluded_domain,
            reason_codes=["domain_excluded"],
            winning_rule="domain_excluded",
            decision_trace_id=request_id,
        )

    @classmethod
    def for_fail_safe(cls, processing_time_ms: int = 0) -> ResponseMetadata:
        """Metadata for FAIL_SAFE fallback (REFUSE). Same shape as safe_default."""
        return cls(
            risk_score=1.0,
            deliberation_cycles=0,
            hindsight_score=0.0,
            triggered_principles=["SYSTEM.FAIL_SAFE"],
            processing_time_ms=processing_time_ms,
            final_action="REFUSE",
            path="DELIBERATIVE_PATH",
            cycles=0,
            intent_clarity="LOW",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
        )


@dataclass
class FinalResponse:
    """Risposta finale dell'Orchestrator."""

    content: str
    response_type: ResponseType
    metadata: ResponseMetadata = field(default_factory=ResponseMetadata)
    policy_overlay: PolicyOverlay | None = None
    meta_analysis: MetaAnalysis | None = None

    @classmethod
    def refusal(cls, content: str, metadata: ResponseMetadata) -> FinalResponse:
        """Factory per risposte di rifiuto."""
        return cls(
            content=content,
            response_type=ResponseType.FULL_REFUSAL,
            metadata=metadata,
        )

    @classmethod
    def safe_default(cls, processing_time_ms: int = 0) -> FinalResponse:
        """Factory per risposta safe di fallback. final_action sempre valorizzata
        (FAIL_SAFE -> REFUSE)."""
        return cls(
            content="[SYSTEM_ERROR]",
            response_type=ResponseType.FULL_REFUSAL,
            metadata=ResponseMetadata.for_fail_safe(processing_time_ms),
        )


OrchestratorPath = Literal["FAST_PATH", "DELIBERATIVE_PATH", "COMPLIANCE_FAST_PATH", "DOMAIN_EXCLUDED", "ERROR_PATH"]
PathTakenType = Literal[
    "fast",
    "deliberative",
    "deliberative_moral",
    "deliberative_sensitive",
    "domain_excluded",
    "error",
]


@dataclass
class OrchestratorResult:
    """
    Risultato completo dell'Orchestrator.
    path: FAST_PATH | DELIBERATIVE_PATH; cycles: 0 for FAST_PATH, else deliberation cycle count.
    """

    response: FinalResponse
    request_id: str = ""
    path_taken: PathTakenType = "fast"
    path: OrchestratorPath = "DELIBERATIVE_PATH"
    total_cycles: int = 0
    converged: bool = True
    error: str | None = None
    errors: list[str] | None = None
    execution_trace: dict[str, Any] | None = None
    # Populated for deliberative runs; also set when fast_path falls through to deliberation.
    convergence_snapshot: dict[str, Any] | None = None
    trace: Any = None  # orchestration Trace (request-scoped diagnostica)
    # Optional conversation linkage (multi-turn foundation; dormant when unset)
    conversation_id: str | None = None
    turn_index: int | None = None
    parent_request_id: str | None = None
    conversation_state_provided: bool = False
    conversation_governance_state_out: Any | None = None  # ConversationGovernanceState when set
    conversation_state_updated: bool = False
    compliance_verdict: ComplianceVerdict | None = None
    delivery_context_broader_than_governance: bool = False
    mismatch_guard_action: str = "none"
    governance_context_mode: str = "none"
    candidate_context_mode: str = "none"
    prior_turn_count: int = 0
    history_source: str = "none"
    """
    DCCL verdict from this turn, if the DCCL was invoked.
    None for backward-compatible scenarios (DCCL disabled or pre-Commit 2 call sites).
    Populated by Commit 2; consumed by downstream modules in Commit 3.
    """

    @property
    def cycles(self) -> int:
        """Exposed for benchmark: 0 for FAST_PATH, else deliberation cycle count."""
        return self.total_cycles


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True)
class RiskThresholds:
    """
    Soglie per la classificazione del rischio.
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    low: float = 0.3
    medium: float = 0.7


@dataclass(frozen=True)
class OrchestratorConfig:
    """
    Configurazione dell'Orchestrator.
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    max_deliberation_cycles: int = 2
    risk_thresholds: RiskThresholds = field(default_factory=RiskThresholds)
    timeout_ms: int = 600000
    enable_perspectives: bool = True
    num_simulations: int = 3
    min_hindsight_score: float = 0.8
    max_critical_violations: int = 0
    early_exit_hindsight_threshold: float = 0.6
    enable_simulation: bool = True
    enable_hindsight: bool = True
    safe_response_on_error: bool = True
    skip_optional_modules_threshold: float = 0.95
    soft_timeout_threshold: float = 0.90
    parallel_module_calls: bool = True
    enable_simulator_gating: bool = False  # Cycle 2+ skip simulator when safe (opt-in to preserve behavior)
    enable_hindsight_gating: bool = True  # Default True: skip hindsight except in final cycle; set False to run every cycle
    simulator_gate_semantic_harm_threshold: float = 0.4  # Run if prev sim semantic_expected_harm >= this
    simulator_gate_delta_chars_threshold: int = 100  # Run if draft changed by >= this many chars
    # Conservative skip: only if prior semantic_expected_harm is strictly below this (and other gates pass).
    simulator_gate_skip_max_prior_semantic_harm: float = 0.25
    # Upper bound for borderline REFUSE deliberation (inclusive).
    # When a REFUSE decision has a risk_score between
    # risk_thresholds.medium and borderline_refuse_upper inclusive,
    # the deliberative pipeline will be invoked instead of early-fast refusal.
    borderline_refuse_upper: float = 0.95
    # Soft revision: single rewrite pass to incorporate pending suggestions after convergence.
    enable_soft_revision: bool = True
    soft_revision_min_suggestions: int = 1  # Minimum total suggestions to trigger
    soft_revision_max_approval: float = 0.95  # Skip rewrite if weighted approval exceeds this
    early_exit_perspectives_threshold: float = 0.85  # Early exit se critic PROCEED + perspectives >= questo
    # When True and parallel_module_calls is True, critic runs in parallel
    # with simulator and perspectives instead of acting as a sequential gate.
    # Hard violations are still honoured: sim/persp results are discarded when
    # the critic finds a hard violation. Default True for latency savings.
    parallel_critic_with_modules: bool = True
    # When True (default), each deliberation cycle picks critic_gated vs
    # full_parallel from risk posture; when False, only parallel_critic_with_modules
    # selects between them (legacy static fork).
    enable_dynamic_parallel_scheduler: bool = True
    # When True, risk estimation and speculative draft generation run in
    # parallel. The draft is used directly for benign/fast/deliberative
    # routes and discarded on REFUSE. Zero impact on decision quality.
    enable_speculative_generation: bool = True
    # Stricter than early_exit_perspectives_threshold; used only for cycle-1 early convergence.
    cycle1_early_convergence_min_weighted_approval: float = 0.78
    cycle1_early_convergence_max_semantic_harm: float = 0.35
    cycle1_early_convergence_min_per_perspective_approval: float = 0.70
    # Opt-in: benign, non-operational informational recovery in a sensitive
    # overlay returns NORMAL_COMPLETE instead of being floored to SAFE_COMPLETE.
    # Default False preserves the current regulated -> SAFE_COMPLETE behavior.
    regulated_informational_normal_complete: bool = False


# =============================================================================
# Module result protocols (structural subtyping; no import from runtime modules)
# =============================================================================


class PolicyGenerationResultProtocol(Protocol):
    """Protocol for policy LLM generation/rewrite result. Minimum: text; optional prompt_used/system_used."""

    text: str
    prompt_used: str | None
    system_used: str | None


class ViolationLikeProtocol(Protocol):
    """Protocol for a single violation (e.g. from critic). Used when iterating critique.violations."""

    principle_id: str
    rationale: str


class CriticReportProtocol(Protocol):
    """Protocol for full critic critique result."""

    violations: list[ViolationLikeProtocol]
    has_critical_violations: bool
    violated_hard: bool
    decision: str
    revision_guidance: str
    prompt: str
    system_prompt: str


class QuickCheckResultProtocol(Protocol):
    """Protocol for critic quick_check result."""

    passed: bool
    critical_violation: Any


class ConsequenceLikeProtocol(Protocol):
    """Protocol for a single simulated consequence. Runner uses .text, .likelihood, .harm_severity, .outcome_valence."""

    text: str
    likelihood: float
    harm_severity: float
    outcome_valence: float


class SimulationResultProtocol(Protocol):
    """Protocol for simulator simulate() result."""

    semantic_expected_harm: float
    expected_valence: float
    consequences: list[Any]
    worst_case_valence: float
    raw_response: str
    parse_attempts: int
    prompt: str
    dominant_harm_types: list[str]
    worst_harm: Any


class AggregatedHindsightProtocol(Protocol):
    """Protocol for HindsightResult.aggregated (aggregation of evaluations)."""

    expected_value: float
    worst_case: float
    best_case: float
    variance: float
    recommendation: Any
    evaluations: list[Any]


class HindsightResultProtocol(Protocol):
    """Protocol for hindsight evaluate_response/aggregate result."""

    aggregated: AggregatedHindsightProtocol
    raw_response: str
    parse_attempts: int
    prompt: str
    system_prompt: str


class PerspectiveResultProtocol(Protocol):
    """Protocol for a single perspective evaluation result."""

    perspective_id: str
    perspective_name: str
    approval_score: float
    concerns: list[str]
    suggestions: list[str]
    rationale: str


class PerspectiveAggregationProtocol(Protocol):
    """Protocol for EnsembleResult.aggregation."""

    weighted_approval: float
    min_approval: float
    recommendation: Any  # "proceed" | "revise" | "refuse"


class EnsembleResultProtocol(Protocol):
    """Protocol for perspectives ensemble evaluate() result (_perspectives_aggregation).
    EnsembleResult delegates recommendation to aggregation; expose for protocol consumers.
    """

    results: list[Any]
    aggregation: Any  # PerspectiveAggregation
    evaluation_count: int
    failed_perspectives: list[str]


class LoggerProtocol(Protocol):
    """Protocol for optional logger with log_call (used by deliberation_runner for diagnostics)."""

    def log_call(
        self,
        *,
        module: str = "",
        action: str = "",
        duration_ms: float = 0,
        prompt: str = "",
        system_prompt: str = "",
        raw_response: str = "",
        response: str = "",  # alias used by callers (same as raw_response)
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Log an LLM or module call for diagnostics."""
        ...


class RiskCategoryLikeProtocol(Protocol):
    """Protocol for risk_category/actionability_risk etc. (e.g. enum with .value)."""

    @property
    def value(self) -> str: ...


class RiskEstimationProtocol(Protocol):
    """Protocol for risk estimation result (read-only; RiskEstimation is frozen)."""

    @property
    def score(self) -> float: ...

    @property
    def risk_category(self) -> RiskCategoryLikeProtocol: ...

    @property
    def detected_language(self) -> str: ...

    @property
    def intent_type(self) -> str: ...

    @property
    def actionability_risk(self) -> RiskCategoryLikeProtocol: ...

    @property
    def detected_domain(self) -> str | None: ...

    @property
    def rationale(self) -> str: ...

    @property
    def operational_risk(self) -> RiskCategoryLikeProtocol: ...

    @property
    def raw_response(self) -> str: ...

    @property
    def used_fallback_parse(self) -> bool: ...

    @property
    def risk_policy_action(self) -> RiskCategoryLikeProtocol: ...

    @property
    def harm_type(self) -> str: ...


# =============================================================================
# Deliberation state
# =============================================================================


@dataclass
class DeliberationState:
    """Deliberation process state. Tracks all module results across cycles."""

    cycle: int = 0
    draft_response: str = ""
    critiques: list[CriticReportProtocol] = field(default_factory=list)
    simulations: list[SimulationResultProtocol] = field(default_factory=list)
    hindsight: HindsightResultProtocol | None = None
    perspectives: list[PerspectiveResultProtocol] = field(default_factory=list)
    decision: DecisionType | None = None
    errors: list[str] = field(default_factory=list)
    # Optional attributes set during deliberation (used by deliberation_runner)
    _prev_delib_context: Any = field(default=None, repr=False)
    _hindsight_skipped_reason: str | None = field(default=None, repr=False)
    _perspectives_aggregation: EnsembleResultProtocol | None = field(default=None, repr=False)
    # Soft revision tracking
    soft_revision_applied: bool = False
    soft_revision_guidance_used: str = ""
    # Simulator gating observability (set by DeliberationRunner per cycle)
    _simulator_ran_this_cycle: bool | None = field(default=None, repr=False)
    _simulator_gate_reason_codes: list[str] = field(default_factory=list, repr=False)
    _simulator_carry_forward: bool = field(default=False, repr=False)
    _parallel_scheduler_strategy: Literal["critic_gated", "full_parallel"] | None = field(default=None, repr=False)
    _parallel_scheduler_reason_codes: list[str] = field(default_factory=list, repr=False)
    _critic_short_circuit: bool = field(default=False, repr=False)
    _scheduler_skipped_modules: list[str] = field(default_factory=list, repr=False)
    # Last convergence evaluation (observability; set by ConvergenceEvaluator.determine_decision)
    _convergence_evaluation_snapshot: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def last_critique(self) -> CriticReportProtocol | None:
        """Last critique if available."""
        return self.critiques[-1] if self.critiques else None

    @property
    def has_critical_violations(self) -> bool:
        """True if the last critique has critical violations. Uses getattr for mock compatibility."""
        if not self.critiques:
            return False
        last = self.critiques[-1]
        return bool(getattr(last, "has_critical_violations", False))

    @property
    def hindsight_score(self) -> float:
        """Aggregated hindsight score. Uses getattr for mock compatibility."""
        if self.hindsight is not None:
            agg = getattr(self.hindsight, "aggregated", None)
            if agg is not None:
                return float(getattr(agg, "expected_value", 0.0))
            if hasattr(self.hindsight, "expected_value"):
                return float(self.hindsight.expected_value)
            if hasattr(self.hindsight, "reward_score"):
                return float(self.hindsight.reward_score)
        if self.simulations:
            last_sim = self.simulations[-1]
            if hasattr(last_sim, "hindsight_score"):
                return float(last_sim.hindsight_score)
        return 0.0

    @property
    def triggered_principles(self) -> list[str]:
        """List of violated principle IDs. Uses getattr for mock compatibility."""
        principles = []
        for critique in self.critiques:
            for v in getattr(critique, "violations", []):
                pid = getattr(v, "principle_id", None)
                if pid is not None:
                    principles.append(str(pid))
        return list(set(principles))

    def fork(self) -> DeliberationState:
        """Copia shallow per branching parallelo. Solo le liste mutabili sono copiate."""
        return DeliberationState(
            cycle=self.cycle,
            draft_response=self.draft_response,
            critiques=list(self.critiques),
            simulations=list(self.simulations),
            perspectives=list(self.perspectives),
            decision=self.decision,
            hindsight=self.hindsight,
            errors=list(self.errors),
            _prev_delib_context=self._prev_delib_context,
            _hindsight_skipped_reason=self._hindsight_skipped_reason,
            _perspectives_aggregation=self._perspectives_aggregation,
            soft_revision_applied=self.soft_revision_applied,
            soft_revision_guidance_used=self.soft_revision_guidance_used,
            _simulator_ran_this_cycle=self._simulator_ran_this_cycle,
            _simulator_gate_reason_codes=list(self._simulator_gate_reason_codes),
            _simulator_carry_forward=self._simulator_carry_forward,
            _parallel_scheduler_strategy=self._parallel_scheduler_strategy,
            _parallel_scheduler_reason_codes=list(self._parallel_scheduler_reason_codes),
            _critic_short_circuit=self._critic_short_circuit,
            _scheduler_skipped_modules=list(self._scheduler_skipped_modules),
            _convergence_evaluation_snapshot=(
                dict(self._convergence_evaluation_snapshot) if self._convergence_evaluation_snapshot else None
            ),
        )


class ConstitutionStoreProtocol(Protocol):
    """Protocol for the Constitution Store."""

    def get_constitution(self, domain: str | None = None) -> Any:
        """Returns the constitution for the given domain."""
        ...

    def get_relevant_principles(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = "risk_routing",
    ) -> Sequence[Any]:
        """Returns relevant principles for the query."""
        ...

    def detect_relevant_domains(self, prompt: str) -> list[str]:
        """Returns list of relevant domain names for the prompt (optional)."""
        ...


@dataclass(frozen=True)
class RequestAnalysisContext:
    """
    Request-scoped constitution analysis for a single deliberative path.

    Built once from the constitution store (relevant principles + constitution object)
    and passed to consumers (critic, delib overlay) to avoid redundant retrieval calls.
    Treat as read-only after construction.
    """

    relevant_principles: tuple[Any, ...]
    constitution: Any
    detected_domain: str | None
    retrieval_metadata: dict[str, Any] = field(default_factory=dict)
    prefilter_cache_status: str | None = None
    retrieval_count: int = 0
    retrieval_duration_ms: float = 0.0
    retrieval_started_at_ms: int = 0
    retrieval_top_k: int = 10


class OutputProtectorProtocol(Protocol):
    """Protocol for output protection (validate, prepare_system_prompt)."""

    def prepare_system_prompt(self, base_prompt: str) -> str:
        """Return system prompt with protections applied."""
        ...

    def validate(self, output: str) -> ProtectionResult:
        """Validate output and return protection result."""
        ...


@dataclass
class DeliberationDependencies:
    """Runtime module dependencies injected into DeliberationRunner."""

    policy: PolicyLLMProtocol | None
    critic: CriticProtocol | None
    simulator: SimulatorProtocol | None
    hindsight: HindsightProtocol | None
    perspectives: PerspectiveEnsembleProtocol | None
    constitution_store: ConstitutionStoreProtocol | None
    output_protector: OutputProtectorProtocol


# =============================================================================
# Helpers (usati da assembler/diagnostics)
# =============================================================================


def risk_category_str(risk_estimation: RiskEstimationProtocol) -> str:
    """Extract risk_category as lowercase string from risk_estimation (for metadata/DCF)."""
    rc = risk_estimation.risk_category
    return str(rc.value).strip().lower()
