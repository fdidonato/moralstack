"""
[§5.6/§7 fail-safe] The store raises inside the risk-owned retrieval wave
(`estimator.py` `_get_principles_context` except-branch): `_estimate_risk` still
returns a usable `RiskEstimation` (never fails open, never raises into the
caller), with `retrieval_succeeded=False` / `retrieval_error` set; the controller
then passes `request_analysis=None` so deliberation retrieves independently via
its own (working) store.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest, RiskThresholds
from moralstack.utils.output_protection import create_protector


@dataclass
class _Principle:
    id: str
    title: str = "t"
    rule: str = "rule text"
    level: str = "hard"


class _RaisingStore:
    """Risk-side store: raises on every get_relevant_principles call."""

    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None) -> list[Any]:
        raise RuntimeError("simulated retrieval failure")

    def get_debug_info(self) -> dict[str, Any]:
        return {}


class _CountingStore:
    """Deliberation-side store: works fine, used to prove the independent fallback retrieval."""

    def __init__(self, principles: list[_Principle]) -> None:
        self._principles = principles
        self.calls: list[dict[str, Any]] = []

    def get_relevant_principles(
        self, query: str, top_k: int = 10, domain: str | None = None, *, retrieval_phase: str = "risk_routing"
    ) -> list[_Principle]:
        self.calls.append({"query": query, "top_k": top_k, "domain": domain, "retrieval_phase": retrieval_phase})
        return list(self._principles)

    def get_debug_info(self) -> dict[str, Any]:
        return {"prefiltered_domains": ["core"]}

    def get_constitution(self, domain: str | None = None) -> Any:
        return SimpleNamespace(principles=list(self._principles), active_overlay=None, constitution_corrupted=False)

    def has_excluded_domains(self) -> bool:
        return False


@dataclass
class _GenResult:
    text: str
    tokens_used: int = 10
    prompt_tokens: int = 5
    completion_tokens: int = 5

    def token_usage_json(self) -> str | None:
        return None


class _MiniPolicy:
    def __init__(self, intent_json: str, signals_json: str, operational_json: str) -> None:
        self.model = "test-model"
        self._responses = {
            "SEMANTIC INTENT JUDGE": intent_json,
            "HARM SIGNAL DETECTOR": signals_json,
            "OPERATIONAL RISK ASSESSOR": operational_json,
        }

    def generate(self, *, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> _GenResult:
        for marker, text in self._responses.items():
            if marker in system:
                return _GenResult(text=text)
        return _GenResult(text="{}")


def _signal_json() -> str:
    import json

    out = {sig.key: "no" for sig in signal_registry.signals.values()}
    out["domain_sensitivity"] = "HIGH"
    return json.dumps(out)


_INTENT_SENSITIVE_JSON = """{
  "rationale": "sensitive discussion, no operational intent",
  "detected_language": "en",
  "intent_to_harm": "no",
  "requested_instructions": "no",
  "intent_operational": false,
  "request_type": "sensitive_topic",
  "harm_type": "none",
  "intent_clarity": "HIGH",
  "stated_personal_bias": "no",
  "seeks_norm_circumvention": "no"
}"""

_OPERATIONAL_MODERATE_JSON = """{
  "rationale": "sensitive but not actionable",
  "operational_risk": "LOW",
  "risk_score": 0.5,
  "confidence": 0.8,
  "misuse_plausibility": "LOW",
  "actionability_risk": "LOW",
  "risk_policy_action": "DELIBERATE"
}"""


def test_estimator_get_principles_context_fails_safe_on_retrieval_error() -> None:
    """Unit-level: `_get_principles_context` never raises; returns a failed-but-safe result."""
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    est = LLMBasedRiskEstimator(policy=MagicMock(), config=config, constitution_store=_RaisingStore())

    result = est._get_principles_context("any prompt")  # must not raise

    assert result.formatted_context == ""
    assert result.retrieval_attempted is True
    assert result.retrieval_succeeded is False
    assert result.retrieval_error is not None


def test_full_estimate_never_raises_when_retrieval_raises() -> None:
    """`estimate()` (the full parallel mini-analysis) still returns a usable RiskEstimation."""
    risk_config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    risk_policy = _MiniPolicy(
        intent_json=_INTENT_SENSITIVE_JSON,
        signals_json=_signal_json(),
        operational_json=_OPERATIONAL_MODERATE_JSON,
    )
    risk_estimator = LLMBasedRiskEstimator(policy=risk_policy, config=risk_config, constitution_store=_RaisingStore())

    result = risk_estimator.estimate("some request")  # must not raise

    assert result.retrieval_succeeded is False
    assert result.relevant_principles == ()


def test_controller_deliberation_retrieves_independently_when_risk_retrieval_raises() -> None:
    """End-to-end: controller.process() does not crash; deliberation still retrieves via its own store."""
    risk_config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    risk_policy = _MiniPolicy(
        intent_json=_INTENT_SENSITIVE_JSON,
        signals_json=_signal_json(),
        operational_json=_OPERATIONAL_MODERATE_JSON,
    )
    risk_estimator = LLMBasedRiskEstimator(policy=risk_policy, config=risk_config, constitution_store=_RaisingStore())

    deliberation_store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = MagicMock()
    critic.config = SimpleNamespace(top_k_principles=5)
    seen: list[list[str]] = []

    def _critique(request, response, constitution, principles=None, **_kw: Any) -> MagicMock:
        seen.append([p.id for p in (principles or [])])
        return MagicMock(
            violations=[],
            severity_score=0.0,
            has_critical_violations=False,
            violated_hard=False,
            decision="PROCEED",
            revision_guidance="",
            raw_response="{}",
            parse_attempts=1,
            prompt="",
            system_prompt="",
        )

    critic.critique.side_effect = _critique

    config = OrchestratorConfig(
        risk_thresholds=RiskThresholds(),
        max_deliberation_cycles=1,
        timeout_ms=60_000,
        parallel_module_calls=False,
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
    )
    policy = MagicMock()
    policy.generate.return_value = _GenResult(text="A safe response.")
    controller = OrchestrationController(
        config=config,
        policy=policy,
        risk_estimator=risk_estimator,
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=deliberation_store,
        output_protector=create_protector(),
        protected_system_prompt="You are a helpful assistant.",
    )

    request = ProcessedRequest(prompt="Please discuss a sensitive but safe topic in detail.")
    result = controller.process(request)  # must not raise / fail open

    assert result is not None
    assert len(deliberation_store.calls) >= 1
    assert seen and seen[0] == ["HARD.1"]
