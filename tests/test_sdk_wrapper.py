"""Tests for moralstack.sdk.wrapper — govern(), GovernedClient, GovernedCompletions."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.orchestration.contract import DeveloperContract
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.response import GovernedResponse
from moralstack.sdk.wrapper import (
    GovernedClient,
    GovernedCompletions,
    _extract_developer_contract,
    _extract_last_user_message,
    _inject_safe_guidance,
    _messages_to_turns,
    govern,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_orchestrator(final_action: str = "NORMAL_COMPLETE", content: str = "OK") -> Any:
    """Return a mock Orchestrator whose process() returns a preset result."""
    orchestrator = MagicMock()
    result = MagicMock()
    result.response.content = content
    result.response.metadata.final_action = final_action
    result.response.metadata.risk_score = 0.1
    result.response.metadata.risk_category = "CLEARLY_BENIGN"
    result.response.metadata.path = "FAST_PATH"
    result.response.metadata.domain_overlay = None
    result.response.metadata.reason_codes = []
    result.response.metadata.winning_rule = "low_risk"
    result.response.metadata.decision_reason = "Benign"
    result.response.metadata.processing_time_ms = 100
    result.response.metadata.deliberation_cycles = 0
    result.response.metadata.triggered_principles = []
    result.response.metadata.why_not_refuse = ""
    result.response.metadata.why_not_safe_complete = ""
    result.conversation_id = "conv-test"
    result.turn_index = 0
    result.conversation_governance_state_out = None
    orchestrator.process.return_value = result
    return orchestrator


def _make_governed_client(final_action: str = "NORMAL_COMPLETE") -> tuple[Any, GovernedClient]:
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="OpenAI response"))],
        model="gpt-4o",
    )
    orch = _make_orchestrator(final_action)
    config = GovernanceConfig()
    client = GovernedClient(mock_openai, orch, config)
    return mock_openai, client


# =============================================================================
# _extract_last_user_message
# =============================================================================


class TestExtractLastUserMessage:
    def test_single_user_message(self):
        msgs = [{"role": "user", "content": "Hello"}]
        assert _extract_last_user_message(msgs) == "Hello"

    def test_last_user_message_wins(self):
        msgs = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Reply"},
            {"role": "user", "content": "Second"},
        ]
        assert _extract_last_user_message(msgs) == "Second"

    def test_no_user_message_returns_empty(self):
        msgs = [{"role": "system", "content": "System"}, {"role": "assistant", "content": "Resp"}]
        assert _extract_last_user_message(msgs) == ""

    def test_empty_messages_returns_empty(self):
        assert _extract_last_user_message([]) == ""

    def test_multimodal_content(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "Hi"}, {"type": "image_url", "image_url": {}}]}]
        assert _extract_last_user_message(msgs) == "Hi"


# =============================================================================
# _messages_to_turns
# =============================================================================


class TestMessageToTurns:
    def test_user_and_assistant_converted(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        turns = _messages_to_turns(msgs)
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "Hello"
        assert turns[1].role == "assistant"
        assert turns[1].content == "Hi"

    def test_system_messages_excluded(self):
        msgs = [{"role": "system", "content": "You are helpful"}]
        turns = _messages_to_turns(msgs)
        assert len(turns) == 0

    def test_empty_messages(self):
        assert _messages_to_turns([]) == []


# =============================================================================
# _inject_safe_guidance
# =============================================================================


class TestInjectSafeGuidance:
    def _make_result_with_content(self, content: str) -> Any:
        result = MagicMock()
        result.response.content = content
        result.response.metadata.triggered_principles = []
        result.response.metadata.reason_codes = []
        result.response.metadata.decision_reason = ""
        return result

    def test_injects_into_existing_system_message(self):
        kwargs = {"messages": [{"role": "system", "content": "Existing"}, {"role": "user", "content": "Q"}]}
        result = self._make_result_with_content("Be careful.")
        modified = _inject_safe_guidance(kwargs, result)
        system_msg = next(m for m in modified["messages"] if m["role"] == "system")
        assert "Existing" in system_msg["content"]
        assert "Be careful." in system_msg["content"]

    def test_inserts_system_message_when_missing(self):
        kwargs = {"messages": [{"role": "user", "content": "Q"}]}
        result = self._make_result_with_content("Be careful.")
        modified = _inject_safe_guidance(kwargs, result)
        assert modified["messages"][0]["role"] == "system"
        assert "Be careful." in modified["messages"][0]["content"]

    def test_does_not_modify_original_kwargs(self):
        original_msgs = [{"role": "user", "content": "Q"}]
        kwargs = {"messages": original_msgs}
        result = self._make_result_with_content("Guidance")
        _inject_safe_guidance(kwargs, result)
        # Original must not be mutated
        assert len(original_msgs) == 1


# =============================================================================
# GovernedClient.__getattr__ passthrough
# =============================================================================


class TestGovernedClientPassthrough:
    def test_non_chat_attributes_delegate_to_original(self):
        mock_openai = MagicMock()
        mock_openai.models = MagicMock(name="models")
        orch = _make_orchestrator()
        client = GovernedClient(mock_openai, orch, GovernanceConfig())

        assert client.models is mock_openai.models

    def test_chat_attribute_is_governed(self):
        _, client = _make_governed_client()
        from moralstack.sdk.wrapper import GovernedChat

        assert isinstance(client.chat, GovernedChat)

    def test_completions_attribute_is_governed(self):
        _, client = _make_governed_client()

        assert isinstance(client.chat.completions, GovernedCompletions)


# =============================================================================
# GovernedCompletions.create() — routing
# =============================================================================


MESSAGES = [{"role": "user", "content": "Tell me about quantum physics"}]


class TestGovernedCompletionsRouting:
    def test_normal_complete_calls_openai(self):
        mock_openai, client = _make_governed_client("NORMAL_COMPLETE")
        resp = client.chat.completions.create(model="gpt-4o", messages=MESSAGES)

        mock_openai.chat.completions.create.assert_called_once()
        assert isinstance(resp, GovernedResponse)
        assert resp.governance_metadata.final_action == "NORMAL_COMPLETE"

    def test_refuse_does_not_call_openai(self):
        mock_openai, client = _make_governed_client("REFUSE")
        # Override content
        client._orchestrator.process.return_value.response.content = "I cannot help."
        resp = client.chat.completions.create(model="gpt-4o", messages=MESSAGES)

        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedResponse)
        assert resp.governance_metadata.final_action == "REFUSE"
        assert "I cannot help." in resp.content

    def test_safe_complete_calls_openai_with_modified_kwargs(self):
        mock_openai, client = _make_governed_client("SAFE_COMPLETE")
        client._orchestrator.process.return_value.response.content = "Use caution."
        client.chat.completions.create(model="gpt-4o", messages=MESSAGES)

        mock_openai.chat.completions.create.assert_called_once()
        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        # Must include system message with guidance
        msgs = call_kwargs.get("messages", [])
        system_msgs = [m for m in msgs if m.get("role") == "system"]
        assert len(system_msgs) >= 1

    def test_session_turn_index_increments(self):
        _, client = _make_governed_client("NORMAL_COMPLETE")
        client.chat.completions.create(model="gpt-4o", messages=MESSAGES)
        client.chat.completions.create(model="gpt-4o", messages=MESSAGES)
        # After 2 calls, counter must be 2
        assert client._session._turn_counter == 2

    def test_pipeline_failure_refuse_policy(self):
        mock_openai = MagicMock()
        orch = _make_orchestrator()
        orch.process.side_effect = RuntimeError("Pipeline down")
        config = GovernanceConfig(failure_policy="refuse")
        client = GovernedClient(mock_openai, orch, config)

        resp = client.chat.completions.create(model="gpt-4o", messages=MESSAGES)
        mock_openai.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedResponse)
        assert resp.governance_metadata.final_action == "REFUSE"

    def test_pipeline_failure_passthrough_policy(self):
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Fallback"))]
        )
        orch = _make_orchestrator()
        orch.process.side_effect = RuntimeError("Pipeline down")
        config = GovernanceConfig(failure_policy="passthrough")
        client = GovernedClient(mock_openai, orch, config)

        resp = client.chat.completions.create(model="gpt-4o", messages=MESSAGES)
        mock_openai.chat.completions.create.assert_called_once()
        assert isinstance(resp, GovernedResponse)
        assert resp.is_passthrough is True


# =============================================================================
# govern() factory
# =============================================================================


class TestGovernFactory:
    def test_govern_returns_governed_client(self):
        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()

        with patch("moralstack.sdk.wrapper._bootstrap_pipeline") as mock_bootstrap:
            mock_bootstrap.return_value = _make_orchestrator()
            client = govern(mock_openai, config=GovernanceConfig(api_key="sk-test"))

        assert isinstance(client, GovernedClient)

    def test_govern_uses_default_config_when_none(self):
        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()

        with patch("moralstack.sdk.wrapper._bootstrap_pipeline") as mock_bootstrap:
            mock_bootstrap.return_value = _make_orchestrator()
            client = govern(mock_openai)

        assert client._config is not None
        assert isinstance(client._config, GovernanceConfig)

    def test_govern_passes_config_to_bootstrap(self):
        mock_openai = MagicMock()
        mock_openai.chat = MagicMock()
        config = GovernanceConfig(api_key="sk-test", domain_overlay="legal")

        with patch("moralstack.sdk.wrapper._bootstrap_pipeline") as mock_bootstrap:
            mock_bootstrap.return_value = _make_orchestrator()
            govern(mock_openai, config=config)

        mock_bootstrap.assert_called_once_with(config)


# =============================================================================
# GovernedClient — auto run_id initialisation
# =============================================================================


class TestGovernedClientRunId:
    def test_run_id_set_in_context_after_init(self):
        """GovernedClient.__init__ must register a non-empty run_id in the observability context."""
        from moralstack.observability.context import get_current_run_id

        mock_openai = MagicMock()
        orch = _make_orchestrator()
        GovernedClient(mock_openai, orch, GovernanceConfig())

        run_id = get_current_run_id()
        assert run_id is not None
        assert len(run_id) > 0

    def test_run_id_stored_on_instance(self):
        """_run_id attribute on GovernedClient must be a valid UUID string."""
        import uuid

        mock_openai = MagicMock()
        orch = _make_orchestrator()
        client = GovernedClient(mock_openai, orch, GovernanceConfig())

        # Must not raise — UUID4 format
        parsed = uuid.UUID(client._run_id)
        assert parsed.version == 4

    def test_different_clients_get_different_run_ids(self):
        """Each GovernedClient instance must receive a unique run_id."""
        mock_openai = MagicMock()
        orch = _make_orchestrator()
        c1 = GovernedClient(mock_openai, orch, GovernanceConfig())
        c2 = GovernedClient(mock_openai, orch, GovernanceConfig())

        assert c1._run_id != c2._run_id

    def test_db_init_skipped_in_file_only_mode(self):
        """In file_only mode no DB calls must be made."""
        from moralstack.sdk.wrapper import GovernedClient

        mock_openai = MagicMock()
        orch = _make_orchestrator()

        with (
            patch("moralstack.observability.config.get_observability_mode", return_value="file_only"),
            patch("moralstack.observability.sinks.sqlite_sink.init_db") as mock_init,
            patch("moralstack.observability.sinks.sqlite_sink.create_run") as mock_create,
        ):
            GovernedClient(mock_openai, orch, GovernanceConfig())

        mock_init.assert_not_called()
        mock_create.assert_not_called()

    def test_db_init_called_in_db_only_mode(self):
        """In db_only mode, init_db and create_run must be called when DB path is configured."""
        from moralstack.sdk.wrapper import GovernedClient

        mock_openai = MagicMock()
        orch = _make_orchestrator()

        with (
            patch(
                "moralstack.sdk.wrapper.GovernedClient._init_run_context",
                wraps=GovernedClient._init_run_context,
            ),
            patch("moralstack.observability.config.get_observability_mode", return_value="db_only"),
            patch("moralstack.observability.config.get_db_path", return_value="/tmp/test.db"),
            patch("moralstack.observability.sinks.sqlite_sink.init_db", return_value=True) as mock_init,
            patch("moralstack.observability.sinks.sqlite_sink.create_run", return_value=True) as mock_create,
        ):
            GovernedClient(mock_openai, orch, GovernanceConfig())

        mock_init.assert_called_once_with("/tmp/test.db")
        mock_create.assert_called_once()


# =============================================================================
# GovernedCompletions.create() — safe flush
# =============================================================================


class TestGovernedCompletionsFlush:
    def test_flush_called_after_normal_complete(self):
        """obs.flush() must be called after every successful create() regardless of final_action."""
        mock_openai, client = _make_governed_client("NORMAL_COMPLETE")

        with patch("moralstack.observability.service.get_obs") as mock_get_obs:
            mock_obs = MagicMock()
            mock_get_obs.return_value = mock_obs
            client.chat.completions.create(model="gpt-4o", messages=MESSAGES)

        mock_obs.flush.assert_called_once()

    def test_flush_called_after_refuse(self):
        """obs.flush() must be called even when the pipeline returns REFUSE."""
        mock_openai, client = _make_governed_client("REFUSE")
        client._orchestrator.process.return_value.response.content = "No."

        with patch("moralstack.observability.service.get_obs") as mock_get_obs:
            mock_obs = MagicMock()
            mock_get_obs.return_value = mock_obs
            client.chat.completions.create(model="gpt-4o", messages=MESSAGES)

        mock_obs.flush.assert_called_once()

    def test_flush_called_after_pipeline_failure(self):
        """obs.flush() must be called even when the pipeline raises an exception."""
        mock_openai = MagicMock()
        orch = _make_orchestrator()
        orch.process.side_effect = RuntimeError("Pipeline down")
        client = GovernedClient(mock_openai, orch, GovernanceConfig(failure_policy="refuse"))

        with patch("moralstack.observability.service.get_obs") as mock_get_obs:
            mock_obs = MagicMock()
            mock_get_obs.return_value = mock_obs
            client.chat.completions.create(model="gpt-4o", messages=MESSAGES)

        mock_obs.flush.assert_called_once()

    def test_flush_failure_does_not_propagate(self):
        """A flush() error must never surface to the caller."""
        mock_openai, client = _make_governed_client("NORMAL_COMPLETE")

        with patch("moralstack.observability.service.get_obs") as mock_get_obs:
            mock_obs = MagicMock()
            mock_obs.flush.side_effect = RuntimeError("Queue exploded")
            mock_get_obs.return_value = mock_obs
            # Must not raise
            resp = client.chat.completions.create(model="gpt-4o", messages=MESSAGES)

        assert resp is not None


# =============================================================================
# _extract_developer_contract
# =============================================================================


class TestExtractDeveloperContract:
    """Tests for the _extract_developer_contract helper (Step 2)."""

    def test_no_messages_returns_none(self):
        assert _extract_developer_contract([]) is None

    def test_no_system_message_returns_none(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        assert _extract_developer_contract(msgs) is None

    def test_single_system_message_returns_contract(self):
        msgs = [
            {"role": "system", "content": "You are a careful assistant"},
            {"role": "user", "content": "Hello"},
        ]
        c = _extract_developer_contract(msgs)
        assert isinstance(c, DeveloperContract)
        assert c.raw_text == "You are a careful assistant"
        assert c.mode == "opaque"
        # Hash is deterministic and 16 chars; exact value is asserted in
        # tests/test_developer_contract.py::test_hash_stability_across_versions.
        assert len(c.contract_hash) == 16

    def test_empty_system_content_returns_none(self):
        msgs = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "Hello"},
        ]
        assert _extract_developer_contract(msgs) is None

    def test_whitespace_only_system_content_returns_none(self):
        msgs = [
            {"role": "system", "content": "   \n\t  "},
            {"role": "user", "content": "Hello"},
        ]
        assert _extract_developer_contract(msgs) is None

    def test_multiple_system_messages_last_wins(self):
        msgs = [
            {"role": "system", "content": "First system"},
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "Second system"},
            {"role": "assistant", "content": "Hi"},
            {"role": "system", "content": "Third system"},
        ]
        c = _extract_developer_contract(msgs)
        assert c is not None
        assert c.raw_text == "Third system"

    def test_multimodal_system_content_text_parts_only(self):
        msgs = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "You are X"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    {"type": "text", "text": "and Y"},
                ],
            },
            {"role": "user", "content": "Hello"},
        ]
        c = _extract_developer_contract(msgs)
        assert c is not None
        assert c.raw_text == "You are X and Y"

    def test_multimodal_system_content_no_text_returns_none(self):
        msgs = [
            {
                "role": "system",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            },
            {"role": "user", "content": "Hello"},
        ]
        assert _extract_developer_contract(msgs) is None

    def test_string_content_not_list(self):
        """Sanity check: non-multimodal content (a plain string) is handled."""
        msgs = [{"role": "system", "content": "plain string"}, {"role": "user", "content": "Q"}]
        c = _extract_developer_contract(msgs)
        assert c is not None
        assert c.raw_text == "plain string"

    def test_system_among_other_roles_extracted(self):
        msgs = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier reply"},
            {"role": "system", "content": "You are X"},
            {"role": "user", "content": "now"},
        ]
        c = _extract_developer_contract(msgs)
        assert c is not None
        assert c.raw_text == "You are X"

    def test_returned_contract_is_immutable(self):
        """Sanity check: the returned object preserves DeveloperContract frozen semantics."""
        import dataclasses

        msgs = [{"role": "system", "content": "X"}, {"role": "user", "content": "Q"}]
        c = _extract_developer_contract(msgs)
        assert c is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.raw_text = "Y"  # type: ignore[misc]


# =============================================================================
# Integration: _create_inner populates developer_contract
# =============================================================================


class TestCreateInnerPropagatesContract:
    """
    Verifies that GovernedCompletions._create_inner builds a ProcessedRequest
    whose developer_contract reflects the role='system' message in `messages`.

    These tests reuse the existing `_make_governed_client` helper at the top of
    this file. They invoke the wrapper through the public surface
    (`client.chat.completions.create(...)`) and inspect the captured
    `ProcessedRequest` via the MagicMock `call_args` of `orchestrator.process`.

    The orchestrator is configured to return a `REFUSE` result so that
    `_create_inner` exits early without calling the OpenAI client.
    """

    def test_contract_is_none_when_no_system_message(self):
        _, client = _make_governed_client("REFUSE")
        client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "Hello"}])
        # The captured ProcessedRequest is the first positional argument of orchestrator.process.
        call_args = client._orchestrator.process.call_args
        request = call_args.args[0]
        assert request.developer_contract is None

    def test_contract_is_populated_when_system_present(self):
        _, client = _make_governed_client("REFUSE")
        client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a careful assistant"},
                {"role": "user", "content": "Hello"},
            ],
        )
        call_args = client._orchestrator.process.call_args
        request = call_args.args[0]
        assert request.developer_contract is not None
        assert request.developer_contract.raw_text == "You are a careful assistant"
        assert request.developer_contract.mode == "opaque"

    def test_contract_is_none_when_system_is_empty(self):
        _, client = _make_governed_client("REFUSE")
        client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "   "},
                {"role": "user", "content": "Hello"},
            ],
        )
        call_args = client._orchestrator.process.call_args
        request = call_args.args[0]
        assert request.developer_contract is None
