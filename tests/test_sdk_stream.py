"""Tests for SDK streaming — GovernedStreamResponse, GovernedRefusalStream."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.sdk.response import GovernanceMetadata
from moralstack.sdk.wrapper import (
    GovernedClient,
    GovernedRefusalStream,
    GovernedStreamResponse,
    GovernedSyntheticStream,
    _SyntheticStreamChunk,
)


def _join_stream_text(stream: Any) -> str:
    return "".join(c.choices[0].delta.content for c in stream)


@pytest.fixture(autouse=True)
def disable_observability_for_stream_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Session autouse conftest sets MORALSTACK_DB_PATH=:memory:, which makes
    get_observability_mode() default to db_only and triggers SQLite init on
    every GovernedClient. Streaming tests use broad MagicMock orchestrator
    results; finalize_governance_audit then builds meta and runs
    emit_request_meta_updated -> _json_safe, which is extremely expensive on
    MagicMocks, and get_obs().flush() can block on the write queue.

    For this module only: no DB path for observability, file_only routing,
    no-op observability emits/flushes, and no-op finalize_governance_audit so
    SDK create() stays a pure unit test.
    """
    monkeypatch.setattr("moralstack.observability.config.get_db_path", lambda: None)
    monkeypatch.setattr(
        "moralstack.observability.config.get_observability_mode",
        lambda: "file_only",
    )
    # sqlite_sink and router bind config helpers at import time; patch those names too.
    monkeypatch.setattr("moralstack.observability.sinks.sqlite_sink.get_db_path", lambda: None)
    monkeypatch.setattr(
        "moralstack.observability.sinks.sqlite_sink.get_observability_mode",
        lambda: "file_only",
    )
    monkeypatch.setattr(
        "moralstack.observability.router.get_observability_mode",
        lambda: "file_only",
    )

    def _noop_emit(self: object, *_a: object, **_kw: object) -> None:
        return None

    def _noop_emit_batch(self: object, *_a: object, **_kw: object) -> None:
        return None

    def _noop_flush(self: object, *_a: object, **_kw: object) -> None:
        return None

    monkeypatch.setattr(
        "moralstack.observability.service.ObservabilityService.emit",
        _noop_emit,
    )
    monkeypatch.setattr(
        "moralstack.observability.service.ObservabilityService.emit_batch",
        _noop_emit_batch,
    )
    monkeypatch.setattr(
        "moralstack.observability.service.ObservabilityService.flush",
        _noop_flush,
    )

    # finalize_governance_audit runs build_request_meta + emit_request_meta_updated;
    # the latter calls _json_safe on meta built from broad MagicMocks (very expensive).
    monkeypatch.setattr(
        "moralstack.observability.governance_audit.finalize_governance_audit",
        lambda **kwargs: {},
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

    def test_stream_normal_complete_replays_governed_text_without_upstream(self):
        # Plan 1: streaming NORMAL_COMPLETE replays the governed pipeline text as a
        # synthetic stream; the wrapped client is never called.
        mock_openai, client = self._make_governed_client("NORMAL_COMPLETE")
        client._orchestrator.process.return_value.response.content = "governed answer"
        msgs = [{"role": "user", "content": "Hello"}]
        resp = client.chat.completions.create(model="gpt-4o", messages=msgs, stream=True)
        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedSyntheticStream)
        assert _join_stream_text(resp) == "governed answer"

    def test_stream_refuse_returns_refusal_stream(self):
        mock_openai, client = self._make_governed_client("REFUSE")
        msgs = [{"role": "user", "content": "How to hack?"}]
        resp = client.chat.completions.create(model="gpt-4o", messages=msgs, stream=True)
        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedRefusalStream)

    def test_stream_safe_complete_replays_governed_text_without_upstream(self):
        # Plan 1: streaming SAFE_COMPLETE replays the governed pipeline text as a
        # synthetic stream; the wrapped client is never called.
        mock_openai, client = self._make_governed_client("SAFE_COMPLETE")
        client._orchestrator.process.return_value.response.content = "Use caution."
        msgs = [{"role": "user", "content": "Tell me about medications"}]
        resp = client.chat.completions.create(model="gpt-4o", messages=msgs, stream=True)
        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedSyntheticStream)
        assert _join_stream_text(resp) == "Use caution."


class TestGovernedSyntheticStream:
    def test_replays_text_byte_for_byte_word_by_word(self):
        result = _make_result("NORMAL_COMPLETE")
        text = "Hello   world, this is\na test."
        stream = GovernedSyntheticStream(text, result)
        chunks = list(stream)
        assert len(chunks) > 1  # word-by-word, not a single chunk
        assert _join_stream_text(chunks) == text

    def test_only_last_chunk_has_finish_reason(self):
        result = _make_result("NORMAL_COMPLETE")
        stream = GovernedSyntheticStream("one two three", result)
        chunks = list(stream)
        assert all(c.choices[0].finish_reason is None for c in chunks[:-1])
        assert chunks[-1].choices[0].finish_reason == "stop"

    def test_all_chunks_share_one_stream_id(self):
        result = _make_result("NORMAL_COMPLETE")
        chunks = list(GovernedSyntheticStream("alpha beta gamma", result))
        assert len({c.id for c in chunks}) == 1

    def test_empty_text_yields_single_terminal_chunk(self):
        result = _make_result("NORMAL_COMPLETE")
        chunks = list(GovernedSyntheticStream("", result))
        assert len(chunks) == 1
        assert chunks[0].choices[0].finish_reason == "stop"

    def test_metadata_reflects_governance_decision(self):
        result = _make_result("NORMAL_COMPLETE")
        stream = GovernedSyntheticStream("text", result)
        assert isinstance(stream.governance_metadata, GovernanceMetadata)
        assert stream.governance_metadata.final_action == "NORMAL_COMPLETE"


class TestStreamingWithContract:
    """Streaming + developer contract: no live upstream tokens; validated text replayed."""

    def _make_governed_client(self, final_action: str) -> tuple[Any, GovernedClient]:
        from moralstack.sdk.config import GovernanceConfig

        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = MagicMock()
        orch = MagicMock()
        result = _make_result(final_action)
        orch.process.return_value = result
        client = GovernedClient(mock_openai, orch, GovernanceConfig())
        return mock_openai, client, result

    # A system message yields a DeveloperContract (build_conversation_context).
    CONTRACT_MSGS = [
        {"role": "system", "content": "You only answer PING with PONG."},
        {"role": "user", "content": "PING"},
    ]

    def test_compliance_fast_path_replays_governed_draft_no_upstream(self):
        mock_openai, client, result = self._make_governed_client("NORMAL_COMPLETE")
        result.path = "COMPLIANCE_FAST_PATH"
        result.delivery_context_broader_than_governance = False
        result.response.content = "PONG"

        resp = client.chat.completions.create(model="gpt-4o", messages=self.CONTRACT_MSGS, stream=True)

        # Validated draft is reused: no upstream generation at all.
        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedSyntheticStream)
        assert _join_stream_text(resp) == "PONG"
        assert resp.governance_metadata.final_action == "NORMAL_COMPLETE"

    def test_normal_with_contract_replays_governed_text_no_upstream(self):
        # Plan 1: with a developer contract present, streaming NORMAL_COMPLETE
        # replays the governed pipeline text and never calls the wrapped client.
        mock_openai, client, result = self._make_governed_client("NORMAL_COMPLETE")
        result.path = "FAST_PATH"
        result.delivery_context_broader_than_governance = False
        result.response.content = "GOVERNED ANSWER"

        resp = client.chat.completions.create(model="gpt-4o", messages=self.CONTRACT_MSGS, stream=True)

        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedSyntheticStream)
        assert _join_stream_text(resp) == "GOVERNED ANSWER"

    def test_refuse_with_contract_replays_refusal_as_stream(self):
        # Plan 1: a governed REFUSE on a streaming request with a contract yields
        # the governed refusal text; the wrapped client is never called.
        mock_openai, client, result = self._make_governed_client("REFUSE")
        result.path = "FAST_PATH"
        result.delivery_context_broader_than_governance = False
        result.response.content = "I cannot provide that content."

        resp = client.chat.completions.create(model="gpt-4o", messages=self.CONTRACT_MSGS, stream=True)

        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedRefusalStream)
        assert _join_stream_text(resp) == "I cannot provide that content."
