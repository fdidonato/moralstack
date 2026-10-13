"""
Concurrency tests for moralstack/orchestration/ledger_storage.py.

The ledger storage is shared by every request of a pipeline (the proxy runs
requests in parallel through a threadpool), so ``InMemoryLedgerStorage`` must be
safe for concurrent use. The tests are deterministic: a hook fired from inside
``LedgerKey.__eq__`` (which the storage calls while it iterates ``_items``)
starts a concurrent mutation exactly mid-iteration. Every wait is bounded
(<= 2 s) and no sleep decides an outcome.

Tests are coupled to two private details of the storage: the
``stored_key != key`` comparison inside ``get_entries`` and the ``_items`` /
``_next_entry_id`` / ``_lock`` attributes.
"""

from __future__ import annotations

import threading
import types
from typing import Callable, Optional

import pytest

from moralstack.orchestration import ledger_storage
from moralstack.orchestration.ledger import CachedDecision, LedgerEntry, LedgerKey
from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage

_WAIT = 2.0  # upper bound for any wait/join, seconds
_BLOCKED_PUT_WAIT = 0.5  # how long the hook waits for a put that may be blocked


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


class _Hook:
    """One-shot callback holder shared by every _HookKey instance."""

    fn: Optional[Callable[[], None]] = None

    @classmethod
    def arm(cls, fn: Callable[[], None]) -> None:
        cls.fn = fn

    @classmethod
    def fire(cls) -> None:
        fn, cls.fn = cls.fn, None
        if fn is not None:
            fn()


class _HookKey(LedgerKey):
    """
    LedgerKey whose __eq__ fires a one-shot hook before delegating.

    Plain subclass (no second @dataclass). Coupled to the storage comparing keys
    with ``stored_key != key`` while iterating ``_items``: the comparison lands
    in this __eq__, i.e. in the middle of the iteration.
    """

    def __eq__(self, other: object) -> bool:
        _Hook.fire()
        return LedgerKey.__eq__(self, other)

    __hash__ = LedgerKey.__hash__


def _hook_key() -> _HookKey:
    return _HookKey(contract_hash="hook", posture="NORMAL", domain=None)


def _other_key() -> LedgerKey:
    return LedgerKey(contract_hash="other", posture="NORMAL", domain=None)


class _Worker:
    """Runs ``fn`` in a daemon thread, capturing exceptions; bounded cleanup."""

    def __init__(self, fn: Callable[[], None]) -> None:
        self.started = threading.Event()
        self.done = threading.Event()
        self.error: Optional[BaseException] = None
        self._fn = fn
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.started.set()
        try:
            self._fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the test
            self.error = exc
        finally:
            self.done.set()

    def start_and_wait_blocked(self) -> None:
        """Start the thread and give it a bounded chance to finish (hook body)."""
        self.thread.start()
        self.started.wait(_WAIT)
        self.done.wait(_BLOCKED_PUT_WAIT)

    def join(self) -> None:
        if self.thread.is_alive():
            self.thread.join(_WAIT)


@pytest.fixture
def workers():
    created: list[_Worker] = []
    try:
        yield created
    finally:
        _Hook.fn = None
        for w in created:
            w.join()


def _arm_worker(workers: list[_Worker], fn: Callable[[], None]) -> _Worker:
    worker = _Worker(fn)
    workers.append(worker)
    _Hook.arm(worker.start_and_wait_blocked)
    return worker


def test_get_entries_survives_concurrent_put_during_iteration(workers):
    storage = InMemoryLedgerStorage()
    hk, other = _hook_key(), _other_key()
    storage.put(hk, _make_entry("a"))
    storage.put(hk, _make_entry("b"))
    worker = _arm_worker(workers, lambda: storage.put(other, _make_entry("c")))

    result = storage.get_entries(hk)  # must not raise RuntimeError

    assert len(result) == 2
    worker.thread.join(_WAIT)
    assert not worker.thread.is_alive()
    assert worker.error is None
    assert storage.size() == 3
    assert [e.original_prompt for e in storage.get_entries(other)] == ["c"]


def test_concurrent_put_eviction_during_get_entries(workers):
    storage = InMemoryLedgerStorage(max_entries=2)
    hk, other = _hook_key(), _other_key()
    storage.put(hk, _make_entry("a"))
    storage.put(hk, _make_entry("b"))
    worker = _arm_worker(workers, lambda: storage.put(other, _make_entry("c")))

    storage.get_entries(hk)  # must not raise

    worker.thread.join(_WAIT)
    assert not worker.thread.is_alive()
    assert worker.error is None
    assert storage.size() == 2
    # FIFO: the oldest entry ("a") was evicted by the concurrent put.
    assert [e.original_prompt for e in storage.get_entries(hk)] == ["b"]
    assert [e.original_prompt for e in storage.get_entries(other)] == ["c"]


def test_expired_pop_does_not_race_with_put(workers, monkeypatch):
    storage = InMemoryLedgerStorage(ttl_seconds=10.0)
    hk, other = _hook_key(), _other_key()
    # Entries are created BEFORE patching the clock (default_factory of
    # _StoredItem.inserted_at is bound to the real time.time at class creation).
    storage.put(hk, _make_entry("a"))
    storage.put(hk, _make_entry("b"))
    real_now = ledger_storage.time.time()
    # Patch only the module-level name; the real time.time (bound by _StoredItem's
    # default_factory) is untouched.
    future = types.SimpleNamespace(time=lambda: real_now + 10.0 + 1.0)
    monkeypatch.setattr(ledger_storage, "time", future)
    worker = _arm_worker(workers, lambda: storage.put(other, _make_entry("c")))

    result = storage.get_entries(hk)  # must not raise

    assert result == []
    worker.thread.join(_WAIT)
    assert worker.error is None
    # Both expired entries popped; the concurrent entry survived (checked via size).
    assert storage.size() == 1


class _RacyIdStorage(InMemoryLedgerStorage):
    """
    Storage whose first armed read of ``_next_entry_id`` starts a concurrent put.

    Coupled to the private attribute ``_next_entry_id`` (assigned in __init__,
    put and clear).
    """

    def __init__(self) -> None:
        self._nid = 0
        self.armed = False
        self.worker: Optional[_Worker] = None
        super().__init__()

    @property
    def _next_entry_id(self) -> int:
        value = self._nid
        if self.armed:
            self.armed = False
            assert self.worker is not None
            self.worker.start_and_wait_blocked()
        return value

    @_next_entry_id.setter
    def _next_entry_id(self, value: int) -> None:
        self._nid = value


def test_concurrent_puts_get_unique_ids(workers):
    storage = _RacyIdStorage()
    key = LedgerKey(contract_hash="k", posture="NORMAL", domain=None)
    worker = _Worker(lambda: storage.put(key, _make_entry("b")))
    workers.append(worker)
    storage.worker = worker
    storage.armed = True

    storage.put(key, _make_entry("a"))

    worker.thread.join(_WAIT)
    assert not worker.thread.is_alive()
    assert worker.error is None
    assert storage.size() == 2


def test_get_entries_returns_detached_snapshot():
    storage = InMemoryLedgerStorage()
    key = LedgerKey(contract_hash="k", posture="NORMAL", domain=None)
    storage.put(key, _make_entry("a"))

    first = storage.get_entries(key)
    first.append(_make_entry("junk"))
    first.clear()
    assert storage.size() == 1

    snapshot = storage.get_entries(key)
    storage.put(key, _make_entry("b"))
    assert len(snapshot) == 1
    assert len(storage.get_entries(key)) == 2


def test_clear_is_atomic_with_get_entries(workers):
    storage = InMemoryLedgerStorage()
    hk = _hook_key()
    storage.put(hk, _make_entry("a"))
    storage.put(hk, _make_entry("b"))
    worker = _arm_worker(workers, storage.clear)

    storage.get_entries(hk)  # must not raise

    worker.thread.join(_WAIT)
    assert worker.error is None
    assert storage.size() == 0


class _RaisingKey(LedgerKey):
    """LedgerKey whose __eq__ raises on its second call (shared counter)."""

    calls = 0

    def __eq__(self, other: object) -> bool:
        type(self).calls += 1
        if type(self).calls >= 2:
            raise ValueError("boom")
        return LedgerKey.__eq__(self, other)

    __hash__ = LedgerKey.__hash__


def test_lock_released_when_get_entries_raises(workers):
    _RaisingKey.calls = 0
    storage = InMemoryLedgerStorage()
    rk = _RaisingKey(contract_hash="raise", posture="NORMAL", domain=None)
    storage.put(rk, _make_entry("a"))
    storage.put(rk, _make_entry("b"))

    with pytest.raises(ValueError, match="boom"):
        storage.get_entries(rk)

    worker = _Worker(lambda: storage.put(_other_key(), _make_entry("c")))
    workers.append(worker)
    worker.thread.start()
    worker.thread.join(_WAIT)
    assert not worker.thread.is_alive()
    assert worker.error is None
    assert storage.size() == 3


def test_insertion_order_preserved(workers):
    storage = InMemoryLedgerStorage()
    keys = [LedgerKey(contract_hash=f"k{i}", posture="NORMAL", domain=None) for i in range(4)]
    storage.put(keys[0], _make_entry("0"))
    storage.put(keys[1], _make_entry("1"))
    worker = _Worker(lambda: storage.put(keys[2], _make_entry("2")))
    workers.append(worker)
    worker.thread.start()
    worker.thread.join(_WAIT)
    assert not worker.thread.is_alive()
    storage.put(keys[3], _make_entry("3"))

    assert list(storage._items) == [(keys[0], 0), (keys[1], 1), (keys[2], 2), (keys[3], 3)]


def test_storage_uses_non_reentrant_lock():
    storage = InMemoryLedgerStorage()
    assert type(storage._lock) is type(threading.Lock())
