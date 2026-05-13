"""
SessionStore — container of conversation governance states keyed by conversation_id.

Two main use cases:
- Server proxy (Step 11): one process handles many concurrent conversations.
- SDK (multi-user clients): a single GovernedClient instance servicing multiple users.

Defines:
- SessionStoreProtocol: structural protocol for any backend.
- InMemorySessionStore: process-local dict-based implementation with per-entry TTL
  and optional capacity cap (FIFO eviction).

Normative reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §2.4.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from moralstack.orchestration.conversation_state import ConversationGovernanceState

_LOG = logging.getLogger(__name__)


# =============================================================================
# Storage protocol
# =============================================================================


class SessionStoreProtocol(Protocol):
    """Structural protocol for any backend storing ConversationGovernanceState per conversation_id."""

    def get(self, conversation_id: str) -> "ConversationGovernanceState | None": ...

    def put(self, conversation_id: str, state: "ConversationGovernanceState") -> None: ...

    def delete(self, conversation_id: str) -> None: ...

    def list_active(self) -> list[str]: ...


# =============================================================================
# In-memory implementation
# =============================================================================


DEFAULT_SESSION_TTL_SECONDS = 3600  # 1 hour
DEFAULT_MAX_SESSIONS = 10_000


@dataclass
class _SessionEntry:
    """Internal wrapper combining a governance state with insertion time."""

    state: "ConversationGovernanceState"
    inserted_at: float = field(default_factory=time.time)


class InMemorySessionStore:
    """
    Process-local SessionStore with per-entry TTL and FIFO capacity cap.

    - TTL: ttl_seconds (default 3600). Entries older than TTL are filtered out on read
      and lazily evicted; list_active() does not include them. No background thread.
    - Capacity: max_sessions (default 10000). When exceeded on put(), the oldest
      entry is evicted (FIFO order by insertion). The cap is a safety against
      unbounded memory growth; expected usage should rely on TTL expiry instead.

    Reference identity: get() returns the same ConversationGovernanceState object
    that was passed to put(). The state itself is frozen (immutable), so identity
    preservation is unambiguous.

    NOT thread-safe by design. Server proxy (Step 11) will add locking at a higher level.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds}")
        if max_sessions < 1:
            raise ValueError(f"max_sessions must be >= 1, got {max_sessions}")
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        # OrderedDict preserves insertion order for FIFO eviction.
        self._entries: OrderedDict[str, _SessionEntry] = OrderedDict()

    def get(self, conversation_id: str) -> "ConversationGovernanceState | None":
        entry = self._entries.get(conversation_id)
        if entry is None:
            self._emit_get_event(conversation_id=conversation_id, outcome="miss", state=None, ttl_age=None)
            return None
        if self._is_expired(entry):
            # Drop lazily.
            age = time.time() - entry.inserted_at
            self._entries.pop(conversation_id, None)
            self._emit_get_event(
                conversation_id=conversation_id,
                outcome="expired",
                state=None,
                ttl_age=age,
            )
            return None
        age = time.time() - entry.inserted_at
        self._emit_get_event(
            conversation_id=conversation_id,
            outcome="hit",
            state=entry.state,
            ttl_age=age,
        )
        return entry.state

    def put(self, conversation_id: str, state: "ConversationGovernanceState") -> None:
        # If overwriting, remove the old entry first so insertion order reflects the new put.
        if conversation_id in self._entries:
            del self._entries[conversation_id]
        self._entries[conversation_id] = _SessionEntry(state=state)
        # Capacity cap: FIFO eviction.
        evicted_ids: list[str] = []
        while len(self._entries) > self._max_sessions:
            evicted_id, _ = self._entries.popitem(last=False)
            evicted_ids.append(evicted_id)
            _LOG.debug("InMemorySessionStore evicted conversation (capacity): id=%s", evicted_id)
        self._emit_put_event(
            conversation_id=conversation_id,
            outcome="stored",
            state=state,
            evicted_ids=evicted_ids,
        )

    def delete(self, conversation_id: str) -> None:
        self._entries.pop(conversation_id, None)

    def list_active(self) -> list[str]:
        active: list[str] = []
        expired_ids: list[str] = []
        for conv_id, entry in self._entries.items():
            if self._is_expired(entry):
                expired_ids.append(conv_id)
                continue
            active.append(conv_id)
        for cid in expired_ids:
            self._entries.pop(cid, None)
        return active

    def size(self) -> int:
        """Number of stored entries, including expired ones (lazy eviction)."""
        return len(self._entries)

    def clear(self) -> None:
        """Drop all entries. Helper for tests; not part of SessionStoreProtocol."""
        self._entries.clear()

    def _is_expired(self, entry: _SessionEntry) -> bool:
        age = time.time() - entry.inserted_at
        return age > self._ttl_seconds

    # ------------------------------------------------------------------
    # Step 13 — observability for SessionStore lifecycle
    # ------------------------------------------------------------------

    def _emit_get_event(
        self,
        *,
        conversation_id: str,
        outcome: str,
        state: "ConversationGovernanceState | None",
        ttl_age: float | None,
    ) -> None:
        """Emit ``session_store.get`` (hit/miss/expired). Best-effort."""
        try:
            from moralstack.observability.conversation_events import emit_session_store_get

            emit_session_store_get(
                conversation_id=conversation_id,
                outcome=outcome,
                state=state,
                ttl_age_seconds=ttl_age,
            )
        except Exception:
            _LOG.debug("InMemorySessionStore: emit_session_store_get failed", exc_info=True)

    def _emit_put_event(
        self,
        *,
        conversation_id: str,
        outcome: str,
        state: "ConversationGovernanceState",
        evicted_ids: list[str] | None,
    ) -> None:
        """Emit ``session_store.put`` with optional eviction info. Best-effort."""
        try:
            from moralstack.observability.conversation_events import emit_session_store_put

            emit_session_store_put(
                conversation_id=conversation_id,
                outcome=outcome,
                state=state,
                evicted_ids=evicted_ids if evicted_ids else None,
            )
        except Exception:
            _LOG.debug("InMemorySessionStore: emit_session_store_put failed", exc_info=True)
