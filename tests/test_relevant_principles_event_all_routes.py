"""
[FIX-PASS] Exactly one `RELEVANT_PRINCIPLES_RETRIEVED` per request on EVERY route
that can return after a successful risk-owned retrieval, not only the deliberative
and FAST_PATH routes already covered by `test_observability_relevant_principles_single_emit.py`
and `test_fast_path_single_retrieval.py`.

Covers: COMPLIANCE_FAST_PATH, benign fast path, SAFE_COMPLETE, hard-signal REFUSE.
Offline/deterministic: constitution store, critic, and policy are doubles; no network.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.compliance import StructuredRule
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk.categories import RiskPolicyAction
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.types import Decision, OrchestratorConfig, ProcessedRequest, RiskThresholds
from moralstack.utils.output_protection import create_protector


@dataclass
class _Principle:
    id: str
    title: str = "t"
    rule: str = "rule text"
    level: str = "soft"


class _CountingStore:
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
    out["domain_sensitivity"] = "LOW"
    out.update(overrides)
    return json.dumps(out)


_INTENT_BENIGN_JSON = """{
  "rationale": "benign factual query",
  "detected_language": "en",
  "intent_to_harm": "no",
  "requested_instructions": "no",
  "intent_operational": false,
  "request_type": "factual_query",
  "harm_type": "none",
  "intent_clarity": "HIGH",
  "stated_personal_bias": "no",
  "seeks_norm_circumvention": "no"
}"""

_OPERATIONAL_BENIGN_JSON = """{
  "rationale": "benign",
  "operational_risk": "NONE",
  "risk_score": 0.1,
  "confidence": 0.9,
  "misuse_plausibility": "LOW",
  "actionability_risk": "LOW",
  "risk_policy_action": "ALLOW"
}"""


def _make_risk_estimator(store: _CountingStore) -> LLMBasedRiskEstimator:
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    policy = _MiniPolicy(
        intent_json=_INTENT_BENIGN_JSON,
        signals_json=_signal_json(),
        operational_json=_OPERATIONAL_BENIGN_JSON,
    )
    return LLMBasedRiskEstimator(policy=policy, config=config, constitution_store=store)


def _make_controller(store: _CountingStore, critic: Any) -> OrchestrationController:
    """Matches `test_fast_path_single_retrieval.py::_make_controller` (proven pattern:
    `enable_speculative_generation` defaults True, `_run_speculative_overlap` runs the
    same real risk-owned retrieval)."""
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
    policy.generate.return_value = _GenResult(text="A safe response, long enough to not hit the refusal fallback path.")
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


def _capture_events() -> tuple[list[dict[str, Any]], Any]:
    events: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        events.append(kwargs)

    return events, _capture


def _retrieved(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("event_type") == "RELEVANT_PRINCIPLES_RETRIEVED"]


def _ping_contract() -> DeveloperContract:
    rule = StructuredRule(rule_id="r1", trigger_pattern="PING", action_payload="PONG")
    return replace(
        DeveloperContract.from_text("if user says PING reply PONG"),
        structured_rules=(rule,),
    )


def test_relevant_principles_retrieved_emitted_on_compliance_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """COMPLIANCE_FAST_PATH (DCCL structured rule MATCH) still emits the single
    controller-level event, even though it never touches deliberation/fast_path."""
    monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    controller = _make_controller(store, MagicMock())
    req = ProcessedRequest(
        prompt="PING",
        request_id="req-compliance-principles",
        developer_contract=_ping_contract(),
    )
    events, capture = _capture_events()

    with (
        patch.object(controller, "_nonblocking_speculative_draft", return_value="PONG"),
        patch("moralstack.observability.emit_helpers.persist_orchestration_event", side_effect=capture),
        patch("moralstack.orchestration.deliberation_runner.persist_orchestration_event", side_effect=capture),
    ):
        result = controller.process(req)

    assert result.path == "COMPLIANCE_FAST_PATH"
    retrieved = _retrieved(events)
    assert len(retrieved) == 1, f"expected exactly one RELEVANT_PRINCIPLES_RETRIEVED, got: {events}"
    assert retrieved[0]["payload"]["source"] == "controller"


def test_relevant_principles_retrieved_emitted_on_benign_fast_path() -> None:
    """benign route (`_route_benign`) is a non-deliberative, non-FAST_PATH early
    return; the event must still be observable exactly once."""
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    controller = _make_controller(store, MagicMock())
    events, capture = _capture_events()

    # Force the "benign" route directly (this test targets the observability wiring
    # of that route, not routing arithmetic — same technique as
    # test_fast_path_single_retrieval.py::test_quick_check_self_retrieves_when_no_context_supplied).
    with (
        patch(
            "moralstack.orchestration.controller.get_route",
            return_value=("benign", False, RiskPolicyAction.ALLOW),
        ),
        patch("moralstack.observability.emit_helpers.persist_orchestration_event", side_effect=capture),
    ):
        result = controller.process(ProcessedRequest(prompt="What is the weather like today?"))

    assert result is not None
    retrieved = _retrieved(events)
    assert len(retrieved) == 1, f"expected exactly one RELEVANT_PRINCIPLES_RETRIEVED, got: {events}"
    assert retrieved[0]["payload"]["source"] == "controller"


def test_relevant_principles_retrieved_emitted_on_safe_complete() -> None:
    """SAFE_COMPLETE (`_route_safe_complete`) is a non-deliberative early return;
    the event must still be observable exactly once."""
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    controller = _make_controller(store, MagicMock())
    decision = Decision(
        final_action="SAFE_COMPLETE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )
    expl = DecisionExplanation(final_action="SAFE_COMPLETE", risk_category="benign")
    events, capture = _capture_events()

    # Same technique as test_orchestrator.py::test_dispatch_to_route_safe_complete:
    # patch decide_action + force overlay_sensitive=True so apply_safe_complete_gating
    # does not downgrade the decision back to NORMAL_COMPLETE.
    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(decision, expl)),
        patch("moralstack.orchestration.controller.is_overlay_sensitive", return_value=True),
        patch("moralstack.observability.emit_helpers.persist_orchestration_event", side_effect=capture),
    ):
        result = controller.process(ProcessedRequest(prompt="What is the weather like today?"))

    assert result is not None
    retrieved = _retrieved(events)
    assert len(retrieved) == 1, f"expected exactly one RELEVANT_PRINCIPLES_RETRIEVED, got: {events}"
    assert retrieved[0]["payload"]["source"] == "controller"


def test_relevant_principles_retrieved_emitted_on_hard_signal_refuse() -> None:
    """Hard-signal REFUSE (`_route_refuse`, bypassing the deliberative loop) is a
    non-deliberative early return; the event must still be observable exactly once."""
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    controller = _make_controller(store, MagicMock())
    decision = Decision(
        final_action="REFUSE",
        path="FAST_PATH",
        intent_clarity="LOW",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=[],
        hard_violations=["CORE.TEST.1"],
        risk_signals=[],
    )
    expl = DecisionExplanation(final_action="REFUSE", risk_category="clearly_harmful")
    events, capture = _capture_events()

    # Same technique as test_orchestrator.py::test_dispatch_to_route_refuse: patch
    # decide_action with non-empty hard_violations so is_hard_signal_refuse is True
    # and get_route deterministically resolves to "refuse" (real, unpatched get_route).
    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(decision, expl)),
        patch("moralstack.observability.emit_helpers.persist_orchestration_event", side_effect=capture),
    ):
        result = controller.process(ProcessedRequest(prompt="Some request."))

    assert result is not None
    retrieved = _retrieved(events)
    assert len(retrieved) == 1, f"expected exactly one RELEVANT_PRINCIPLES_RETRIEVED, got: {events}"
    assert retrieved[0]["payload"]["source"] == "controller"
