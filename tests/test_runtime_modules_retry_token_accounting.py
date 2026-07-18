"""Tests for retry-failed LLM call token accounting in runtime modules."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from moralstack.constitution.schema import Constitution, Principle
from moralstack.runtime.modules.critic_module import CriticConfig, LLMConstitutionalCritic
from moralstack.runtime.modules.hindsight_module import HindsightConfig, LLMHindsightEvaluator
from moralstack.runtime.modules.perspective_module import (
    EnsembleConfig,
    LLMPerspectiveEnsemble,
    Perspective,
)
from moralstack.runtime.modules.simulator_module import Consequence, LLMConsequenceSimulator, SimulatorConfig


@dataclass
class _GenResult:
    text: str
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    token_usage_source: str = "exact"

    def __post_init__(self) -> None:
        if self.tokens_used == 0 and self.prompt_tokens:
            self.tokens_used = self.prompt_tokens + self.completion_tokens


def _policy_with_generate(fn, model=""):
    policy = SimpleNamespace()
    policy.generate = fn
    policy.model = model
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


@pytest.fixture
def capture_async_persist(monkeypatch):
    calls: list[dict] = []

    def _persist(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("moralstack.observability.emit_helpers.async_persist_llm_call", _persist)
    return calls


_VALID_CRITIC = json.dumps(
    {
        "decision": "PROCEED",
        "violations": [],
        "revision_guidance": "",
        "violated_hard": False,
    }
)


def test_critic_retry_failed_attempt_persists_billable_row_with_tokens(capture_async_persist):
    responses = [
        _GenResult(text="not json", tokens_used=40, prompt_tokens=25, completion_tokens=15),
        _GenResult(text=_VALID_CRITIC, tokens_used=60, prompt_tokens=35, completion_tokens=25),
    ]
    critic = LLMConstitutionalCritic(
        policy=_policy_with_generate(lambda **kwargs: responses.pop(0), model="gpt-crit"),
        config=CriticConfig(max_retries=2),
    )
    report = critic.critique("req", "resp", _constitution_with_principles())
    assert report.tokens_used == 60
    assert len(capture_async_persist) == 1
    row = capture_async_persist[0]
    assert row["call_outcome"] == "retry_failed"
    assert row["billable_provider_call"] is True
    assert row["model"] == "gpt-crit"
    assert row["token_usage_json"] is not None
    assert json.loads(row["token_usage_json"])["total_tokens"] == 40


_VALID_SIMULATOR = json.dumps(
    {
        "consequences": [
            {
                "text": "harm",
                "likelihood": 0.5,
                "scenario_type": "social_impact",
                "outcome_valence": -0.5,
                "affected_stakeholders": ["user"],
                "harm_type": "physical_harm",
                "harm_severity": 0.7,
                "harm_scope": "individual",
                "reversibility": 0.3,
            }
        ]
    }
)


def test_simulator_retry_failed_attempt_persists_token_usage(capture_async_persist):
    responses = [
        _GenResult(text="bad", tokens_used=30, prompt_tokens=20, completion_tokens=10),
        _GenResult(text=_VALID_SIMULATOR, tokens_used=50, prompt_tokens=30, completion_tokens=20),
    ]
    simulator = LLMConsequenceSimulator(
        policy=_policy_with_generate(lambda **kwargs: responses.pop(0), model="gpt-sim"),
        config=SimulatorConfig(max_retries=2, use_seeded_generation=False),
    )
    result = simulator._simulate_batch("req", "resp", 1)
    assert result.tokens_used == 50
    assert len(capture_async_persist) == 1
    assert capture_async_persist[0]["token_usage_json"] is not None
    # The retry row must attribute its tokens to the generating model, not "".
    assert capture_async_persist[0]["model"] == "gpt-sim"


def test_hindsight_retry_failed_attempt_persists_billable_row_with_tokens(capture_async_persist):
    valid = json.dumps(
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
    )
    responses = [
        _GenResult(text="bad", tokens_used=20, prompt_tokens=12, completion_tokens=8),
        _GenResult(text=valid, tokens_used=45, prompt_tokens=25, completion_tokens=20),
    ]
    evaluator = LLMHindsightEvaluator(
        policy=_policy_with_generate(lambda **kwargs: responses.pop(0), model="gpt-hind"),
        config=HindsightConfig(max_retries=2),
    )
    result = evaluator._evaluate_batch("req", "resp", [Consequence(text="x", likelihood=0.5, scenario_id="s1")])
    assert result.tokens_used == 45
    assert len(capture_async_persist) == 1
    assert capture_async_persist[0]["module"] == "hindsight"
    assert capture_async_persist[0]["model"] == "gpt-hind"


def test_perspective_retry_failed_attempt_persists_billable_row_with_tokens(capture_async_persist):
    perspective = Perspective(id="user", name="User", prompt_template="t", weight=1.0)
    valid = json.dumps(
        {
            "approval_score": 0.8,
            "concerns": [],
            "suggestions": [],
            "rationale": "ok",
        }
    )
    responses = [
        _GenResult(text="bad", tokens_used=15, prompt_tokens=10, completion_tokens=5),
        _GenResult(text=valid, tokens_used=25, prompt_tokens=15, completion_tokens=10),
    ]
    ensemble = LLMPerspectiveEnsemble(
        policy=_policy_with_generate(lambda **kwargs: responses.pop(0), model="gpt-persp"),
        config=EnsembleConfig(max_retries=2, max_perspectives=1),
    )
    result = ensemble._evaluate_single_perspective("sys", perspective)
    assert result is not None
    assert result.tokens_used == 25
    assert len(capture_async_persist) == 1
    assert "user" in capture_async_persist[0]["prompt"]
    assert capture_async_persist[0]["model"] == "gpt-persp"


def test_retry_failed_row_excludes_infra_errors_not_parsing_errors(capture_async_persist):
    critic = LLMConstitutionalCritic(
        policy=_policy_with_generate(lambda **kwargs: (_ for _ in ()).throw(ConnectionError("network down"))),
        config=CriticConfig(max_retries=2),
    )
    with pytest.raises(RuntimeError):
        critic.critique("req", "resp", _constitution_with_principles())
    assert capture_async_persist == []


def test_critic_simulator_hindsight_perspective_numeric_fields_unchanged_by_retry_fix(capture_async_persist):
    responses = [
        _GenResult(text="bad", tokens_used=40, prompt_tokens=25, completion_tokens=15),
        _GenResult(text=_VALID_CRITIC, tokens_used=60, prompt_tokens=35, completion_tokens=25),
    ]
    critic = LLMConstitutionalCritic(
        policy=_policy_with_generate(lambda **kwargs: responses.pop(0)),
        config=CriticConfig(max_retries=2),
    )
    report = critic.critique("req", "resp", _constitution_with_principles())
    assert report.tokens_used == 60
    assert report.prompt_tokens == 35
    assert report.completion_tokens == 25
