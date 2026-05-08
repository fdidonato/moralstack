"""
Tipi base e modelli dati per MoralStack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Protocol

# =============================================================================
# Turn and Context
# =============================================================================


@dataclass
class Turn:
    """Singolo turno di conversazione."""

    role: Literal["user", "assistant"]
    content: str


@dataclass
class UserContext:
    """Contesto utente per la richiesta."""

    locale: str = "en-US"
    permission_level: Literal["standard", "research", "admin"] = "standard"
    domain_overlay: Optional[str] = None


# =============================================================================
# Principles and Violations
# =============================================================================


@dataclass
class Violation:
    """Rappresenta una singola violazione di principio."""

    principle_id: str
    principle_title: str
    severity: float  # [0, 1]
    constraint_type: Literal["hard", "soft"]
    rationale: str
    evidence: str

    def __post_init__(self) -> None:
        self.severity = max(0.0, min(1.0, self.severity))


# =============================================================================
# Protocols
# =============================================================================


class PrincipleLikeProtocol(Protocol):
    """Protocol for principle-like objects (e.g. from constitution store get_relevant_principles)."""

    id: str
    title: str
    level: str


class PolicyLLMProtocol(Protocol):
    """Protocollo per il Policy LLM."""

    def generate(self, prompt: str, system: str = "", config: Any = None) -> Any: ...
    def rewrite(self, prompt: str, draft: str, guidance: str, system: str = "", config: Any = None) -> Any: ...
    def refuse(self, prompt: str, guidance: str, config: Any = None, language: str | None = None) -> Any: ...


class RiskEstimatorProtocol(Protocol):
    """Protocollo per il Risk Estimator."""

    def estimate(self, prompt: str) -> Any: ...
    def quick_estimate(self, prompt: str) -> Any: ...


class CriticProtocol(Protocol):
    """Protocollo per il Constitutional Critic."""

    def critique(
        self,
        request: str,
        response: str,
        constitution: Any,
        principles: Optional[List[Any]] = None,
        request_id: str = "",
        delib_context: Any = None,
        previous_violations: str = "",
        previous_guidance: str = "",
    ) -> Any: ...
    def quick_check(self, request: str, response: str, constitution: Any) -> Any: ...
    def critique_with_relevant_principles(
        self,
        request: str,
        response: str,
        domain: str | None = None,
        request_id: str = "",
        delib_context: Any = None,
        previous_violations: str = "",
        previous_guidance: str = "",
    ) -> Any: ...


class SimulatorProtocol(Protocol):
    """Protocollo per il Consequence Simulator."""

    def simulate(self, request: str, response: str, num_scenarios: int = 3, delib_context: Any = None) -> Any: ...


class HindsightProtocol(Protocol):
    """Protocollo per l'Hindsight Evaluator."""

    def evaluate_response(self, request: str, response: str, consequences: Optional[List[Any]] = None) -> Any: ...
    def evaluate(
        self,
        request: str,
        response: str,
        consequences: List[Any],
        delib_context: Any = None,
    ) -> Any: ...
    def aggregate(self, evaluations: List[Any]) -> Any: ...


class PerspectiveEnsembleProtocol(Protocol):
    """Protocollo per il Perspective Ensemble."""

    def evaluate(
        self,
        request: str,
        response: str,
        perspectives: Optional[List[Any]] = None,
        delib_context: Any = None,
    ) -> Any: ...
