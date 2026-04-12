"""Tests for SDK streaming — GovernedStreamResponse, GovernedRefusalStream."""

from typing import Any
from unittest.mock import MagicMock

from moralstack.sdk.response import GovernanceMetadata
from moralstack.sdk.wrapper import (
    GovernedClient,
    GovernedRefusalStream,
    GovernedStreamResponse,
    _SyntheticStreamChunk,
)


def _make_result(final_action: str = "NORMAL_COMPLETE") -> Any:
    result = MagicMock()
    result.response.content = "Refusal text"
    result.response.metadata.final_action = final_action
    result.response.metadata.risk_score = 0.1
    result.response.metadata.risk_category = "CLEARLY_BENIGN"
    result.response.metadata.path = "FAST_PATH"
    result.response.metadata.domain_overlay = None
    result.response.metadata.reason_codes = []
    result.response.metadata.winning_rule = "low_risk"
    result.response.metadata.decision_reason = "OK"
    result.response.metadata.processing_time_ms = 100
    result.response.metadata.deliberation_cycles = 0
    result.response.metadata.triggered_principles = []
    result.response.metadata.why_not_refuse = ""
    result.response.metadata.why_not_safe_complete = ""
    result.conversation_id = "conv-stream"
    result.turn_index = 0
    result.conversation_governance_state_out = None
    return result


class TestGovernedStreamResponse:
    def test_governance_metadata_available_before_iteration(self):
        mock_stream = iter([MagicMock(), MagicMock()])
        result = _make_result("NORMAL_COMPLETE")
        resp = GovernedStreamResponse(mock_stream, result)
        assert isinstance(resp.governance_metadata, GovernanceMetadata)
        assert resp.governance_metadata.final_action == "NORMAL_COMPLETE"

    def test_iteration_yields_original_chunks(self):
        chunk1, chunk2 = MagicMock(), MagicMock()
        mock_stream = iter([chunk1, chunk2])
        result = _make_result("NORMAL_COMPLETE")
        resp = GovernedStreamResponse(mock_stream, result)
        chunks = list(resp)
        assert chunks[0] is chunk1
        assert chunks[1] is chunk2

    def test_context_manager_protocol(self):
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        result = _make_result()
        with GovernedStreamResponse(mock_stream, result) as resp:
            assert isinstance(resp, GovernedStreamResponse)


class TestGovernedRefusalStream:
    def test_governance_metadata_has_refuse_action(self):
        result = _make_result("REFUSE")
        refusal = GovernedRefusalStream(result)
        assert refusal.governance_metadata.final_action == "REFUSE"

    def test_iteration_yields_single_synthetic_chunk(self):
        result = _make_result("REFUSE")
        refusal = GovernedRefusalStream(result)
        chunks = list(refusal)
        assert len(chunks) == 1
        assert isinstance(chunks[0], _SyntheticStreamChunk)

    def test_refusal_chunk_has_content(self):
        result = _make_result("REFUSE")
        result.response.content = "I cannot help with that."
        refusal = GovernedRefusalStream(result)
        chunks = list(refusal)
        assert chunks[0].choices[0].delta.content == "I cannot help with that."

    def test_context_manager_protocol(self):
        result = _make_result("REFUSE")
        with GovernedRefusalStream(result) as resp:
            assert isinstance(resp, GovernedRefusalStream)


class TestStreamingViaCreate:
    def _make_governed_client(self, final_action: str) -> tuple[Any, GovernedClient]:
        from moralstack.sdk.config import GovernanceConfig

        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = MagicMock()

        orch = MagicMock()
        result = _make_result(final_action)
        orch.process.return_value = result

        client = GovernedClient(mock_openai, orch, GovernanceConfig())
        return mock_openai, client

    def test_stream_normal_complete_returns_stream_response(self):
        mock_openai, client = self._make_governed_client("NORMAL_COMPLETE")
        msgs = [{"role": "user", "content": "Hello"}]
        resp = client.chat.completions.create(model="gpt-4o", messages=msgs, stream=True)
        mock_openai.chat.completions.create.assert_called_once()
        assert isinstance(resp, GovernedStreamResponse)

    def test_stream_refuse_returns_refusal_stream(self):
        mock_openai, client = self._make_governed_client("REFUSE")
        msgs = [{"role": "user", "content": "How to hack?"}]
        resp = client.chat.completions.create(model="gpt-4o", messages=msgs, stream=True)
        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedRefusalStream)

    def test_stream_safe_complete_calls_openai_with_modified_kwargs(self):
        mock_openai, client = self._make_governed_client("SAFE_COMPLETE")
        msgs = [{"role": "user", "content": "Tell me about medications"}]
        client.chat.completions.create(model="gpt-4o", messages=msgs, stream=True)
        mock_openai.chat.completions.create.assert_called_once()
        # Ensure stream=True is preserved in modified kwargs
        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
