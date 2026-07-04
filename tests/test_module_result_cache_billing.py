"""Tests for cache-hit billing (from_cache + billable_provider_call)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.types import DeliberationState, OrchestratorConfig, ProcessedRequest
from moralstack.runtime.modules.hindsight_module import HindsightResult, LLMHindsightEvaluator
from moralstack.runtime.modules.perspective_module import EnsembleConfig, EnsembleResult, LLMPerspectiveEnsemble
from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator, SimulationResult, SimulatorConfig
from moralstack.utils.cache import get_global_cache


@pytest.fixture(autouse=True)
def _clear_module_cache():
    get_global_cache().clear_all()
    yield
    get_global_cache().clear_all()


@pytest.fixture
def capture_persist(monkeypatch):
    calls: list[dict] = []

    def _record(_logger, _diag, persist_kwargs):
        if persist_kwargs is not None:
            calls.append(persist_kwargs)

    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", _record)
    return calls


def _runner_with_module(module_attr: str, module: object) -> DeliberationRunner:
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
    setattr(deps, module_attr, module)
    return DeliberationRunner(config, deps, "protected", None, MagicMock())


def test_simulate_cache_hit_marked_non_billable_and_no_double_count(monkeypatch):
    policy = MagicMock()
    config = SimulatorConfig(enable_caching=True, use_seeded_generation=False)
    simulator = LLMConsequenceSimulator(policy=policy, config=config)
    fixed = SimulationResult(tokens_used=50, prompt_tokens=30, completion_tokens=20)
    monkeypatch.setattr(simulator, "_simulate_batch", lambda *a, **k: fixed)

    r1 = simulator.simulate("req", "resp", 3)
    assert r1.from_cache is False
    r2 = simulator.simulate("req", "resp", 3)
    assert r2.from_cache is True
    assert r2 is not r1


def test_hindsight_cache_hit_marked_non_billable_and_no_double_count(monkeypatch):
    from moralstack.runtime.modules.hindsight_module import HindsightConfig
    from moralstack.runtime.modules.simulator_module import Consequence

    policy = MagicMock()
    config = HindsightConfig(enable_caching=True, use_batch_evaluation=False)
    evaluator = LLMHindsightEvaluator(policy=policy, config=config)
    fixed = HindsightResult(tokens_used=40, prompt_tokens=25, completion_tokens=15)
    monkeypatch.setattr(evaluator, "_evaluate_individual", lambda *a, **k: fixed)
    consequences = [Consequence(text="harm scenario", likelihood=0.5)]

    r1 = evaluator.evaluate("req", "resp", consequences)
    assert r1.from_cache is False
    r2 = evaluator.evaluate("req", "resp", consequences)
    assert r2.from_cache is True


def test_perspective_cache_hit_marked_non_billable_when_caching_enabled(monkeypatch):
    policy = MagicMock()

    config = EnsembleConfig(enable_caching=True, parallel_evaluation=False, max_perspectives=1)
    ensemble = LLMPerspectiveEnsemble(policy=policy, config=config)
    fixed = EnsembleResult(tokens_used=60, prompt_tokens=40, completion_tokens=20)
    monkeypatch.setattr(ensemble, "_evaluate_sequential", lambda *a, **k: fixed)

    r1 = ensemble.evaluate("req", "resp")
    assert r1.from_cache is False
    r2 = ensemble.evaluate("req", "resp")
    assert r2.from_cache is True


def test_cache_miss_first_call_always_billable(capture_persist):
    simulation = SimulationResult(tokens_used=50, prompt_tokens=30, completion_tokens=20, from_cache=False)
    simulator = MagicMock()
    simulator.simulate.return_value = simulation
    runner = _runner_with_module("simulator", simulator)
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    state = DeliberationState(draft_response="draft")
    runner._simulate(state, request)
    assert len(capture_persist) == 1
    assert capture_persist[0]["billable_provider_call"] is True
    assert capture_persist[0].get("cache_status") is None


def test_simulator_cache_hit_persist_row_non_billable(capture_persist):
    simulation = SimulationResult(tokens_used=50, prompt_tokens=30, completion_tokens=20, from_cache=True)
    simulator = MagicMock()
    simulator.simulate.return_value = simulation
    runner = _runner_with_module("simulator", simulator)
    request = ProcessedRequest(prompt="hello", request_id="req-1")
    state = DeliberationState(draft_response="draft")
    runner._simulate(state, request)
    assert capture_persist[0]["billable_provider_call"] is False
    assert capture_persist[0]["cache_status"] == "hit"
