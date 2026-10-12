"""Characterization pins for how the risk estimator resolves and persists its three mini models.

Phase C0 pinned the behavior at the starting HEAD; phase C3 flipped the design-A pins (marked
"Flipped in C3") and added the resolution-rule tests. Rule: slot -> main risk policy model
(``policy.model``) -> None; ``OPENAI_MODEL`` and ``MORALSTACK_RISK_MODEL`` are not read by the loader.

Env isolation: every ``MORALSTACK_RISK_*`` knob, ``OPENAI_MODEL``, ``OPENAI_API_KEY``,
``OPENAI_BASE_URL`` and ``MORALSTACK_DB_PATH`` is cleared before each test (and any baseline is
read after the clearing). Every test that can build a pooled policy patches
``moralstack.models.policy.OpenAIPolicy``; none relies on a missing key.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import moralstack.models.risk.estimator as risk_estimator_module
from moralstack.models.risk.config_loader import resolve_risk_mini_model
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
from moralstack.reports.markdown_export import _get_benchmark_models_config_fallback, format_models_used_markdown

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


# -- design A: slots inherit the policy model (flipped in C3) ----------------------------------


def _bomb(**kwargs: Any) -> Any:
    pytest.fail(f"no pooled policy expected, got OpenAIPolicy({kwargs})")


def test_nothing_set_all_minis_use_the_policy_model_without_a_pool(captured):
    """Flipped in C3 (was: slots followed env OPENAI_MODEL, pooling ``env-primary``). Also needs no API key."""
    main = FakePolicy("primary-x")
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=_bomb):
        est = LLMBasedRiskEstimator(policy=main).estimate("hello world")

    assert est.score == 0.1
    assert sorted(main.calls) == sorted(_MINIS)
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "primary-x")


def test_risk_policy_model_drives_all_minis_when_risk_model_was_applied_by_the_builder(captured):
    """Flipped in C3 (was: MORALSTACK_RISK_MODEL fed the slots through the loader).

    The builder applies ``MORALSTACK_RISK_MODEL`` to the risk policy; the minis inherit ``policy.model``.
    """
    main = FakePolicy("risk-y")
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=_bomb):
        LLMBasedRiskEstimator(policy=main).estimate("hello world")

    assert sorted(main.calls) == sorted(_MINIS)
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "risk-y")


def test_env_risk_model_and_openai_model_are_ignored_by_the_estimator(monkeypatch, captured):
    monkeypatch.setenv("OPENAI_MODEL", "legacy-z")
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "risk-env")
    main = FakePolicy("primary-x")
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=_bomb):
        LLMBasedRiskEstimator(policy=main).estimate("hello world")

    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "primary-x")


def test_only_intent_slot_set_pools_intent_and_the_others_use_the_policy_model(monkeypatch, captured):
    monkeypatch.setenv("MORALSTACK_RISK_INTENT_MODEL", "slot-i")
    main = FakePolicy("primary-x")
    pool = PoolRecorder()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        LLMBasedRiskEstimator(policy=main).estimate("hello world")

    assert set(pool.built) == {"slot-i"}
    assert sorted(main.calls) == ["estimate_operational", "estimate_signals"]
    assert _persisted_models(captured) == {
        "estimate_intent": "slot-i",
        "estimate_signals": "primary-x",
        "estimate_operational": "primary-x",
    }


def test_whitespace_slot_values_count_as_unset(monkeypatch, captured):
    for key in ("INTENT", "SIGNALS", "OPERATIONAL"):
        monkeypatch.setenv(f"MORALSTACK_RISK_{key}_MODEL", "   ")
    main = FakePolicy("primary-x")
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=_bomb):
        LLMBasedRiskEstimator(policy=main).estimate("hello world")

    assert _persisted_models(captured) == dict.fromkeys(_MINIS, "primary-x")


def test_explicit_config_slot_wins_over_the_policy_model(captured):
    main = FakePolicy("primary-x")
    pool = PoolRecorder()
    cfg = RiskEstimatorConfig(intent_model="cfg-i", max_retries=1)
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        LLMBasedRiskEstimator(policy=main, config=cfg).estimate("hello world")

    assert set(pool.built) == {"cfg-i"}
    assert _persisted_models(captured)["estimate_intent"] == "cfg-i"
    assert _persisted_models(captured)["estimate_signals"] == "primary-x"


def test_two_estimators_built_under_different_env_are_independent(monkeypatch):
    monkeypatch.setenv("MORALSTACK_RISK_INTENT_MODEL", "slot-a")
    first = LLMBasedRiskEstimator(policy=FakePolicy("primary-x"))
    monkeypatch.setenv("MORALSTACK_RISK_INTENT_MODEL", "slot-b")
    second = LLMBasedRiskEstimator(policy=FakePolicy("primary-x"))

    assert first.config.intent_model == "slot-a"
    assert second.config.intent_model == "slot-b"
    assert first._effective_mini_model("estimate_intent") == "slot-a"
    assert second._effective_mini_model("estimate_intent") == "slot-b"


# -- resolve_risk_mini_model unit cases --------------------------------------------------------


def test_resolve_risk_mini_model_slot_wins_then_inherits_then_none():
    assert resolve_risk_mini_model("slot", "main") == "slot"
    assert resolve_risk_mini_model(None, "main") == "main"
    assert resolve_risk_mini_model("", "main") == "main"
    assert resolve_risk_mini_model(None, None) is None
    assert resolve_risk_mini_model("", "") is None


# -- policy edge cases -------------------------------------------------------------------------


def test_policy_without_model_attribute_persists_none_and_builds_no_pool(captured):
    """Flipped in C3 (was: persisted the schema default "gpt-4o")."""
    policy = ModellessPolicy()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=_bomb):
        est = LLMBasedRiskEstimator(policy=policy).estimate("hello world")

    assert est.score == 0.1
    assert sorted(policy.calls) == sorted(_MINIS)
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, None)


def test_mock_model_attribute_is_not_persisted_as_a_repr(captured):
    policy = FakePolicy("ignored")
    policy.model = MagicMock()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=_bomb):
        LLMBasedRiskEstimator(policy=policy).estimate("hello world")

    assert sorted(policy.calls) == sorted(_MINIS)
    assert _persisted_models(captured) == dict.fromkeys(_MINIS, None)


def test_empty_policy_model_is_treated_as_none(captured):
    policy = FakePolicy("")
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=_bomb):
        LLMBasedRiskEstimator(policy=policy).estimate("hello world")

    assert _persisted_models(captured) == dict.fromkeys(_MINIS, None)


def test_loader_slots_are_none_when_env_is_empty():
    """Flipped in C3 (was: ("gpt-4o",) * 3). Baseline read AFTER the env was cleared by the autouse fixture."""
    cfg = LLMBasedRiskEstimator(policy=None).config

    assert (cfg.intent_model, cfg.signals_model, cfg.operational_model) == (None, None, None)


# -- calibration-guard row end to end ----------------------------------------------------------


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


def _estimate_with_guard(captured):
    pool = PoolRecorder(_GUARD_ANSWERS)
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        LLMBasedRiskEstimator(policy=FakePolicy("main-m", _GUARD_ANSWERS), config=_slots("s-i", "s-s", "s-o")).estimate(
            "hello world"
        )
    assert len(captured.singles) == 1
    guard = captured.singles[0]
    assert guard.payload["action"] == "calibration_guard"
    return guard


def test_guard_row_persists_null_model_and_no_longer_defines_the_risk_label(tmp_path, monkeypatch, captured):
    """Flipped in C3: estimate() -> route() -> SqliteReadStore with the guard row sorted first.

    Was: the synthetic guard row carried the main policy model and defined the "risk" label when it
    sorted first. Now its model is NULL, so the label is a real mini model. The ``started_at`` rewrite
    reproduces the worst case (production builds the guard row after the minis, review NB5).
    """
    run_id, request_id = "run-pin9", "req-pin9"
    _db_setup(tmp_path, monkeypatch, run_id, request_id)
    guard = _estimate_with_guard(captured)
    assert guard.payload["model"] is None
    assert guard.payload["billable_provider_call"] is False
    guard.payload["started_at"] = 1  # earliest row of the run
    for env in [*captured.batches[0], guard]:
        route(env)

    rows = SqliteReadStore().get_llm_calls_for_request(run_id, request_id)
    assert {r["action"]: r["model"] for r in rows} == {
        "calibration_guard": None,
        "estimate_intent": "s-i",
        "estimate_signals": "s-s",
        "estimate_operational": "s-o",
    }
    assert SqliteReadStore().get_models_used_for_run(run_id)["risk"] in {"s-i", "s-s", "s-o"}


def test_guard_row_alone_yields_no_risk_key(tmp_path, monkeypatch, captured):
    run_id, request_id = "run-guard-only", "req-guard-only"
    _db_setup(tmp_path, monkeypatch, run_id, request_id)
    guard = _estimate_with_guard(captured)
    route(guard)

    assert "risk" not in SqliteReadStore().get_models_used_for_run(run_id)


def test_get_models_used_guard_row_inserted_directly_feeds_no_key(tmp_path, monkeypatch):
    """Flipped in C4: a guard row with a model, inserted directly, no longer defines "risk".

    Historical DBs hold guard rows with a fake model (719 gpt-4o-mini rows); C4 excludes
    ``calibration_guard`` rows by action, so "risk" comes from the first real mini row.
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

    assert SqliteReadStore().get_models_used_for_run(run_id) == {
        "risk": "gpt-4o",
        "risk_intent": "gpt-4o",
        "risk_signals": "gpt-4o",
        "risk_operational": "gpt-4o",
    }


# -- labels agree with the model the estimator really uses -------------------------------------


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"MORALSTACK_RISK_MODEL": "risk-env"},
        {"MORALSTACK_RISK_INTENT_MODEL": "slot-i"},
        {
            "MORALSTACK_RISK_MODEL": "risk-env",
            "MORALSTACK_RISK_INTENT_MODEL": "slot-i",
            "MORALSTACK_RISK_SIGNALS_MODEL": "slot-s",
            "MORALSTACK_RISK_OPERATIONAL_MODEL": "slot-o",
        },
    ],
)
def test_report_labels_equal_the_models_the_estimator_uses(monkeypatch, captured, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    ms = _get_benchmark_models_config_fallback("base-m", "judge-m", moralstack_policy_model="policy-m")["moralstack"]
    # The builder/benchmark risk policy: MORALSTACK_RISK_MODEL when set, else the primary model.
    main = FakePolicy(env.get("MORALSTACK_RISK_MODEL", "policy-m"))
    pool = PoolRecorder()
    with patch("moralstack.models.policy.OpenAIPolicy", side_effect=pool):
        LLMBasedRiskEstimator(policy=main).estimate("hello world")

    persisted = _persisted_models(captured)
    assert ms["risk_parallel"] is True
    assert ms["risk_intent"] == persisted["estimate_intent"]
    assert ms["risk_signals"] == persisted["estimate_signals"]
    assert ms["risk_operational"] == persisted["estimate_operational"]


def test_fallback_labels_always_set_risk_parallel_and_render_three_rows(monkeypatch):
    for env in ({}, {"MORALSTACK_RISK_MODEL": "risk-env"}):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        cfg = _get_benchmark_models_config_fallback("base-m", "judge-m", moralstack_policy_model="policy-m")
        assert cfg["moralstack"]["risk_parallel"] is True
        md = format_models_used_markdown(cfg, primary_model="policy-m")
        assert "| **MoralStack risk** | parallel mini-estimators |" in md
        assert "risk · intent" in md and "risk · signals" in md and "risk · operational" in md


# -- CLI display (never "None") ----------------------------------------------------------------


def test_cli_display_renders_the_resolved_model_never_none(capsys):
    from moralstack.cli.loader import ModuleLoader
    from moralstack.cli.models import CLIConfig

    est = LLMBasedRiskEstimator(policy=None, config=RiskEstimatorConfig())
    built = SimpleNamespace(
        policy=object(),
        constitution_store=SimpleNamespace(get_excluded_domains=lambda: set()),
        risk_estimator=est,
    )
    meta = SimpleNamespace(policy_model="p", risk_model="x")
    loader = ModuleLoader(CLIConfig(openai_api_key="k", minimal=True))
    with patch("moralstack.pipeline.deliberation_stack.build_deliberation_modules", return_value=(built, meta)):
        loader.load_all()

    status = loader.get_status()["risk_estimator"]
    assert "intent: x, signals: x, op: x" in status
    assert "None" not in status
    assert "None" not in capsys.readouterr().out


# -- design A reader pin -----------------------------------------------------------------------


def test_risk_model_env_is_read_only_where_the_risk_policy_or_labels_are_chosen():
    """``MORALSTACK_RISK_MODEL`` / the risk ``ENV_MODEL`` appear only in the policy-choice and label sites."""
    root = Path(__file__).resolve().parent.parent
    allowed = {
        "moralstack/pipeline/deliberation_stack.py",
        "scripts/benchmark_moralstack.py",
        "moralstack/reports/markdown_export.py",
        "moralstack/models/risk/config_loader.py",
        "moralstack/utils/env_loader.py",
    }
    pattern = re.compile(r"RISK_ENV_MODEL|MORALSTACK_RISK_MODEL|risk\.config_loader import[^)]*\bENV_MODEL\b")
    hits = set()
    for base in ("moralstack", "scripts"):
        for path in (root / base).rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                hits.add(path.relative_to(root).as_posix())
    assert hits <= allowed, sorted(hits - allowed)
    # The estimator and the CLI display must not read the env var at all.
    for rel in ("moralstack/models/risk/estimator.py", "moralstack/cli/loader.py"):
        assert "MORALSTACK_RISK_MODEL" not in (root / rel).read_text(encoding="utf-8")
