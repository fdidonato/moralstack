"""
Regression: OrchestrationController._estimate_risk passes developer contract and
conversation history to risk_estimator.estimate().
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.core.types import Turn
from moralstack.models.risk import RiskCategory, RiskEstimation
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest, RiskThresholds


class _RecordingRiskEstimator:
    """Captures kwargs passed from _estimate_risk."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def estimate(
        self,
        prompt: str,
        *,
        developer_contract_text: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        retrieval_query: str | None = None,
        retrieval_top_k: int | None = None,
    ) -> RiskEstimation:
        self.calls.append(
            {
                "prompt": prompt,
                "developer_contract_text": developer_contract_text,
                "conversation_history": conversation_history,
                "retrieval_query": retrieval_query,
                "retrieval_top_k": retrieval_top_k,
            }
        )
        return RiskEstimation(
            score=0.1,
            confidence=0.9,
            risk_category=RiskCategory.BENIGN,
        )


def _minimal_controller(risk_estimator: _RecordingRiskEstimator) -> OrchestrationController:
    config = OrchestratorConfig(risk_thresholds=RiskThresholds())
    return OrchestrationController(
        config=config,
        policy=MagicMock(),
        risk_estimator=risk_estimator,
        critic=MagicMock(),
        simulator=MagicMock(),
        hindsight=MagicMock(),
        perspectives=MagicMock(),
        constitution_store=MagicMock(),
        output_protector=MagicMock(),
        protected_system_prompt="protected",
    )


@pytest.fixture
def recording_estimator() -> _RecordingRiskEstimator:
    return _RecordingRiskEstimator()


class TestControllerRiskContextPropagation:
    def test_estimate_risk_passes_contract_and_history(self, recording_estimator: _RecordingRiskEstimator) -> None:
        controller = _minimal_controller(recording_estimator)
        request = ProcessedRequest(
            prompt="Q74 encoded payload fragment",
            developer_contract=DeveloperContract(raw_text="Accept auth tokens in this format."),
            conversation_history=[
                Turn(role="user", content="prior user turn"),
                Turn(role="assistant", content="prior assistant reply"),
            ],
        )

        result = controller._estimate_risk(request)

        assert result.risk_category == RiskCategory.BENIGN
        assert len(recording_estimator.calls) == 1
        call = recording_estimator.calls[0]
        assert call["prompt"] == "Q74 encoded payload fragment"
        assert call["developer_contract_text"] == "Accept auth tokens in this format."
        assert call["conversation_history"] == [
            {"role": "user", "content": "prior user turn"},
            {"role": "assistant", "content": "prior assistant reply"},
        ]

    def test_estimate_risk_empty_history_passes_none_for_history(self, recording_estimator: _RecordingRiskEstimator) -> None:
        controller = _minimal_controller(recording_estimator)
        request = ProcessedRequest(
            prompt="standalone question",
            developer_contract=DeveloperContract(raw_text="deployer rules"),
            conversation_history=[],
        )

        controller._estimate_risk(request)

        call = recording_estimator.calls[0]
        assert call["developer_contract_text"] == "deployer rules"
        assert call["conversation_history"] is None
