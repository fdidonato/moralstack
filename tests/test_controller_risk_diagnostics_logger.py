"""_estimate_risk: the diagnostics logger is best-effort (PROJECT_SPEC §5.6) and the error text is guarded."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.models.risk.categories import RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController, RiskEstimationError
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest, RiskThresholds


class _Estimator:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.exc = exc

    def estimate(self, prompt: str, **_kw: Any) -> RiskEstimation:
        if self.exc is not None:
            raise self.exc
        return RiskEstimation(score=0.1, confidence=0.9, risk_category=RiskCategory.BENIGN)


class _RaisingLogger:
    def log_call(self, **_kw: Any) -> None:
        raise RuntimeError("diagnostics logger down")


class _BadStr(Exception):
    def __str__(self) -> str:
        raise ValueError("unprintable")


def _controller(estimator: _Estimator, logger: Any = None) -> OrchestrationController:
    controller = OrchestrationController(
        config=OrchestratorConfig(risk_thresholds=RiskThresholds()),
        policy=MagicMock(),
        risk_estimator=estimator,
        critic=MagicMock(),
        simulator=MagicMock(),
        hindsight=MagicMock(),
        perspectives=MagicMock(),
        constitution_store=MagicMock(),
        output_protector=MagicMock(),
        protected_system_prompt="protected",
    )
    controller.logger = logger
    return controller


def test_raising_diagnostics_logger_does_not_fail_a_completed_estimation(caplog: pytest.LogCaptureFixture) -> None:
    controller = _controller(_Estimator(), logger=_RaisingLogger())
    with caplog.at_level(logging.WARNING, logger="moralstack.orchestration.controller"):
        result = controller._estimate_risk(ProcessedRequest(prompt="hello"))
    assert result.risk_category == RiskCategory.BENIGN
    warnings = [r for r in caplog.records if "risk diagnostics log_call failed" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None
    assert "diagnostics logger down" not in warnings[0].getMessage()


def test_unprintable_estimator_error_still_raises_risk_estimation_error() -> None:
    controller = _controller(_Estimator(exc=_BadStr()))
    with pytest.raises(RiskEstimationError, match="Risk estimation failed: _BadStr"):
        controller._estimate_risk(ProcessedRequest(prompt="hello"))
