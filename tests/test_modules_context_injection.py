"""
Tests for native context message construction in deliberative modules
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
    def test_legacy_context_block_is_always_empty(self):
        from moralstack.runtime.modules.critic_module import _build_context_block

        assert _build_context_block(None, None) == ""
        assert _build_context_block(None, []) == ""
        contract = DeveloperContract.from_text("You are a medical assistant.")
        history = [_make_turn("user", "Hello"), _make_turn("assistant", "Hi there")]
        assert _build_context_block(contract, history) == ""

    def test_native_messages_include_contract_and_last_3_history(self):
        from moralstack.runtime.modules.message_context import build_module_messages

        contract = DeveloperContract.from_text("Test contract")
        history = [_make_turn("user", f"turn{i}") for i in range(10)]
        messages = build_module_messages(
            system_prompt="system",
            user_prompt="task",
            developer_contract=contract,
            conversation_history=history,
        )
        assert [m["role"] for m in messages] == ["system", "developer", "user", "user", "user", "user"]
        assert messages[1]["content"] == "Test contract"
        assert [m["content"] for m in messages[2:5]] == ["turn7", "turn8", "turn9"]
        assert "Consider the preceding developer message" in messages[-1]["content"]
        assert "Consider the preceding user/assistant messages" in messages[-1]["content"]
        assert messages[-1]["content"].endswith("task")


class TestSimulatorContextBlock:
    def test_empty_when_no_context(self):
        from moralstack.runtime.modules.simulator_module import _build_context_block

        assert _build_context_block(None, None) == ""

    def test_includes_contract(self):
        from moralstack.runtime.modules.simulator_module import _build_context_block

        contract = DeveloperContract.from_text("Simulator test")
        block = _build_context_block(contract, None)
        assert block == ""


class TestHindsightContextBlock:
    def test_empty_when_no_context(self):
        from moralstack.runtime.modules.hindsight_module import _build_context_block

        assert _build_context_block(None, None) == ""

    def test_includes_history(self):
        from moralstack.runtime.modules.hindsight_module import _build_context_block

        history = [_make_turn("user", "test")]
        block = _build_context_block(None, history)
        assert block == ""


class TestDelibContextHistorySnippet:
    def test_default_field_is_empty_string(self):
        from moralstack.models.delib_context import DelibContext

        ctx = DelibContext()
        assert ctx.conversation_history_snippet == ""

    def test_field_accepts_snippet(self):
        from moralstack.models.delib_context import DelibContext

        ctx = DelibContext(conversation_history_snippet="[user]: hi")
        assert ctx.conversation_history_snippet == "[user]: hi"
