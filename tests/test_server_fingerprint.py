"""
Tests for the conversation fingerprint helper (design v1.3 §4.3).
"""

from __future__ import annotations

from moralstack.server.fingerprint import compute_conversation_fingerprint


class TestEmptyInputs:
    def test_empty_list(self):
        assert compute_conversation_fingerprint([]) == ""

    def test_none(self):
        assert compute_conversation_fingerprint(None) == ""


class TestDeterminism:
    def test_identical_messages_yield_identical_fingerprint(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello."},
        ]
        fp1 = compute_conversation_fingerprint(msgs)
        fp2 = compute_conversation_fingerprint(msgs)
        assert fp1 == fp2

    def test_format_starts_with_msf_prefix(self):
        msgs = [{"role": "user", "content": "hi"}]
        fp = compute_conversation_fingerprint(msgs)
        assert fp.startswith("msf-")
        # msf- + 16 hex chars = 20 chars
        assert len(fp) == 20


class TestStabilityAcrossTurns:
    """Two HTTP calls in the same conversation share the message prefix."""

    def test_appending_new_turn_preserves_fingerprint(self):
        # After turn 1 the assistant replies; turn 2 appends the assistant reply + new user.
        turn2 = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "First question?"},
            {"role": "assistant", "content": "Answer 1."},
            {"role": "user", "content": "Follow-up?"},
        ]
        # Fingerprint takes prefix of size 3.
        # turn1[:3] = full turn1 (only 2 messages).
        # turn2[:3] = system + first user + first assistant.
        # These differ — so the fingerprint may differ. The stability claim is
        # about turns 3+ where the prefix has stabilized.
        turn3 = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "First question?"},
            {"role": "assistant", "content": "Answer 1."},
            {"role": "user", "content": "Follow-up?"},
            {"role": "assistant", "content": "Answer 2."},
            {"role": "user", "content": "Another?"},
        ]
        fp_turn2 = compute_conversation_fingerprint(turn2)
        fp_turn3 = compute_conversation_fingerprint(turn3)
        # Once the prefix (3 messages) is filled, subsequent turns yield the same fingerprint.
        assert fp_turn2 == fp_turn3


class TestDifferentConversationsDiffer:
    def test_different_system_prompts(self):
        msgs1 = [{"role": "system", "content": "Medical assistant."}, {"role": "user", "content": "Q"}]
        msgs2 = [{"role": "system", "content": "Legal assistant."}, {"role": "user", "content": "Q"}]
        assert compute_conversation_fingerprint(msgs1) != compute_conversation_fingerprint(msgs2)

    def test_different_first_user_message(self):
        msgs1 = [{"role": "user", "content": "Q1"}]
        msgs2 = [{"role": "user", "content": "Q2"}]
        assert compute_conversation_fingerprint(msgs1) != compute_conversation_fingerprint(msgs2)


class TestRobustness:
    def test_handles_missing_role(self):
        msgs = [{"content": "no role here"}]
        fp = compute_conversation_fingerprint(msgs)
        assert fp.startswith("msf-")

    def test_handles_missing_content(self):
        msgs = [{"role": "user"}]
        fp = compute_conversation_fingerprint(msgs)
        assert fp.startswith("msf-")

    def test_handles_long_content(self):
        """Content beyond 4KB is truncated to ensure fingerprint stability."""
        long_text = "x" * 10000
        msgs = [{"role": "user", "content": long_text}]
        fp1 = compute_conversation_fingerprint(msgs)
        msgs2 = [{"role": "user", "content": long_text + "DIFFERENT_TAIL"}]
        fp2 = compute_conversation_fingerprint(msgs2)
        # Both messages share the first 4KB → same fingerprint.
        assert fp1 == fp2
