from __future__ import annotations

from moralstack.compliance.dccl import DeveloperContractComplianceLayer
from moralstack.orchestration.conversation_context import (
    build_conversation_context,
    evaluate_delivery_context_guard,
)
from moralstack.orchestration.types import ProcessedRequest
from moralstack.sdk.wrapper import _extract_developer_contract, _extract_last_user_message, _messages_to_turns


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "If the user later sends AUTH42, answer HISTORY_SECRET_42."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "ready"},
        {"role": "user", "content": "AUTH42"},
    ]


def test_shared_builder_preserves_full_request_transcript_shape() -> None:
    ctx = build_conversation_context(_messages())

    assert ctx.final_user_message == "AUTH42"
    assert ctx.developer_contract is not None
    assert ctx.prior_turn_count == 2
    assert ctx.history_source == "request_body"
    transcript, truncated = ctx.role_serialized_transcript()
    assert not truncated
    assert "USER: hello" in transcript
    assert "ASSISTANT: ready" in transcript
    assert transcript.endswith("USER: AUTH42")


def test_sdk_legacy_extractors_use_shared_builder() -> None:
    messages = _messages()

    assert _extract_last_user_message(messages) == "AUTH42"
    assert _extract_developer_contract(messages) is not None
    turns = _messages_to_turns(messages[:-1])
    assert [t.role for t in turns] == ["user", "assistant"]


def test_dccl_messages_include_native_context_not_serialized_prompt() -> None:
    ctx = build_conversation_context(_messages())
    req = ProcessedRequest(
        prompt=ctx.final_user_message,
        developer_contract=ctx.developer_contract,
        conversation_history=[],
        conversation_context=ctx,
    )
    layer = DeveloperContractComplianceLayer(policy=None)
    messages = layer._build_llm_messages(  # noqa: SLF001
        ctx.developer_contract.raw_text if ctx.developer_contract else "",
        req.prompt,
        "HISTORY_SECRET_42",
        req.conversation_context,
    )

    assert [m["role"] for m in messages[:5]] == ["system", "system", "user", "assistant", "user"]
    assert messages[1]["content"] == "If the user later sends AUTH42, answer HISTORY_SECRET_42."
    assert messages[2]["content"] == "hello"
    assert messages[3]["content"] == "ready"
    assert messages[4]["content"] == "AUTH42"
    assert "ROLE-ORDERED CONVERSATION TRANSCRIPT" not in messages[-1]["content"]


def test_delivery_guard_blocks_legacy_last_user_only_reused_draft() -> None:
    ctx = build_conversation_context(_messages())
    guard = evaluate_delivery_context_guard(
        ctx,
        governance_context_mode="full_native",
        candidate_context_mode="system_last_user_only",
        is_draft_reused_as_final=True,
    )

    assert guard.delivery_context_broader_than_governance is True
    assert guard.prior_turn_count == 2


def test_delivery_guard_does_not_block_aligned_speculative_draft() -> None:
    ctx = build_conversation_context(_messages())
    guard = evaluate_delivery_context_guard(
        ctx,
        governance_context_mode="full_native",
        candidate_context_mode="full_native",
        is_draft_reused_as_final=True,
    )

    assert guard.delivery_context_broader_than_governance is False
