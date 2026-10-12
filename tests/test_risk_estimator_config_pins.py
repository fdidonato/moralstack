"""Characterization pins for the risk-estimator config / fail-closed cleanup (phase C0).

Written BEFORE the cleanup phases (C1-C4 of ``risk-estimator-dual-config``) and green at the
starting HEAD. Pins 4-7, 14 and 15 and the envelope unit pin must stay green, unmodified,
through every later phase: they lock the strict per-mini response formats, the fail-closed
values of ``RiskEstimation.from_error``, hard-signal supremacy and the ``_build_generation_config``
contract. Expected values were observed on the code, never guessed.

Env isolation: every ``MORALSTACK_RISK_*`` knob, ``OPENAI_MODEL``, ``OPENAI_API_KEY``,
``OPENAI_BASE_URL`` and ``MORALSTACK_DB_PATH`` is cleared before each test. Tests that can build
a pooled policy patch ``moralstack.models.policy.OpenAIPolicy``; none relies on a missing key.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

import moralstack.models.risk.estimator as risk_estimator_module
from moralstack.models.risk.categories import RiskCategory, RiskPolicyAction
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.prompts import INTENT_CONTEXT_SYSTEM_PROMPT, OPERATIONAL_RISK_SYSTEM_PROMPT
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.prompt_renderer import get_harm_signal_prompts
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.observability.context import set_current_cycle, set_current_request_id, set_current_run_id
from moralstack.orchestration.path_router import _HARD_SEMANTIC_SIGNALS

_SIGNALS_SYSTEM_PROMPT = get_harm_signal_prompts(signal_registry)[0]

_MINI_BY_SYSTEM_PROMPT = {
    INTENT_CONTEXT_SYSTEM_PROMPT: "estimate_intent",
    _SIGNALS_SYSTEM_PROMPT: "estimate_signals",
    OPERATIONAL_RISK_SYSTEM_PROMPT: "estimate_operational",
}

_BENIGN_INTENT = {
    "request_type": "factual_query",
    "intent_to_harm": "no",
    "requested_instructions": "no",
    "harm_type": "none",
}
_BENIGN_OPERATIONAL = {
    "risk_score": 0.1,
    "confidence": 0.9,
    "operational_risk": "NONE",
    "risk_policy_action": "ALLOW",
}
_Q17_OPERATIONAL = {
    "risk_score": 0.9,
    "confidence": 0.8,
    "operational_risk": "HIGH",
    "risk_policy_action": "DENY",
    "rationale": "x",
}

# from_error values (schema.py RiskEstimation.from_error)
_FROM_ERROR_SIGNALS = ["SYSTEM.REQUIRES_DELIBERATION"]


class FakePolicy:
    """Routes canned JSON by system-prompt identity and records the generation config per mini."""

    tracker = None

    def __init__(self, model: str = "m", answers: dict[str, dict[str, Any]] | None = None) -> None:
        self.model = model
        self._by_mini = answers or {
            "estimate_intent": _BENIGN_INTENT,
            "estimate_signals": {"q1_confidential": "no"},
            "estimate_operational": _BENIGN_OPERATIONAL,
        }
        self.configs: dict[str, Any] = {}

    def generate(self, *, prompt: str, system: str, config: Any = None) -> SimpleNamespace:
        mini = _MINI_BY_SYSTEM_PROMPT[system]
        self.configs[mini] = config
        return SimpleNamespace(text=json.dumps(self._by_mini[mini]))


class RaisingPolicy:
    model = "m"
    tracker = None

    def generate(self, *, prompt: str, system: str, config: Any = None) -> Any:
        raise RuntimeError("provider down")


class NonJsonPolicy:
    model = "m"
    tracker = None

    def generate(self, *, prompt: str, system: str, config: Any = None) -> SimpleNamespace:
        return SimpleNamespace(text="this is not json at all")


_CONFIG = RiskEstimatorConfig(intent_model="m", signals_model="m", operational_model="m", max_retries=1)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Clear every env knob the estimator or a pooled policy could read, then set the obs context."""
    for key in list(os.environ):
        if key.startswith("MORALSTACK_RISK_"):
            monkeypatch.delenv(key, raising=False)
    for key in ("OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "MORALSTACK_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    set_current_run_id("run-config-pins")
    set_current_request_id("req-config-pins")
    set_current_cycle(0)


@pytest.fixture
def captured(monkeypatch):
    """Capture what the estimator enqueues: batches (the 3 minis) and single routes (guard row)."""
    batches: list[list[Any]] = []
    singles: list[Any] = []
    monkeypatch.setattr(risk_estimator_module, "_obs_route_batch", lambda envs: batches.append(list(envs)))
    monkeypatch.setattr(risk_estimator_module, "_obs_route", lambda env: singles.append(env))
    return SimpleNamespace(batches=batches, singles=singles)


# -- pin 4 -------------------------------------------------------------------------------------


def test_mini_calls_use_three_distinct_strict_json_schema_response_formats(captured):
    policy = FakePolicy()
    LLMBasedRiskEstimator(policy=policy, config=_CONFIG).estimate("hello world")

    names = {}
    for mini, cfg in policy.configs.items():
        fmt = cfg.response_format
        assert fmt["type"] == "json_schema", mini
        assert fmt["type"] != "json_object", mini
        assert fmt["json_schema"]["strict"] is True, mini
        names[mini] = fmt["json_schema"]["name"]
    assert names == {
        "estimate_intent": "risk_intent",
        "estimate_signals": "harm_signals",
        "estimate_operational": "risk_operational",
    }


# -- pins 5 / 6 / 14 ---------------------------------------------------------------------------


def _assert_from_error_values(est) -> None:
    assert est.score == 0.6
    assert est.confidence == 0.4
    assert est.risk_category == RiskCategory.SENSITIVE
    assert est.semantic_signals == _FROM_ERROR_SIGNALS
    assert est.risk_policy_action == RiskPolicyAction.DELIBERATE
    assert est.used_fallback_parse is True


def test_semantic_analysis_without_policy_returns_from_error_values(captured):
    """policy=None reaches ``RiskEstimationError("policy not set")``, caught into ``from_error``."""
    estimator = LLMBasedRiskEstimator(policy=None, config=_CONFIG)
    est = estimator._semantic_analysis("hello world")

    _assert_from_error_values(est)
    assert "policy not set" in est.rationale
    assert captured.batches == [] and captured.singles == []


@pytest.mark.parametrize("policy_cls", [RaisingPolicy, NonJsonPolicy])
def test_estimate_with_failing_policy_returns_from_error_values_and_no_hard_skip(policy_cls, captured):
    est = LLMBasedRiskEstimator(policy=policy_cls(), config=_CONFIG).estimate("hello world")

    _assert_from_error_values(est)
    assert est.risk_policy_action != RiskPolicyAction.ALLOW
    assert est.risk_category != RiskCategory.BENIGN


def test_policy_none_estimate_emits_no_batch_and_no_single(captured):
    """Stays green through C2: whichever path ``policy=None`` takes, it persists no ``llm_calls`` row."""
    est = LLMBasedRiskEstimator(policy=None, config=_CONFIG).estimate("hello world")

    assert est.risk_policy_action != RiskPolicyAction.ALLOW
    assert captured.batches == [] and captured.singles == []


# -- pin 7 -------------------------------------------------------------------------------------


def _assert_q17_not_downgraded(est, captured) -> None:
    assert est.score == 0.9
    assert est.risk_category == RiskCategory.CLEARLY_HARMFUL
    assert est.risk_policy_action == RiskPolicyAction.DENY
    # q17 bypasses the calibration guard: no synthetic guard row.
    assert captured.singles == []
    assert len(captured.batches) == 1 and len(captured.batches[0]) == 3


def _q17_answers() -> dict[str, dict[str, Any]]:
    return {
        "estimate_intent": _BENIGN_INTENT,
        "estimate_signals": {"q17_minor_exploitation": "yes"},
        "estimate_operational": _Q17_OPERATIONAL,
    }


def test_hard_signal_estimate_not_downgraded_by_model_resolution(captured):
    """Q17 (a real hard signal, see governance_invariants) keeps the maximum risk, with explicit slots."""
    assert "Q17:minor_exploitation" in _HARD_SEMANTIC_SIGNALS
    policy = FakePolicy(answers=_q17_answers())
    est = LLMBasedRiskEstimator(policy=policy, config=_CONFIG).estimate("hello world")

    _assert_q17_not_downgraded(est, captured)


def test_hard_signal_estimate_not_downgraded_when_slots_come_from_env_resolution(captured):
    """Same Q17 case with ``config=None``: the slots come from the loader, whatever rule that applies.

    The pooled-policy factory is patched (never rely on a missing key): every policy it hands out
    answers like the main one, so the outcome must not depend on which model serves which mini.
    """
    answers = _q17_answers()
    main = FakePolicy(model="main-m", answers=answers)
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=lambda **kw: FakePolicy(kw["model"], answers)):
        est = LLMBasedRiskEstimator(policy=main).estimate("hello world")

    _assert_q17_not_downgraded(est, captured)


# -- pin 15 ------------------------------------------------------------------------------------


def test_build_generation_config_carries_response_format_and_sampling_params():
    estimator = LLMBasedRiskEstimator(policy=None, config=RiskEstimatorConfig(max_tokens=321, temperature=0.2))
    fmt = {"type": "json_schema", "json_schema": {"name": "x", "strict": True, "schema": {}}}

    cfg = estimator._build_generation_config(fmt)

    assert cfg.response_format is fmt
    assert cfg.max_tokens == 321
    assert cfg.temperature == 0.2
    assert cfg.top_p == 0.9


# -- envelope unit pin (A10) -------------------------------------------------------------------


def _envelope(estimator, **kwargs):
    return estimator._build_mini_llm_call_envelope(
        system_prompt="s", prompt="p", raw_response="{}", action="calibration_guard", duration_ms=0.0, attempts=1, **kwargs
    )


def test_envelope_empty_llm_model_persists_null_model():
    """``llm_model=""`` means "no model": the payload carries None (the guard row relies on this in C3)."""
    estimator = LLMBasedRiskEstimator(policy=FakePolicy(model="main-m"), config=_CONFIG)

    assert _envelope(estimator, llm_model="").payload["model"] is None
    # contrast: llm_model omitted falls back to the policy model
    assert _envelope(estimator).payload["model"] == "main-m"
    assert _envelope(estimator, llm_model="pooled-x").payload["model"] == "pooled-x"
