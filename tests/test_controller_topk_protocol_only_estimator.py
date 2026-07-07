"""
[v4.1 blocker-3 fix] The controller computes the unified retrieval top_k with a
PROTOCOL-ONLY risk estimator (implements only `estimate(prompt)` per
`RiskEstimatorProtocol`, no private `_top_k`) without raising `AttributeError`,
falling back to `DEFAULT_RISK_TOP_K`. Pattern mirrors
`tests/test_orchestrator.py:186-194` (`MockRiskEstimator`).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from moralstack.models.risk.categories import RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import DEFAULT_RISK_TOP_K, OrchestrationController
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest, RiskThresholds


class _ProtocolOnlyRiskEstimator:
    """Implements only `estimate(prompt)` — no `_top_k`, no other private attrs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def estimate(self, prompt: str, **kwargs: Any) -> RiskEstimation:
        self.calls.append({"prompt": prompt, **kwargs})
        return RiskEstimation(score=0.1, confidence=0.9, risk_category=RiskCategory.BENIGN)

    def quick_estimate(self, prompt: str) -> Any:
        return None


def _controller(risk_estimator: _ProtocolOnlyRiskEstimator) -> OrchestrationController:
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


def test_estimate_risk_does_not_raise_with_protocol_only_estimator() -> None:
    risk_estimator = _ProtocolOnlyRiskEstimator()
    controller = _controller(risk_estimator)
    request = ProcessedRequest(prompt="hello world")

    result = controller._estimate_risk(request)  # must not raise AttributeError

    assert result.risk_category == RiskCategory.BENIGN
    assert len(risk_estimator.calls) == 1


def test_unified_topk_falls_back_to_default_risk_top_k_without_top_k_attr() -> None:
    risk_estimator = _ProtocolOnlyRiskEstimator()
    controller = _controller(risk_estimator)
    request = ProcessedRequest(prompt="hello world")

    controller._estimate_risk(request)

    call = risk_estimator.calls[0]
    assert call["retrieval_top_k"] >= DEFAULT_RISK_TOP_K
