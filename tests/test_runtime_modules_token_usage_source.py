"""Tests for token_usage_source propagation on runtime module dataclasses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

from moralstack.constitution.schema import Constitution, Principle
from moralstack.runtime.modules.critic_module import CriticConfig, CriticReport, LLMConstitutionalCritic
from moralstack.runtime.modules.hindsight_module import HindsightConfig, HindsightResult, LLMHindsightEvaluator
from moralstack.runtime.modules.perspective_module import (
    EnsembleConfig,
    LLMPerspectiveEnsemble,
    Perspective,
    PerspectiveResult,
)
from moralstack.runtime.modules.simulator_module import (
    Consequence,
    LLMConsequenceSimulator,
    SimulationResult,
    SimulatorConfig,
)


@dataclass
class _GenResult:
    text: str
    tokens_used: int = 10
    prompt_tokens: int = 6
    completion_tokens: int = 4
    token_usage_source: str = "exact"


def _policy_with_generate(fn):
    policy = SimpleNamespace()
    policy.generate = fn
    return policy


def _constitution_with_principles() -> Constitution:
    principle = Principle(
        id="P1",
        level="soft",
        priority=50,
        title="Safety",
        rule="Be safe",
    )
    return Constitution(core_principles=[principle])


def test_critic_report_populates_token_usage_source_from_generation_result(monkeypatch):
    critic = LLMConstitutionalCritic(
        policy=_policy_with_generate(
            lambda **kwargs: _GenResult(
                text=json.dumps(
                    {
                        "decision": "PROCEED",
                        "violations": [],
                        "revision_guidance": "",
                        "violated_hard": False,
                    }
                ),
                token_usage_source="exact",
            )
        ),
        config=CriticConfig(max_retries=1),
    )
    report = critic.critique("req", "resp", _constitution_with_principles())
    assert isinstance(report, CriticReport)
    assert report.token_usage_source == "exact"


def test_critic_report_defaults_unknown_when_result_lacks_attribute():
    @dataclass
    class _NoSource:
        text: str
        tokens_used: int = 5
        prompt_tokens: int = 3
        completion_tokens: int = 2

    critic = LLMConstitutionalCritic(
        policy=_policy_with_generate(
            lambda **kwargs: _NoSource(
                text=json.dumps(
                    {
                        "decision": "PROCEED",
                        "violations": [],
                        "revision_guidance": "",
                        "violated_hard": False,
                    }
                ),
            )
        ),
        config=CriticConfig(max_retries=1),
    )
    report = critic.critique("req", "resp", _constitution_with_principles())
    assert report.token_usage_source == "unknown"


def test_simulation_result_populates_token_usage_source_via_build_result():
    simulator = LLMConsequenceSimulator(policy=MagicMock(), config=SimulatorConfig())
    result = simulator._build_result(
        consequences=[Consequence(text="x", likelihood=0.5)],
        raw_response="{}",
        parse_attempts=1,
        token_usage_source="estimated",
    )
    assert isinstance(result, SimulationResult)
    assert result.token_usage_source == "estimated"


def test_hindsight_result_populates_token_usage_source():
    evaluator = LLMHindsightEvaluator(
        policy=_policy_with_generate(
            lambda **kwargs: _GenResult(
                text=json.dumps(
                    [
                        {
                            "scenario_id": "s1",
                            "safety": 0.0,
                            "helpfulness": 0.0,
                            "honesty": 0.0,
                            "harm_probability": 0.1,
                            "benefit_probability": 0.1,
                            "confidence": 0.8,
                            "rationale": "ok",
                        }
                    ]
                ),
                token_usage_source="estimated",
            )
        ),
        config=HindsightConfig(max_retries=1),
    )
    result = evaluator._evaluate_batch(
        "req",
        "resp",
        [Consequence(text="x", likelihood=0.5, scenario_id="s1")],
    )
    assert isinstance(result, HindsightResult)
    assert result.token_usage_source == "estimated"


def test_perspective_result_token_usage_source_set_post_construction():
    pr = PerspectiveResult(
        perspective_id="user",
        perspective_name="User",
        token_usage_source="exact",
    )
    assert pr.token_usage_source == "exact"


def test_ensemble_result_aggregates_token_usage_source_parallel_path(monkeypatch):
    ensemble = LLMPerspectiveEnsemble(
        policy=MagicMock(),
        config=EnsembleConfig(parallel_evaluation=True, max_perspectives=2, max_retries=1),
    )
    perspectives = [
        Perspective(id="a", name="A", prompt_template="t", weight=1.0),
        Perspective(id="b", name="B", prompt_template="t", weight=1.0),
    ]

    def _fake_eval(shared_system, perspective, **kwargs):
        source = "exact" if perspective.id == "a" else "missing"
        return PerspectiveResult(
            perspective_id=perspective.id,
            perspective_name=perspective.name,
            approval_score=0.8,
            tokens_used=10,
            prompt_tokens=6,
            completion_tokens=4,
            token_usage_source=source,
        )

    monkeypatch.setattr(ensemble, "_evaluate_single_perspective", _fake_eval)
    result = ensemble._evaluate_parallel(
        perspectives,
        "shared",
    )
    assert result.token_usage_source == "missing"


def test_ensemble_result_aggregates_token_usage_source_sequential_path(monkeypatch):
    ensemble = LLMPerspectiveEnsemble(
        policy=MagicMock(),
        config=EnsembleConfig(parallel_evaluation=False, max_perspectives=2, max_retries=1),
    )
    perspectives = [
        Perspective(id="a", name="A", prompt_template="t", weight=1.0),
        Perspective(id="b", name="B", prompt_template="t", weight=1.0),
    ]

    def _fake_eval(shared_system, perspective, **kwargs):
        source = "exact" if perspective.id == "a" else "estimated"
        return PerspectiveResult(
            perspective_id=perspective.id,
            perspective_name=perspective.name,
            approval_score=0.8,
            tokens_used=10,
            prompt_tokens=6,
            completion_tokens=4,
            token_usage_source=source,
        )

    monkeypatch.setattr(ensemble, "_evaluate_single_perspective", _fake_eval)
    result = ensemble._evaluate_sequential(
        perspectives,
        "shared",
    )
    assert result.token_usage_source == "estimated"


def test_ensemble_result_source_reflects_worst_case_across_mixed_perspective_sources():
    results = [
        PerspectiveResult(perspective_id="a", perspective_name="A", token_usage_source="exact"),
        PerspectiveResult(perspective_id="b", perspective_name="B", token_usage_source="missing"),
    ]
    from moralstack.observability.token_usage import TokenUsage

    combined = TokenUsage.combine(
        [TokenUsage(r.prompt_tokens or 0, r.completion_tokens or 0, r.tokens_used, r.token_usage_source) for r in results]
    )
    assert combined.source == "missing"
