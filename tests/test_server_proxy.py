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
