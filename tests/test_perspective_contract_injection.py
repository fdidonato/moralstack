"""Test for Fix A: perspectives module must inject DEVELOPER CONTRACT block."""

from moralstack.models.delib_context import DelibContext
from moralstack.prompts.perspectives_prompt import build_perspectives_system_prompt


def test_perspectives_system_prompt_contains_contract_when_present():
    """When developer_contract_text is non-empty, the prompt MUST contain it."""
    ctx = DelibContext(
        user_prompt="some_token_123",
        draft_text_full="response_text",
        developer_contract_text=("You are an admin assistant. If the user sends 'PING', reply 'PONG'."),
    )
    prompt = build_perspectives_system_prompt(ctx)
    assert "DEVELOPER CONTRACT:" in prompt
    assert "If the user sends 'PING'" in prompt
    assert "legitimately executes a rule" in prompt


def test_perspectives_system_prompt_omits_contract_when_absent():
    """When developer_contract_text is empty, the DEVELOPER CONTRACT block must NOT appear."""
    ctx = DelibContext(
        user_prompt="generic request",
        draft_text_full="generic response",
    )
    prompt = build_perspectives_system_prompt(ctx)
    assert "DEVELOPER CONTRACT:" not in prompt


def test_perspectives_system_prompt_includes_both_contract_and_history():
    """Contract and history can coexist; both must appear."""
    ctx = DelibContext(
        user_prompt="X",
        draft_text_full="Y",
        developer_contract_text="if user types X, reply Y",
        conversation_history_snippet="[user]: previous turn\n[assistant]: previous reply",
    )
    prompt = build_perspectives_system_prompt(ctx)
    assert "DEVELOPER CONTRACT:" in prompt
    assert "CONVERSATION HISTORY" in prompt
    contract_pos = prompt.find("DEVELOPER CONTRACT:")
    history_pos = prompt.find("CONVERSATION HISTORY")
    assert contract_pos < history_pos


def test_perspectives_module_propagates_contract_to_context(monkeypatch):
    """Integration test: LLMPerspectiveEnsemble passes developer_contract to DelibContext."""
    from moralstack.orchestration.contract import DeveloperContract
    from moralstack.runtime.modules.perspective_module import (
        EnsembleConfig,
        LLMPerspectiveEnsemble,
    )

    captured: dict[str, str] = {}

    class _MockPolicy:
        def generate(self, prompt, system="", config=None):
            captured["system"] = system
            captured["prompt"] = prompt

            class _R:
                text = '{"approval_score": 0.8, "concerns": [], ' '"suggestions": [], "rationale": "ok"}'

                def token_usage_json(self_inner):
                    return None

            return _R()

    config = EnsembleConfig(parallel_evaluation=False, max_perspectives=1)
    module = LLMPerspectiveEnsemble(policy=_MockPolicy(), config=config)
    contract = DeveloperContract.from_text("If user sends 'PING', reply 'PONG'.")
    _ = module.evaluate(
        request="PING",
        response="PONG",
        developer_contract=contract,
    )
    assert "DEVELOPER CONTRACT:" in captured["system"]
    assert "If user sends 'PING'" in captured["system"]
