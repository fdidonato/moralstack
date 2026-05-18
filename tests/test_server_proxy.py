"""
Integration tests for the FastAPI server proxy (design v1.3 §4.2).
"""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed (install with [server] extra)")
pytest.importorskip("httpx", reason="httpx not installed (install with [server] extra)")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from moralstack.orchestration.types import (  # noqa: E402
    FinalResponse,
    OrchestratorResult,
    ResponseMetadata,
    ResponseType,
)
from moralstack.runtime.orchestrator import create_minimal_orchestrator  # noqa: E402
from moralstack.sdk.config import GovernanceConfig  # noqa: E402
from moralstack.server.proxy import create_app  # noqa: E402
from tests.test_orchestrator import MockPolicyLLM, MockRiskEstimator  # noqa: E402


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
        body = response.json()
        assert body.get("status") == "ok"
        # The healthz response also reports the proxy observability run_id
        # (empty string when observability is not configured).
        assert "run_id" in body


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
        assert conv_id is not None and conv_id.startswith("msconv-")


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
        """Lineage correlation keeps the same conversation_id across COMPL-AI-style turns."""
        client, mock_openai, _ = client_factory("NORMAL_COMPLETE")

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
        assert conv_id_t1 is not None and conv_id_t1.startswith("msconv-")

        r2 = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "First Q"},
                    {"role": "assistant", "content": "Upstream answer."},
                    {"role": "user", "content": "Second Q"},
                ],
            },
        )
        conv_id_t2 = r2.headers.get("X-Moralstack-Conversation-Id")
        assert conv_id_t2 == conv_id_t1

        r3 = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "First Q"},
                    {"role": "assistant", "content": "Upstream answer."},
                    {"role": "user", "content": "Second Q"},
                    {"role": "assistant", "content": "Upstream answer."},
                    {"role": "user", "content": "Third Q"},
                ],
            },
        )
        conv_id_t3 = r3.headers.get("X-Moralstack-Conversation-Id")
        assert conv_id_t3 == conv_id_t1
        assert mock_openai.chat.completions.create.call_count == 3

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


class TestAsyncConcurrency:
    """
    Concurrent in-flight requests against the ASGI app.

    Starlette ``TestClient`` is not thread-safe for overlapping calls from multiple
    threads; ``httpx.AsyncClient`` + ``ASGITransport`` matches production async
    concurrency and exercises ``run_in_threadpool`` + per-conversation locks.
    """

    @staticmethod
    def _build_app_for_parallel():
        from moralstack.sdk.config import GovernanceConfig
        from moralstack.server.proxy import create_app

        active = threading.Lock()
        in_flight = [0]
        max_active = [0]

        def slow_process(*args, **kwargs):
            del args, kwargs
            with active:
                in_flight[0] += 1
                max_active[0] = max(max_active[0], in_flight[0])
            import time as _time

            _time.sleep(0.05)
            with active:
                in_flight[0] -= 1
            return _make_result("NORMAL_COMPLETE")

        mock_orchestrator = MagicMock()
        mock_orchestrator.process = MagicMock(side_effect=slow_process)
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(return_value=_make_upstream_chat_completion())
        app = create_app(openai_client=mock_openai, orchestrator=mock_orchestrator, config=GovernanceConfig())
        return app, mock_orchestrator, mock_openai, max_active

    def test_parallel_different_conversation_headers_overlap_orchestrator(self):
        app, mock_orch, _, max_active = self._build_app_for_parallel()

        async def _run():
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

                async def one(i: int):
                    r = await client.post(
                        "/v1/chat/completions",
                        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "q"}]},
                        headers={"X-Moralstack-Conversation-Id": f"conv-{i}"},
                    )
                    assert r.status_code == 200

                await asyncio.gather(*(one(i) for i in range(10)))

        asyncio.run(_run())
        assert mock_orch.process.call_count == 10
        assert max_active[0] > 1

    def test_parallel_same_conversation_header_serializes_orchestrator(self):
        app, mock_orch, _, _ = self._build_app_for_parallel()
        overlaps: list[int] = []
        active = threading.Lock()
        count = [0]

        def tracked_process(*args, **kwargs):
            del args, kwargs
            with active:
                count[0] += 1
                if count[0] > 1:
                    overlaps.append(count[0])
            import time as _time

            _time.sleep(0.05)
            with active:
                count[0] -= 1
            return _make_result("NORMAL_COMPLETE")

        mock_orch.process = MagicMock(side_effect=tracked_process)

        async def _run():
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

                async def one():
                    r = await client.post(
                        "/v1/chat/completions",
                        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "q"}]},
                        headers={"X-Moralstack-Conversation-Id": "same-conv"},
                    )
                    assert r.status_code == 200

                await asyncio.gather(*(one() for _ in range(10)))

        asyncio.run(_run())
        assert mock_orch.process.call_count == 10
        assert overlaps == []

    def test_concurrent_distinct_conversations_jsonl_metadata_matches_session(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        End-to-end: with a real Orchestrator behind the proxy, concurrent POSTs with
        distinct X-Moralstack-Conversation-Id must emit proxy.request_finalized JSONL
        lines where envelope session_id matches payload.metadata.conversation_id.
        """

        class SlowRisk(MockRiskEstimator):
            def estimate(self, prompt: str):  # type: ignore[override]
                time.sleep(random.uniform(0.02, 0.06))
                return super().estimate(prompt)

        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(obs_dir))

        real_orch = create_minimal_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=SlowRisk(),
        )
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(return_value=_make_upstream_chat_completion())
        app = create_app(openai_client=mock_openai, orchestrator=real_orch, config=GovernanceConfig())

        n = 8

        async def _run() -> None:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

                async def one(i: int) -> None:
                    r = await client.post(
                        "/v1/chat/completions",
                        json={
                            "model": "gpt-4o",
                            "messages": [{"role": "user", "content": f"hello weather {i}"}],
                        },
                        headers={"X-Moralstack-Conversation-Id": f"conv-{i:03d}"},
                    )
                    assert r.status_code == 200

                await asyncio.gather(*(one(i) for i in range(n)))

        asyncio.run(_run())

        proxy_jsonl = obs_dir / "proxy.request_finalized.jsonl"
        assert proxy_jsonl.is_file()
        events = [json.loads(line) for line in proxy_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(events) == n
        for e in events:
            top_sid = e.get("session_id")
            payload = e.get("payload") or {}
            meta = payload.get("metadata") or {}
            meta_cid = meta.get("conversation_id")
            headers = payload.get("headers") or {}
            hdr_cid = headers.get("X-Moralstack-Conversation-Id")
            assert top_sid and hdr_cid, (top_sid, hdr_cid)
            assert top_sid == hdr_cid, f"session_id {top_sid!r} != header {hdr_cid!r}"
            if meta_cid is not None:
                assert meta_cid == top_sid, f"metadata.conversation_id {meta_cid!r} != session_id {top_sid!r}"

        ss_path = obs_dir / "session_store.put.jsonl"
        if ss_path.is_file():
            ss_events = [json.loads(line) for line in ss_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            for e in ss_events:
                top_sid = e.get("session_id")
                payload = e.get("payload") or {}
                summary = payload.get("state_summary") or {}
                inner = summary.get("conversation_id") if isinstance(summary, dict) else None
                if inner is not None:
                    assert inner == top_sid, f"session_store.put mismatch {inner!r} vs {top_sid!r}"


class TestConversationLockTimeout:
    def test_second_acquire_raises_when_lock_held(self):
        from moralstack.server.proxy import ConversationLockManager, ConversationLockTimeout

        mgr = ConversationLockManager()
        first = mgr.acquire("locked-conv")
        try:

            def try_second():
                with pytest.raises(ConversationLockTimeout):
                    mgr.acquire("locked-conv", timeout=0.05)

            t = threading.Thread(target=try_second)
            t.start()
            t.join()
        finally:
            mgr.release(first)


class TestObservabilityPersistence:
    """
    Verifies that the proxy correctly initializes observability and persists
    request data to the configured backend (SQLite DB + JSONL).

    This was the bug: Step 11/12 proxy never set run_id/request_id in the
    observability context, so DefaultPersistence.ensure_run_and_upsert_request
    silently no-op'd and nothing was written. Fixed by Step 12-bis with
    _initialize_observability_run + set_current_request_id in the handler.
    """

    def test_proxy_persists_to_sqlite_db(self, client_factory, tmp_path, monkeypatch):
        """When MORALSTACK_OBSERVABILITY_DB_PATH is set, the proxy creates a run and persists requests."""
        db_path = str(tmp_path / "test_proxy.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")

        # Re-import the proxy module so create_app picks up the env vars.
        # We rebuild a client_factory inline since the fixture's create_app
        # was instantiated before the monkeypatch.
        from moralstack.sdk.config import GovernanceConfig
        from moralstack.server.proxy import create_app

        mock_orchestrator = MagicMock()
        mock_orchestrator.process = MagicMock(return_value=_make_result("NORMAL_COMPLETE"))
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(return_value=_make_upstream_chat_completion())
        app = create_app(openai_client=mock_openai, orchestrator=mock_orchestrator, config=GovernanceConfig())
        client = TestClient(app)

        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]},
            headers={"X-Moralstack-Conversation-Id": "persistence-test"},
        )
        assert response.status_code == 200

        # The DB must now exist with a runs row and a requests row.
        import os as _os
        import sqlite3

        assert _os.path.exists(db_path), f"DB not created at {db_path}"
        conn = sqlite3.connect(db_path)
        runs = conn.execute("SELECT run_id, run_type FROM runs").fetchall()
        assert len(runs) == 1
        assert runs[0][1] == "proxy"
        requests_count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        assert requests_count == 1
        # The request row has the conversation_id we passed.
        row = conn.execute("SELECT conversation_id, final_response FROM requests LIMIT 1").fetchone()
        assert row[0] == "persistence-test"
        # final_response was updated with the upstream content.
        assert row[1] == "Upstream answer."
        conn.close()

    def test_healthz_reports_run_id_when_persistence_active(self, tmp_path, monkeypatch):
        """The /healthz endpoint reports the proxy run_id when observability is configured."""
        db_path = str(tmp_path / "test_healthz.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")

        from moralstack.sdk.config import GovernanceConfig
        from moralstack.server.proxy import create_app

        app = create_app(openai_client=MagicMock(), orchestrator=MagicMock(), config=GovernanceConfig())
        client = TestClient(app)

        response = client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # run_id is a non-empty UUID-like string when persistence is active.
        assert body["run_id"] and len(body["run_id"]) >= 8

    def test_proxy_persists_orchestration_events(self, tmp_path, monkeypatch):
        """Verify that orchestration events emitted by the pipeline are persisted to DB.

        This is the FK fix: the requests row must be pre-inserted BEFORE the pipeline
        emits orchestration_events / llm_calls / decision_traces (which have FK
        constraints on requests).
        """
        db_path = str(tmp_path / "test_orch_events.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")

        from moralstack.observability import make_envelope, obs
        from moralstack.observability.events import EVENT_ORCHESTRATION_EVENT
        from moralstack.sdk.config import GovernanceConfig
        from moralstack.server.proxy import create_app

        # An orchestrator that emits an orchestration event during process().
        def fake_process(*args, **kwargs):
            del args, kwargs
            from moralstack.observability.context import get_current_request_id, get_current_run_id

            run_id = get_current_run_id()
            request_id = get_current_request_id()
            if run_id and request_id:
                obs.emit(
                    make_envelope(
                        EVENT_ORCHESTRATION_EVENT,
                        run_id=run_id,
                        request_id=request_id,
                        payload={
                            "stage": "deliberation",
                            "component": "critic",
                            "decision": "approved",
                            "status": "ok",
                            "started_at": 1000,
                            "duration_ms": 5,
                        },
                    )
                )
            return _make_result("NORMAL_COMPLETE")

        mock_orchestrator = MagicMock()
        mock_orchestrator.process = fake_process
        mock_openai = MagicMock()
        mock_openai.chat.completions.create = MagicMock(return_value=_make_upstream_chat_completion())

        app = create_app(openai_client=mock_openai, orchestrator=mock_orchestrator, config=GovernanceConfig())
        client = TestClient(app)

        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
            headers={"X-Moralstack-Conversation-Id": "orch-events-test"},
        )
        assert response.status_code == 200

        # Verify the orchestration_event row is in the DB (no FK violation).
        import sqlite3

        conn = sqlite3.connect(db_path)
        events = conn.execute("SELECT stage, component, decision FROM orchestration_events").fetchall()
        conn.close()
        assert len(events) == 1
        assert events[0] == ("deliberation", "critic", "approved")
