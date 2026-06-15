"""Tests for the Tier-1 enumerated-output contract detection and the critic
gate that downgrades SOFT-only REVISE to PROCEED on single enumerated answers.

Regression lock for boolq_contrast: a soft critic violation on a TRUE/FALSE
answer must not be allowed to flip the factual token via the rewrite loop.
"""

from __future__ import annotations

from moralstack.constitution.schema import Constitution, Principle
from moralstack.pipeline.output_contract import detect_enumerated_output
from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic


# --------------------------------------------------------------------------- #
# Pure detection
# --------------------------------------------------------------------------- #
class TestDetectEnumeratedOutput:
    def test_boolq_true_false_quoted(self):
        declared = "answer the question by selecting from the option 'TRUE' or 'FALSE'."
        is_enum, opts = detect_enumerated_output(declared, "TRUE")
        assert is_enum is True
        assert opts == ("FALSE", "TRUE")

    def test_yes_no_prose_binary(self):
        declared = "Please answer with yes or no only."
        is_enum, opts = detect_enumerated_output(declared, "No")
        assert is_enum is True
        assert opts == ("NO", "YES")

    def test_draft_not_member_not_enumerated(self):
        declared = "answer exactly 'TRUE' or 'FALSE'."
        is_enum, _ = detect_enumerated_output(declared, "MAYBE")
        assert is_enum is False

    def test_long_prose_draft_not_enumerated(self):
        declared = "answer exactly 'TRUE' or 'FALSE'."
        draft = "TRUE, because the paragraph clearly supports this conclusion."
        is_enum, _ = detect_enumerated_output(declared, draft)
        assert is_enum is False

    def test_no_instruction_keyword_not_enumerated(self):
        # Quoted tokens present but no answer instruction -> not an option set.
        declared = "The band released 'TRUE' and 'FALSE' as singles."
        is_enum, _ = detect_enumerated_output(declared, "TRUE")
        assert is_enum is False

    def test_single_option_insufficient(self):
        declared = "answer with 'TRUE'."
        is_enum, _ = detect_enumerated_output(declared, "TRUE")
        assert is_enum is False

    def test_empty_inputs(self):
        assert detect_enumerated_output("", "TRUE") == (False, ())
        assert detect_enumerated_output("answer 'TRUE' or 'FALSE'", "") == (False, ())


# --------------------------------------------------------------------------- #
# Critic gate behaviour
# --------------------------------------------------------------------------- #
_REVISE_SOFT_JSON = (
    '{"decision":"REVISE","violated_hard":false,'
    '"violations":[{"principle_id":"SOFT.HONEST.1","severity":0.4,'
    '"rationale":"add nuance","evidence":"x"}],'
    '"revision_guidance":"suggest: present both sides and acknowledge variability"}'
)
_REVISE_HARD_JSON = (
    '{"decision":"REVISE","violated_hard":true,'
    '"violations":[{"principle_id":"CORE.NM.1","severity":0.8,'
    '"rationale":"operational harm","evidence":"x"}],'
    '"revision_guidance":"suggest: remove operational detail"}'
)


class _Result:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens_used = 10
        self.prompt_tokens = 6
        self.completion_tokens = 4


def _make_policy(payload: str):
    class _MockPolicy:
        # No generate_messages attribute -> critic uses the generate() path.
        def generate(self, *args, **kwargs):
            return _Result(payload)

    return _MockPolicy()


def _constitution(principle_id: str, level: str) -> Constitution:
    p = Principle(
        id=principle_id,
        level=level,
        priority=1,
        title=principle_id,
        rule="test rule",
    )
    return Constitution(core_principles=[p])


_ENUM_REQUEST = "answer exactly 'TRUE' or 'FALSE'.\nQuestion: Is pain experienced before limb amputation?\nAnswer:"


class TestCriticEnumeratedGate:
    def test_soft_revise_on_enumerated_downgraded_to_proceed(self):
        critic = LLMConstitutionalCritic(policy=_make_policy(_REVISE_SOFT_JSON))
        report = critic.critique(
            request=_ENUM_REQUEST,
            response="FALSE",
            constitution=_constitution("SOFT.HONEST.1", "soft"),
        )
        assert report.enumerated_output_gate_applied is True
        assert report.decision == "PROCEED"
        assert report.violations == []
        assert report.revision_guidance == ""
        assert report.violated_hard is False

    def test_hard_revise_on_enumerated_not_gated(self):
        critic = LLMConstitutionalCritic(policy=_make_policy(_REVISE_HARD_JSON))
        report = critic.critique(
            request=_ENUM_REQUEST,
            response="FALSE",
            constitution=_constitution("CORE.NM.1", "hard"),
        )
        # HARD violations must never be suppressed by the gate.
        assert report.enumerated_output_gate_applied is False
        assert report.decision == "REVISE"
        assert report.violated_hard is True
        assert len(report.violations) >= 1

    def test_soft_revise_on_non_enumerated_not_gated(self):
        critic = LLMConstitutionalCritic(policy=_make_policy(_REVISE_SOFT_JSON))
        report = critic.critique(
            request="Explain the causes of phantom limb pain in detail.",
            response="Phantom limb pain arises from maladaptive cortical reorganization ...",
            constitution=_constitution("SOFT.HONEST.1", "soft"),
        )
        assert report.enumerated_output_gate_applied is False
        assert report.decision == "REVISE"
        assert len(report.violations) >= 1
