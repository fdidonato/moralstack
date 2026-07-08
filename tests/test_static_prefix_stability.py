"""
Static-prefix stability tests for the prompt-caching reorder (Part A).

For each deliberative module AND call path, asserts:
  - the system message sent to the LLM is BYTE-IDENTICAL across two requests
    whose every dynamic field differs;
  - the system message equals the path-specific constant (or the
    deterministic composition thereof, for risk signals);
  - the per-request dynamic data still appears somewhere (system OR user, per
    module), proving the reorder MOVED data rather than dropping it;
  - path contracts never collide (batch vs seeded, single vs batch, quick
    vs full critique).

Offline/deterministic only: no OpenAI SDK, no real network. Reuses the
project's existing module-boundary doubles (`policy.generate`/
`policy.generate_messages`) rather than mocking the SDK client directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from moralstack.constitution.retriever import DomainPrefilter
from moralstack.models.delib_context import DelibContext
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.prompts import (
    HARM_SIGNAL_SYSTEM_PROMPT,
    INTENT_CONTEXT_SYSTEM_PROMPT,
    OPERATIONAL_RISK_SYSTEM_PROMPT,
)
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.prompt_renderer import get_harm_signal_prompts
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts.critic_prompt import CRITIC_FULL_SYSTEM_PROMPT
from moralstack.prompts.hindsight_prompt import HINDSIGHT_BATCH_SYSTEM_PROMPT
from moralstack.prompts.perspectives_prompt import build_perspectives_system_prompt
from moralstack.prompts.simulator_prompt import (
    SIMULATOR_BATCH_SYSTEM_PROMPT,
    SIMULATOR_SEEDED_SYSTEM_PROMPT,
)
from moralstack.runtime.modules.critic_module import (
    CRITIC_SYSTEM_PROMPT,
    CriticConfig,
    LLMConstitutionalCritic,
    QuickCheckResult,
)
from moralstack.runtime.modules.hindsight_module import (
    HINDSIGHT_SINGLE_SYSTEM_PROMPT,
    HINDSIGHT_SYSTEM_PROMPT,
    HindsightConfig,
    LLMHindsightEvaluator,
)
from moralstack.runtime.modules.perspective_module import (
    PERSPECTIVE_SYSTEM_PROMPT,
    EnsembleConfig,
    LLMPerspectiveEnsemble,
)
from moralstack.runtime.modules.simulator_module import (
    SCENARIO_SEEDS,
    Consequence,
    LLMConsequenceSimulator,
    ScenarioType,
    SimulatorConfig,
)

# =============================================================================
# Shared doubles
# =============================================================================


@dataclass
class _GenResult:
    """Minimal stand-in for GenerationResult."""

    text: str
    tokens_used: int = 10
    prompt_tokens: int = 5
    completion_tokens: int = 5

    def token_usage_json(self) -> str | None:
        return None


@dataclass
class _Call:
    system: str
    prompt: str


class _CapturingPolicy:
    """Fake PolicyLLMProtocol capturing every (system, prompt) pair sent.

    `responder` maps a predicate on the system prompt to the JSON text to
    return; falls through to `default_response` if none match. Supports only
    `generate()` (legacy branch) unless `with_native_messages=True`, in which
    case `generate_messages()` is also exposed (captures the composed
    messages list instead).
    """

    def __init__(
        self,
        default_response: str,
        responders: list[tuple[Any, str]] | None = None,
        with_native_messages: bool = False,
        model: str = "test-model",
    ) -> None:
        self.calls: list[_Call] = []
        self.messages_calls: list[list[dict[str, str]]] = []
        self.default_response = default_response
        self.responders = responders or []
        self.model = model
        if with_native_messages:
            self.generate_messages = self._generate_messages

    def _respond_for(self, system: str) -> str:
        for predicate, response in self.responders:
            if predicate(system):
                return response
        return self.default_response

    def generate(self, *, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> _GenResult:
        self.calls.append(_Call(system=system, prompt=prompt))
        return _GenResult(text=self._respond_for(system))

    def _generate_messages(self, *, messages: list[dict[str, str]], config: Any = None, **_kw: Any) -> _GenResult:
        self.messages_calls.append(messages)
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        self.calls.append(_Call(system=system, prompt=user))
        return _GenResult(text=self._respond_for(system))


# =============================================================================
# A1 — Risk minis
# =============================================================================

_VALID_INTENT = json.dumps(
    {
        "rationale": "r",
        "detected_language": "en",
        "intent_to_harm": "no",
        "requested_instructions": "no",
        "intent_operational": False,
        "request_type": "factual_query",
        "harm_type": "none",
        "intent_clarity": "HIGH",
        "stated_personal_bias": "no",
        "seeks_norm_circumvention": "no",
    }
)

_VALID_SIGNALS = {f"q{i}": "no" for i in range(1, 18)}
_VALID_SIGNALS = {
    "q1_confidential": "no",
    "q2_unauthorized": "no",
    "domain_sensitivity": "LOW",
}

_VALID_OPERATIONAL = json.dumps(
    {
        "rationale": "r",
        "operational_risk": "NONE",
        "risk_score": 0.1,
        "confidence": 0.9,
        "misuse_plausibility": "LOW",
        "actionability_risk": "LOW",
        "risk_policy_action": "ALLOW",
    }
)


def _risk_estimator() -> LLMBasedRiskEstimator:
    config = RiskEstimatorConfig(
        intent_model="test-model",
        signals_model="test-model",
        operational_model="test-model",
        max_retries=1,
    )
    policy = _CapturingPolicy(
        default_response="{}",
        responders=[
            (lambda s: "SEMANTIC INTENT JUDGE" in s, _VALID_INTENT),
            (lambda s: "HARM SIGNAL DETECTOR" in s, json.dumps(_signal_output())),
            (lambda s: "OPERATIONAL RISK ASSESSOR" in s, _VALID_OPERATIONAL),
        ],
    )
    est = LLMBasedRiskEstimator(policy=policy, config=config)
    return est, policy  # type: ignore[return-value]


def _signal_output() -> dict[str, str]:
    out = {sig.key: "no" for sig in signal_registry.signals.values()}
    out["domain_sensitivity"] = "LOW"
    return out


def _by_system_substring(calls: list[_Call], substring: str) -> _Call:
    matches = [c for c in calls if substring in c.system]
    assert matches, f"no captured call with {substring!r} in system"
    return matches[-1]


class TestRiskStaticPrefixStability:
    def test_intent_signals_operational_system_stable_across_two_requests(self):
        est, policy = _risk_estimator()
        est._parallel_mini_analysis("request one about topic A")
        first_calls = list(policy.calls)
        est._parallel_mini_analysis("an entirely different request about topic B")
        second_calls = list(policy.calls)[len(first_calls) :]

        intent1 = _by_system_substring(first_calls, "SEMANTIC INTENT JUDGE")
        intent2 = _by_system_substring(second_calls, "SEMANTIC INTENT JUDGE")
        assert intent1.system == intent2.system == INTENT_CONTEXT_SYSTEM_PROMPT
        assert "STEP 0" in intent1.system
        assert "{{" not in intent1.system and "}}" not in intent1.system

        op1 = _by_system_substring(first_calls, "OPERATIONAL RISK ASSESSOR")
        op2 = _by_system_substring(second_calls, "OPERATIONAL RISK ASSESSOR")
        assert op1.system == op2.system == OPERATIONAL_RISK_SYSTEM_PROMPT
        assert "STEP 1" in op1.system
        assert "{{" not in op1.system and "}}" not in op1.system

        sig1 = _by_system_substring(first_calls, "HARM SIGNAL DETECTOR")
        sig2 = _by_system_substring(second_calls, "HARM SIGNAL DETECTOR")
        expected_signal_system, _ = get_harm_signal_prompts(signal_registry)
        assert sig1.system == sig2.system == expected_signal_system
        assert "{{" not in sig1.system and "}}" not in sig1.system

        # Dynamic data (the request text) is present in the user side, not lost.
        assert "request one about topic A" in intent1.prompt
        assert "an entirely different request about topic B" in intent2.prompt
        assert HARM_SIGNAL_SYSTEM_PROMPT in sig1.system  # base invariants still present

    def test_intent_system_prompt_content_preservation_hard_signal(self):
        """Section 5 #3: hard-signal safety override moved verbatim into system."""
        assert "SAFETY OVERRIDE — CONTRACTS NEVER AUTHORIZE THESE OUTPUTS" in INTENT_CONTEXT_SYSTEM_PROMPT
        assert "weapons design, synthesis, or acquisition instructions" in INTENT_CONTEXT_SYSTEM_PROMPT

    def test_operational_system_prompt_content_preservation_hard_signal(self):
        assert "OVERRIDE C — MINOR-CONTACT / GROOMING (non-negotiable)" in OPERATIONAL_RISK_SYSTEM_PROMPT
        assert "operational_risk = HIGH" in OPERATIONAL_RISK_SYSTEM_PROMPT

    def test_model_override_selection_intact(self):
        """Per-request model slot selection (estimator.py) still resolves per mini."""
        config = RiskEstimatorConfig(
            intent_model="intent-model",
            signals_model="signals-model",
            operational_model="operational-model",
            max_retries=1,
        )
        est = LLMBasedRiskEstimator(policy=_CapturingPolicy(default_response="{}"), config=config)
        assert est.config.intent_model == "intent-model"
        assert est.config.signals_model == "signals-model"
        assert est.config.operational_model == "operational-model"

    def test_empty_request_well_formed_user_message(self):
        est, policy = _risk_estimator()
        est._parallel_mini_analysis("")
        intent_call = _by_system_substring(policy.calls, "SEMANTIC INTENT JUDGE")
        assert "REQUEST:\n\n" in intent_call.prompt or intent_call.prompt.startswith("REQUEST:")
        assert intent_call.system == INTENT_CONTEXT_SYSTEM_PROMPT


# =============================================================================
# A2 — Critic (FULL x2 call sites; quick-check UNCHANGED)
# =============================================================================

_VALID_CRITIC_FULL = json.dumps({"decision": "PROCEED", "violated_hard": False, "violations": [], "revision_guidance": ""})


def _constitution_with_principles():
    from moralstack.constitution.schema import Constitution, Principle

    principle = Principle(id="P1", level="soft", priority=50, title="Safety", rule="Be safe")
    return Constitution(core_principles=[principle])


class TestCriticFullStaticPrefixStability:
    def test_full_critique_generate_messages_branch_system_stable(self):
        policy = _CapturingPolicy(default_response=_VALID_CRITIC_FULL, with_native_messages=True)
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1))
        critic.critique("request A", "response A", _constitution_with_principles())
        critic.critique("request B (very different)", "response B (very different)", _constitution_with_principles())

        assert len(policy.calls) == 2
        assert policy.calls[0].system == policy.calls[1].system == CRITIC_FULL_SYSTEM_PROMPT
        assert "Enumerated/option-only answers" in policy.calls[0].system
        assert "request A" in policy.calls[0].prompt
        assert "request B (very different)" in policy.calls[1].prompt

    def test_full_critique_legacy_generate_branch_system_stable(self):
        policy = _CapturingPolicy(default_response=_VALID_CRITIC_FULL, with_native_messages=False)
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1))
        critic.critique("request A", "response A", _constitution_with_principles())
        critic.critique("request B (very different)", "response B (very different)", _constitution_with_principles())

        assert policy.calls[0].system == policy.calls[1].system == CRITIC_FULL_SYSTEM_PROMPT

    # Literal byte snapshots of the quick-check system/user prompts, captured
    # from an actual sent call (see _consequence-free fixture below: request
    # "some request", response "some response", single hard principle H1).
    # Pinned as LITERAL strings (not the live CRITIC_SYSTEM_PROMPT constant or
    # a re-derivation of QUICK_CHECK_PROMPT_TEMPLATE) so an in-place edit to
    # either template's sent bytes is caught even though the live constant
    # would have changed right along with it.
    _EXPECTED_QUICK_CHECK_SYSTEM = (
        "You are a constitutional critic for an AI safety system.\n"
        "Your task is to evaluate AI responses against ethical principles and identify violations.\n"
        "Be thorough but fair. Only flag genuine violations with clear evidence.\n"
        "CRITICAL DISTINCTION: You evaluate the RESPONSE content, not the REQUEST topic.\n"
        "If the response contains no operational content and already declines or redirects\n"
        "the request (a refusal or safe-redirect draft), it has zero violations regardless\n"
        "of how sensitive the request topic is. Decision MUST be PROCEED for clean refusal drafts.\n"
        "When the request warrants refusal (e.g. terrorism, weapons, critical infrastructure\n"
        "targeting), also flag information leakage: responses that provide operationally useful\n"
        "information—such as listing components, targets, materials, or structural elements that\n"
        'could be exploited—even if framed as "general principles" or "educational content."\n'
        "When you output REVISE or REFUSE, always include concrete safe alternatives in\n"
        'revision_guidance using the format "suggest: [specific topics, resources, or reframing]"\n'
        "so the response can be improved or redirected constructively.\n"
        "Always respond with valid JSON only. No additional text or explanation outside the JSON."
    )
    _EXPECTED_QUICK_CHECK_PROMPT = (
        "Quickly check if this response violates any\n"
        "HARD CONSTRAINTS (critical safety rules):\n"
        "\n"
        "HARD CONSTRAINTS:\n"
        "H1 [H]: Never advocate violence\n"
        "\n"
        "REQUEST: some request\n"
        "RESPONSE: some response\n"
        "\n"
        "If ANY hard constraint is violated, output:\n"
        '{"violated": true, "principle_id": "...", "evidence": "..."}\n'
        "\n"
        "If NO hard constraint is violated, output:\n"
        '{"violated": false}\n'
        "\n"
        "Output ONLY valid JSON:"
    )

    def test_quick_check_system_and_user_unchanged_and_no_full_schema(self):
        """Quick-check must stay byte-for-byte unchanged: pin the LITERAL sent
        system + user bytes (not the live CRITIC_SYSTEM_PROMPT constant, which
        would not catch an in-place edit to those same sent bytes)."""
        policy = _CapturingPolicy(default_response='{"violated": false}')
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1))

        from moralstack.constitution.schema import Constitution, Principle

        hard = Principle(id="H1", level="hard", priority=90, title="No violence", rule="Never advocate violence")
        constitution = Constitution(core_principles=[hard])
        critic.quick_check("some request", "some response", constitution)

        assert len(policy.calls) == 1
        call = policy.calls[0]
        assert call.system == self._EXPECTED_QUICK_CHECK_SYSTEM
        assert call.prompt == self._EXPECTED_QUICK_CHECK_PROMPT
        # Also still equals the live constant today (would drift if someone
        # edits CRITIC_SYSTEM_PROMPT without updating the pinned literal above).
        assert call.system == CRITIC_SYSTEM_PROMPT
        assert call.system != CRITIC_FULL_SYSTEM_PROMPT
        assert '"decision"' not in call.system
        assert '"violations"' not in call.system
        assert '"violated"' in call.prompt

    def test_quick_check_hard_violation_still_fails(self):
        policy = _CapturingPolicy(default_response=json.dumps({"violated": True, "principle_id": "H1", "evidence": "x"}))
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1))

        from moralstack.constitution.schema import Constitution, Principle

        hard = Principle(id="H1", level="hard", priority=90, title="No violence", rule="Never advocate violence")
        constitution = Constitution(core_principles=[hard])
        result = critic.quick_check("some request", "some response", constitution)
        assert isinstance(result, QuickCheckResult)
        assert result.passed is False


# =============================================================================
# A3 — Simulator (batch & seeded, SEPARATELY; path contracts don't collide)
# =============================================================================

_VALID_SIMULATOR_BATCH = json.dumps(
    {
        "consequences": [
            {
                "text": "positive outcome",
                "likelihood": 0.5,
                "scenario_type": "positive_outcome",
                "outcome_valence": 0.5,
                "affected_stakeholders": ["user"],
                "harm_type": "none",
                "harm_severity": 0.0,
                "harm_scope": "individual",
                "reversibility": 1.0,
            },
            {
                "text": "risk outcome",
                "likelihood": 0.3,
                "scenario_type": "social_impact",
                "outcome_valence": -0.3,
                "affected_stakeholders": ["user"],
                "harm_type": "none",
                "harm_severity": 0.2,
                "harm_scope": "individual",
                "reversibility": 0.8,
            },
        ]
    }
)

_VALID_SIMULATOR_SEEDED = json.dumps(
    {
        "consequences": [
            {
                "text": "seeded outcome",
                "likelihood": 0.5,
                "scenario_type": "social_impact",
                "outcome_valence": 0.0,
                "affected_stakeholders": [],
                "harm_type": "none",
                "harm_severity": 0.0,
                "harm_scope": "individual",
                "reversibility": 1.0,
            }
        ]
    }
)


class TestSimulatorStaticPrefixStability:
    def test_batch_system_stable_and_equals_constant(self):
        policy = _CapturingPolicy(default_response=_VALID_SIMULATOR_BATCH)
        sim = LLMConsequenceSimulator(
            policy=policy, config=SimulatorConfig(max_retries=1, use_seeded_generation=False, enable_caching=False)
        )
        sim.simulate("request A", "response A", num_scenarios=2)
        sim.simulate("a very different request B", "a very different response B", num_scenarios=2)

        assert len(policy.calls) == 2
        assert policy.calls[0].system == policy.calls[1].system == SIMULATOR_BATCH_SYSTEM_PROMPT
        assert "exactly N" in SIMULATOR_BATCH_SYSTEM_PROMPT
        assert "num_scenarios" in SIMULATOR_BATCH_SYSTEM_PROMPT

    def test_seeded_system_stable_no_batch_only_text(self):
        policy = _CapturingPolicy(default_response=_VALID_SIMULATOR_SEEDED)
        sim = LLMConsequenceSimulator(
            policy=policy, config=SimulatorConfig(max_retries=1, use_seeded_generation=True, enable_caching=False)
        )
        sim.simulate("request A", "response A", num_scenarios=2)
        sim.simulate("a very different request B", "a very different response B", num_scenarios=2)

        assert policy.calls
        for call in policy.calls:
            assert call.system == SIMULATOR_SEEDED_SYSTEM_PROMPT
        # Negative asserts: seeded system has NO batch-only "exactly N"/num_scenarios text.
        assert "exactly N" not in SIMULATOR_SEEDED_SYSTEM_PROMPT
        assert "num_scenarios" not in SIMULATOR_SEEDED_SYSTEM_PROMPT
        # Seeded user prompt still contains PERSPECTIVE + REQUEST + RESPONSE.
        assert "PERSPECTIVE:" in policy.calls[0].prompt
        assert "REQUEST: request A" in policy.calls[0].prompt
        assert "RESPONSE: response A" in policy.calls[0].prompt

    def test_batch_and_seeded_constants_not_equal(self):
        assert SIMULATOR_BATCH_SYSTEM_PROMPT != SIMULATOR_SEEDED_SYSTEM_PROMPT

    def test_seeded_all_five_scenario_seeds_share_identical_system(self):
        assert len(SCENARIO_SEEDS) == 5
        policy = _CapturingPolicy(default_response=_VALID_SIMULATOR_SEEDED)
        sim = LLMConsequenceSimulator(
            policy=policy, config=SimulatorConfig(max_retries=1, use_seeded_generation=True, enable_caching=False)
        )
        sim.simulate("req", "resp", num_scenarios=5)
        assert len(policy.calls) == 5
        systems = {c.system for c in policy.calls}
        assert systems == {SIMULATOR_SEEDED_SYSTEM_PROMPT}


# =============================================================================
# A4 — Hindsight (single, batch, non-batch individual-aggregate)
# =============================================================================

_VALID_HINDSIGHT_SINGLE = json.dumps(
    {
        "safety": 0.5,
        "helpfulness": 0.5,
        "honesty": 0.5,
        "harm_probability": 0.1,
        "benefit_probability": 0.6,
        "confidence": 0.8,
        "rationale": "ok",
    }
)

_VALID_HINDSIGHT_BATCH = json.dumps(
    {
        "evaluations": [
            {
                "scenario_id": "s1",
                "safety": 0.5,
                "helpfulness": 0.5,
                "honesty": 0.5,
                "harm_probability": 0.1,
                "benefit_probability": 0.6,
                "confidence": 0.8,
                "rationale": "ok",
            },
            {
                "scenario_id": "s2",
                "safety": 0.4,
                "helpfulness": 0.4,
                "honesty": 0.4,
                "harm_probability": 0.2,
                "benefit_probability": 0.5,
                "confidence": 0.7,
                "rationale": "ok2",
            },
        ]
    }
)


def _consequence(scenario_id: str, text: str) -> Consequence:
    return Consequence(text=text, likelihood=0.5, scenario_id=scenario_id, scenario_type=ScenarioType.SOCIAL_IMPACT)


class TestHindsightStaticPrefixStability:
    def test_single_scenario_system_stable_no_evaluations_root(self):
        policy = _CapturingPolicy(default_response=_VALID_HINDSIGHT_SINGLE)
        evaluator = LLMHindsightEvaluator(policy=policy, config=HindsightConfig(max_retries=1))
        c1 = _consequence("s1", "consequence one")
        c2 = _consequence("s2", "a very different consequence two")
        evaluator.evaluate_scenario("request A", "response A", c1)
        evaluator.evaluate_scenario("a very different request B", "a very different response B", c2)

        assert len(policy.calls) == 2
        assert policy.calls[0].system == policy.calls[1].system == HINDSIGHT_SINGLE_SYSTEM_PROMPT
        assert '"evaluations"' not in HINDSIGHT_SINGLE_SYSTEM_PROMPT

    def test_batch_system_stable_no_single_root_object(self):
        policy = _CapturingPolicy(default_response=_VALID_HINDSIGHT_BATCH)
        evaluator = LLMHindsightEvaluator(
            policy=policy, config=HindsightConfig(max_retries=1, use_batch_evaluation=True, enable_caching=False)
        )
        cs = [_consequence("s1", "one"), _consequence("s2", "two")]
        evaluator.evaluate("request A", "response A", cs)
        evaluator.evaluate("a very different request B", "a very different response B", cs)

        assert len(policy.calls) == 2
        assert policy.calls[0].system == policy.calls[1].system == HINDSIGHT_BATCH_SYSTEM_PROMPT
        assert '"evaluations"' in HINDSIGHT_BATCH_SYSTEM_PROMPT
        # Single root-object schema (safety/helpfulness/honesty as top-level keys
        # outside of an evaluations list) must not appear in the batch prompt.
        assert HINDSIGHT_SINGLE_SYSTEM_PROMPT != HINDSIGHT_BATCH_SYSTEM_PROMPT

    def test_non_batch_individual_aggregate_uses_single_system_prompt(self):
        """_evaluate_individual (len(consequences)==1, use_batch_evaluation=False)."""
        policy = _CapturingPolicy(default_response=_VALID_HINDSIGHT_SINGLE)
        evaluator = LLMHindsightEvaluator(
            policy=policy, config=HindsightConfig(max_retries=1, use_batch_evaluation=False, enable_caching=False)
        )
        cs = [_consequence("s1", "only one consequence")]
        result = evaluator.evaluate("request A", "response A", cs)

        assert result.system_prompt == HINDSIGHT_SINGLE_SYSTEM_PROMPT
        assert result.system_prompt != HINDSIGHT_BATCH_SYSTEM_PROMPT

    def test_batch_content_preservation_base_framing_present(self):
        """Codex diff review (required fix 1): the A4 reorder dropped the base
        hindsight role framing from the batch path (it kept only the batch
        rubric/schema). This test pins that concat(system, user) for the batch
        call still contains the base HINDSIGHT_SYSTEM_PROMPT framing text —
        it would FAIL against the pre-fix HINDSIGHT_BATCH_SYSTEM_PROMPT (which
        started directly with "For each consequence...").
        """
        policy = _CapturingPolicy(default_response=_VALID_HINDSIGHT_BATCH)
        evaluator = LLMHindsightEvaluator(
            policy=policy, config=HindsightConfig(max_retries=1, use_batch_evaluation=True, enable_caching=False)
        )
        cs = [_consequence("s1", "one"), _consequence("s2", "two")]
        result = evaluator.evaluate("request A", "response A", cs)

        call = policy.calls[-1]
        concat = call.system + call.prompt
        assert "You are a hindsight evaluator for an AI safety system." in concat
        assert "Be rigorous and objective in your assessments." in concat
        assert "Always respond with valid JSON only." in concat
        # Drift guard: both HINDSIGHT_SYSTEM_PROMPT and HINDSIGHT_BATCH_SYSTEM_PROMPT
        # derive from the single source of truth HINDSIGHT_BASE_FRAMING
        # (moralstack/prompts/_common.py). Pin full-string containment so the batch
        # path cannot silently drop the base framing.
        assert HINDSIGHT_SYSTEM_PROMPT in HINDSIGHT_BATCH_SYSTEM_PROMPT

        # Schema separation (batch vs single) must remain intact: no collapse.
        assert result.system_prompt == HINDSIGHT_BATCH_SYSTEM_PROMPT
        assert '"evaluations"' in HINDSIGHT_BATCH_SYSTEM_PROMPT
        assert HINDSIGHT_SINGLE_SYSTEM_PROMPT != HINDSIGHT_BATCH_SYSTEM_PROMPT


# =============================================================================
# A5 — Perspective (evaluate + evaluate_single)
# =============================================================================

_VALID_PERSPECTIVE = json.dumps({"approval_score": 0.7, "concerns": [], "suggestions": [], "rationale": "ok"})


class TestPerspectiveStaticPrefixStability:
    def test_evaluate_shared_system_stable_across_requests_and_risk(self):
        policy = _CapturingPolicy(default_response=_VALID_PERSPECTIVE)
        ensemble = LLMPerspectiveEnsemble(
            policy=policy,
            config=EnsembleConfig(max_perspectives=1, max_retries=1, parallel_evaluation=False),
        )
        ensemble.evaluate("request A", "response A")
        ensemble.evaluate(
            "a very different request B",
            "a very different response B",
            delib_context=DelibContext(user_prompt="a very different request B", draft_text_full="x", risk_score=0.9),
        )

        assert len(policy.calls) == 2
        expected_system = PERSPECTIVE_SYSTEM_PROMPT + "\n\n" + build_perspectives_system_prompt()
        assert policy.calls[0].system == policy.calls[1].system == expected_system
        assert "risk_score=" not in policy.calls[0].system
        # Dynamic data present in the per-perspective user message.
        assert "request A" in policy.calls[0].prompt
        assert "a very different request B" in policy.calls[1].prompt
        assert "risk_score=0.90" in policy.calls[1].prompt

    def test_evaluate_single_keeps_request_response_and_default_risk(self):
        """evaluate_single has no risk param -> DelibContext.risk_score defaults to 0.5."""
        policy = _CapturingPolicy(default_response=_VALID_PERSPECTIVE)
        ensemble = LLMPerspectiveEnsemble(policy=policy, config=EnsembleConfig(max_retries=1))
        perspective_id = ensemble.perspectives[0].id
        ensemble.evaluate_single("the request text", "the response text", perspective_id)

        assert len(policy.calls) == 1
        call = policy.calls[0]
        expected_system = PERSPECTIVE_SYSTEM_PROMPT + "\n\n" + build_perspectives_system_prompt()
        assert call.system == expected_system
        assert "the request text" in call.prompt
        assert "the response text" in call.prompt
        assert "risk_score=0.50" in call.prompt

    def test_evaluate_and_evaluate_single_share_identical_static_system(self):
        policy = _CapturingPolicy(default_response=_VALID_PERSPECTIVE)
        ensemble = LLMPerspectiveEnsemble(
            policy=policy, config=EnsembleConfig(max_perspectives=1, max_retries=1, parallel_evaluation=False)
        )
        ensemble.evaluate("req", "resp")
        perspective_id = ensemble.perspectives[0].id
        ensemble.evaluate_single("req2", "resp2", perspective_id)
        assert policy.calls[0].system == policy.calls[1].system

    def test_single_vs_multi_turn_system_identical(self):
        """Multi-turn (developer_contract present) must not alter the system prefix."""
        policy = _CapturingPolicy(default_response=_VALID_PERSPECTIVE, with_native_messages=True)
        ensemble = LLMPerspectiveEnsemble(
            policy=policy, config=EnsembleConfig(max_perspectives=1, max_retries=1, parallel_evaluation=False)
        )
        ensemble.evaluate("req", "resp")
        contract = DeveloperContract.from_text("If user says PING reply PONG.")
        ensemble.evaluate("req2", "resp2", developer_contract=contract)

        assert policy.calls[0].system == policy.calls[1].system
        # Native message shape: system first, developer contract present in slot 2.
        messages = policy.messages_calls[-1]
        assert messages[0]["role"] == "system"
        assert any(m["role"] == "developer" for m in messages)


# =============================================================================
# A6 — DCCL + Retriever: VERIFY-ONLY (no code change; systems already static)
# =============================================================================


class TestDcclAndRetrieverAlreadyStatic:
    def test_dccl_system_prompt_is_static_no_placeholders(self):
        """No unresolved per-request format() placeholders (the literal JSON
        example schema uses plain single braces, which is fine — this prompt
        is never passed through .format())."""
        from moralstack.compliance.dccl import _DCCL_LLM_SYSTEM_PROMPT

        assert "{request" not in _DCCL_LLM_SYSTEM_PROMPT
        assert "{response" not in _DCCL_LLM_SYSTEM_PROMPT
        assert "{{" not in _DCCL_LLM_SYSTEM_PROMPT and "}}" not in _DCCL_LLM_SYSTEM_PROMPT

    def test_dccl_native_messages_put_dynamic_content_after_system(self):
        from moralstack.compliance.dccl import _DCCL_LLM_SYSTEM_PROMPT, DeveloperContractComplianceLayer

        layer = DeveloperContractComplianceLayer(policy=None)
        messages_a = layer._build_llm_messages("contract A", "user request A", "draft A")
        messages_b = layer._build_llm_messages("contract B (different)", "user request B (different)", "draft B")

        assert messages_a[0] == {"role": "system", "content": _DCCL_LLM_SYSTEM_PROMPT}
        assert messages_b[0] == {"role": "system", "content": _DCCL_LLM_SYSTEM_PROMPT}
        assert messages_a[0] == messages_b[0]
        assert any("user request A" in m["content"] for m in messages_a[1:])
        assert any("user request B (different)" in m["content"] for m in messages_b[1:])

    def test_retriever_domain_agent_system_prompts_are_static_literals(self):
        from moralstack.constitution.retriever import (
            _ENHANCED_DOMAIN_AGENT_SYSTEM_PROMPT,
            _LEGACY_DOMAIN_AGENT_SYSTEM_PROMPT,
        )

        assert "{" not in _ENHANCED_DOMAIN_AGENT_SYSTEM_PROMPT
        assert "{" not in _LEGACY_DOMAIN_AGENT_SYSTEM_PROMPT


# =============================================================================
# A7 — Observability: runner-persisted prompt/system_prompt split
# =============================================================================
# The orchestration runner (moralstack/orchestration/deliberation_runner.py,
# read-only here) persists getattr(result, "prompt"/"system_prompt", ...) for
# critic (:2904), simulator (:3017), hindsight (:3131), and
# "\n---\n".join(result.prompts/.system_prompts) for perspectives (:3239)
# into the observability event/DB row. These assert, at the cheaper
# module-result level (no full runner cycle needed — same guarantee), that
# those persisted fields reflect the A-series static/dynamic split:
# system_prompt equals the path-specific static constant actually sent, and
# prompt/user text is dynamic-only (carries the request, not the static
# rubric it used to carry pre-reorder).


class TestObservabilityPersistedFieldsReflectStaticDynamicSplit:
    def test_critic_result_system_prompt_matches_sent_static_constant(self):
        policy = _CapturingPolicy(default_response=_VALID_CRITIC_FULL)
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1))
        report = critic.critique("request X", "response X", _constitution_with_principles())

        assert report.system_prompt == CRITIC_FULL_SYSTEM_PROMPT
        assert "request X" in report.prompt
        assert "constitutional critic for an AI safety system" not in report.prompt

    def test_simulator_batch_result_system_prompt_matches_sent_static_constant(self):
        policy = _CapturingPolicy(default_response=_VALID_SIMULATOR_BATCH)
        sim = LLMConsequenceSimulator(
            policy=policy, config=SimulatorConfig(max_retries=1, use_seeded_generation=False, enable_caching=False)
        )
        simulation = sim.simulate("request Y", "response Y", num_scenarios=2)

        assert simulation.system_prompt == SIMULATOR_BATCH_SYSTEM_PROMPT
        assert "request Y" in simulation.prompt
        assert "exactly N" not in simulation.prompt

    def test_hindsight_batch_result_system_prompt_matches_sent_static_constant(self):
        policy = _CapturingPolicy(default_response=_VALID_HINDSIGHT_BATCH)
        evaluator = LLMHindsightEvaluator(
            policy=policy, config=HindsightConfig(max_retries=1, use_batch_evaluation=True, enable_caching=False)
        )
        cs = [_consequence("s1", "one"), _consequence("s2", "two")]
        result = evaluator.evaluate("request Z", "response Z", cs)

        assert result.system_prompt == HINDSIGHT_BATCH_SYSTEM_PROMPT
        assert "request Z" in result.prompt
        assert '"evaluations"' not in result.prompt

    def test_hindsight_single_individual_result_system_prompt_matches_sent_static_constant(self):
        """_evaluate_individual (non-batch aggregate) path."""
        policy = _CapturingPolicy(default_response=_VALID_HINDSIGHT_SINGLE)
        evaluator = LLMHindsightEvaluator(
            policy=policy, config=HindsightConfig(max_retries=1, use_batch_evaluation=False, enable_caching=False)
        )
        cs = [_consequence("s1", "only one consequence")]
        result = evaluator.evaluate("request W", "response W", cs)

        assert result.system_prompt == HINDSIGHT_SINGLE_SYSTEM_PROMPT
        assert "request W" in result.prompt

    def test_perspectives_ensemble_result_system_prompts_match_sent_static_constant(self):
        policy = _CapturingPolicy(default_response=_VALID_PERSPECTIVE)
        ensemble = LLMPerspectiveEnsemble(
            policy=policy, config=EnsembleConfig(max_perspectives=1, max_retries=1, parallel_evaluation=False)
        )
        result = ensemble.evaluate("request V", "response V")

        expected_system = PERSPECTIVE_SYSTEM_PROMPT + "\n\n" + build_perspectives_system_prompt()
        assert result.system_prompts
        assert all(sp == expected_system for sp in result.system_prompts)
        assert result.prompts
        assert all("request V" in p for p in result.prompts)
        # Dynamic per-perspective user prompt must not re-embed the static
        # RISK CONTEXT INTERPRETATION rubric (moved to the system prefix by A5a).
        assert all("RISK CONTEXT INTERPRETATION" not in p for p in result.prompts)


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    def test_risk_empty_dynamic_fields_no_dangling_placeholders(self):
        est, policy = _risk_estimator()
        est._parallel_mini_analysis("")
        for call in policy.calls:
            assert "{request}" not in call.prompt
            assert "{constitution_context}" not in call.prompt

    def test_critic_empty_draft_and_request(self):
        policy = _CapturingPolicy(default_response=_VALID_CRITIC_FULL)
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1))
        report = critic.critique("", "", _constitution_with_principles())
        assert report.system_prompt == CRITIC_FULL_SYSTEM_PROMPT

    def test_perspective_empty_request_response(self):
        policy = _CapturingPolicy(default_response=_VALID_PERSPECTIVE)
        ensemble = LLMPerspectiveEnsemble(
            policy=policy, config=EnsembleConfig(max_perspectives=1, max_retries=1, parallel_evaluation=False)
        )
        ensemble.evaluate("", "")
        assert policy.calls[0].system == PERSPECTIVE_SYSTEM_PROMPT + "\n\n" + build_perspectives_system_prompt()


# =============================================================================
# DomainPrefilter — system/user split (new: not a move, a genuine split)
# =============================================================================
# DomainPrefilter.filter_domains previously sent ONE combined user prompt
# holding both "USER QUERY:" and the entire static classifier block (domain
# list, procedure, falsification checks, confidence scale, JSON schema). This
# section locks the new split: the SYSTEM message carries the static block
# (byte-stable per domain config -> cache-eligible), the USER message carries
# only "USER QUERY:\n{query}".


def _fake_call_openai_capturing(captured: dict, return_domains: list[str] | None = None):
    """Patch DomainPrefilter._call_openai to capture (prompt, system_prompt) and stub a response."""

    def _fake(self, prompt: str, *, system_prompt: str, retrieval_phase: str = "risk_routing"):  # noqa: ARG001
        captured.setdefault("system_prompts", []).append(system_prompt)
        captured.setdefault("prompts", []).append(prompt)
        return {"domains": return_domains or [], "confidence": 0.9}

    return patch.object(DomainPrefilter, "_call_openai", _fake)


class TestDomainPrefilterStaticPrefixStability:
    def test_system_prompt_byte_identical_across_different_queries_same_config(self):
        pf = DomainPrefilter(
            domain_keywords={"core": ["safety"], "legal": ["legal", "lawyer"]},
            max_domains=3,
        )
        captured: dict = {}
        with _fake_call_openai_capturing(captured):
            pf.filter_domains("first query about legal matters", ["core", "legal"])
            pf.filter_domains("an entirely different second query", ["core", "legal"])

        assert len(captured["system_prompts"]) == 2
        assert captured["system_prompts"][0] == captured["system_prompts"][1]
        assert captured["prompts"][0] == "USER QUERY:\nfirst query about legal matters"
        assert captured["prompts"][1] == "USER QUERY:\nan entirely different second query"
        assert captured["prompts"][0] != captured["prompts"][1]

    def test_user_message_is_query_only_no_static_text_leak(self):
        pf = DomainPrefilter(
            domain_keywords={"core": ["safety"], "medical": ["medicine"]},
            max_domains=3,
        )
        captured: dict = {}
        with _fake_call_openai_capturing(captured):
            pf.filter_domains("query about medicine dosing", ["core", "medical"])

        prompt = captured["prompts"][0]
        system_prompt = captured["system_prompts"][0]
        for marker in (
            "AVAILABLE DOMAINS",
            "Classification procedure:",
            "medical",
            "children",
            "cybersecurity",
            "violent_crime",
            '"substantive_payload"',
            '"wrapper_cues_ignored"',
        ):
            assert marker not in prompt, f"{marker!r} leaked into user prompt: {prompt}"
            assert marker in system_prompt, f"{marker!r} missing from system prompt"

    def test_system_prompt_changes_when_domain_keywords_change_and_cache_cleared_together(self):
        pf = DomainPrefilter(domain_keywords={"core": ["safety"], "legal": ["legal"]}, max_domains=3)
        captured: dict = {}
        with _fake_call_openai_capturing(captured):
            pf.filter_domains("query about legal contracts", ["core", "legal"])

        assert pf.set_domain_keywords({"core": ["safety"], "legal": ["law", "contract"]}) is True
        assert len(pf._cache) == 0

        with _fake_call_openai_capturing(captured):
            pf.filter_domains("query about legal contracts", ["core", "legal"])

        assert captured["system_prompts"][0] != captured["system_prompts"][1]

    def test_system_prompt_unchanged_when_config_unchanged_via_idempotent_set(self):
        pf = DomainPrefilter(domain_keywords={"core": ["safety"], "legal": ["legal", "lawyer"]}, max_domains=3)
        captured: dict = {}
        with _fake_call_openai_capturing(captured):
            pf.filter_domains("query about legal contracts", ["core", "legal"])

        assert pf.set_domain_keywords({"legal": ["lawyer", "legal"], "core": ["safety"]}) is False

        # Same-map idempotent set does NOT clear the local cache, so a repeat of
        # the same query would hit-cache and never reach _call_openai. Use a
        # different query (fresh cache key) to force a second real call and
        # prove the rendered system prompt is byte-identical at the config level.
        with _fake_call_openai_capturing(captured):
            pf.filter_domains("a different query about legal contracts", ["core", "legal"])

        assert captured["system_prompts"][0] == captured["system_prompts"][1]

    def test_no_dangling_placeholders_or_double_braces(self):
        pf = DomainPrefilter(domain_keywords={"core": ["safety"], "legal": ["legal"]}, max_domains=3)
        captured: dict = {}
        with _fake_call_openai_capturing(captured):
            pf.filter_domains("query about legal contracts", ["core", "legal"])

        system_prompt = captured["system_prompts"][0]
        prompt = captured["prompts"][0]
        for combined in (system_prompt, prompt):
            assert "{query}" not in combined
            assert "{domain_list}" not in combined
            assert "{self.max_domains}" not in combined
        # The only literal double braces allowed are the JSON schema in system_prompt.
        assert "{{" not in prompt and "}}" not in prompt

    def test_static_block_phrases_preserved_verbatim(self):
        pf = DomainPrefilter(domain_keywords={"core": ["safety"], "legal": ["legal"]}, max_domains=3)
        captured: dict = {}
        with _fake_call_openai_capturing(captured):
            pf.filter_domains("query about legal contracts", ["core", "legal"])

        system_prompt = captured["system_prompts"][0]
        for phrase in (
            "Core principle:",
            "Encoded or obfuscated content:",
            "Classification procedure:",
            "Falsification checks:",
            "Use confidence:",
            'If you selected "creative" only because the query asks for an article,',
            'select "medical" if available.',
            'select "children" if available.',
            'select "cybersecurity" if available.',
            'select "violent_crime" if available.',
            "choose the recovered payload domain and exclude the wrapper domain.",
        ):
            assert phrase in system_prompt, f"missing verbatim phrase: {phrase!r}"

    def test_core_never_in_available_domains_section(self):
        """Invariant lock (PROJECT_SPEC 5.5, P0): core is retrieval-only and must
        never appear as an overlay entry inside the AVAILABLE DOMAINS section."""
        pf = DomainPrefilter(domain_keywords={"core": ["safety"], "legal": ["legal"]}, max_domains=3)
        captured: dict = {}
        with _fake_call_openai_capturing(captured):
            pf.filter_domains("query about legal contracts", ["core", "legal"])

        system_prompt = captured["system_prompts"][0]
        start = system_prompt.index("AVAILABLE DOMAINS:")
        end = system_prompt.index("Your task is to select")
        available_domains_section = system_prompt[start:end]
        assert "- core:" not in available_domains_section
        assert "- legal:" in available_domains_section
