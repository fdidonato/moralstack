"""Perspectives module must pass developer/history as native messages."""

from moralstack.models.delib_context import DelibContext
from moralstack.prompts.perspectives_prompt import build_perspectives_system_prompt


def test_perspectives_system_prompt_does_not_inline_contract_when_present():
    ctx = DelibContext(
        user_prompt="some_token_123",
        draft_text_full="response_text",
        developer_contract_text=("You are an admin assistant. If the user sends 'PING', reply 'PONG'."),
    )
    prompt = build_perspectives_system_prompt(ctx)
    assert "DEVELOPER CONTRACT:" not in prompt
    assert "If the user sends 'PING'" not in prompt


def test_perspectives_system_prompt_omits_contract_when_absent():
    """When developer_contract_text is empty, the DEVELOPER CONTRACT block must NOT appear."""
    ctx = DelibContext(
        user_prompt="generic request",
        draft_text_full="generic response",
    )
    prompt = build_perspectives_system_prompt(ctx)
    assert "DEVELOPER CONTRACT:" not in prompt


def test_perspectives_system_prompt_does_not_inline_contract_or_history():
    ctx = DelibContext(
        user_prompt="X",
        draft_text_full="Y",
        developer_contract_text="if user types X, reply Y",
        conversation_history_snippet="[user]: previous turn\n[assistant]: previous reply",
    )
    prompt = build_perspectives_system_prompt(ctx)
    assert "DEVELOPER CONTRACT:" not in prompt
    assert "CONVERSATION HISTORY" not in prompt
    assert "previous turn" not in prompt


def test_perspectives_module_propagates_contract_to_context(monkeypatch):
    """Integration test: LLMPerspectiveEnsemble passes developer_contract to DelibContext."""
    from moralstack.orchestration.contract import DeveloperContract
    from moralstack.runtime.modules.perspective_module import (
        EnsembleConfig,
        LLMPerspectiveEnsemble,
    )

    captured: dict[str, object] = {}

    class _MockPolicy:
        def generate_messages(self, *, messages, config=None):
            captured["messages"] = messages

            class _R:
                text = '{"approval_score": 0.8, "concerns": [], ' '"suggestions": [], "rationale": "ok"}'

                def token_usage_json(self_inner):
                    return None

            return _R()

        def generate(self, prompt, system="", config=None):
            raise AssertionError("perspectives context path must use native messages")

    config = EnsembleConfig(parallel_evaluation=False, max_perspectives=1)
    module = LLMPerspectiveEnsemble(policy=_MockPolicy(), config=config)
    contract = DeveloperContract.from_text("If user sends 'PING', reply 'PONG'.")
    _ = module.evaluate(
        request="PING",
        response="PONG",
        developer_contract=contract,
    )
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert [m["role"] for m in messages[:2]] == ["system", "developer"]
    assert "If user sends 'PING'" in messages[1]["content"]
    assert "DEVELOPER CONTRACT:" not in messages[0]["content"]
    assert "Consider the preceding developer message" in messages[-1]["content"]
