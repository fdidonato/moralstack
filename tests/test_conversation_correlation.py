"""Unit tests for lineage-based conversation correlation (server proxy)."""

from __future__ import annotations

import hashlib
import json
import threading
from unittest.mock import Mock

import pytest

from moralstack.server.conversation_correlation import (
    _EMPTY_HISTORY_CANONICAL_HASH,
    ConversationCorrelationStore,
    canonical_history_hash,
    canonical_parent_history_hash,
)


def _reference_canonical_history_hash(messages: list[dict]) -> str:
    """
    Independent reimplementation of ``canonical_history_hash``'s algorithm
    (normalization + hashing reproduced, the module function is NOT called).

    Used as a golden-digest lock: the composite-key isolation design (P3 /
    P0-3 / A3) must not incidentally change the hash output, since the
    isolation lives entirely in the store's internal map key, never in the
    digest.
    """
    max_len = 4096

    def truncate(s: str) -> str:
        return s if len(s) <= max_len else s[:max_len]

    def normalize_content(content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return truncate(content)
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    parts.append(json.dumps(block, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
                    continue
                btype = str(block.get("type") or "")
                if btype == "text":
                    parts.append(truncate(str(block.get("text") or "")))
                else:
                    parts.append(json.dumps(block, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            return truncate("".join(parts))
        return truncate(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")))

    normalized = [{"role": str(m.get("role", "") or ""), "content": normalize_content(m.get("content"))} for m in messages]
    blob = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class TestCanonicalHistoryHash:
    def test_deterministic(self):
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
        assert canonical_history_hash(msgs) == canonical_history_hash(list(msgs))

    def test_hash_functions_unchanged_golden_digest(self):
        """
        Golden-digest lock (P3 design point, "do not deviate" in the handoff):
        canonical_history_hash / canonical_parent_history_hash stay byte-for-byte
        unchanged. Composite (principal, hash) keying lives entirely inside the
        store's internal map key, never in the digest itself.
        """
        cases = [
            ([], "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
            (
                [{"role": "user", "content": "hi"}],
                "4e79873118cd9be7a1f0308b9cd772950c5410c74ca3fe1ba2626cba009a9237",
            ),
            (
                [
                    {"role": "system", "content": "You are helpful. 你好"},
                    {"role": "user", "content": "First Q café"},
                    {"role": "assistant", "content": "Reply 🌍"},
                    {"role": "user", "content": "Second Q"},
                ],
                "4666af65b1114ebc48fa2e9f3e8fa41494d995ad413b38f764159bf5d9ed5714",
            ),
        ]
        for messages, golden_digest in cases:
            actual = canonical_history_hash(messages)
            assert actual == golden_digest, f"digest moved for {messages!r}: {actual!r} != {golden_digest!r}"
            assert actual == _reference_canonical_history_hash(messages)

        # _MAX_CONTENT_PER_MESSAGE (4096) truncation boundary: content at exactly
        # the boundary and content beyond it must hash identically (both truncate
        # to the same 4096-char prefix).
        at_boundary = [{"role": "user", "content": "a" * 4096}]
        beyond_boundary = [{"role": "user", "content": "a" * 5000}]
        assert canonical_history_hash(at_boundary) == canonical_history_hash(beyond_boundary)
        assert canonical_history_hash(beyond_boundary) == _reference_canonical_history_hash(beyond_boundary)

    def test_empty_list_equals_root_constant(self):
        """The parent-lookup root path (canonical_parent_history_hash for a
        single opening user turn) depends on this identity holding."""
        assert canonical_history_hash([]) == _EMPTY_HISTORY_CANONICAL_HASH

    def test_canonical_parent_history_hash_golden_digest(self):
        """
        Golden-digest lock for ``canonical_parent_history_hash`` directly
        (FIX 4, diff review 2026-07-09): the prior golden test only pinned
        ``canonical_history_hash``; this pins the parent-hash function itself
        so composite-key isolation cannot incidentally change either digest.
        """
        # Empty-history-root case: a single opening user turn has no prior
        # history, so the parent hash is the frozen empty-list root constant.
        assert canonical_parent_history_hash([{"role": "user", "content": "hi"}]) == _EMPTY_HISTORY_CANONICAL_HASH

        # Fixed multi-turn input (same case pinned above for
        # canonical_history_hash): the parent hash of the trailing user turn
        # must equal canonical_history_hash(messages[:-1]) and a hard-coded
        # pre-change hex digest.
        multi_turn = [
            {"role": "system", "content": "You are helpful. 你好"},
            {"role": "user", "content": "First Q café"},
            {"role": "assistant", "content": "Reply 🌍"},
            {"role": "user", "content": "Second Q"},
        ]
        golden_parent_digest = "86d67e09fd83cf50b53927afacfff5af76e124ebea65be1f3820a31524f0beae"
        actual_parent = canonical_parent_history_hash(multi_turn)
        assert actual_parent == golden_parent_digest
        assert actual_parent == canonical_history_hash(multi_turn[:-1])
        assert actual_parent == _reference_canonical_history_hash(multi_turn[:-1])


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

    def test_resolve_default_principal_matches_pre_change_behavior(self):
        """resolve(msgs) (no principal kwarg) must be identical to
        resolve(msgs, principal="") — the (\"\", hash) keyspace matches the
        pre-change, no-principal path exactly."""
        store = ConversationCorrelationStore()
        msgs = [{"role": "user", "content": "same content"}]
        cid_default = store.resolve(msgs)
        cid_explicit_empty = store.resolve(list(msgs), principal="")
        assert cid_default == cid_explicit_empty


class TestPrincipalIsolation:
    def test_resolve_isolates_principals(self):
        """Core bug fix: identical histories, different principals -> different
        conversation_ids; the same principal reused -> the same id."""
        store = ConversationCorrelationStore()
        msgs = [{"role": "user", "content": "identical history"}]

        cid_a1 = store.resolve(msgs, principal="A")
        cid_b = store.resolve(msgs, principal="B")
        cid_a2 = store.resolve(msgs, principal="A")

        assert cid_a1 != cid_b
        assert cid_a1 == cid_a2

    def test_observe_completed_turn_lineage_within_principal(self):
        store = ConversationCorrelationStore()
        t0 = [
            {"role": "system", "content": "Scenario rules"},
            {"role": "user", "content": "First test message"},
        ]
        cid0 = store.resolve(t0, principal="A")
        store.observe_completed_turn(
            messages=t0,
            assistant_content="Model reply",
            conversation_id=cid0,
            principal="A",
        )
        t1 = t0 + [{"role": "assistant", "content": "Model reply"}, {"role": "user", "content": "Second"}]
        cid1 = store.resolve(t1, principal="A")
        assert cid1 == cid0

    def test_observe_completed_turn_does_not_leak_lineage_across_principal(self):
        """
        Primary P3/P0-3 regression lock: byte-identical follow-up history resolved
        under a DIFFERENT principal must NOT chain into principal A's lineage —
        it must mint a fresh conversation_id.
        """
        store = ConversationCorrelationStore()
        t0 = [
            {"role": "system", "content": "Scenario rules"},
            {"role": "user", "content": "First test message"},
        ]
        cid0 = store.resolve(t0, principal="A")
        store.observe_completed_turn(
            messages=t0,
            assistant_content="Model reply",
            conversation_id=cid0,
            principal="A",
        )
        t1 = t0 + [{"role": "assistant", "content": "Model reply"}, {"role": "user", "content": "Second"}]
        cid1_other_principal = store.resolve(t1, principal="B")
        assert cid1_other_principal != cid0

    def test_odd_character_principal_isolation_stable(self):
        """Principals containing odd characters (no serialization to spoof with
        tuple keying) still isolate and remain stable."""
        store = ConversationCorrelationStore()
        msgs = [{"role": "user", "content": "same"}]
        cid_brace = store.resolve(msgs, principal="}")
        cid_colon = store.resolve(msgs, principal=":")
        cid_brace_again = store.resolve(list(msgs), principal="}")
        assert cid_brace != cid_colon
        assert cid_brace == cid_brace_again


class TestBoundedStoreTTL:
    def test_ttl_expiry_mints_new_id(self):
        clock = {"t": 1000.0}
        store = ConversationCorrelationStore(ttl_seconds=10.0, time_fn=lambda: clock["t"])
        msgs = [{"role": "user", "content": "hi"}]

        cid0 = store.resolve(msgs)

        # Boundary: age == ttl_seconds is NOT expired (strict '>').
        clock["t"] = 1010.0
        assert store.resolve(list(msgs)) == cid0

        # Past the TTL: stale entry evicted-on-read, a new id is minted.
        clock["t"] = 1010.001
        cid1 = store.resolve(list(msgs))
        assert cid1 != cid0

    def test_ttl_hit_path_does_not_refresh_inserted_at(self):
        clock = {"t": 0.0}
        store = ConversationCorrelationStore(ttl_seconds=5.0, time_fn=lambda: clock["t"])
        msgs = [{"role": "user", "content": "hi"}]

        cid0 = store.resolve(msgs)
        clock["t"] = 4.0
        # A read at t=4 must NOT push expiry out to t=9; expiry stays anchored
        # to the original insertion at t=0.
        assert store.resolve(list(msgs)) == cid0
        clock["t"] = 5.001
        assert store.resolve(list(msgs)) != cid0

    def test_ttl_lineage_survives_when_only_inter_turn_delay_elapses(self):
        """3-turn conversation where turn-1 entries have individually expired by
        the time of turn 3, but chaining still works because each turn writes
        fresh request/completed entries. Locks: TTL only needs to exceed the
        inter-turn delay, not the whole conversation duration."""
        clock = {"t": 0.0}
        ttl = 100.0
        store = ConversationCorrelationStore(ttl_seconds=ttl, time_fn=lambda: clock["t"])

        t0 = [{"role": "user", "content": "Q1"}]
        cid0 = store.resolve(t0)
        store.observe_completed_turn(messages=t0, assistant_content="A1", conversation_id=cid0)

        clock["t"] = 60.0  # inter-turn delay < ttl
        t1 = t0 + [{"role": "assistant", "content": "A1"}, {"role": "user", "content": "Q2"}]
        cid1 = store.resolve(t1)
        store.observe_completed_turn(messages=t1, assistant_content="A2", conversation_id=cid1)
        assert cid1 == cid0

        clock["t"] = 120.0  # turn-0 entries (inserted at t=0) are now expired,
        # but turn-1 entries (inserted at t=60) are still within ttl.
        t2 = t1 + [{"role": "assistant", "content": "A2"}, {"role": "user", "content": "Q3"}]
        cid2 = store.resolve(t2)
        assert cid2 == cid0


class TestMaxsizeEviction:
    def test_maxsize_eviction_oldest_first(self):
        store = ConversationCorrelationStore(max_entries=2)
        cid_a = store.resolve([{"role": "user", "content": "history A"}])
        store.resolve([{"role": "user", "content": "history B"}])
        store.resolve([{"role": "user", "content": "history C"}])

        assert store.size() <= 2
        # 'history A' was the oldest insertion -> evicted -> re-resolving mints
        # a new id (rather than returning the original cid_a).
        cid_a_again = store.resolve([{"role": "user", "content": "history A"}])
        assert cid_a_again != cid_a

    def test_maxsize_one_observe_after_eviction_no_crash(self):
        store = ConversationCorrelationStore(max_entries=1)
        t0 = [{"role": "user", "content": "Q1"}]
        cid0 = store.resolve(t0)
        # Every new history evicts the prior single entry.
        store.resolve([{"role": "user", "content": "Q-other"}])
        # observe_completed_turn's target hash was already evicted; must not
        # raise (KeyError or otherwise) — it simply (re)inserts.
        store.observe_completed_turn(messages=t0, assistant_content="A1", conversation_id=cid0)
        assert store.size() <= 1


class TestConcurrency:
    def test_concurrent_resolve_thread_safety(self):
        store = ConversationCorrelationStore(max_entries=200)
        errors: list[BaseException] = []

        def worker(worker_id: int) -> None:
            try:
                for i in range(50):
                    msgs = [{"role": "user", "content": f"w{worker_id}-{i}"}]
                    store.resolve(msgs)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert store.size() <= 200

    def test_concurrent_mixed_principals_identical_history(self):
        """Threads racing with different principals on an identical history:
        no exceptions, and ids partition cleanly by principal."""
        store = ConversationCorrelationStore(max_entries=1000)
        msgs = [{"role": "user", "content": "shared identical history"}]
        errors: list[BaseException] = []
        results: dict[str, set[str]] = {"A": set(), "B": set(), "C": set()}
        lock = threading.Lock()

        def worker(principal: str) -> None:
            try:
                for _ in range(30):
                    cid = store.resolve(list(msgs), principal=principal)
                    with lock:
                        results[principal].add(cid)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(p,)) for p in ("A", "B", "C") for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Each principal converges to exactly one id (races resolve to a single
        # winner id per principal since resolve() is lock-protected).
        assert all(len(ids) == 1 for ids in results.values())
        assert results["A"] != results["B"] != results["C"]
        assert results["A"].isdisjoint(results["B"])
        assert results["A"].isdisjoint(results["C"])
        assert results["B"].isdisjoint(results["C"])


class TestResolveBestEffort:
    """
    Blocking regression lock (diff review 2026-07-09): ``resolve()`` must never
    raise from the TTL/eviction helper paths (``_get_id``/``_put_id``), and must
    always return a valid ``msconv-*`` id even when a helper fails. This runs on
    the request path, ahead of the proxy's fail-closed ``try`` block, so an
    unguarded raise here would break the request (PROJECT_SPEC §5 invariant #6).
    """

    def test_resolve_survives_lookup_failure_and_mints_fresh_id(self):
        """The hit-lookup branch (`_get_id`) raises -> `resolve()` swallows it
        and still returns a valid minted id, never propagating the exception."""
        store = ConversationCorrelationStore()
        store._get_id = lambda key: (_ for _ in ()).throw(RuntimeError("boom"))
        cid = store.resolve([{"role": "user", "content": "hi"}])
        assert isinstance(cid, str)
        assert cid.startswith("msconv-")

    def test_resolve_survives_insert_failure_and_still_returns_id(self):
        """The insert branch (`_put_id`) raises on every call -> `resolve()`
        swallows it and still returns the freshly minted id."""
        store = ConversationCorrelationStore()
        store._put_id = lambda key, conversation_id: (_ for _ in ()).throw(RuntimeError("boom"))
        cid = store.resolve([{"role": "user", "content": "hi"}])
        assert isinstance(cid, str)
        assert cid.startswith("msconv-")

    def test_resolve_survives_time_fn_failure_during_eviction(self):
        """A failing injected clock (used internally by `_get_id`/`_put_id` for
        TTL/eviction bookkeeping) must not escape `resolve()`."""
        store = ConversationCorrelationStore(time_fn=Mock(side_effect=RuntimeError("clock failed")))
        cid = store.resolve([{"role": "user", "content": "hi"}])
        assert isinstance(cid, str)
        assert cid.startswith("msconv-")


class TestStoreConstructorValidation:
    def test_invalid_ttl_raises(self):
        with pytest.raises(ValueError, match="ttl_seconds"):
            ConversationCorrelationStore(ttl_seconds=0)

    def test_invalid_max_entries_raises(self):
        with pytest.raises(ValueError, match="max_entries"):
            ConversationCorrelationStore(max_entries=0)
