"""
Integration tests for the FastAPI server proxy (design v1.3 §4.2).
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed (install with [server] extra)")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.orchestration.types import (  # noqa: E402
    FinalResponse,
    OrchestratorResult,
    ResponseMetadata,
    ResponseType,
)


def _make_result(final_action: str, content: str = "Test content.") -> OrchestratorResult:
    metadata = ResponseMetadata()
    metadata.final_action = final_action
    metadata.risk_score = 0.4
    metadata.triggered_principles = ["safety"]
    metadata.reason_codes = ["TEST"]
    rtype = ResponseType.FULL_REFUSAL if final_action == "REFUSE" else ResponseType.WITH_CAVEAT
    response = FinalResponse(content=content, response_type=rtype, metadata=metadata)
    return OrchestratorResult(
        response=response,
        request_id="req-test",
        path_taken="deliberative",
        path="DELIBERATIVE_PATH",
        total_cycles=1,
        converged=True,
    )


def _make_upstream_chat_completion(content: str = "Upstream answer.") -> Any:
    """Build a MagicMock that mimics openai.types.ChatCompletion via model_dump()."""
    payload = {
        "id": "chatcmpl-upstream-1",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    upstream = MagicMock()
    upstream.model_dump = MagicMock(return_value=payload)
    return upstream


@pytest.fixture
def client_factory():
    """Factory that returns (client, mock_openai, mock_orchestrator) for a given final_action."""
    from moralstack.sdk.config import GovernanceConfig
    from moralstack.server.proxy import create_app

    def _build(final_action: str, refuse_content: str = "Cannot help with that."):
        mock_orchestrator = MagicMock()
        if final_action == "REFUSE":
            mock_orchestrator.process = MagicMock(return_value=_make_result("REFUSE", content=refuse_content))
        else:
            mock_orchestrator.process = MagicMock(return_value=_make_result(final_action, content="guidance"))

        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(return_value=_make_upstream_chat_completion())

        app = create_app(openai_client=mock_openai, orchestrator=mock_orchestrator, config=GovernanceConfig())
        return TestClient(app), mock_openai, mock_orchestrator

    return _build


class TestHealthz:
    def test_healthz_ok(self, client_factory):
        client, _, _ = client_factory("NORMAL_COMPLETE")
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestRouting:
    def test_refuse_returns_synthetic_completion(self, client_factory):
        client, mock_openai, _ = client_factory("REFUSE", refuse_content="I cannot.")
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q"}]},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["object"] == "chat.completion"
        assert payload["choices"][0]["message"]["content"] == "I cannot."
        assert payload["choices"][0]["finish_reason"] == "content_filter"
        # Upstream MUST NOT be called for REFUSE
        mock_openai.chat.completions.create.assert_not_called()

    def test_safe_complete_forwards_to_upstream_with_synthetic_turn(self, client_factory):
        client, mock_openai, _ = client_factory("SAFE_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Q"},
                ],
            },
        )
        assert response.status_code == 200
        mock_openai.chat.completions.create.assert_called_once()
        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        forwarded_messages = call_kwargs["messages"]
        # Synthetic user turn appended at the end
        assert forwarded_messages[-1]["role"] == "user"
        assert "governance" in forwarded_messages[-1]["content"].lower()
        # System message preserved byte-identical
        system_msgs = [m for m in forwarded_messages if m["role"] == "system"]
        assert system_msgs[0]["content"] == "You are helpful."

    def test_normal_complete_forwards_unchanged(self, client_factory):
        client, mock_openai, _ = client_factory("NORMAL_COMPLETE")
        original_messages = [{"role": "user", "content": "Q"}]
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": original_messages},
        )
        assert response.status_code == 200
        mock_openai.chat.completions.create.assert_called_once()
        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        # No synthetic turn — original messages preserved
        assert call_kwargs["messages"] == original_messages


class TestGovernanceHeaders:
    def test_headers_present_on_refuse(self, client_factory):
        client, _, _ = client_factory("REFUSE")
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q"}]},
        )
        assert response.headers.get("X-Moralstack-Decision") == "REFUSE"
        assert response.headers.get("X-Moralstack-Risk-Score") == "0.4000"
        assert "X-Moralstack-Path" in response.headers
        assert "X-Moralstack-Conversation-Id" in response.headers

    def test_headers_present_on_normal_complete(self, client_factory):
        client, _, _ = client_factory("NORMAL_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q"}]},
        )
        assert response.headers.get("X-Moralstack-Decision") == "NORMAL_COMPLETE"
        assert "X-Moralstack-Risk-Score" in response.headers

    def test_headers_present_on_safe_complete(self, client_factory):
        client, _, _ = client_factory("SAFE_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q"}]},
        )
        assert response.headers.get("X-Moralstack-Decision") == "SAFE_COMPLETE"


class TestConversationIdResolution:
    def test_extra_body_wins_over_fingerprint(self, client_factory):
        client, _, _ = client_factory("NORMAL_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Q"}],
                "extra_body": {"moralstack_conversation_id": "custom-conv-123"},
            },
        )
        assert response.headers.get("X-Moralstack-Conversation-Id") == "custom-conv-123"

    def test_header_wins_over_fingerprint(self, client_factory):
        client, _, _ = client_factory("NORMAL_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q"}]},
            headers={"X-Moralstack-Conversation-Id": "header-conv-456"},
        )
        assert response.headers.get("X-Moralstack-Conversation-Id") == "header-conv-456"

    def test_fingerprint_fallback(self, client_factory):
        client, _, _ = client_factory("NORMAL_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q"}]},
        )
        conv_id = response.headers.get("X-Moralstack-Conversation-Id")
        assert conv_id is not None and conv_id.startswith("msf-")


class TestValidation:
    def test_empty_messages_returns_400(self, client_factory):
        client, _, _ = client_factory("NORMAL_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
        )
        assert response.status_code == 400

    def test_missing_messages_returns_400(self, client_factory):
        client, _, _ = client_factory("NORMAL_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o"},
        )
        assert response.status_code == 400

    def test_invalid_json_returns_400(self, client_factory):
        client, _, _ = client_factory("NORMAL_COMPLETE")
        response = client.post(
            "/v1/chat/completions",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400


class TestUpstreamFailure:
    def test_upstream_error_returns_502(self, client_factory):
        client, mock_openai, _ = client_factory("NORMAL_COMPLETE")
        mock_openai.chat.completions.create = MagicMock(side_effect=RuntimeError("Upstream down"))
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q"}]},
        )
        assert response.status_code == 502


class TestConcurrency:
    """Two concurrent requests on the same conversation_id must be serialized."""

    def test_concurrent_same_conversation_serialized(self, client_factory):
        client, mock_openai, mock_orchestrator = client_factory("NORMAL_COMPLETE")

        # Slow down the orchestrator to detect ordering
        call_log: list[float] = []
        import time as _time

        original_process = mock_orchestrator.process

        def slow_process(*args, **kwargs):
            call_log.append(_time.time())
            _time.sleep(0.05)
            return original_process.return_value

        mock_orchestrator.process = MagicMock(side_effect=slow_process)

        results: list[int] = []

        def do_request():
            r = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q"}]},
                headers={"X-Moralstack-Conversation-Id": "concurrent-conv"},
            )
            results.append(r.status_code)

        threads = [threading.Thread(target=do_request) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results == [200, 200]
        # Calls must have been serialized — second started at least 40ms after first
        assert len(call_log) == 2
        assert call_log[1] - call_log[0] >= 0.04


class TestMultiTurnConversation:
    """
    End-to-end multi-turn conversation tests via the proxy (Step 12).

    Verifies that the proxy correctly:
    - Increments turn_index across sequential requests on the same conversation_id.
    - Recovers the previous ConversationGovernanceState from the SessionStore.
    - Persists the new state after each turn for the next turn to use.
    - Builds conversation_history from the messages payload correctly.
    """

    def test_turn_index_grows_with_user_messages(self, client_factory):
        """turn_index must reflect the count of user messages in the payload."""
        client, _, mock_orchestrator = client_factory("NORMAL_COMPLETE")

        # Turn 1
        client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q1"}]},
            headers={"X-Moralstack-Conversation-Id": "multiturn-test-1"},
        )
        assert mock_orchestrator.process.call_args[1]["turn_index"] == 0
        assert mock_orchestrator.process.call_args[1]["conversation_id"] == "multiturn-test-1"

        # Turn 2 (full history)
        client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2"},
                ],
            },
            headers={"X-Moralstack-Conversation-Id": "multiturn-test-1"},
        )
        assert mock_orchestrator.process.call_args[1]["turn_index"] == 1

        # Turn 3 (longer history)
        client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2"},
                    {"role": "assistant", "content": "A2"},
                    {"role": "user", "content": "Q3"},
                ],
            },
            headers={"X-Moralstack-Conversation-Id": "multiturn-test-1"},
        )
        assert mock_orchestrator.process.call_args[1]["turn_index"] == 2

    def test_conversation_history_extracted_from_payload(self, client_factory):
        """ProcessedRequest.conversation_history must include all messages except the last user prompt."""
        client, _, mock_orchestrator = client_factory("NORMAL_COMPLETE")

        client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2 — current"},
                ],
            },
            headers={"X-Moralstack-Conversation-Id": "multiturn-test-history"},
        )
        processed_request = mock_orchestrator.process.call_args[0][0]
        assert processed_request.prompt == "Q2 — current"
        assert len(processed_request.conversation_history) == 2
        assert processed_request.conversation_history[0].role == "user"
        assert processed_request.conversation_history[0].content == "Q1"
        assert processed_request.conversation_history[1].role == "assistant"
        assert processed_request.conversation_history[1].content == "A1"

    def test_state_persisted_and_recovered_across_turns(self, client_factory):
        """ConversationGovernanceState is stored after turn N and recovered at turn N+1.

        IMPORTANT — discovered during simulation:
        - ResponseType.NORMAL does NOT exist; use ResponseType.DIRECT for NORMAL_COMPLETE results.
        - ConversationGovernanceState has `conversation_id` field, NOT `session_id`.
        """
        from moralstack.orchestration.conversation_state import ConversationGovernanceState

        client, _, mock_orchestrator = client_factory("NORMAL_COMPLETE")

        def _make_result_with_state(turn_idx: int):
            metadata = ResponseMetadata()
            metadata.final_action = "NORMAL_COMPLETE"
            metadata.risk_score = 0.1 + 0.1 * turn_idx
            response = FinalResponse(content=f"reply_{turn_idx}", response_type=ResponseType.DIRECT, metadata=metadata)
            state = ConversationGovernanceState(
                conversation_id="multiturn-state-test",
                turn_index=turn_idx,
            )
            result = OrchestratorResult(
                response=response,
                request_id=f"req-{turn_idx}",
                path_taken="deliberative",
                path="DELIBERATIVE_PATH",
                total_cycles=1,
                converged=True,
            )
            result.conversation_governance_state_out = state
            return result

        # Turn 1
        mock_orchestrator.process = MagicMock(return_value=_make_result_with_state(0))
        client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q1"}]},
            headers={"X-Moralstack-Conversation-Id": "multiturn-state-test"},
        )
        assert mock_orchestrator.process.call_args[1]["conversation_state"] is None

        # Turn 2 — the state from turn 1 should be recovered
        mock_orchestrator.process = MagicMock(return_value=_make_result_with_state(1))
        client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "Q2"},
                ],
            },
            headers={"X-Moralstack-Conversation-Id": "multiturn-state-test"},
        )
        recovered_state = mock_orchestrator.process.call_args[1]["conversation_state"]
        assert recovered_state is not None
        assert isinstance(recovered_state, ConversationGovernanceState)
        assert recovered_state.turn_index == 0  # State saved after turn 1.

    def test_conversation_id_stable_across_turns(self, client_factory):
        """The conversation_id resolved via fingerprint is stable from turn 2 onwards."""
        client, _, _ = client_factory("NORMAL_COMPLETE")

        r1 = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "First Q"},
                ],
            },
        )
        conv_id_t1 = r1.headers.get("X-Moralstack-Conversation-Id")
        assert conv_id_t1 is not None and conv_id_t1.startswith("msf-")

        # Turn 2: now we have 3 messages in the prefix (system + user + assistant) — different fingerprint.
        r2 = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "First Q"},
                    {"role": "assistant", "content": "First A"},
                    {"role": "user", "content": "Second Q"},
                ],
            },
        )
        conv_id_t2 = r2.headers.get("X-Moralstack-Conversation-Id")
        assert conv_id_t2 is not None and conv_id_t2.startswith("msf-")

        # Turn 3: same prefix (system + user + assistant) — same fingerprint as turn 2.
        r3 = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "First Q"},
                    {"role": "assistant", "content": "First A"},
                    {"role": "user", "content": "Second Q"},
                    {"role": "assistant", "content": "Second A"},
                    {"role": "user", "content": "Third Q"},
                ],
            },
        )
        conv_id_t3 = r3.headers.get("X-Moralstack-Conversation-Id")
        assert conv_id_t3 == conv_id_t2  # Stable from turn 2 onwards.

    def test_separate_conversations_independent(self, client_factory):
        """Two conversations with different conversation_ids do not share state."""
        client, _, mock_orchestrator = client_factory("NORMAL_COMPLETE")

        client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q_A1"}]},
            headers={"X-Moralstack-Conversation-Id": "conv-A"},
        )
        client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Q_B1"}]},
            headers={"X-Moralstack-Conversation-Id": "conv-B"},
        )

        assert mock_orchestrator.process.call_count == 2
        all_kwargs = [c.kwargs for c in mock_orchestrator.process.call_args_list]
        assert all_kwargs[0]["conversation_id"] == "conv-A"
        assert all_kwargs[1]["conversation_id"] == "conv-B"
        # Both are turn 0 (first turn in each conversation independently).
        assert all_kwargs[0]["turn_index"] == 0
        assert all_kwargs[1]["turn_index"] == 0
