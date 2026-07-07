"""
[Gap 3] Integration: `controller.process(...)` retrieves constitution principles
EXACTLY ONCE per request on the happy path (risk-owned, single upstream wave),
and deliberation reuses the risk-retrieved principles instead of re-retrieving.

Offline/deterministic: the constitution store and policy are doubles; no network.
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
    level: str = "soft"


class _CountingStore:
    """Constitution store double; counts get_relevant_principles calls."""

    def __init__(self, principles: list[_Principle] | None = None) -> None:
        self._principles = principles if principles is not None else [_Principle("SOFT.1", level="soft")]
        self.calls: list[dict[str, Any]] = []

    def get_relevant_principles(
        self, query: str, top_k: int = 10, domain: str | None = None, *, retrieval_phase: str = "risk_routing"
    ) -> list[_Principle]:
        self.calls.append({"query": query, "top_k": top_k, "domain": domain, "retrieval_phase": retrieval_phase})
        return list(self._principles)

    def get_debug_info(self) -> dict[str, Any]:
        return {"prefiltered_domains": ["core"], "prefilter_cache_status": "hit"}

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
    """Policy double for the 3 risk mini-estimators: routes by system-prompt substring."""

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


def _signal_json(**overrides: str) -> str:
    import json

    out = {sig.key: "no" for sig in signal_registry.signals.values()}
    out["domain_sensitivity"] = "HIGH"
    out.update(overrides)
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


def _make_risk_estimator(store: _CountingStore) -> LLMBasedRiskEstimator:
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    policy = _MiniPolicy(
        intent_json=_INTENT_SENSITIVE_JSON,
        signals_json=_signal_json(),
        operational_json=_OPERATIONAL_MODERATE_JSON,
    )
    return LLMBasedRiskEstimator(policy=policy, config=config, constitution_store=store)


def _mock_critique_report(*, principles_seen: list[str]) -> MagicMock:
    report = MagicMock(
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
        tokens_used=0,
        prompt_tokens=None,
        completion_tokens=None,
        token_usage_source=None,
        enumerated_output_gate_applied=False,
    )
    return report


def _make_controller(store: _CountingStore, critic: MagicMock) -> OrchestrationController:
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
    return OrchestrationController(
        config=config,
        policy=policy,
        risk_estimator=_make_risk_estimator(store),
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=create_protector(),
        protected_system_prompt="You are a helpful assistant.",
    )


def _build_critic(seen_principles: list[list[str]]) -> MagicMock:
    critic = MagicMock()
    critic.config = SimpleNamespace(top_k_principles=5)

    def _critique(request, response, constitution, principles=None, **_kw: Any) -> MagicMock:
        seen_principles.append([p.id for p in (principles or [])])
        return _mock_critique_report(principles_seen=seen_principles[-1])

    critic.critique.side_effect = _critique
    critic.critique_with_relevant_principles.side_effect = AssertionError(
        "critique_with_relevant_principles must not be called — reuse must not re-retrieve"
    )
    return critic


def test_exactly_one_store_retrieval_on_happy_path() -> None:
    """One get_relevant_principles call total (risk + deliberation combined)."""
    store = _CountingStore([_Principle("HARD.1", level="hard"), _Principle("SOFT.1", level="soft")])
    seen: list[list[str]] = []
    critic = _build_critic(seen)
    controller = _make_controller(store, critic)

    request = ProcessedRequest(prompt="Please discuss a sensitive but safe topic in detail.")
    result = controller.process(request)

    assert result is not None
    assert len(store.calls) == 1, f"expected exactly one store retrieval, got {len(store.calls)}: {store.calls}"


def test_deliberation_reuses_risk_retrieved_principles_by_identity() -> None:
    """The critic sees the same principle IDs the risk-owned retrieval returned."""
    store = _CountingStore([_Principle("HARD.1", level="hard"), _Principle("SOFT.1", level="soft")])
    seen: list[list[str]] = []
    critic = _build_critic(seen)
    controller = _make_controller(store, critic)

    request = ProcessedRequest(prompt="Please discuss a sensitive but safe topic in detail.")
    controller.process(request)

    assert seen, "critic.critique was never called"
    assert set(seen[0]) == {"HARD.1", "SOFT.1"}
    critic.critique_with_relevant_principles.assert_not_called()


def test_successful_empty_retrieval_is_authoritative_no_reretrieval() -> None:
    """
    Store returns [] (successful, empty). Risk records retrieval_succeeded=True;
    the controller passes an EMPTY (but authoritative) RequestAnalysisContext;
    exactly one store call total; critic does NOT re-retrieve (v4.1 blocker-1 fix).
    """
    store = _CountingStore([])
    seen: list[list[str]] = []
    critic = _build_critic(seen)
    controller = _make_controller(store, critic)

    request = ProcessedRequest(prompt="Please discuss a sensitive but safe topic in detail.")
    controller.process(request)

    assert len(store.calls) == 1
    critic.critique_with_relevant_principles.assert_not_called()
    # critic.critique was still invoked (authoritative empty set), even though it
    # will short-circuit internally to an empty_skipped report given zero principles.
    assert critic.critique.called
