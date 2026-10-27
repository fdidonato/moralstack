"""
Base types and data models for MoralStack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Protocol

# =============================================================================
# Turn and Context
# =============================================================================


@dataclass
class Turn:
    """Single conversation turn."""

    role: Literal["user", "assistant"]
    content: str


@dataclass
class UserContext:
    """User context for the request."""

    locale: str = "en-US"
    permission_level: Literal["standard", "research", "admin"] = "standard"
    domain_overlay: Optional[str] = None


# =============================================================================
# Principles and Violations
# =============================================================================


@dataclass
class Violation:
    """Represents a single principle violation."""

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


class PolicyLLMProtocol(Protocol):
    """Protocol for the Policy LLM."""

    def generate(
        self,
        prompt: str,
        system: str = "",
        config: Any = None,
        prediction: dict[str, str] | None = None,
        model_override: str | None = None,
        overrides: Any = None,
    ) -> Any: ...
    def generate_messages(
        self,
        messages: list[dict[str, str]],
        config: Any = None,
        prediction: dict[str, str] | None = None,
        model_override: str | None = None,
        overrides: Any = None,
    ) -> Any: ...
    def rewrite(
        self,
        prompt: str,
        draft: str,
        guidance: str,
        system: str = "",
        config: Any = None,
        overrides: Any = None,
    ) -> Any: ...


class RiskEstimatorProtocol(Protocol):
    """Protocol for the Risk Estimator."""

    def estimate(self, prompt: str) -> Any: ...


class CriticProtocol(Protocol):
    """Protocol for the Constitutional Critic."""

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
        *,
        developer_contract: Any = None,
        conversation_history: Optional[List[Turn]] = None,
    ) -> Any: ...
    def quick_check(
        self,
        request: str,
        response: str,
        constitution: Any,
        pre_retrieved_principles: Optional[List[Any]] = None,
    ) -> Any: ...
    def critique_with_relevant_principles(
        self,
        request: str,
        response: str,
        domain: str | None = None,
        request_id: str = "",
        delib_context: Any = None,
        previous_violations: str = "",
        previous_guidance: str = "",
        *,
        developer_contract: Any = None,
        conversation_history: Optional[List[Turn]] = None,
    ) -> Any: ...


class SimulatorProtocol(Protocol):
    """Protocol for the Consequence Simulator."""

    def simulate(
        self,
        request: str,
        response: str,
        num_scenarios: int = 3,
        delib_context: Any = None,
        *,
        developer_contract: Any = None,
        conversation_history: Optional[List[Turn]] = None,
    ) -> Any: ...


class HindsightProtocol(Protocol):
    """Protocol for the Hindsight Evaluator."""

    def evaluate_response(self, request: str, response: str, consequences: Optional[List[Any]] = None) -> Any: ...
    def evaluate(
        self,
        request: str,
        response: str,
        consequences: List[Any],
        delib_context: Any = None,
        *,
        developer_contract: Any = None,
        conversation_history: Optional[List[Turn]] = None,
    ) -> Any: ...
    def aggregate(self, evaluations: List[Any]) -> Any: ...


class PerspectiveEnsembleProtocol(Protocol):
    """Protocol for the Perspective Ensemble."""

    def evaluate(
        self,
        request: str,
        response: str,
        perspectives: Optional[List[Any]] = None,
        delib_context: Any = None,
        *,
        developer_contract: Any = None,
        conversation_history: Optional[List[Turn]] = None,
    ) -> Any: ...
