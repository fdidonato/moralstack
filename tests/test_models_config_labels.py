"""Characterization pins for the report "Models used" labels built from env (phase C0).

``_get_benchmark_models_config_fallback`` has no coverage today. It labels the risk module with a
single ``risk`` key and no ``risk_parallel`` flag, so ``format_models_used_markdown`` renders one
risk row. C3 makes it return ``risk_parallel`` plus the three mini keys; the label pins below flip
there and each flip is justified in the C3 commit message.

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


def test_benchmark_label_fallback_matrix_nothing_set():
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
    }


def test_benchmark_label_fallback_matrix_risk_model_only(monkeypatch):
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "risk-env")
    ms = _fallback()["moralstack"]

    assert ms["risk"] == "risk-env"
    assert "risk_parallel" not in ms
    assert not {"risk_intent", "risk_signals", "risk_operational"} & set(ms)


def test_benchmark_label_fallback_matrix_slots_are_not_labelled_today(monkeypatch):
    """Flips in C3: per-mini slots set in env do not reach the fallback labels at all today."""
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "risk-env")
    monkeypatch.setenv("MORALSTACK_RISK_INTENT_MODEL", "slot-i")
    monkeypatch.setenv("MORALSTACK_RISK_SIGNALS_MODEL", "slot-s")
    monkeypatch.setenv("MORALSTACK_RISK_OPERATIONAL_MODEL", "slot-o")
    ms = _fallback()["moralstack"]

    assert ms["risk"] == "risk-env"
    assert "risk_parallel" not in ms
    assert not {"risk_intent", "risk_signals", "risk_operational"} & set(ms)


def test_benchmark_label_fallback_renders_a_single_risk_row_today():
    """Flips in C3 (three rows). Literal snapshot of the risk line and its neighbours."""
    md = format_models_used_markdown(_fallback(), primary_model="policy-m")

    assert (
        "| **MoralStack policy (rewrite)** | policy-m |\n"
        "| **MoralStack risk** | policy-m |\n"
        "| **MoralStack critic** | policy-m |\n"
    ) in md
    assert "risk · intent" not in md
