"""Tests for billable_provider_call discriminator in deliberation_runner."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from moralstack.models.risk import RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.deliberation_runner import (
    DeliberationRunner,
    _module_model,
    _policy_llm_model_for_action,
)
from moralstack.orchestration.types import Decision, DeliberationState, OrchestratorConfig, ProcessedRequest


@pytest.fixture
def capture_persist(monkeypatch):
    calls: list[dict] = []

    def _record(_logger, _diag, persist_kwargs):
        if persist_kwargs is not None:
            calls.append(persist_kwargs)

    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", _record)
    return calls


def _risk(score: float = 0.1) -> RiskEstimation:
    return RiskEstimation(score=score, confidence=0.9, risk_category=RiskCategory.BENIGN)


def _build_runner(**deps_overrides) -> DeliberationRunner:
    config = OrchestratorConfig()
    deps = SimpleNamespace(
        policy=MagicMock(model="gpt-policy"),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=MagicMock(),
    )
    for key, value in deps_overrides.items():
        setattr(deps, key, value)
    protector = MagicMock()
    protector.validate.return_value = SimpleNamespace(had_leakage=False, cleaned="safe text")
    deps.output_protector = protector
    return DeliberationRunner(config, deps, "protected", None, MagicMock())


def test_fast_path_speculative_reuse_marked_non_billable(capture_persist):
    runner = _build_runner()
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    risk = _risk()
    decision = Decision(
        final_action="NORMAL_COMPLETE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )
    runner.run_fast_path(
        request,
        risk,
        start_time=0.0,
        decision=decision,
        speculative_draft="cached draft text",
    )
    speculative = [c for c in capture_persist if "speculative-reuse" in (c.get("action") or "")]
    assert len(speculative) == 1
    assert speculative[0]["billable_provider_call"] is False


def test_fast_path_leakage_detected_marked_non_billable(capture_persist, monkeypatch):
    runner = _build_runner()
    runner._output_protector.validate.return_value = SimpleNamespace(
        had_leakage=True,
        cleaned="cleaned",
        leakage_type="test",
    )

    @dataclass
    class GenResult:
        text: str = "leaky"
        tokens_used: int = 10

        def token_usage_json(self) -> str | None:
            return None

    runner.policy.generate.return_value = GenResult()
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    risk = _risk()
    decision = Decision(
        final_action="NORMAL_COMPLETE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )
    runner.run_fast_path(request, risk, start_time=0.0, decision=decision)
    leakage = [c for c in capture_persist if c.get("module") == "output_protection"]
    assert len(leakage) == 1
    assert leakage[0]["billable_provider_call"] is False


def test_speculative_reuse_call_marked_non_billable(capture_persist):
    runner = _build_runner()
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    state = DeliberationState(cycle=1, draft_response="existing draft")
    risk = _risk()
    runner._generate_or_revise(state, request, risk_estimation=risk)
    reuse = [c for c in capture_persist if c.get("call_kind") == "speculative_reuse"]
    assert len(reuse) == 1
    assert reuse[0]["billable_provider_call"] is False


def test_skipped_empty_guidance_rewrite_marked_non_billable(capture_persist, monkeypatch):
    runner = _build_runner()
    monkeypatch.setattr(
        "moralstack.orchestration.deliberation_runner.build_aggregated_guidance",
        lambda *_a, **_k: "",
    )
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    state = DeliberationState(cycle=2, draft_response="draft")
    risk = _risk(0.5)
    runner._generate_or_revise(state, request, risk_estimation=risk)
    skipped = [c for c in capture_persist if "SKIPPED_EMPTY_GUIDANCE" in (c.get("action") or "")]
    assert len(skipped) == 1
    assert skipped[0]["billable_provider_call"] is False


def test_leakage_detected_output_protection_marked_non_billable(capture_persist, monkeypatch):
    runner = _build_runner()
    runner._output_protector.validate.return_value = SimpleNamespace(
        had_leakage=True,
        cleaned="cleaned",
        leakage_type="test",
    )

    @dataclass
    class GenResult:
        text: str = "leaky"
        tokens_used: int = 10

        def token_usage_json(self) -> str | None:
            return None

    runner.policy.rewrite.return_value = GenResult()
    monkeypatch.setattr(
        "moralstack.orchestration.deliberation_runner.build_aggregated_guidance",
        lambda *_a, **_k: "revise this",
    )
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    state = DeliberationState(cycle=2, draft_response="draft")
    risk = _risk(0.5)
    runner._generate_or_revise(state, request, risk_estimation=risk)
    leakage = [c for c in capture_persist if c.get("module") == "output_protection"]
    assert len(leakage) == 1
    assert leakage[0]["billable_provider_call"] is False


def test_critique_skip_marked_non_billable_when_skipped(capture_persist):
    critique = SimpleNamespace(
        violations=[],
        revision_guidance="",
        skipped=True,
        skip_reason="timeout",
        parse_attempts=0,
    )
    critic = MagicMock()
    critic.critique_with_relevant_principles = None
    critic.critique.return_value = critique
    runner = _build_runner(critic=critic)
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    state = DeliberationState(draft_response="draft")
    runner._critique(state, request, constitution=MagicMock())
    critic_rows = [c for c in capture_persist if c.get("module") == "critic"]
    assert len(critic_rows) == 1
    assert critic_rows[0]["billable_provider_call"] is False


def test_critique_real_call_marked_billable_when_not_skipped(capture_persist):
    critique = SimpleNamespace(
        violations=[],
        revision_guidance="",
        skipped=False,
        parse_attempts=1,
        prompt="p",
        system_prompt="s",
        raw_response="r",
        tokens_used=10,
        prompt_tokens=6,
        completion_tokens=4,
    )
    critic = MagicMock()
    critic.critique_with_relevant_principles = None
    critic.critique.return_value = critique
    runner = _build_runner(critic=critic)
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    state = DeliberationState(draft_response="draft")
    runner._critique(state, request, constitution=MagicMock())
    critic_rows = [c for c in capture_persist if c.get("module") == "critic"]
    assert len(critic_rows) == 1
    assert critic_rows[0]["billable_provider_call"] is True


def test_real_generate_and_rewrite_calls_remain_billable_by_default(capture_persist, monkeypatch):
    runner = _build_runner()

    @dataclass
    class GenResult:
        text: str = "answer"
        tokens_used: int = 10

        def token_usage_json(self) -> str | None:
            return None

    runner.policy.generate.return_value = GenResult()
    runner.policy.rewrite.return_value = GenResult(text="revised")
    monkeypatch.setattr(
        "moralstack.orchestration.deliberation_runner.build_aggregated_guidance",
        lambda *_a, **_k: "guidance",
    )
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    state = DeliberationState(cycle=1, draft_response="")
    risk = _risk()
    runner._generate_or_revise(state, request, risk_estimation=risk)
    policy_rows = [c for c in capture_persist if c.get("module") == "policy" and c.get("action") == "generate"]
    assert len(policy_rows) == 1
    assert policy_rows[0].get("billable_provider_call", True) is True

    capture_persist.clear()
    state2 = DeliberationState(cycle=2, draft_response="draft")
    runner._generate_or_revise(state2, request, risk_estimation=risk)
    rewrite_rows = [c for c in capture_persist if c.get("module") == "policy" and c.get("action") == "rewrite"]
    assert len(rewrite_rows) == 1
    assert rewrite_rows[0].get("billable_provider_call", True) is True


def test_module_model_prefers_policy_model_over_module_model():
    module = SimpleNamespace(policy=SimpleNamespace(model="inner-model"), model="outer-model")
    assert _module_model(module) == "inner-model"


def test_policy_llm_model_for_action_rewrite_uses_rewrite_model():
    policy = SimpleNamespace(model="gpt-4", rewrite_model="gpt-4-rewrite")
    assert _policy_llm_model_for_action(policy, "rewrite") == "gpt-4-rewrite"
    assert _policy_llm_model_for_action(policy, "generate") == "gpt-4"
