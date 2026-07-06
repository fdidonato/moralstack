"""Perspectives module must pass developer/history as native messages."""

from moralstack.prompts.perspectives_prompt import build_perspectives_system_prompt


def test_perspectives_system_prompt_does_not_inline_contract_when_present():
    # Prompt-caching reorder (A5a): build_perspectives_system_prompt is now
    # ctx-independent (static only) — it structurally cannot inline a
    # developer contract or REQUEST/RESPONSE regardless of what the caller's
    # context carries. Developer contract/history stay in their own message
    # slots (see test_perspectives_module_propagates_contract_to_context).
    prompt = build_perspectives_system_prompt()
    assert "DEVELOPER CONTRACT:" not in prompt
    assert "If the user sends 'PING'" not in prompt


def test_perspectives_system_prompt_omits_contract_when_absent():
    """The DEVELOPER CONTRACT block must NOT appear (system prompt is ctx-independent)."""
    prompt = build_perspectives_system_prompt()
    assert "DEVELOPER CONTRACT:" not in prompt


def test_perspectives_system_prompt_does_not_inline_contract_or_history():
    prompt = build_perspectives_system_prompt()
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
