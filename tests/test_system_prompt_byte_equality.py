"""
Byte-equality invariant: when developer_contract is None, the resolver returns
exactly the legacy expressions used in deliberation_runner.py and controller.py.

This is the non-negotiable contract of Step 8 (design v1.3 §10): the legacy single-turn
flow is byte-identical to baseline. Any failure of these tests means the v0.4 rollout
has broken something it must not.
"""

from __future__ import annotations

from moralstack.orchestration._policy_helpers import (
    CONSTRAINED_GENERATION_INSTRUCTION,
    SAFE_COMPLETE_GENERATION_INSTRUCTION,
)
from moralstack.orchestration.system_prompt_resolver import effective_system_for_request
from moralstack.orchestration.types import ProcessedRequest

# Representative bases including edge cases.
_REPRESENTATIVE_BASES = [
    "",
    "You are a helpful assistant.",
    "Long base text with multiple paragraphs.\n\nParagraph 2 with details.",
    "Base with trailing newline.\n",
    "Base with unicode: 你好, café, 🌍",
]


def _request_no_contract() -> ProcessedRequest:
    return ProcessedRequest(prompt="user query", developer_contract=None)


class TestByteEqualityNormalMode:
    """mode='normal' with no contract → exactly the base string."""

    def test_for_each_representative_base(self):
        request = _request_no_contract()
        for base in _REPRESENTATIVE_BASES:
            out = effective_system_for_request(base=base, request=request, mode="normal")
            assert out == base, f"normal mode regression for base={base!r}: got {out!r}"


class TestByteEqualitySafeCompleteMode:
    """
    mode='safe_complete' with no contract → exactly the legacy expression
    `(base or "") + "\\n\\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION` (was line 626 of
    deliberation_runner.py before Step 8).
    """

    def test_for_each_representative_base(self):
        request = _request_no_contract()
        for base in _REPRESENTATIVE_BASES:
            legacy = (base or "") + "\n\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION
            out = effective_system_for_request(base=base, request=request, mode="safe_complete")
            assert out == legacy, f"safe_complete regression for base={base!r}"


class TestByteEqualityConstrainedMode:
    """
    mode='constrained' with no contract → exactly the legacy expression
    `(base or "") + "\\n\\n" + CONSTRAINED_GENERATION_INSTRUCTION` (was lines 2537 and 2553
    of deliberation_runner.py before Step 8).
    """

    def test_for_each_representative_base(self):
        request = _request_no_contract()
        for base in _REPRESENTATIVE_BASES:
            legacy = (base or "") + "\n\n" + CONSTRAINED_GENERATION_INSTRUCTION
            out = effective_system_for_request(base=base, request=request, mode="constrained")
            assert out == legacy, f"constrained regression for base={base!r}"


class TestByteEqualityRealPolicyPrompt:
    """
    Byte-equality with the REAL POLICY_SYSTEM_PROMPT used in production. This is the
    closest test to the actual production behavior.
    """

    def test_normal_mode_real_prompt(self):
        from moralstack.orchestration._policy_helpers import POLICY_SYSTEM_PROMPT

        request = _request_no_contract()
        out = effective_system_for_request(base=POLICY_SYSTEM_PROMPT, request=request, mode="normal")
        assert out == POLICY_SYSTEM_PROMPT

    def test_safe_complete_mode_real_prompt(self):
        from moralstack.orchestration._policy_helpers import POLICY_SYSTEM_PROMPT

        request = _request_no_contract()
        legacy = POLICY_SYSTEM_PROMPT + "\n\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION
        out = effective_system_for_request(base=POLICY_SYSTEM_PROMPT, request=request, mode="safe_complete")
        assert out == legacy

    def test_constrained_mode_real_prompt(self):
        from moralstack.orchestration._policy_helpers import POLICY_SYSTEM_PROMPT

        request = _request_no_contract()
        legacy = POLICY_SYSTEM_PROMPT + "\n\n" + CONSTRAINED_GENERATION_INSTRUCTION
        out = effective_system_for_request(base=POLICY_SYSTEM_PROMPT, request=request, mode="constrained")
        assert out == legacy
