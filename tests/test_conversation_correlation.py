"""Unit tests for lineage-based conversation correlation (server proxy)."""

from __future__ import annotations

from moralstack.server.conversation_correlation import (
    ConversationCorrelationStore,
    canonical_history_hash,
)


class TestCanonicalHistoryHash:
    def test_deterministic(self):
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
        assert canonical_history_hash(msgs) == canonical_history_hash(list(msgs))


class TestConversationCorrelationStore:
    def test_resolve_new_conversation_id_format(self):
        store = ConversationCorrelationStore()
        cid = store.resolve([{"role": "user", "content": "hi"}])
        assert cid.startswith("msconv-")

    def test_continuation_matches_parent_completed_hash(self):
        store = ConversationCorrelationStore()
        t0 = [
            {"role": "system", "content": "Scenario rules"},
            {"role": "user", "content": "First test message"},
        ]
        cid0 = store.resolve(t0)
        store.observe_completed_turn(
            messages=t0,
            assistant_content="Model reply",
            conversation_id=cid0,
        )
        t1 = t0 + [{"role": "assistant", "content": "Model reply"}, {"role": "user", "content": "Second"}]
        cid1 = store.resolve(t1)
        assert cid1 == cid0

    def test_exact_request_replay_returns_same_id(self):
        store = ConversationCorrelationStore()
        msgs = [{"role": "user", "content": "x"}]
        assert store.resolve(msgs) == store.resolve(list(msgs))
