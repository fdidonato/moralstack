"""
Tests for SessionState wrapping an external SessionStore — Step 5 multi-conversation case.

The legacy single-conversation tests live in tests/test_sdk_session.py and remain
unchanged; this file covers the new multi-conversation usage pattern.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from moralstack.orchestration.conversation_state import ConversationGovernanceState
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.session import SessionState
from moralstack.sdk.session_store import InMemorySessionStore


def _make_result(state_out: Any = None) -> Any:
    result = MagicMock()
    result.conversation_governance_state_out = state_out
    return result


class TestSessionStateAcceptsExternalStore:
    def test_external_store_used_for_persistence(self):
        store = InMemorySessionStore()
        cfg = GovernanceConfig(enable_session_tracking=True)
        session = SessionState(cfg, store=store)
        conv_id = session.conversation_id
        assert conv_id is not None

        new_state = ConversationGovernanceState(conversation_id=conv_id, turn_index=1)
        session.update_from_result(_make_result(new_state))

        # The state is persisted in the external store, accessible directly.
        assert store.get(conv_id) is new_state

    def test_current_state_reads_from_store(self):
        store = InMemorySessionStore()
        cfg = GovernanceConfig(enable_session_tracking=True)
        session = SessionState(cfg, store=store)
        conv_id = session.conversation_id

        # Directly seed the store as if another SessionState wrote there.
        seeded = ConversationGovernanceState(conversation_id=conv_id, turn_index=3)
        store.put(conv_id, seeded)

        # SessionState reads from the store on every current_state access.
        assert session.current_state is seeded


class TestMultipleSessionStatesShareStore:
    """
    Demonstrates the multi-conversation use case: two SessionState views share
    one SessionStore, each scoped to its own conversation_id.
    """

    def test_two_sessions_two_conversations(self):
        store = InMemorySessionStore()
        cfg = GovernanceConfig(enable_session_tracking=True)
        s1 = SessionState(cfg, store=store)
        s2 = SessionState(cfg, store=store)

        # Different UUIDs.
        assert s1.conversation_id != s2.conversation_id

        # Each updates its own state.
        st1 = ConversationGovernanceState(conversation_id=s1.conversation_id, turn_index=1)
        st2 = ConversationGovernanceState(conversation_id=s2.conversation_id, turn_index=1)
        s1.update_from_result(_make_result(st1))
        s2.update_from_result(_make_result(st2))

        # Both entries coexist in the store.
        assert store.size() == 2
        assert set(store.list_active()) == {s1.conversation_id, s2.conversation_id}

        # Cross-reading is isolated.
        assert s1.current_state is st1
        assert s2.current_state is st2

    def test_reset_only_clears_own_conversation(self):
        store = InMemorySessionStore()
        cfg = GovernanceConfig(enable_session_tracking=True)
        s1 = SessionState(cfg, store=store)
        s2 = SessionState(cfg, store=store)

        s1.update_from_result(_make_result(ConversationGovernanceState(conversation_id=s1.conversation_id, turn_index=1)))
        s2.update_from_result(_make_result(ConversationGovernanceState(conversation_id=s2.conversation_id, turn_index=1)))

        old_id_1 = s1.conversation_id
        s1.reset()

        # s1's previous conversation is gone from the store.
        assert store.get(old_id_1) is None
        # s2's state is untouched.
        assert s2.current_state is not None
        assert s2.conversation_id in store.list_active()


class TestExternalStoreNotMutatedWhenTrackingDisabled:
    """When tracking is disabled, SessionState must not write anything to the external store."""

    def test_disabled_tracking_no_writes(self):
        store = InMemorySessionStore()
        cfg = GovernanceConfig(enable_session_tracking=False)
        session = SessionState(cfg, store=store)

        # conversation_id is None when disabled.
        assert session.conversation_id is None
        # current_state is None.
        assert session.current_state is None

        # update_from_result should be a no-op (no conversation_id to key on).
        state = ConversationGovernanceState(conversation_id="some-id", turn_index=1)
        session.update_from_result(_make_result(state))

        # The store is still empty.
        assert store.size() == 0

    def test_disabled_tracking_reset_no_writes(self):
        store = InMemorySessionStore()
        cfg = GovernanceConfig(enable_session_tracking=False)
        session = SessionState(cfg, store=store)
        session.reset()
        assert store.size() == 0
        assert session.conversation_id is None
