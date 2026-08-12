"""
[problem D] Exactly one `RELEVANT_PRINCIPLES_RETRIEVED` per request: emitted by the
controller when the risk-owned retrieval succeeds; the runner emits it only on its
own fallback retrieval — never both. `RELEVANT_PRINCIPLES_REUSED` is still emitted
per critic reuse (best-effort, unaffected by this change). `retrieval_phase` labels
the single wave `"risk_routing"` (risk-owned) vs `"deliberation_retrieval"`
(fallback wave).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

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


class _CountingStore:
    def __init__(self, principles: list[_Principle]) -> None:
        self._principles = principles
        self.calls: list[dict[str, Any]] = []

    def get_relevant_principles(
        self, query: str, top_k: int = 10, domain: str | None = None, *, retrieval_phase: str = "risk_routing"
    ) -> list[_Principle]:
        self.calls.append({"query": query, "top_k": top_k, "domain": domain, "retrieval_phase": retrieval_phase})
        return list(self._principles)

    def get_debug_info(self) -> dict[str, Any]:
        return {"prefiltered_domains": ["core"], "prefilter_cache_status": "hit"}

    def retrieve(
        self, query: str, top_k: int = 10, domain: str | None = None, *, retrieval_phase: str = "risk_routing"
    ) -> Any:
        from moralstack.constitution.retrieval_result import PrincipleRetrievalResult

        self.calls.append({"query": query, "top_k": top_k, "domain": domain, "retrieval_phase": retrieval_phase})
        return PrincipleRetrievalResult(
            principles=tuple(self._principles),
            prefiltered_domains=("core",),
            debug_info={"prefiltered_domains": ["core"], "prefilter_cache_status": "hit"},
        )

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


def _make_risk_estimator(store: Any) -> LLMBasedRiskEstimator:
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    policy = _MiniPolicy(
        intent_json=_INTENT_SENSITIVE_JSON,
        signals_json=_signal_json(),
        operational_json=_OPERATIONAL_MODERATE_JSON,
    )
    return LLMBasedRiskEstimator(policy=policy, config=config, constitution_store=store)


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


def _build_critic() -> MagicMock:
    critic = MagicMock()
    critic.config = SimpleNamespace(top_k_principles=5)

    def _critique(request, response, constitution, principles=None, **_kw: Any) -> MagicMock:
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
    critic.critique_with_relevant_principles.side_effect = AssertionError("must not re-retrieve")
    return critic


def _run_with_event_capture(controller: OrchestrationController, prompt: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        events.append(kwargs)

    with (
        patch("moralstack.observability.emit_helpers.persist_orchestration_event", side_effect=_capture),
        patch("moralstack.orchestration.deliberation_runner.persist_orchestration_event", side_effect=_capture),
    ):
        controller.process(ProcessedRequest(prompt=prompt))
    return events


def test_exactly_one_relevant_principles_retrieved_per_request_happy_path() -> None:
    store = _CountingStore([_Principle("HARD.1", level="hard"), _Principle("SOFT.1", level="soft")])
    critic = _build_critic()
    controller = _make_controller(store, critic)

    events = _run_with_event_capture(controller, "Please discuss a sensitive but safe topic in detail.")

    retrieved = [e for e in events if e.get("event_type") == "RELEVANT_PRINCIPLES_RETRIEVED"]
    assert len(retrieved) == 1, f"expected exactly one RELEVANT_PRINCIPLES_RETRIEVED, got: {events}"
    assert retrieved[0]["payload"]["source"] == "controller"


def test_relevant_principles_reused_still_emitted_on_critic_reuse() -> None:
    store = _CountingStore([_Principle("HARD.1", level="hard"), _Principle("SOFT.1", level="soft")])
    critic = _build_critic()
    controller = _make_controller(store, critic)

    events = _run_with_event_capture(controller, "Please discuss a sensitive but safe topic in detail.")

    reused = [e for e in events if e.get("event_type") == "RELEVANT_PRINCIPLES_REUSED"]
    assert len(reused) == 1, f"expected exactly one RELEVANT_PRINCIPLES_REUSED, got: {events}"
    assert reused[0]["payload"]["reuse_target"] == "critic"


def test_retrieval_phase_label_risk_routing_on_single_wave() -> None:
    """The risk-owned single wave labels the store call under the default 'risk_routing' phase."""
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = _build_critic()
    controller = _make_controller(store, critic)

    controller.process(ProcessedRequest(prompt="Please discuss a sensitive but safe topic in detail."))

    assert len(store.calls) == 1
    assert store.calls[0]["retrieval_phase"] == "risk_routing"


def test_retrieval_phase_label_deliberation_retrieval_on_fallback() -> None:
    """When the risk-owned retrieval is unavailable, the runner's fallback wave labels
    its store call 'deliberation_retrieval'."""
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = _build_critic()

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
        risk_estimator=None,  # no risk-owned retrieval -> runner fallback
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=create_protector(),
        protected_system_prompt="You are a helpful assistant.",
    )

    controller.process(ProcessedRequest(prompt="Please discuss a sensitive but safe topic in detail."))

    assert len(store.calls) == 1
    assert store.calls[0]["retrieval_phase"] == "deliberation_retrieval"
