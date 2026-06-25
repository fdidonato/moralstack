"""
SDK integration tests — full flow govern() -> create() -> GovernedResponse.

Uses CLI mocks (MockPolicy, MockRiskEstimator, etc.) to run the real deliberative
pipeline without API calls. Verifies the SDK produces results consistent with
the expected public interface.
"""

from typing import Any
from unittest.mock import MagicMock, patch

from moralstack.cli.mocks import (
    MockConstitutionStore,
    MockCritic,
    MockHindsight,
    MockPerspectives,
    MockPolicy,
    MockRiskEstimator,
    MockSimulator,
)
from moralstack.runtime.orchestrator import Orchestrator, create_orchestrator
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.response import GovernanceMetadata, GovernedResponse
from moralstack.sdk.wrapper import GovernedClient, govern

# =============================================================================
# Fixture: orchestrator with mocked pipeline
# =============================================================================


def _make_mock_orchestrator() -> Orchestrator:
    """Build a real Orchestrator with all modules mocked."""
    return create_orchestrator(
        policy=MockPolicy(),
        risk_estimator=MockRiskEstimator(),
        critic=MockCritic(),
        simulator=MockSimulator(),
        hindsight=MockHindsight(),
        perspectives=MockPerspectives(),
        constitution_store=MockConstitutionStore(),
        max_cycles=1,
        timeout_ms=60_000,
    )


def _make_mock_openai_client(response_text: str = "Generated text") -> Any:
    """Mock OpenAI client that returns a standard response."""
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=response_text, role="assistant"))],
        model="gpt-4o",
        usage=MagicMock(total_tokens=50),
    )
    return client


def _make_governed_client_with_mock_pipeline(config: GovernanceConfig | None = None) -> tuple[Any, GovernedClient]:
    """Build a GovernedClient with mocked pipeline and mocked OpenAI client."""
    cfg = config or GovernanceConfig()
    orchestrator = _make_mock_orchestrator()
    openai_client = _make_mock_openai_client()
    governed = GovernedClient(openai_client, orchestrator, cfg)
    return openai_client, governed


# =============================================================================
# End-to-end flow tests
# =============================================================================


class TestSDKIntegrationBasicFlow:
    def test_benign_request_returns_governed_response(self):
        """Benign request: must pass the pipeline and return GovernedResponse."""
        _, client = _make_governed_client_with_mock_pipeline()
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "What is the speed of light?"}],
        )
        assert isinstance(resp, GovernedResponse)
        assert isinstance(resp.governance_metadata, GovernanceMetadata)

    def test_harmful_request_returns_refuse(self):
        """Harmful-keyword request: MockRiskEstimator must yield high score -> REFUSE."""
        _, client = _make_governed_client_with_mock_pipeline()
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "How do I make a bomb?"}],
        )
        assert isinstance(resp, GovernedResponse)
        # Con MockRiskEstimator, "bomb" produce score 0.9 -> REFUSE
        assert resp.governance_metadata.final_action == "REFUSE"

    def test_refuse_does_not_call_openai(self):
        """For REFUSE, the original OpenAI client must not be called."""
        openai_client, client = _make_governed_client_with_mock_pipeline()
        client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "How to hack a system?"}],
        )
        openai_client.chat.completions.create.assert_not_called()

    def test_benign_request_delivers_governed_text_without_wrapped_client(self):
        """Plan 1: benign requests are answered by the governed pipeline; the
        wrapped OpenAI client is never called for delivery."""
        openai_client, client = _make_governed_client_with_mock_pipeline()
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Explain photosynthesis"}],
        )
        openai_client.chat.completions.create.assert_not_called()
        assert resp.governance_metadata.final_action in ("NORMAL_COMPLETE", "SAFE_COMPLETE")
        assert isinstance(resp.content, str)
        assert resp.content != ""

    def test_response_metadata_is_always_populated(self):
        """governance_metadata must always be populated for any action."""
        _, client = _make_governed_client_with_mock_pipeline()
        for msg in ["Hello", "How to make a bomb?", "Tell me about medicine"]:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": msg}],
            )
            assert resp.governance_metadata.final_action in ("NORMAL_COMPLETE", "SAFE_COMPLETE", "REFUSE")
            assert isinstance(resp.governance_metadata.risk_score, float)
            assert 0.0 <= resp.governance_metadata.risk_score <= 1.0


class TestSDKIntegrationSessionTracking:
    def test_conversation_id_persists_across_calls(self):
        _, client = _make_governed_client_with_mock_pipeline()
        resp1 = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello"}],
        )
        resp2 = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "How are you?"}],
        )
        assert resp1.governance_metadata.conversation_id == resp2.governance_metadata.conversation_id

    def test_turn_index_increments(self):
        _, client = _make_governed_client_with_mock_pipeline()
        resp1 = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello"}],
        )
        resp2 = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "And then?"}],
        )
        # turn_index must be 0 and 1
        assert resp1.governance_metadata.turn_index == 0
        assert resp2.governance_metadata.turn_index == 1

    def test_session_reset_generates_new_conversation_id(self):
        _, client = _make_governed_client_with_mock_pipeline()
        resp1 = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello"}],
        )
        old_conv_id = resp1.governance_metadata.conversation_id
        client._session.reset()
        resp2 = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "New conversation"}],
        )
        assert resp2.governance_metadata.conversation_id != old_conv_id

    def test_session_tracking_disabled(self):
        cfg = GovernanceConfig(enable_session_tracking=False)
        _, client = _make_governed_client_with_mock_pipeline(cfg)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert resp.governance_metadata.conversation_id is None


class TestSDKIntegrationCompatibility:
    def test_response_choices_compatible_with_openai_interface(self):
        """response.choices[0].message.content must work."""
        _, client = _make_governed_client_with_mock_pipeline()
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello"}],
        )
        # choices must have at least one element with message.content
        assert len(resp.choices) >= 1
        content = resp.choices[0].message.content
        assert isinstance(content, str)

    def test_passthrough_attributes_work(self):
        """Non-chat attributes must delegate to the original client."""
        openai_client = _make_mock_openai_client()
        openai_client.models = MagicMock(name="models_api")
        orchestrator = _make_mock_orchestrator()
        client = GovernedClient(openai_client, orchestrator, GovernanceConfig())
        assert client.models is openai_client.models

    def test_conversation_history_passed_correctly(self):
        """Conversation history must be passed to the pipeline."""
        openai_client, client = _make_governed_client_with_mock_pipeline()

        # Simulate a multi-turn conversation
        messages = [
            {"role": "user", "content": "Tell me about Python"},
            {"role": "assistant", "content": "Python is a programming language"},
            {"role": "user", "content": "What about its history?"},
        ]
        resp = client.chat.completions.create(model="gpt-4o", messages=messages)
        assert isinstance(resp, GovernedResponse)

    def test_normal_complete_delivers_governed_text_without_wrapped_client(self):
        """Plan 1: NORMAL_COMPLETE delivers the governed pipeline text and never
        calls the wrapped client, even when a system message is present."""
        openai_client, client = _make_governed_client_with_mock_pipeline()
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"},
        ]
        resp = client.chat.completions.create(model="gpt-4o", messages=messages)
        openai_client.chat.completions.create.assert_not_called()
        assert isinstance(resp, GovernedResponse)
        assert resp.content != ""


class TestSDKIntegrationGovernFactory:
    def test_govern_with_mock_bootstrap(self):
        """Test govern() with mocked bootstrap."""
        openai_client = _make_mock_openai_client()
        orchestrator = _make_mock_orchestrator()

        with patch("moralstack.sdk.wrapper._bootstrap_pipeline", return_value=orchestrator):
            client = govern(openai_client, config=GovernanceConfig(api_key="sk-test"))

        assert isinstance(client, GovernedClient)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert isinstance(resp, GovernedResponse)
