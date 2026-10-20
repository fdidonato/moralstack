"""
MoralStack Orchestrator - Controllo flusso deliberativo.

Punto di ingresso pubblico: delega a OrchestrationController (moralstack.orchestration).
Mantiene API invariata per compatibilità con script e test.
"""

from __future__ import annotations

from typing import Any

from moralstack.core.types import (
    CriticProtocol,
    HindsightProtocol,
    PerspectiveEnsembleProtocol,
    PolicyLLMProtocol,
    RiskEstimatorProtocol,
    SimulatorProtocol,
    Turn,
    UserContext,
)
from moralstack.models.risk.categories import OperationalRisk, RiskCategory
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.conversation_state import ConversationGovernanceState
from moralstack.orchestration.default_event_emitter import DefaultEventEmitter
from moralstack.orchestration.default_persistence import DefaultPersistence
from moralstack.orchestration.response_assembler import ResponseAssembler

# Re-export tipi ed eccezioni per compatibilità (API pubblica)
from moralstack.orchestration.types import (
    ConstitutionStoreProtocol,
    CritiqueError,
    Decision,
    DecisionType,
    DeliberationState,
    FinalResponse,
    GenerationError,
    MoralStackError,
    OrchestratorConfig,
    OrchestratorResult,
    OrchestratorTimeoutError,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
    RiskEstimationError,
    RiskThresholds,
)
from moralstack.prompts.catalog import PromptSet, default_prompt_set
from moralstack.utils.output_protection import create_protector

__all__ = [
    "Orchestrator",
    "create_orchestrator",
    "create_minimal_orchestrator",
    "ProcessedRequest",
    "ResponseMetadata",
    "FinalResponse",
    "OrchestratorResult",
    "ResponseType",
    "DecisionType",
    "Decision",
    "RiskThresholds",
    "OrchestratorConfig",
    "DeliberationState",
    "ResponseAssembler",
    "RiskCategory",
    "OperationalRisk",
    "MoralStackError",
    "RiskEstimationError",
    "GenerationError",
    "CritiqueError",
    "OrchestratorTimeoutError",
    "Turn",
    "UserContext",
    "ConversationGovernanceState",
]


def create_orchestrator(
    policy: PolicyLLMProtocol | None = None,
    risk_estimator: RiskEstimatorProtocol | None = None,
    critic: CriticProtocol | None = None,
    simulator: SimulatorProtocol | None = None,
    hindsight: HindsightProtocol | None = None,
    perspectives: PerspectiveEnsembleProtocol | None = None,
    constitution_store: ConstitutionStoreProtocol | None = None,
    max_cycles: int = 2,
    timeout_ms: int = 600000,
    enable_perspectives: bool = True,
    enable_simulation: bool = True,
    enable_hindsight: bool = True,
    ledger: Any = None,
    prompt_set: PromptSet | None = None,
) -> "Orchestrator":
    """Factory that builds an ``Orchestrator`` with the given modules and optional ledger.

    ``timeout_ms`` defaults to the same value as ``OrchestratorConfig.timeout_ms``.
    ``prompt_set`` is the source of every prompt text (None = packaged defaults).
    """
    config = OrchestratorConfig(
        max_deliberation_cycles=max_cycles,
        timeout_ms=timeout_ms,
        enable_perspectives=enable_perspectives,
        enable_simulation=enable_simulation,
        enable_hindsight=enable_hindsight,
    )
    return Orchestrator(
        config=config,
        policy=policy,
        risk_estimator=risk_estimator,
        critic=critic,
        simulator=simulator,
        hindsight=hindsight,
        perspectives=perspectives,
        constitution_store=constitution_store,
        ledger=ledger,
        prompt_set=prompt_set,
    )


def create_minimal_orchestrator(
    policy: PolicyLLMProtocol | None = None,
    risk_estimator: RiskEstimatorProtocol | None = None,
    prompt_set: PromptSet | None = None,
) -> "Orchestrator":
    """Crea un Orchestrator minimale (solo policy e risk)."""
    config = OrchestratorConfig(
        max_deliberation_cycles=2,
        enable_perspectives=False,
        enable_simulation=False,
        enable_hindsight=False,
    )
    return Orchestrator(
        config=config,
        policy=policy,
        risk_estimator=risk_estimator,
        prompt_set=prompt_set,
    )


class Orchestrator:
    """
    Facade: delega a OrchestrationController.
    API pubblica invariata (process, set_logger, execution_trace, config, assembler).

    Step 14.2: optional ``ledger`` wires ``SemanticDecisionLedger`` into the controller
    for the conversational fast-path. When ``ledger`` is ``None`` (default), behaviour
    matches pre-14.2 (no semantic cache short-circuit).
    """

    def __init__(
        self,
        config: OrchestratorConfig | None = None,
        policy: PolicyLLMProtocol | None = None,
        risk_estimator: RiskEstimatorProtocol | None = None,
        critic: CriticProtocol | None = None,
        simulator: SimulatorProtocol | None = None,
        hindsight: HindsightProtocol | None = None,
        perspectives: PerspectiveEnsembleProtocol | None = None,
        constitution_store: ConstitutionStoreProtocol | None = None,
        ledger: Any = None,
        prompt_set: PromptSet | None = None,
    ) -> None:
        self._prompt_set = prompt_set or default_prompt_set()
        self.config = config or OrchestratorConfig()
        self.policy = policy
        self.risk_estimator = risk_estimator
        self.critic = critic
        self.simulator = simulator
        self.hindsight = hindsight
        self.perspectives = perspectives
        self.constitution_store = constitution_store
        self.ledger = ledger
        self.logger = None

        output_protector = create_protector(enable_canary=True, enable_delimiters=True, prompt_set=self._prompt_set)
        protected_system_prompt = output_protector.prepare_system_prompt(self._prompt_set.text("policy.system"))

        self._controller = OrchestrationController(
            config=self.config,
            policy=policy,
            risk_estimator=risk_estimator,
            critic=critic,
            simulator=simulator,
            hindsight=hindsight,
            perspectives=perspectives,
            constitution_store=constitution_store,
            output_protector=output_protector,
            protected_system_prompt=protected_system_prompt,
            logger=self.logger,
            persistence=DefaultPersistence(),
            event_emitter=DefaultEventEmitter(),
            ledger=ledger,
            prompt_set=self._prompt_set,
        )
        self.assembler = self._controller.assembler
        self.execution_trace = self._controller.execution_trace

    @property
    def prompt_set(self) -> PromptSet:
        """The ``PromptSet`` this orchestrator's prompt texts come from (read-only)."""
        return self._prompt_set

    def set_logger(self, logger: Any) -> None:
        """Imposta il logger per tracciare chiamate LLM."""
        self.logger = logger
        self._controller.set_logger(logger)

    def process(self, request: ProcessedRequest | str, **kwargs: Any) -> OrchestratorResult:
        """Entry point principale. Delega al controller. Optional conversation_* kwargs are forwarded."""
        return self._controller.process(request, **kwargs)
