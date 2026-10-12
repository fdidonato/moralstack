"""Characterization pins for the report "Models used" labels built from env (phase C0).

``_get_benchmark_models_config_fallback`` has no coverage today. It labels the risk module with a
single ``risk`` key; since C3 it also returns ``risk_parallel`` plus the three mini keys (resolved with
``resolve_risk_mini_model``), so ``format_models_used_markdown`` renders three risk rows. The tests
marked "Flipped in C3" changed there; each flip is justified in the C3 commit message.

Env isolation: every ``MORALSTACK_*_MODEL`` knob read by the function and ``OPENAI_MODEL`` is cleared
before each test.
"""

from __future__ import annotations

import os

import pytest

from moralstack.reports.markdown_export import _get_benchmark_models_config_fallback, format_models_used_markdown

_NON_RISK_MODEL_ENVS = (
    "MORALSTACK_CRITIC_MODEL",
    "MORALSTACK_SIMULATOR_MODEL",
    "MORALSTACK_HINDSIGHT_MODEL",
    "MORALSTACK_PERSPECTIVES_MODEL",
    "MORALSTACK_POLICY_REWRITE_MODEL",
    "OPENAI_MODEL",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("MORALSTACK_RISK_"):
            monkeypatch.delenv(key, raising=False)
    for key in _NON_RISK_MODEL_ENVS:
        monkeypatch.delenv(key, raising=False)


def _fallback() -> dict:
    return _get_benchmark_models_config_fallback("base-m", "judge-m", moralstack_policy_model="policy-m")


def _mini_keys(ms: dict) -> tuple:
    return (ms["risk_intent"], ms["risk_signals"], ms["risk_operational"])


def test_benchmark_label_fallback_matrix_nothing_set():
    """Flipped in C3: also sets ``risk_parallel`` and the three mini labels (all the primary model)."""
    cfg = _fallback()

    assert cfg["baseline"] == "base-m" and cfg["judge"] == "judge-m"
    assert cfg["moralstack"] == {
        "policy": "policy-m",
        "policy_rewrite": "policy-m",
        "risk": "policy-m",
        "critic": "policy-m",
        "simulator": "policy-m",
        "hindsight": "policy-m",
        "perspectives": "policy-m",
        "risk_parallel": True,
        "risk_intent": "policy-m",
        "risk_signals": "policy-m",
        "risk_operational": "policy-m",
    }


def test_benchmark_label_fallback_matrix_risk_model_only(monkeypatch):
    """Flipped in C3: the three mini labels follow MORALSTACK_RISK_MODEL; ``risk_parallel`` is set."""
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "risk-env")
    ms = _fallback()["moralstack"]

    assert ms["risk"] == "risk-env"
    assert ms["risk_parallel"] is True
    assert _mini_keys(ms) == ("risk-env",) * 3


def test_benchmark_label_fallback_matrix_one_slot(monkeypatch):
    monkeypatch.setenv("MORALSTACK_RISK_INTENT_MODEL", "slot-i")
    ms = _fallback()["moralstack"]

    assert ms["risk_parallel"] is True
    assert _mini_keys(ms) == ("slot-i", "policy-m", "policy-m")


def test_benchmark_label_fallback_matrix_all_slots(monkeypatch):
    """Flipped in C3 (was: per-mini slots set in env did not reach the fallback labels at all)."""
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "risk-env")
    monkeypatch.setenv("MORALSTACK_RISK_INTENT_MODEL", "slot-i")
    monkeypatch.setenv("MORALSTACK_RISK_SIGNALS_MODEL", "slot-s")
    monkeypatch.setenv("MORALSTACK_RISK_OPERATIONAL_MODEL", "slot-o")
    ms = _fallback()["moralstack"]

    assert ms["risk"] == "risk-env"
    assert ms["risk_parallel"] is True
    assert _mini_keys(ms) == ("slot-i", "slot-s", "slot-o")


def test_benchmark_label_fallback_renders_three_risk_rows():
    """Flipped in C3 (was: a single risk row). Literal snapshot of the risk lines and their neighbours."""
    md = format_models_used_markdown(_fallback(), primary_model="policy-m")

    assert (
        "| **MoralStack policy (rewrite)** | policy-m |\n"
        "| **MoralStack risk** | parallel mini-estimators |\n"
        "| **MoralStack risk · intent** | policy-m |\n"
        "| **MoralStack risk · signals** | policy-m |\n"
        "| **MoralStack risk · operational** | policy-m |\n"
        "| **MoralStack critic** | policy-m |\n"
    ) in md
