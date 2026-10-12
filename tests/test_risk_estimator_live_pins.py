"""Characterization pins for the live risk-estimator path (3 mini-estimators + calibration guard).

These tests pin behavior that was previously covered only through retired entry points
(``parse_risk_response``) or not covered at all: the merge -> ``parse_risk_dict`` round trip,
the crisis clamp, the calibration-guard row emitted by ``estimate()``, and the empty-prompt
short circuit. Expected values were observed on the code at the time of writing, never guessed.

Only names that are part of the live path are used, so the file compiles unchanged before and
after the removal of the single-estimator residues.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

import moralstack.models.risk.estimator as risk_estimator_module
from moralstack.models.risk.calibration import merge_mini_estimator_results, parse_risk_dict
from moralstack.models.risk.categories import RiskCategory, RiskPolicyAction
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.prompts import INTENT_CONTEXT_SYSTEM_PROMPT, OPERATIONAL_RISK_SYSTEM_PROMPT
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.prompt_renderer import get_harm_signal_prompts
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.observability.context import set_current_cycle, set_current_request_id, set_current_run_id

_SIGNALS_SYSTEM_PROMPT = get_harm_signal_prompts(signal_registry)[0]

_MINI_BY_SYSTEM_PROMPT = {
    INTENT_CONTEXT_SYSTEM_PROMPT: "estimate_intent",
    _SIGNALS_SYSTEM_PROMPT: "estimate_signals",
    OPERATIONAL_RISK_SYSTEM_PROMPT: "estimate_operational",
}


class FakePolicy:
    """Routes canned JSON by system-prompt identity; records calls keyed by mini name.

    Calls are keyed by mini name (never by arrival order) because the three minis run on a
    thread pool and complete in arbitrary order.
    """

    model = "m"
    tracker = None

    def __init__(self, intent: dict[str, Any], signals: dict[str, Any], operational: dict[str, Any]) -> None:
        self._by_mini = {
            "estimate_intent": intent,
            "estimate_signals": signals,
            "estimate_operational": operational,
        }
        self.calls: dict[str, list[dict[str, Any]]] = {name: [] for name in self._by_mini}

    def _answer(self, system: str, record: dict[str, Any]) -> SimpleNamespace:
        mini = _MINI_BY_SYSTEM_PROMPT[system]
        self.calls[mini].append(record)
        return SimpleNamespace(text=json.dumps(self._by_mini[mini]))

    def generate(self, *, prompt: str, system: str, config: Any = None) -> SimpleNamespace:
        return self._answer(system, {"kind": "generate", "prompt": prompt, "system": system})

    def generate_messages(self, *, messages: list[dict[str, str]], config: Any = None) -> SimpleNamespace:
        return self._answer(messages[0]["content"], {"kind": "messages", "messages": messages})

    @property
    def total_calls(self) -> int:
        return sum(len(v) for v in self.calls.values())


_CONFIG = RiskEstimatorConfig(
    intent_model="m",
    signals_model="m",
    operational_model="m",
    max_retries=1,
)


@pytest.fixture(autouse=True)
def _isolated_risk_env(monkeypatch):
    """Drop every risk env knob and the DB path so defaults apply and nothing is persisted."""
    for key in list(os.environ):
        if key.startswith("MORALSTACK_RISK_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    set_current_run_id("run-live-pins")
    set_current_request_id("req-live-pins")
    set_current_cycle(0)


@pytest.fixture
def captured(monkeypatch):
    """Capture what the estimator enqueues: batches (the 3 minis) and single routes (guard row)."""
    batches: list[list[Any]] = []
    singles: list[Any] = []
    monkeypatch.setattr(risk_estimator_module, "_obs_route_batch", lambda envs: batches.append(list(envs)))
    monkeypatch.setattr(risk_estimator_module, "_obs_route", lambda env: singles.append(env))
    return SimpleNamespace(batches=batches, singles=singles)


_BENIGN_INTENT = {
    "request_type": "factual_query",
    "intent_to_harm": "no",
    "requested_instructions": "no",
    "harm_type": "none",
}


def _crisis_intent(request_type: str = "sensitive_topic", **extra: Any) -> dict[str, Any]:
    return {
        "request_type": request_type,
        "intent_to_harm": "no",
        "requested_instructions": "no",
        "harm_type": "emotional_relational",
        **extra,
    }


_CRISIS_SIGNALS = {
    "q7_only_emotional": "yes",
    "q8_self_harm_suicide": "yes",
    "domain_sensitivity": "HIGH",
}


def _estimate(intent: dict[str, Any], signals: dict[str, Any], operational: dict[str, Any], **kwargs: Any):
    policy = FakePolicy(intent, signals, operational)
    estimator = LLMBasedRiskEstimator(policy=policy, config=_CONFIG)
    return estimator.estimate("hello world", **kwargs), policy


# --------------------------------------------------------------------------
# A-B: merge -> parse_risk_dict round trip
# --------------------------------------------------------------------------

_INTENT_KEYS = {
    "detected_language",
    "intent_to_harm",
    "requested_instructions",
    "intent_operational",
    "request_type",
    "harm_type",
    "intent_clarity",
    "stated_personal_bias",
    "seeks_norm_circumvention",
    "rationale",
}
_OPERATIONAL_KEYS = {
    "operational_risk",
    "risk_score",
    "confidence",
    "misuse_plausibility",
    "actionability_risk",
    "risk_policy_action",
}


def _split_by_owner(full: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Split a monolithic-shaped dict by the key ownership of merge_mini_estimator_results."""
    intent = {k: v for k, v in full.items() if k in _INTENT_KEYS}
    operational = {k: v for k, v in full.items() if k in _OPERATIONAL_KEYS}
    signals = {k: v for k, v in full.items() if k not in _INTENT_KEYS and k not in _OPERATIONAL_KEYS}
    return intent, signals, operational


_NO_Q = {
    "q1_confidential": "no",
    "q2_unauthorized": "no",
    "q3_breach": "no",
    "q4_fraud": "no",
    "q5_physical": "no",
    "q6_illegal_items": "no",
    "q7_only_emotional": "no",
    "q8_self_harm_suicide": "no",
    "q10_weapons_explosives_toxins": "no",
    "q11_privacy_doxxing_stalking": "no",
    "q12_medical_harmful": "no",
}

_ROUNDTRIP_CASES = {
    "ethics_hacking": {
        **_NO_Q,
        "q9_cyber_malware": "yes",
        "requested_instructions": "no",
        "intent_to_harm": "no",
        "intent_operational": False,
        "operational_risk": "NONE",
        "request_type": "sensitive_topic",
        "harm_type": "none",
        "risk_score": 0.7,
        "confidence": 0.8,
        "domain_sensitivity": "MEDIUM",
        "rationale": "Discussion about ethics of hacking, no how-to requested",
    },
    "conceptual_q9": {
        **_NO_Q,
        "q9_cyber_malware": "yes",
        "requested_instructions": "no",
        "intent_to_harm": "no",
        "intent_operational": False,
        "operational_risk": "NONE",
        "request_type": "sensitive_topic",
        "harm_type": "none",
        "risk_score": 0.5,
        "confidence": 0.8,
        "domain_sensitivity": "MEDIUM",
        "rationale": "Conceptual discussion about malware, no instructions",
    },
    "howto_score": {
        **_NO_Q,
        "q9_cyber_malware": "yes",
        "requested_instructions": "yes",
        "intent_to_harm": "no",
        "intent_operational": True,
        "operational_risk": "LOW",
        "request_type": "potential_harm",
        "harm_type": "illegal",
        "risk_score": 0.35,
        "confidence": 0.8,
        "domain_sensitivity": "LOW",
        "rationale": "How-to request for bypass, not clear_harm",
    },
    "howto_bypass_2fa": {
        **_NO_Q,
        "q9_cyber_malware": "yes",
        "requested_instructions": "yes",
        "intent_to_harm": "no",
        "intent_operational": True,
        "operational_risk": "HIGH",
        "request_type": "clear_harm",
        "harm_type": "illegal",
        "risk_score": 0.9,
        "confidence": 0.85,
        "domain_sensitivity": "LOW",
        "rationale": "Request for how-to bypass 2FA",
    },
}


@pytest.mark.parametrize("case", sorted(_ROUNDTRIP_CASES))
def test_merge_roundtrip_matches_single_dict(case):
    """parse_risk_dict(merge(split(d))) must satisfy the same calibration assertions as the single dict."""
    full = _ROUNDTRIP_CASES[case]
    merged = merge_mini_estimator_results(*_split_by_owner(full))
    out = parse_risk_dict(merged)

    if case in ("ethics_hacking", "conceptual_q9"):
        assert out.category != RiskCategory.CLEARLY_HARMFUL
        assert out.score <= 0.3
        assert out.risk_policy_action != RiskPolicyAction.DENY
    elif case == "howto_score":
        assert out.score >= 0.5
    else:
        assert out.category == RiskCategory.CLEARLY_HARMFUL
        assert out.risk_policy_action == RiskPolicyAction.DENY


# --------------------------------------------------------------------------
# A-C: crisis clamp through estimate()
# --------------------------------------------------------------------------


def test_crisis_clamp_low(captured):
    """Self-harm help-seeking with a low raw score is raised to the low clamp (0.35)."""
    est, _ = _estimate(
        _crisis_intent(),
        _CRISIS_SIGNALS,
        {
            "risk_score": 0.1,
            "confidence": 0.85,
            "operational_risk": "NONE",
            "risk_policy_action": "DELIBERATE",
            "rationale": "x",
        },
    )
    assert est.score == 0.35
    assert est.risk_category == RiskCategory.SENSITIVE
    assert est.risk_policy_action == RiskPolicyAction.DELIBERATE
    assert est.request_type == "crisis_support"


def test_crisis_high_after_guard(captured):
    """A 0.9 raw score is capped by the guard first, then lands on the low clamp (0.35), not above it."""
    est, _ = _estimate(
        _crisis_intent(),
        _CRISIS_SIGNALS,
        {
            "risk_score": 0.9,
            "confidence": 0.85,
            "operational_risk": "NONE",
            "risk_policy_action": "DELIBERATE",
            "rationale": "x",
        },
    )
    assert est.score == 0.35
    assert est.risk_category == RiskCategory.SENSITIVE
    assert est.request_type == "crisis_support"
    # The guard fired for this input (benign request_type, no harm intent).
    assert len(captured.singles) == 1


def test_crisis_clamp_high_without_guard(captured):
    """request_type outside the guard set and an operational intent reach the high clamp (0.65)."""
    est, _ = _estimate(
        _crisis_intent(request_type="potential_harm", harm_type="physical", intent_operational=True),
        {"q8_self_harm_suicide": "yes"},
        {
            "risk_score": 0.99,
            "confidence": 0.85,
            "operational_risk": "HIGH",
            "risk_policy_action": "DENY",
            "rationale": "x",
        },
    )
    assert est.score == 0.65
    assert est.risk_category == RiskCategory.SENSITIVE
    assert est.risk_policy_action == RiskPolicyAction.DELIBERATE
    assert est.request_type == "crisis_support"
    assert captured.singles == []


# --------------------------------------------------------------------------
# A-D: calibration-guard row emitted by estimate()
# --------------------------------------------------------------------------

_GUARD_OPERATIONAL = {
    "risk_score": 0.9,
    "confidence": 0.8,
    "operational_risk": "HIGH",
    "risk_policy_action": "DENY",
    "misuse_plausibility": "HIGH",
    "actionability_risk": "HIGH",
    "rationale": "x",
}


def test_estimate_emits_calibration_guard_row(captured):
    # sensitive_topic is a guard request type whose final score is not lowered further by parse_risk_dict.
    intent = {**_BENIGN_INTENT, "request_type": "sensitive_topic"}
    est, policy = _estimate(intent, {"q1_confidential": "no"}, _GUARD_OPERATIONAL)

    assert policy.total_calls == 3
    # Exactly one batch with the 3 minis, in the fixed order.
    assert len(captured.batches) == 1
    assert [env.payload["action"] for env in captured.batches[0]] == [
        "estimate_intent",
        "estimate_signals",
        "estimate_operational",
    ]
    # Exactly one single enqueue: the synthetic guard row (catches a duplicated enqueue).
    assert len(captured.singles) == 1
    payload = captured.singles[0].payload
    assert payload["action"] == "calibration_guard"
    assert payload["sequence_in_cycle"] == -8
    assert payload["billable_provider_call"] is False
    # Changed in C3 of risk-estimator-dual-config: synthetic row, no provider call, so no model is persisted.
    assert policy.model == "m"
    assert payload["model"] is None
    assert payload["prompt"] == "<synthetic — no LLM call>"
    raw = json.loads(payload["raw_response"])
    assert raw["guard_applied"] is True
    assert raw["request_type"] == "sensitive_topic"
    assert "risk_score 0.90→0.45" in raw["notes"]
    assert raw["caps_applied"] == {
        "risk_score_max": 0.45,
        "operational_risk_max": "LOW",
        "risk_policy_action_max": "DELIBERATE",
        "misuse_plausibility_max": "MEDIUM",
        "actionability_risk_max": "MEDIUM",
    }
    assert est.score == 0.45
    assert est.risk_category == RiskCategory.SENSITIVE
    assert est.risk_policy_action == RiskPolicyAction.DELIBERATE


def test_estimate_q17_bypasses_guard(captured):
    """q17 (minor exploitation) must not be recalibrated: no guard row and no cap."""
    est, _ = _estimate(
        _BENIGN_INTENT,
        {"q17_minor_exploitation": "yes"},
        {
            "risk_score": 0.9,
            "confidence": 0.8,
            "operational_risk": "HIGH",
            "risk_policy_action": "DENY",
            "rationale": "x",
        },
    )
    assert captured.singles == []
    assert len(captured.batches) == 1 and len(captured.batches[0]) == 3
    assert est.score == 0.9
    assert est.risk_category == RiskCategory.CLEARLY_HARMFUL
    assert est.risk_policy_action == RiskPolicyAction.DENY


# --------------------------------------------------------------------------
# A-E: empty prompt short circuit
# --------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", ["", "  \n\t "])
def test_estimate_empty_prompt_makes_no_llm_call(prompt, captured):
    policy = FakePolicy({}, {}, {})
    est = LLMBasedRiskEstimator(policy=policy, config=_CONFIG).estimate(prompt)

    assert est.risk_category == RiskCategory.BENIGN
    assert est.score == 0.1
    assert est.confidence == 1.0
    assert policy.total_calls == 0
    assert captured.batches == [] and captured.singles == []


# --------------------------------------------------------------------------
# Native-message context path
# --------------------------------------------------------------------------


def test_contract_and_history_travel_as_native_messages(captured):
    """With a contract/history and a policy exposing generate_messages, each mini gets native messages."""
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
    _, policy = _estimate(
        _BENIGN_INTENT,
        {"q1_confidential": "no"},
        {"risk_score": 0.1, "confidence": 0.9, "operational_risk": "NONE", "risk_policy_action": "ALLOW"},
        developer_contract_text="  You are a chemistry assistant  ",
        conversation_history=history,
    )
    for mini, calls in policy.calls.items():
        assert len(calls) == 1, mini
        assert calls[0]["kind"] == "messages"
        roles = [m["role"] for m in calls[0]["messages"]]
        assert roles == ["system", "developer", "user", "assistant", "user"]
        assert calls[0]["messages"][1]["content"] == "You are a chemistry assistant"
