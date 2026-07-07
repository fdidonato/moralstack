"""
[v4.2 blocker] FAST_PATH (and its quick-check-failed deliberative fallback) must
also reuse the risk-owned single-wave retrieval — "exactly one retrieval per
request" holds GLOBALLY across routes, not only the deliberative path.

Offline/deterministic: constitution store, critic, and policy are doubles; no
network. `decide_action` is patched (established pattern, see
`test_orchestrator.py::TestControllerRouteDispatching`) only to select the
FAST_PATH route deterministically; the risk-owned retrieval itself is real.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk.categories import RiskPolicyAction
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.registry import registry as signal_registry
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
    out["domain_sensitivity"] = "LOW"
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


def _fast_path_decision() -> Decision:
    # final_action=NORMAL_COMPLETE + path=DELIBERATIVE_PATH dispatches to
    # _route_fast_path when the (real) risk is benign — see
    # test_orchestrator.py::test_dispatch_to_route_fast_path for the same technique.
    return Decision(
        final_action="NORMAL_COMPLETE",
        path="DELIBERATIVE_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )


def _patch_decide_action():
    decision = _fast_path_decision()
    expl = DecisionExplanation(final_action="NORMAL_COMPLETE", risk_category="benign")
    return patch("moralstack.orchestration.controller.decide_action", return_value=(decision, expl))


def _quick_check_result(passed: bool) -> MagicMock:
    return MagicMock(passed=passed, critical_violation=None, check_time_ms=1.0)


def test_fast_path_quick_check_reuses_risk_context_no_reretrieval() -> None:
    """FAST_PATH: total store retrieval count == 1; quick_check filters shared list to
    HARD (does not call the store) when quick_check passes."""
    store = _CountingStore([_Principle("HARD.1", level="hard"), _Principle("SOFT.1", level="soft")])
    critic = MagicMock()
    seen_calls: list[list[Any] | None] = []

    def _quick_check(request, response, constitution, pre_retrieved_principles=None):
        seen_calls.append(pre_retrieved_principles)
        return _quick_check_result(True)

    critic.quick_check.side_effect = _quick_check
    controller = _make_controller(store, critic)

    with _patch_decide_action():
        result = controller.process(ProcessedRequest(prompt="What is the weather like today?"))

    assert result is not None
    assert len(store.calls) == 1
    assert seen_calls, "quick_check was never called"
    ids = [p.id for p in (seen_calls[0] or [])]
    # quick_check received the full shared list (it filters to HARD internally).
    assert "HARD.1" in ids


def test_fast_path_quick_check_failed_fallback_reuses_context() -> None:
    """Quick-check fails -> deliberative fallback reuses the risk-owned context; still 1 total retrieval."""
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = MagicMock()
    critic.config = SimpleNamespace(top_k_principles=5)
    critic.quick_check.return_value = _quick_check_result(False)
    critique_calls: list[list[str]] = []

    def _critique(request, response, constitution, principles=None, **_kw: Any) -> MagicMock:
        critique_calls.append([p.id for p in (principles or [])])
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
    controller = _make_controller(store, critic)

    with _patch_decide_action():
        controller.process(ProcessedRequest(prompt="What is the weather like today?"))

    assert len(store.calls) == 1, f"expected exactly one store retrieval, got {len(store.calls)}: {store.calls}"
    assert critique_calls, "quick-check-failed fallback did not reach the critic"
    assert "HARD.1" in critique_calls[0]


def test_quick_check_self_retrieves_when_no_context_supplied() -> None:
    """Fail-safe: quick_check self-retrieves when no shared context is supplied (no risk estimator)."""
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = MagicMock()
    critic.quick_check.return_value = _quick_check_result(True)

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
        risk_estimator=None,  # no risk estimator -> retrieval_succeeded is always False
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=create_protector(),
        protected_system_prompt="You are a helpful assistant.",
    )

    # No risk estimator -> the default RiskEstimation (score=0.5) does not naturally
    # route to fast_path via get_route's score<low condition; force the route
    # directly (this test targets the quick_check fallback, not routing arithmetic).
    with (
        _patch_decide_action(),
        patch(
            "moralstack.orchestration.controller.get_route",
            return_value=("fast_path", False, RiskPolicyAction.ALLOW),
        ),
    ):
        controller.process(ProcessedRequest(prompt="What is the weather like today?"))

    # quick_check received no shared context, so run_fast_path called it with
    # pre_retrieved_principles=None -> quick_check self-retrieves via the store.
    critic.quick_check.assert_called_once()
    call_args = critic.quick_check.call_args.args
    assert call_args[-1] is None


def test_relevant_principles_retrieved_emitted_on_fast_path() -> None:
    """Single RELEVANT_PRINCIPLES_RETRIEVED event emitted at controller level for a
    non-deliberative (FAST_PATH) route — observable even though it never enters
    run_deliberative_path."""
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = MagicMock()
    critic.quick_check.return_value = _quick_check_result(True)
    controller = _make_controller(store, critic)

    events: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        events.append(kwargs)

    with (
        _patch_decide_action(),
        patch("moralstack.observability.emit_helpers.persist_orchestration_event", side_effect=_capture),
    ):
        controller.process(ProcessedRequest(prompt="What is the weather like today?"))

    retrieved_events = [e for e in events if e.get("event_type") == "RELEVANT_PRINCIPLES_RETRIEVED"]
    assert len(retrieved_events) == 1, f"expected exactly one RELEVANT_PRINCIPLES_RETRIEVED event, got {events}"
    assert retrieved_events[0]["payload"]["source"] == "controller"


def test_controller_success_does_not_call_runner_retrieval_event() -> None:
    """
    A controller-supplied successful risk context must NOT trigger the runner's
    own `_record_retrieval_start_and_event` (which would double-emit
    RELEVANT_PRINCIPLES_RETRIEVED) — proving no double-emit on the fast-path route.
    """
    from moralstack.orchestration.deliberation_runner import DeliberationRunner

    store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = MagicMock()
    critic.quick_check.return_value = _quick_check_result(True)
    controller = _make_controller(store, critic)

    def _boom(self: Any, **_kw: Any) -> None:
        raise AssertionError("runner must not emit its own retrieval event when the controller supplied one")

    with (
        _patch_decide_action(),
        patch.object(DeliberationRunner, "_record_retrieval_start_and_event", _boom),
    ):
        result = controller.process(ProcessedRequest(prompt="What is the weather like today?"))

    assert result is not None


def test_quick_check_hard_fallback_when_shared_list_has_no_hard() -> None:
    """
    Shared list has zero HARD principles -> quick_check falls back to the
    constitution's own HARD constraints instead of skipping the check
    (`critic_module.py:647-649` preserved fallback). Asserting `policy.generate`
    was actually called (not just `passed=True`) distinguishes "fell back and ran
    the check" from "skipped the check" (both would report `passed=True`).
    """
    from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic

    soft_only = [_Principle("SOFT.1", level="soft")]
    hard_constitution_principle = _Principle("HARD.CONST.1", level="hard")

    policy = MagicMock()
    policy.generate.return_value = _GenResult(text='{"violated": false}')
    critic = LLMConstitutionalCritic(policy=policy, store=None)

    constitution = SimpleNamespace(principles=[hard_constitution_principle])

    result = critic.quick_check("prompt", "response", constitution, soft_only)

    assert result.passed is True
    policy.generate.assert_called_once()


def test_compliance_fast_path_reuses_or_adds_no_retrieval() -> None:
    """
    COMPLIANCE_FAST_PATH (`run_benign_fast_path`) consumes no principles, so it must
    add no retrieval call regardless of what the risk-owned wave produced.
    """
    from moralstack.orchestration.deliberation_runner import DeliberationRunner
    from moralstack.orchestration.types import Decision, DeliberationDependencies

    store = _CountingStore([_Principle("HARD.1", level="hard")])
    policy = MagicMock()
    policy.generate.return_value = _GenResult(text="A compliant response.")
    deps = DeliberationDependencies(
        policy=policy,
        critic=MagicMock(),
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=create_protector(),
    )
    config = OrchestratorConfig(risk_thresholds=RiskThresholds())
    runner = DeliberationRunner(config, deps, protected_system_prompt="sys", logger=None, assembler=MagicMock())

    from moralstack.models.risk.categories import RiskCategory
    from moralstack.models.risk.schema import RiskEstimation

    risk_estimation = RiskEstimation(score=0.1, confidence=0.9, risk_category=RiskCategory.BENIGN)
    decision = Decision(
        final_action="NORMAL_COMPLETE",
        path="COMPLIANCE_FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )

    runner.run_benign_fast_path(
        request=ProcessedRequest(prompt="AGENT"),
        risk_estimation=risk_estimation,
        start_time=0.0,
        decision=decision,
    )

    assert store.calls == []
