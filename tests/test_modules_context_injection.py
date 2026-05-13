"""
Tests for context block injection in critic/simulator/hindsight modules
and DelibContext snippet flow for the perspective module.
"""

from __future__ import annotations

from moralstack.orchestration.contract import DeveloperContract


def _make_turn(role: str, content: str):
    class _T:
        pass

    t = _T()
    t.role = role
    t.content = content
    return t


class TestCriticContextBlock:
    def test_empty_when_no_contract_no_history(self):
        from moralstack.runtime.modules.critic_module import _build_context_block

        assert _build_context_block(None, None) == ""
        assert _build_context_block(None, []) == ""

    def test_includes_contract_only(self):
        from moralstack.runtime.modules.critic_module import _build_context_block

        contract = DeveloperContract.from_text("You are a medical assistant.")
        block = _build_context_block(contract, None)
        assert "DEVELOPER CONTRACT" in block
        assert "You are a medical assistant." in block
        assert "CONVERSATION HISTORY" not in block

    def test_includes_history_only(self):
        from moralstack.runtime.modules.critic_module import _build_context_block

        history = [_make_turn("user", "Hello"), _make_turn("assistant", "Hi there")]
        block = _build_context_block(None, history)
        assert "CONVERSATION HISTORY" in block
        assert "[user]: Hello" in block
        assert "[assistant]: Hi there" in block
        assert "DEVELOPER CONTRACT" not in block

    def test_includes_both_when_present(self):
        from moralstack.runtime.modules.critic_module import _build_context_block

        contract = DeveloperContract.from_text("Test contract")
        history = [_make_turn("user", "Hello")]
        block = _build_context_block(contract, history)
        assert "DEVELOPER CONTRACT" in block
        assert "CONVERSATION HISTORY" in block

    def test_only_last_3_turns_in_block(self):
        from moralstack.runtime.modules.critic_module import _build_context_block

        history = [_make_turn("user", f"turn{i}") for i in range(10)]
        block = _build_context_block(None, history)
        assert "turn7" in block and "turn8" in block and "turn9" in block
        assert "turn0" not in block

    def test_content_truncated_to_200_chars(self):
        from moralstack.runtime.modules.critic_module import _build_context_block

        long_content = "x" * 500
        history = [_make_turn("user", long_content)]
        block = _build_context_block(None, history)
        assert "x" * 200 in block
        assert "x" * 250 not in block


class TestSimulatorContextBlock:
    def test_empty_when_no_context(self):
        from moralstack.runtime.modules.simulator_module import _build_context_block

        assert _build_context_block(None, None) == ""

    def test_includes_contract(self):
        from moralstack.runtime.modules.simulator_module import _build_context_block

        contract = DeveloperContract.from_text("Simulator test")
        block = _build_context_block(contract, None)
        assert "DEVELOPER CONTRACT" in block


class TestHindsightContextBlock:
    def test_empty_when_no_context(self):
        from moralstack.runtime.modules.hindsight_module import _build_context_block

        assert _build_context_block(None, None) == ""

    def test_includes_history(self):
        from moralstack.runtime.modules.hindsight_module import _build_context_block

        history = [_make_turn("user", "test")]
        block = _build_context_block(None, history)
        assert "CONVERSATION HISTORY" in block


class TestDelibContextHistorySnippet:
    def test_default_field_is_empty_string(self):
        from moralstack.models.delib_context import DelibContext

        ctx = DelibContext()
        assert ctx.conversation_history_snippet == ""

    def test_field_accepts_snippet(self):
        from moralstack.models.delib_context import DelibContext

        ctx = DelibContext(conversation_history_snippet="[user]: hi")
        assert ctx.conversation_history_snippet == "[user]: hi"
