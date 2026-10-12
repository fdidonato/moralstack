"""Characterization pins for how the risk estimator resolves and persists its three mini models.

Phase C0 of ``risk-estimator-dual-config``: every test asserts the behavior at the starting HEAD.
Tests marked "flips in Cn" document today's value; the named phase changes the expectation and
justifies it in its commit message. The rest stay green unmodified through C1-C4.

Env isolation: every ``MORALSTACK_RISK_*`` knob, ``OPENAI_MODEL``, ``OPENAI_API_KEY``,
``OPENAI_BASE_URL`` and ``MORALSTACK_DB_PATH`` is cleared before each test (and any baseline is
read after the clearing). Every test that can build a pooled policy patches
``moralstack.models.policy.OpenAIPolicy``; none relies on a missing key.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

import moralstack.models.risk.estimator as risk_estimator_module
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.prompts import INTENT_CONTEXT_SYSTEM_PROMPT, OPERATIONAL_RISK_SYSTEM_PROMPT
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.prompt_renderer import get_harm_signal_prompts
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.observability.context import set_current_cycle, set_current_request_id, set_current_run_id
from moralstack.observability.events import EVENT_LLM_CALL, make_envelope
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.router import route
from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request

_MINIS = ("estimate_intent", "estimate_signals", "estimate_operational")
_MINI_BY_SYSTEM_PROMPT = {
    INTENT_CONTEXT_SYSTEM_PROMPT: "estimate_intent",
    get_harm_signal_prompts(signal_registry)[0]: "estimate_signals",
    OPERATIONAL_RISK_SYSTEM_PROMPT: "estimate_operational",
}

_BENIGN_ANSWERS: dict[str, dict[str, Any]] = {
    "estimate_intent": {
        "request_type": "factual_query",
        "intent_to_harm": "no",
        "requested_instructions": "no",
        "harm_type": "none",
    },
    "estimate_signals": {"q1_confidential": "no"},
    "estimate_operational": {
        "risk_score": 0.1,
        "confidence": 0.9,
        "operational_risk": "NONE",
        "risk_policy_action": "ALLOW",
    },
}
# sensitive_topic intent + high operational score: triggers the calibration guard (synthetic row).
_GUARD_ANSWERS: dict[str, dict[str, Any]] = {
    "estimate_intent": {**_BENIGN_ANSWERS["estimate_intent"], "request_type": "sensitive_topic"},
    "estimate_signals": {"q1_confidential": "no"},
    "estimate_operational": {
        "risk_score": 0.9,
        "confidence": 0.8,
        "operational_risk": "HIGH",
        "risk_policy_action": "DENY",
        "misuse_plausibility": "HIGH",
        "actionability_risk": "HIGH",
        "rationale": "x",
    },
}


class FakePolicy:
    """Canned-JSON policy routed by system-prompt identity; counts calls per mini."""

    tracker = None

    def __init__(self, model: str, answers: dict[str, dict[str, Any]] | None = None) -> None:
        self.model = model
        self._answers = answers or _BENIGN_ANSWERS
        self.calls: list[str] = []

    def generate(self, *, prompt: str, system: str, config: Any = None) -> SimpleNamespace:
        mini = _MINI_BY_SYSTEM_PROMPT[system]
        self.calls.append(mini)
        return SimpleNamespace(text=json.dumps(self._answers[mini]))


class ModellessPolicy:
    """A policy exposing no ``model`` attribute at all."""

    tracker = None

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, *, prompt: str, system: str, config: Any = None) -> SimpleNamespace:
        mini = _MINI_BY_SYSTEM_PROMPT[system]
        self.calls.append(mini)
        return SimpleNamespace(text=json.dumps(_BENIGN_ANSWERS[mini]))


class PoolRecorder:
    """Stand-in for ``OpenAIPolicy``: records every pooled-policy construction."""

    def __init__(self, answers: dict[str, dict[str, Any]] | None = None) -> None:
        self.answers = answers
        self.built: dict[str, FakePolicy] = {}

    def __call__(self, **kwargs: Any) -> FakePolicy:
        pol = FakePolicy(kwargs["model"], self.answers)
        self.built[kwargs["model"]] = pol
        return pol


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("MORALSTACK_RISK_"):
            monkeypatch.delenv(key, raising=False)
    for key in ("OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "MORALSTACK_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    set_current_run_id("run-model-resolution")
    set_current_request_id("req-model-resolution")
    set_current_cycle(0)


@pytest.fixture
def captured(monkeypatch):
    batches: list[list[Any]] = []
    singles: list[Any] = []
    monkeypatch.setattr(risk_estimator_module, "_obs_route_batch", lambda envs: batches.append(list(envs)))
    monkeypatch.setattr(risk_estimator_module, "_obs_route", lambda env: singles.append(env))
    return SimpleNamespace(batches=batches, singles=singles)


def _persisted_models(captured) -> dict[str, str | None]:
    assert len(captured.batches) == 1
    return {env.payload["action"]: env.payload["model"] for env in captured.batches[0]}


def _slots(intent: str, signals: str, operational: str) -> RiskEstimatorConfig:
    return RiskEstimatorConfig(intent_model=intent, signals_model=signals, operational_model=operational, max_retries=1)


# -- pin 1 / 2 / 3 -----------------------------------------------------------------------------


def test_slots_distinct_from_policy_model_route_to_pooled_policies_and_persist_slot_model(captured):
    main = FakePolicy("main-m")
    pool = PoolRecorder()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        est = LLMBasedRiskEstimator(policy=main, config=_slots("s-i", "s-s", "s-o")).estimate("hello world")

    assert est.score == 0.1
    assert main.calls == []
    assert {m: p.calls for m, p in pool.built.items()} == {
        "s-i": ["estimate_intent"],
        "s-s": ["estimate_signals"],
        "s-o": ["estimate_operational"],
    }
    assert _persisted_models(captured) == {
        "estimate_intent": "s-i",
        "estimate_signals": "s-s",
        "estimate_operational": "s-o",
    }


def test_pooled_policy_creation_failure_falls_back_to_main_policy_and_persists_main_model(captured):
    main = FakePolicy("main-m")
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=ValueError("no credentials")):
        est = LLMBasedRiskEstimator(policy=main, config=_slots("s-i", "s-s", "s-o")).estimate("hello world")

    assert est.score == 0.1  # a normal estimate, not the from_error values
    assert sorted(main.calls) == sorted(_MINIS)
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "main-m")


def test_all_three_slots_equal_policy_model_never_builds_a_pooled_policy(captured):
    main = FakePolicy("main-m")
    pool = PoolRecorder()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        LLMBasedRiskEstimator(policy=main, config=_slots("main-m", "main-m", "main-m")).estimate("hello world")

    assert pool.built == {}
    assert sorted(main.calls) == sorted(_MINIS)
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "main-m")


# -- pin 12: design-A flips (config=None) ------------------------------------------------------


def test_config_none_slots_follow_env_openai_model_not_the_policy_model(monkeypatch, captured):
    """Flips in C3: today (a) the slots come from env OPENAI_MODEL, so the minis ignore the policy model.

    C3 makes an unset slot inherit the policy model: ``builder-primary`` served by the main policy,
    no pooled policy built.
    """
    monkeypatch.setenv("OPENAI_MODEL", "env-primary")
    main = FakePolicy("builder-primary")
    pool = PoolRecorder()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        LLMBasedRiskEstimator(policy=main).estimate("hello world")

    assert main.calls == []
    assert set(pool.built) == {"env-primary"}
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "env-primary")


def test_config_none_slots_follow_env_risk_model(monkeypatch, captured):
    """Flips in C3: today (b) MORALSTACK_RISK_MODEL feeds the slots through the loader.

    C3 stops reading it there; the builder applies it to the risk policy instead, so the minis
    inherit the policy model.
    """
    monkeypatch.setenv("OPENAI_MODEL", "env-primary")
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "risk-env")
    main = FakePolicy("builder-primary")
    pool = PoolRecorder()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        LLMBasedRiskEstimator(policy=main).estimate("hello world")

    assert main.calls == []
    assert set(pool.built) == {"risk-env"}
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "risk-env")


def test_policy_without_model_attribute_persists_the_gpt4o_slot_default(captured):
    """Flips in C3: today (c) a policy with no ``.model`` is persisted as the schema default "gpt-4o".

    C3 persists None instead (no fake model).
    """
    policy = ModellessPolicy()
    pool = PoolRecorder()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        est = LLMBasedRiskEstimator(policy=policy).estimate("hello world")

    assert est.score == 0.1
    assert pool.built == {}
    assert sorted(policy.calls) == sorted(_MINIS)
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "gpt-4o")


def test_loader_slots_today_resolve_to_gpt4o_when_env_is_empty():
    """Flips in C3 (slots become None). Baseline read AFTER the env was cleared by the autouse fixture."""
    cfg = LLMBasedRiskEstimator(policy=None).config

    assert (cfg.intent_model, cfg.signals_model, cfg.operational_model) == ("gpt-4o", "gpt-4o", "gpt-4o")


# -- pin 9: calibration-guard row end to end ---------------------------------------------------


def _db_setup(tmp_path, monkeypatch, run_id: str, request_id: str) -> None:
    dbp = str(tmp_path / "pin9.db")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    create_run(run_id, run_type="test", meta={})
    upsert_request(run_id, request_id, prompt="p", domain="")
    set_current_run_id(run_id)
    set_current_request_id(request_id)


def test_get_models_used_includes_guard_model_today_end_to_end(tmp_path, monkeypatch, captured):
    """Flips in C3: estimate() -> route() -> SqliteReadStore with the guard row sorted first.

    Today the synthetic guard row carries the main policy model and defines the "risk" label when it
    sorts first. In production the guard row is built after the three futures resolve, so it sorts
    last within a request (review NB5); the ``started_at`` rewrite below reproduces the worst case.
    C3 persists NULL on the guard row, so the label becomes a real mini model.
    """
    run_id, request_id = "run-pin9", "req-pin9"
    _db_setup(tmp_path, monkeypatch, run_id, request_id)
    pool = PoolRecorder(_GUARD_ANSWERS)
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        LLMBasedRiskEstimator(policy=FakePolicy("main-m", _GUARD_ANSWERS), config=_slots("s-i", "s-s", "s-o")).estimate(
            "hello world"
        )

    assert len(captured.singles) == 1
    guard = captured.singles[0]
    assert guard.payload["action"] == "calibration_guard"
    assert guard.payload["model"] == "main-m"
    guard.payload["started_at"] = 1  # earliest row of the run
    for env in [*captured.batches[0], guard]:
        route(env)

    rows = SqliteReadStore().get_llm_calls_for_request(run_id, request_id)
    assert {r["action"]: r["model"] for r in rows} == {
        "calibration_guard": "main-m",
        "estimate_intent": "s-i",
        "estimate_signals": "s-s",
        "estimate_operational": "s-o",
    }
    assert SqliteReadStore().get_models_used_for_run(run_id)["risk"] == "main-m"


def test_get_models_used_guard_row_inserted_directly_feeds_risk_until_c4(tmp_path, monkeypatch):
    """Stays in C3, flips in C4: a guard row with a model, inserted directly, still defines "risk".

    C3 only stops the estimator from writing a model on that row; the read side is untouched until
    C4 excludes ``calibration_guard`` rows by action.
    """
    run_id, request_id = "run-pin9-direct", "req-pin9-direct"
    _db_setup(tmp_path, monkeypatch, run_id, request_id)
    for started_at, action, model in [
        (1, "calibration_guard", "gpt-4o-mini"),
        (2, "estimate_intent", "gpt-4o"),
        (3, "estimate_signals", "gpt-4o"),
        (4, "estimate_operational", "gpt-4o"),
    ]:
        route(
            make_envelope(
                EVENT_LLM_CALL,
                run_id=run_id,
                request_id=request_id,
                payload={
                    "phase": "risk_estimation",
                    "module": "risk_estimator",
                    "action": action,
                    "model": model,
                    "started_at": started_at,
                    "prompt": "",
                    "raw_response": "",
                    "attempts": 1,
                },
            )
        )

    assert SqliteReadStore().get_models_used_for_run(run_id) == {"risk": "gpt-4o-mini"}
