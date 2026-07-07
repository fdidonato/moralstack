"""
[Gap 4] Retrieval query policy in `_estimate_risk`: RAW prompt when there is no
meaningful developer-contract text AND no conversation history; ENRICHED
(contract + recent history + prompt) otherwise. OR semantics (not AND) — a
contract alone, history alone, or both must all enrich; only the true
no-context path stays RAW (§5.4: byte-identical single-turn retrieval query).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from moralstack.core.types import Turn
from moralstack.models.risk import RiskCategory, RiskEstimation
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest, RiskThresholds


class _RecordingRiskEstimator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def estimate(self, prompt: str, **kwargs: Any) -> RiskEstimation:
        self.calls.append({"prompt": prompt, **kwargs})
        return RiskEstimation(score=0.1, confidence=0.9, risk_category=RiskCategory.BENIGN)


def _controller(risk_estimator: _RecordingRiskEstimator) -> OrchestrationController:
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


def test_query_is_raw_when_no_contract_and_no_history():
    risk_estimator = _RecordingRiskEstimator()
    controller = _controller(risk_estimator)
    request = ProcessedRequest(prompt="standalone question")

    controller._estimate_risk(request)

    assert risk_estimator.calls[0]["retrieval_query"] == "standalone question"


def test_query_is_enriched_when_contract_present_no_history():
    risk_estimator = _RecordingRiskEstimator()
    controller = _controller(risk_estimator)
    request = ProcessedRequest(
        prompt="standalone question",
        developer_contract=DeveloperContract(raw_text="You are a helpful bot."),
    )

    controller._estimate_risk(request)

    query = risk_estimator.calls[0]["retrieval_query"]
    assert query != "standalone question"
    assert "You are a helpful bot." in query
    assert "standalone question" in query


def test_query_is_enriched_when_history_present_no_contract():
    risk_estimator = _RecordingRiskEstimator()
    controller = _controller(risk_estimator)
    request = ProcessedRequest(
        prompt="follow-up question",
        conversation_history=[
            Turn(role="user", content="prior user turn"),
            Turn(role="assistant", content="prior assistant reply"),
        ],
    )

    controller._estimate_risk(request)

    query = risk_estimator.calls[0]["retrieval_query"]
    assert query != "follow-up question"
    assert "prior user turn" in query
    assert "follow-up question" in query


def test_query_is_enriched_when_both_contract_and_history_present():
    risk_estimator = _RecordingRiskEstimator()
    controller = _controller(risk_estimator)
    request = ProcessedRequest(
        prompt="follow-up question",
        developer_contract=DeveloperContract(raw_text="You are a helpful bot."),
        conversation_history=[Turn(role="user", content="prior user turn")],
    )

    controller._estimate_risk(request)

    query = risk_estimator.calls[0]["retrieval_query"]
    assert "You are a helpful bot." in query
    assert "prior user turn" in query
    assert "follow-up question" in query


def test_query_stays_raw_when_contract_present_but_empty_text():
    """A present-but-empty contract (falsy raw_text) counts as 'no contract'."""
    risk_estimator = _RecordingRiskEstimator()
    controller = _controller(risk_estimator)
    request = ProcessedRequest(
        prompt="standalone question",
        developer_contract=DeveloperContract(raw_text=""),
    )

    controller._estimate_risk(request)

    assert risk_estimator.calls[0]["retrieval_query"] == "standalone question"


def test_query_stays_raw_when_history_is_empty_list():
    risk_estimator = _RecordingRiskEstimator()
    controller = _controller(risk_estimator)
    request = ProcessedRequest(prompt="standalone question", conversation_history=[])

    controller._estimate_risk(request)

    assert risk_estimator.calls[0]["retrieval_query"] == "standalone question"
