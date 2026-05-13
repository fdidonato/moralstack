"""
LedgerStorage — persistence layer for the SemanticDecisionLedger.

This module defines a storage protocol and an in-memory implementation with
LRU eviction and TTL expiry. The ledger logic itself (lookup, threshold,
intent check) lives in moralstack/orchestration/ledger.py.

Design intent: storage is intentionally decoupled from semantics. A future
Redis-backed or SQLite-backed implementation can replace InMemoryLedgerStorage
without touching SemanticDecisionLedger.

Normative reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §5.7.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from moralstack.orchestration.ledger import LedgerEntry, LedgerKey

_LOG = logging.getLogger(__name__)


# =============================================================================
# Storage protocol
# =============================================================================


class LedgerStorageProtocol(Protocol):
    """Structural protocol for any backend storing LedgerEntry records keyed by LedgerKey."""

    def get_entries(self, key: "LedgerKey") -> list["LedgerEntry"]:
        """Return all entries for the given exact-match key. Empty list when none."""
        ...

    def put(self, key: "LedgerKey", entry: "LedgerEntry") -> None:
        """Persist an entry under the given key. Implementations may evict old entries."""
        ...

    def size(self) -> int:
        """Total number of stored entries (across all keys)."""
        ...

    def clear(self) -> None:
        """Drop all entries (useful for tests)."""
        ...


# =============================================================================
# In-memory implementation
# =============================================================================


DEFAULT_MAX_ENTRIES = 1000
DEFAULT_TTL_SECONDS = 3600  # 1 hour


@dataclass
class _StoredItem:
    """Internal wrapper combining a LedgerEntry with insertion time."""

    entry: "LedgerEntry"
    inserted_at: float = field(default_factory=time.time)


class InMemoryLedgerStorage:
    """
    Process-local LedgerStorage with LRU eviction and per-entry TTL.

    - Capacity: max_entries (default 1000). When exceeded, the least-recently-inserted
      entry is evicted. Eviction happens across all keys (global LRU).
    - TTL: ttl_seconds (default 3600). Entries older than TTL are filtered out on read
      and not returned. Lazy expiry — no background thread.

    NOT thread-safe by design. The ledger is used within a single request pipeline
    in the v0.4 rollout. Step 11 (server proxy) will add locking at a higher level.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds}")
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        # OrderedDict[(LedgerKey, entry_id) -> _StoredItem], LRU order by insertion.
        # entry_id is a monotonic int that disambiguates multiple entries under the same key.
        self._items: OrderedDict[tuple["LedgerKey", int], _StoredItem] = OrderedDict()
        self._next_entry_id = 0

    def get_entries(self, key: "LedgerKey") -> list["LedgerEntry"]:
        now = time.time()
        out: list["LedgerEntry"] = []
        expired_keys: list[tuple["LedgerKey", int]] = []
        for (stored_key, entry_id), item in self._items.items():
            if stored_key != key:
                continue
            age = now - item.inserted_at
            if age > self._ttl_seconds:
                expired_keys.append((stored_key, entry_id))
                continue
            out.append(item.entry)
        # Drop expired entries lazily.
        for k in expired_keys:
            self._items.pop(k, None)
        return out

    def put(self, key: "LedgerKey", entry: "LedgerEntry") -> None:
        entry_id = self._next_entry_id
        self._next_entry_id += 1
        composite_key = (key, entry_id)
        self._items[composite_key] = _StoredItem(entry=entry)
        # LRU eviction if over capacity.
        while len(self._items) > self._max_entries:
            evicted_key, _ = self._items.popitem(last=False)
            _LOG.debug("InMemoryLedgerStorage evicted entry (LRU): key=%s", evicted_key)

    def size(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._next_entry_id = 0
