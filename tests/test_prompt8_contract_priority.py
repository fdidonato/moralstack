"""
Prompt 8 regression tests: DEVELOPER CONTRACT priority in intent mini-estimator.

All tests use mocks only (no network).
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.prompts import INTENT_CONTEXT_PROMPT_TEMPLATE, INTENT_CONTEXT_SYSTEM_PROMPT
from moralstack.models.risk.schema import RiskEstimatorConfig

_INTENT_JSON = """{
  "rationale": "mock",
  "detected_language": "en",
  "intent_to_harm": "no",
  "requested_instructions": "no",
  "intent_operational": false,
  "request_type": "factual_query",
  "harm_type": "none",
  "intent_clarity": "HIGH",
  "stated_personal_bias": "no",
  "seeks_norm_circumvention": "no"
}"""


class TestIntentTemplateStep0Present:
    def test_step0_and_examples_and_deferral_notes(self) -> None:
        # Prompt-caching reorder (A1): STEP 0 + the worked examples are static
        # content and now live in the system prefix, not the per-request user
        # template. `INTENT_CONTEXT_PROMPT_TEMPLATE` is dynamic-only (REQUEST +
        # constitution_context); the content-preservation guarantee is that
        # this text is still present, just relocated to the system prompt.
        t = INTENT_CONTEXT_SYSTEM_PROMPT
        assert "STEP 0 — DEVELOPER CONTRACT CHECK" in t
        assert "PRIORITY" in t
        assert "EXAMPLE A" in t
        assert "EXAMPLE B" in t
        assert "EXAMPLE C" in t
        assert "do NOT classify as OFFENSIVE" in t
        assert "does NOT apply to the execution of a benign" in t
        assert "DEVELOPER CONTRACT rule (e.g." in t
        # And confirm it did NOT stay behind in the dynamic user template.
        assert "STEP 0 — DEVELOPER CONTRACT CHECK" not in INTENT_CONTEXT_PROMPT_TEMPLATE


class TestIntentSystemPromptInvariant6Priority:
    def test_priority_and_safety_override_unchanged_substance(self) -> None:
        p = INTENT_CONTEXT_SYSTEM_PROMPT
        assert "PRIORITY OVER ALL OTHER FRAMINGS" in p
        assert "RULE EXECUTION IS THE EXPECTED LEGITIMATE BEHAVIOR" in p
        assert "This is NOT an attack" in p
        assert "PROMPT INJECTION ATTEMPT" not in p
        assert "weapons design" in p
        assert "synthesis" in p
        assert "child sexual abuse material" in p


@pytest.fixture
def _isolated_risk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)


@pytest.fixture
def risk_config() -> RiskEstimatorConfig:
    return RiskEstimatorConfig(
        intent_model="test-model",
        signals_model="test-model",
        operational_model="test-model",
        max_retries=1,
    )


@pytest.fixture
def mock_policy() -> MagicMock:
    policy = MagicMock()
    policy.model = "test-model"
    policy.generate.return_value = type("R", (), {"text": _INTENT_JSON})()
    return policy


@pytest.fixture
def llm_risk_estimator(mock_policy: MagicMock, risk_config: RiskEstimatorConfig) -> LLMBasedRiskEstimator:
    return LLMBasedRiskEstimator(policy=mock_policy, config=risk_config, constitution_store=None)


class TestNoContractInjectedBlockAbsent:
    def test_estimate_without_contract_no_deployer_block_in_intent_user_prompt(
        self,
        _isolated_risk_env: None,
        llm_risk_estimator: LLMBasedRiskEstimator,
        mock_policy: MagicMock,
    ) -> None:
        def _gen(*args: Any, **kwargs: Any) -> Any:
            return type("R", (), {"text": _INTENT_JSON})()

        mock_policy.generate.side_effect = _gen

        with (
            patch.object(llm_risk_estimator, "_get_principles_context", return_value=("", None)),
            patch.object(llm_risk_estimator, "_policy_for_mini_estimator_model", return_value=mock_policy),
            patch("moralstack.models.risk.estimator.persist_llm_call", return_value=False),
        ):
            llm_risk_estimator.estimate("hello world")

        intent_calls = [
            c for c in mock_policy.generate.call_args_list if "SEMANTIC INTENT JUDGE" in str(c.kwargs.get("system", ""))
        ]
        assert len(intent_calls) == 1
        system_prompt = str(intent_calls[0].kwargs.get("system", ""))
        user_prompt = str(intent_calls[0].kwargs.get("prompt", ""))
        assert "DEVELOPER CONTRACT (system prompt declared" not in user_prompt
        assert "DEVELOPER CONTRACT (system prompt declared" not in system_prompt
        # STEP 0 is static content and is sent in the system prefix (A1 reorder).
        assert "STEP 0 — DEVELOPER CONTRACT CHECK" in system_prompt


class TestTemplateExamplesAntiBenchmarkLeak:
    def test_no_password_phrase_no_mixed_case_token_pattern(self) -> None:
        # The worked examples (AGENT / CERT-A18-OK / PROVIDE_SYNTH) are static
        # and now live in the system prompt (A1 reorder); the dynamic user
        # template no longer carries them.
        t = INTENT_CONTEXT_SYSTEM_PROMPT
        assert "the password" not in t
        assert re.search(r"[A-Z][a-z][A-Z]\d", t) is None
        assert "AGENT" in t
        assert "CERT-A18-OK" in t
        assert "PROVIDE_SYNTH" in t


class TestExampleCPreservesSafetyCounterExample:
    def test_example_c_mentions_safety_override_near_counter_example(self) -> None:
        t = INTENT_CONTEXT_SYSTEM_PROMPT
        start = t.index("EXAMPLE C")
        end = t.index("These three examples", start)
        window = t[start:end]
        assert "safety override" in window.lower()
        assert "counter-example" in window
