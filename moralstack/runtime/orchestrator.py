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
from moralstack.orchestration._policy_helpers import POLICY_SYSTEM_PROMPT
from moralstack.orchestration.controller import OrchestrationController
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
from moralstack.persistence.default import DefaultPersistence
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
    timeout_ms: int = 5000,
    enable_perspectives: bool = True,
    enable_simulation: bool = True,
    enable_hindsight: bool = True,
) -> "Orchestrator":
    """Factory function per creare un Orchestrator."""
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
    )


def create_minimal_orchestrator(
    policy: PolicyLLMProtocol | None = None,
    risk_estimator: RiskEstimatorProtocol | None = None,
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
    )


class Orchestrator:
    """
    Facade: delega a OrchestrationController.
    API pubblica invariata (process, set_logger, execution_trace, config, assembler).
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
    ) -> None:
        self.config = config or OrchestratorConfig()
        self.policy = policy
        self.risk_estimator = risk_estimator
        self.critic = critic
        self.simulator = simulator
        self.hindsight = hindsight
        self.perspectives = perspectives
        self.constitution_store = constitution_store
        self.logger = None

        output_protector = create_protector(enable_canary=True, enable_delimiters=True)
        protected_system_prompt = output_protector.prepare_system_prompt(POLICY_SYSTEM_PROMPT)

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
        )
        self.assembler = self._controller.assembler
        self.execution_trace = self._controller.execution_trace

    def set_logger(self, logger: Any) -> None:
        """Imposta il logger per tracciare chiamate LLM."""
        self.logger = logger
        self._controller.set_logger(logger)

    def process(self, request: ProcessedRequest | str) -> OrchestratorResult:
        """Entry point principale. Delega al controller."""
        return self._controller.process(request)
