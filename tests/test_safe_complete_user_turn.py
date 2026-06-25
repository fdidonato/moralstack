"""
Tests for Step 10 / design v1.3 section 3.7: caveat-as-extra-user-turn pattern.

Verify that:
- _build_safe_complete_user_turn produces a well-formed user message.
- The synthetic user turn is appended to messages (not prepended).
- The user's system prompt is byte-identical before/after (no modification).
- No double injection of governance content.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from moralstack.orchestration.types import (
    FinalResponse,
    OrchestratorResult,
    ResponseMetadata,
    ResponseType,
)


def _make_safe_complete_result(content: str = "Take care to be cautious.") -> OrchestratorResult:
    metadata = ResponseMetadata()
    metadata.final_action = "SAFE_COMPLETE"
    metadata.risk_score = 0.5
    metadata.triggered_principles = ["safety"]
    metadata.reason_codes = ["SENSITIVE_TOPIC"]
    response = FinalResponse(content=content, response_type=ResponseType.WITH_CAVEAT, metadata=metadata)
    return OrchestratorResult(
        response=response,
        request_id="req-1",
        path_taken="deliberative",
        path="DELIBERATIVE_PATH",
        total_cycles=1,
        converged=True,
    )


@pytest.fixture
def mock_openai_response():
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock()
    resp.choices[0].message.content = "Safe response."
    return resp


@pytest.fixture
def wrapper_setup(mock_openai_response):
    from moralstack.sdk.config import GovernanceConfig
    from moralstack.sdk.session import SessionState
    from moralstack.sdk.wrapper import GovernedChat, GovernedClient

    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(return_value=mock_openai_response)

    mock_orchestrator = MagicMock()
    mock_orchestrator.process = MagicMock(return_value=_make_safe_complete_result())

    config = GovernanceConfig()
    governed = GovernedClient.__new__(GovernedClient)
    governed._client = mock_client
    governed._orchestrator = mock_orchestrator
    governed._config = config
    governed._session = SessionState(config)
    governed.chat = GovernedChat(governed)

    return governed, mock_client, mock_orchestrator


class TestBuildSafeCompleteUserTurn:
    def test_returns_user_role(self):
        from moralstack.sdk.wrapper import _build_safe_complete_user_turn

        result = _make_safe_complete_result()
        turn = _build_safe_complete_user_turn(result)
        assert turn["role"] == "user"

    def test_content_contains_governance_guidance(self):
        from moralstack.sdk.wrapper import _build_safe_complete_user_turn

        result = _make_safe_complete_result(content="Be cautious about X.")
        turn = _build_safe_complete_user_turn(result)
        assert "Be cautious about X." in turn["content"]
        assert "governance" in turn["content"].lower()

    def test_fallback_when_no_content(self):
        from moralstack.sdk.wrapper import _build_safe_complete_user_turn

        result = _make_safe_complete_result(content="")
        turn = _build_safe_complete_user_turn(result)
        assert turn["role"] == "user"
        assert "safety" in turn["content"] or "SENSITIVE_TOPIC" in turn["content"]


class TestSafeCompleteDeliversGovernedText:
    """
    Plan 1: SAFE_COMPLETE delivers the governed pipeline text directly. The
    wrapped client is never called for delivery, so there is no longer an
    upstream messages payload to inspect. The caveat-as-extra-user-turn pattern
    (``_build_safe_complete_user_turn``) is retained as a pure helper but is no
    longer applied to a wrapped-client call.
    """

    def test_wrapped_client_never_called_for_safe_complete(self, wrapper_setup):
        governed, mock_client, _ = wrapper_setup
        original_messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Tell me about something sensitive"},
        ]
        resp = governed.chat.completions.create(model="gpt-4o", messages=original_messages)

        mock_client.chat.completions.create.assert_not_called()
        assert resp.governance_metadata.final_action == "SAFE_COMPLETE"

    def test_delivers_governed_pipeline_content(self, wrapper_setup):
        governed, mock_client, _ = wrapper_setup
        original_messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Question"},
        ]
        resp = governed.chat.completions.create(model="gpt-4o", messages=original_messages)

        mock_client.chat.completions.create.assert_not_called()
        # The governed SAFE_COMPLETE content from _make_safe_complete_result.
        assert resp.content == "Take care to be cautious."

    def test_no_wrapped_client_call_when_system_absent(self, wrapper_setup):
        governed, mock_client, _ = wrapper_setup
        original_messages = [{"role": "user", "content": "Question"}]
        resp = governed.chat.completions.create(model="gpt-4o", messages=original_messages)

        mock_client.chat.completions.create.assert_not_called()
        assert resp.governance_metadata.final_action == "SAFE_COMPLETE"


class TestInjectSafeGuidanceRemoved:
    """The legacy _inject_safe_guidance must be gone."""

    def test_legacy_function_not_present(self):
        import moralstack.sdk.wrapper as wrapper_module

        assert not hasattr(wrapper_module, "_inject_safe_guidance")
