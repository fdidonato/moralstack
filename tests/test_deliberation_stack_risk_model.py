"""Characterization pins for how the shared builder wires the risk policy and its model (phase C0).

``build_deliberation_modules`` is the one place that picks the risk policy for the SDK and the CLI:
``MORALSTACK_RISK_MODEL`` unset -> the primary policy object; set -> a second ``OpenAIPolicy``.
These pins stay green unmodified through C1-C4, except the slot-value test, which flips in C3.

``OpenAIPolicy`` and ``ConstitutionStore`` are stubbed in the builder's namespace (no key, no
network, no constitution load). The autouse fixture clears every env knob the builder reads.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.pipeline import deliberation_stack
from moralstack.pipeline.deliberation_stack import build_deliberation_modules


class RecordingPolicy:
    """Stand-in for ``OpenAIPolicy``: keeps the constructor kwargs for assertions."""

    instances: list[RecordingPolicy] = []

    def __init__(
        self, api_key: str | None = None, model: str | None = None, base_url: str | None = None, prompt_set: object = None
    ) -> None:
        self.api_key = api_key
        self.model = model or "gpt-4o"
        self.base_url = base_url
        RecordingPolicy.instances.append(self)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("MORALSTACK_RISK_"):
            monkeypatch.delenv(key, raising=False)
    for key in ("OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "MORALSTACK_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    RecordingPolicy.instances = []


def _build(**kwargs: Any):
    with (
        patch.object(deliberation_stack, "OpenAIPolicy", RecordingPolicy),
        patch.object(deliberation_stack, "ConstitutionStore", MagicMock()),
    ):
        return build_deliberation_modules(api_key="k-builder", minimal=True, **kwargs)


def test_builder_model_resolution_matrix_risk_model_unset_uses_the_primary_policy():
    modules, meta = _build(primary_model="sdk-model", base_url="https://builder.example/v1")

    assert meta.risk_model == "sdk-model"
    assert meta.policy_model == "sdk-model"
    assert modules.risk_estimator.policy is modules.policy
    assert len(RecordingPolicy.instances) == 1


def test_builder_model_resolution_matrix_risk_model_set_builds_a_second_policy_without_base_url(monkeypatch):
    """Documents an out-of-scope gap: the risk policy drops the builder ``base_url`` (Hypothesis row 2)."""
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "risk-x")
    modules, meta = _build(primary_model="sdk-model", base_url="https://builder.example/v1")

    assert meta.risk_model == "risk-x"
    assert meta.policy_model == "sdk-model"
    risk_policy = modules.risk_estimator.policy
    assert risk_policy is not modules.policy
    assert risk_policy.model == "risk-x"
    assert risk_policy.base_url is None
    assert risk_policy.api_key == "k-builder"
    assert modules.policy.base_url == "https://builder.example/v1"


def test_builder_whitespace_risk_model_counts_as_unset(monkeypatch):
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "   ")
    modules, meta = _build(primary_model="sdk-model")

    assert meta.risk_model == "sdk-model"
    assert modules.risk_estimator.policy is modules.policy


def test_builder_slots_are_none_and_minis_inherit_the_primary_model_not_env_openai_model(monkeypatch):
    """Flipped in C3 (was: slots read from env ``OPENAI_MODEL``, ignoring ``primary_model``).

    Unset slots are now None and inherit the risk policy's model, so an SDK
    ``GovernanceConfig(model=...)`` that differs from env ``OPENAI_MODEL`` drives the three minis.
    """
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    modules, meta = _build(primary_model="sdk-model")

    estimator = modules.risk_estimator
    cfg = estimator.config
    assert (cfg.intent_model, cfg.signals_model, cfg.operational_model) == (None, None, None)
    assert meta.risk_model == "sdk-model"
    for mini in ("estimate_intent", "estimate_signals", "estimate_operational"):
        assert estimator._effective_mini_model(mini) == "sdk-model"


def test_builder_risk_model_drives_all_three_minis(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setenv("MORALSTACK_RISK_MODEL", "risk-x")
    modules, meta = _build(primary_model="sdk-model")

    assert meta.risk_model == "risk-x"
    for mini in ("estimate_intent", "estimate_signals", "estimate_operational"):
        assert modules.risk_estimator._effective_mini_model(mini) == "risk-x"
