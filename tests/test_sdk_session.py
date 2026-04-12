"""Tests for moralstack.sdk.session — SessionState."""

from typing import Any
from unittest.mock import MagicMock

from moralstack.orchestration.conversation_state import ConversationGovernanceState
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.session import SessionState


def _make_result(state_out: Any = None) -> Any:
    result = MagicMock()
    result.conversation_governance_state_out = state_out
    return result


class TestSessionStateInit:
    def test_session_tracking_enabled_generates_uuid(self):
        cfg = GovernanceConfig(enable_session_tracking=True)
        session = SessionState(cfg)
        assert session.conversation_id is not None
        assert len(session.conversation_id) == 36  # UUID4 format

    def test_session_tracking_disabled_returns_none(self):
        cfg = GovernanceConfig(enable_session_tracking=False)
        session = SessionState(cfg)
        assert session.conversation_id is None

    def test_initial_state_is_none(self):
        cfg = GovernanceConfig()
        session = SessionState(cfg)
        assert session.current_state is None


class TestSessionTurnCounter:
    def test_first_turn_is_zero(self):
        session = SessionState(GovernanceConfig())
        assert session.next_turn_index() == 0

    def test_increments_each_call(self):
        session = SessionState(GovernanceConfig())
        assert session.next_turn_index() == 0
        assert session.next_turn_index() == 1
        assert session.next_turn_index() == 2

    def test_counter_is_independent_per_instance(self):
        s1 = SessionState(GovernanceConfig())
        s2 = SessionState(GovernanceConfig())
        s1.next_turn_index()
        assert s2.next_turn_index() == 0


class TestSessionUpdateFromResult:
    def test_updates_governance_state_when_present(self):
        session = SessionState(GovernanceConfig())
        expected_state = ConversationGovernanceState(conversation_id="abc", turn_index=0)
        result = _make_result(state_out=expected_state)
        session.update_from_result(result)
        assert session.current_state is expected_state

    def test_ignores_none_state_out(self):
        session = SessionState(GovernanceConfig())
        result = _make_result(state_out=None)
        session.update_from_result(result)
        assert session.current_state is None

    def test_ignores_non_governance_state(self):
        session = SessionState(GovernanceConfig())
        result = _make_result(state_out="not a state")
        session.update_from_result(result)
        assert session.current_state is None

    def test_successive_updates(self):
        session = SessionState(GovernanceConfig())
        s1 = ConversationGovernanceState(turn_index=0)
        s2 = ConversationGovernanceState(turn_index=1)
        session.update_from_result(_make_result(s1))
        assert session.current_state is s1
        session.update_from_result(_make_result(s2))
        assert session.current_state is s2


class TestSessionReset:
    def test_reset_generates_new_conversation_id(self):
        cfg = GovernanceConfig(enable_session_tracking=True)
        session = SessionState(cfg)
        old_id = session.conversation_id
        session.reset()
        assert session.conversation_id != old_id
        assert session.conversation_id is not None

    def test_reset_clears_turn_counter(self):
        session = SessionState(GovernanceConfig())
        session.next_turn_index()
        session.next_turn_index()
        session.reset()
        assert session.next_turn_index() == 0

    def test_reset_clears_governance_state(self):
        session = SessionState(GovernanceConfig())
        state = ConversationGovernanceState(turn_index=5)
        session.update_from_result(_make_result(state))
        session.reset()
        assert session.current_state is None

    def test_reset_with_tracking_disabled_keeps_none(self):
        cfg = GovernanceConfig(enable_session_tracking=False)
        session = SessionState(cfg)
        session.reset()
        assert session.conversation_id is None
