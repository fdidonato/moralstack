"""
Test suite for moralstack/sdk/session_store.py — InMemorySessionStore.
"""

from __future__ import annotations

import time

import pytest

from moralstack.orchestration.conversation_state import ConversationGovernanceState
from moralstack.sdk.session_store import (
    DEFAULT_MAX_SESSIONS,
    DEFAULT_SESSION_TTL_SECONDS,
    InMemorySessionStore,
    SessionStoreProtocol,
)


def _make_state(conv_id: str = "conv-x", turn: int = 0) -> ConversationGovernanceState:
    return ConversationGovernanceState(conversation_id=conv_id, turn_index=turn)


class TestInMemorySessionStoreInit:
    def test_default_capacity(self):
        store = InMemorySessionStore()
        assert store._ttl_seconds == DEFAULT_SESSION_TTL_SECONDS
        assert store._max_sessions == DEFAULT_MAX_SESSIONS

    def test_custom_capacity(self):
        store = InMemorySessionStore(ttl_seconds=60.0, max_sessions=5)
        assert store._ttl_seconds == 60.0
        assert store._max_sessions == 5

    def test_invalid_ttl_raises(self):
        with pytest.raises(ValueError, match="ttl_seconds"):
            InMemorySessionStore(ttl_seconds=0)

    def test_invalid_max_sessions_raises(self):
        with pytest.raises(ValueError, match="max_sessions"):
            InMemorySessionStore(max_sessions=0)


class TestPutAndGet:
    def test_put_then_get(self):
        store = InMemorySessionStore()
        state = _make_state("conv-1", turn=2)
        store.put("conv-1", state)
        retrieved = store.get("conv-1")
        assert retrieved is state  # Reference identity preserved.

    def test_get_unknown_returns_none(self):
        store = InMemorySessionStore()
        assert store.get("missing") is None

    def test_put_overwrites_existing(self):
        store = InMemorySessionStore()
        s1 = _make_state("conv-1", turn=0)
        s2 = _make_state("conv-1", turn=1)
        store.put("conv-1", s1)
        store.put("conv-1", s2)
        retrieved = store.get("conv-1")
        assert retrieved is s2

    def test_multiple_conversations_isolated(self):
        store = InMemorySessionStore()
        s_a = _make_state("conv-a", turn=5)
        s_b = _make_state("conv-b", turn=10)
        store.put("conv-a", s_a)
        store.put("conv-b", s_b)
        assert store.get("conv-a") is s_a
        assert store.get("conv-b") is s_b


class TestDelete:
    def test_delete_removes_entry(self):
        store = InMemorySessionStore()
        store.put("conv-1", _make_state())
        assert store.get("conv-1") is not None
        store.delete("conv-1")
        assert store.get("conv-1") is None

    def test_delete_unknown_is_noop(self):
        store = InMemorySessionStore()
        # Should not raise.
        store.delete("missing")
        assert store.get("missing") is None


class TestListActive:
    def test_empty_store(self):
        store = InMemorySessionStore()
        assert store.list_active() == []

    def test_lists_all_active(self):
        store = InMemorySessionStore()
        store.put("conv-a", _make_state("conv-a"))
        store.put("conv-b", _make_state("conv-b"))
        store.put("conv-c", _make_state("conv-c"))
        active = store.list_active()
        assert set(active) == {"conv-a", "conv-b", "conv-c"}

    def test_excludes_expired_and_evicts_them(self):
        store = InMemorySessionStore(ttl_seconds=0.1)
        store.put("old", _make_state("old"))
        time.sleep(0.15)
        store.put("new", _make_state("new"))
        active = store.list_active()
        assert active == ["new"]
        # Expired entry is also dropped from internal storage.
        assert store.size() == 1


class TestTTL:
    def test_entry_expires(self):
        store = InMemorySessionStore(ttl_seconds=0.1)
        store.put("conv-1", _make_state())
        assert store.get("conv-1") is not None
        time.sleep(0.15)
        assert store.get("conv-1") is None

    def test_expired_entry_dropped_on_get(self):
        store = InMemorySessionStore(ttl_seconds=0.1)
        store.put("conv-1", _make_state())
        time.sleep(0.15)
        store.get("conv-1")
        # Internal storage drops the expired entry.
        assert store.size() == 0


class TestCapacityCap:
    def test_fifo_eviction_on_overflow(self):
        store = InMemorySessionStore(max_sessions=2)
        store.put("first", _make_state("first"))
        store.put("second", _make_state("second"))
        store.put("third", _make_state("third"))
        # 'first' is the oldest insertion → evicted.
        assert store.size() == 2
        assert store.get("first") is None
        assert store.get("second") is not None
        assert store.get("third") is not None

    def test_overwrite_moves_to_recent(self):
        """Overwriting an existing key counts as a fresh insertion (moves to recent)."""
        store = InMemorySessionStore(max_sessions=2)
        store.put("first", _make_state("first"))
        store.put("second", _make_state("second"))
        # Overwrite 'first' — it should move to the most-recent position.
        store.put("first", _make_state("first"))
        # Now insert 'third'. 'second' (the oldest after the overwrite) should be evicted.
        store.put("third", _make_state("third"))
        assert store.get("first") is not None
        assert store.get("second") is None
        assert store.get("third") is not None


class TestClear:
    def test_clear_empties_store(self):
        store = InMemorySessionStore()
        store.put("a", _make_state())
        store.put("b", _make_state())
        assert store.size() == 2
        store.clear()
        assert store.size() == 0
        assert store.list_active() == []


class TestProtocolConformance:
    def test_in_memory_store_satisfies_protocol(self):
        # Structural type check — accepted statically.
        store: SessionStoreProtocol = InMemorySessionStore()
        store.put("conv-1", _make_state())
        assert store.get("conv-1") is not None
        assert store.list_active() == ["conv-1"]
        store.delete("conv-1")
        assert store.list_active() == []
