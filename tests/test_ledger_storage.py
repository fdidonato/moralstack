"""
Test suite for moralstack/orchestration/ledger_storage.py.

Tests are pure unit tests with no network and no real embedder.
"""

from __future__ import annotations

import time

import pytest

from moralstack.orchestration.ledger import CachedDecision, LedgerEntry, LedgerKey
from moralstack.orchestration.ledger_storage import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
    InMemoryLedgerStorage,
)


def _make_entry(prompt: str = "hello", turn: int = 1) -> LedgerEntry:
    return LedgerEntry(
        cached_decision=CachedDecision(
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            governance_posture="NORMAL",
        ),
        embedding=[1.0, 0.0],
        original_prompt=prompt,
        intent_clarity="HIGH",
        request_type="factual",
        turn_index=turn,
    )


class TestInMemoryLedgerStorageInit:
    def test_default_capacity(self):
        s = InMemoryLedgerStorage()
        assert s._max_entries == DEFAULT_MAX_ENTRIES
        assert s._ttl_seconds == DEFAULT_TTL_SECONDS

    def test_custom_capacity(self):
        s = InMemoryLedgerStorage(max_entries=42, ttl_seconds=10.0)
        assert s._max_entries == 42
        assert s._ttl_seconds == 10.0

    def test_invalid_capacity_raises(self):
        with pytest.raises(ValueError, match="max_entries"):
            InMemoryLedgerStorage(max_entries=0)

    def test_invalid_ttl_raises(self):
        with pytest.raises(ValueError, match="ttl_seconds"):
            InMemoryLedgerStorage(ttl_seconds=0)


class TestInMemoryLedgerStoragePutGet:
    def test_put_then_get(self):
        s = InMemoryLedgerStorage()
        key = LedgerKey(contract_hash="abc", posture="NORMAL", domain=None)
        entry = _make_entry()
        s.put(key, entry)
        retrieved = s.get_entries(key)
        assert len(retrieved) == 1
        assert retrieved[0] is entry

    def test_get_unknown_key_returns_empty(self):
        s = InMemoryLedgerStorage()
        result = s.get_entries(LedgerKey(contract_hash="missing", posture="NORMAL", domain=None))
        assert result == []

    def test_multiple_entries_under_same_key(self):
        s = InMemoryLedgerStorage()
        key = LedgerKey(contract_hash="abc", posture="NORMAL", domain=None)
        e1 = _make_entry("first")
        e2 = _make_entry("second")
        s.put(key, e1)
        s.put(key, e2)
        retrieved = s.get_entries(key)
        assert len(retrieved) == 2

    def test_entries_isolated_by_key(self):
        s = InMemoryLedgerStorage()
        k1 = LedgerKey(contract_hash="a", posture="NORMAL", domain=None)
        k2 = LedgerKey(contract_hash="b", posture="NORMAL", domain=None)
        s.put(k1, _make_entry("p1"))
        s.put(k2, _make_entry("p2"))
        assert len(s.get_entries(k1)) == 1
        assert len(s.get_entries(k2)) == 1

    def test_size_counts_all_entries(self):
        s = InMemoryLedgerStorage()
        k = LedgerKey(contract_hash="a", posture="NORMAL", domain=None)
        for i in range(3):
            s.put(k, _make_entry(f"p{i}"))
        assert s.size() == 3

    def test_clear_empties_storage(self):
        s = InMemoryLedgerStorage()
        s.put(LedgerKey(contract_hash="a", posture="NORMAL", domain=None), _make_entry())
        assert s.size() == 1
        s.clear()
        assert s.size() == 0


class TestInMemoryLedgerStorageLRU:
    def test_eviction_when_over_capacity(self):
        s = InMemoryLedgerStorage(max_entries=2, ttl_seconds=3600)
        k = LedgerKey(contract_hash="a", posture="NORMAL", domain=None)
        s.put(k, _make_entry("oldest"))
        s.put(k, _make_entry("middle"))
        s.put(k, _make_entry("newest"))
        # The oldest one is evicted.
        assert s.size() == 2
        retrieved = s.get_entries(k)
        prompts = {e.original_prompt for e in retrieved}
        assert prompts == {"middle", "newest"}

    def test_eviction_across_keys(self):
        """LRU is global, not per-key."""
        s = InMemoryLedgerStorage(max_entries=2, ttl_seconds=3600)
        k1 = LedgerKey(contract_hash="a", posture="NORMAL", domain=None)
        k2 = LedgerKey(contract_hash="b", posture="NORMAL", domain=None)
        s.put(k1, _make_entry("first-under-k1"))
        s.put(k2, _make_entry("first-under-k2"))
        s.put(k1, _make_entry("second-under-k1"))  # Evicts first-under-k1.
        assert s.size() == 2
        assert s.get_entries(k1) == [e for e in s.get_entries(k1) if e.original_prompt == "second-under-k1"] + [
            e for e in s.get_entries(k1) if e.original_prompt != "second-under-k1"
        ]
        # Only one entry left under k1.
        assert len(s.get_entries(k1)) == 1
        assert s.get_entries(k1)[0].original_prompt == "second-under-k1"
        # k2 still has its entry.
        assert len(s.get_entries(k2)) == 1


class TestInMemoryLedgerStorageTTL:
    def test_entry_expires_after_ttl(self):
        s = InMemoryLedgerStorage(max_entries=100, ttl_seconds=0.1)
        k = LedgerKey(contract_hash="a", posture="NORMAL", domain=None)
        s.put(k, _make_entry())
        assert len(s.get_entries(k)) == 1
        time.sleep(0.2)
        # After TTL, the entry is filtered out and lazily dropped.
        assert s.get_entries(k) == []
        assert s.size() == 0

    def test_fresh_entry_not_dropped_when_others_expire(self):
        s = InMemoryLedgerStorage(max_entries=100, ttl_seconds=0.1)
        k = LedgerKey(contract_hash="a", posture="NORMAL", domain=None)
        s.put(k, _make_entry("old"))
        time.sleep(0.15)
        s.put(k, _make_entry("new"))
        retrieved = s.get_entries(k)
        assert len(retrieved) == 1
        assert retrieved[0].original_prompt == "new"


class TestLedgerKeyEquality:
    def test_equal_keys_match(self):
        a = LedgerKey(contract_hash="x", posture="NORMAL", domain="legal")
        b = LedgerKey(contract_hash="x", posture="NORMAL", domain="legal")
        assert a == b

    def test_different_contract_hash_not_equal(self):
        a = LedgerKey(contract_hash="x", posture="NORMAL", domain="legal")
        b = LedgerKey(contract_hash="y", posture="NORMAL", domain="legal")
        assert a != b

    def test_different_posture_not_equal(self):
        a = LedgerKey(contract_hash="x", posture="NORMAL", domain="legal")
        b = LedgerKey(contract_hash="x", posture="ELEVATED", domain="legal")
        assert a != b

    def test_different_domain_not_equal(self):
        a = LedgerKey(contract_hash="x", posture="NORMAL", domain="legal")
        b = LedgerKey(contract_hash="x", posture="NORMAL", domain="healthcare")
        assert a != b

    def test_none_domain_equals_none_domain(self):
        a = LedgerKey(contract_hash="x", posture="NORMAL", domain=None)
        b = LedgerKey(contract_hash="x", posture="NORMAL", domain=None)
        assert a == b
