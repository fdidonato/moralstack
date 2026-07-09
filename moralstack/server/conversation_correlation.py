"""
Lineage-based conversation correlation for OpenAI-compatible HTTP clients.

Clients such as COMPL-AI ``llm_rules`` resend the full message history on every
turn without a stable ``conversation_id``. This module maps request histories to
a process-local conversation identifier using parent/child history hashes, and
records completed turns (including assistant replies) so the next request can
link back to the same conversation.

The internal lineage map is keyed by ``(principal, history_hash)`` so that two
byte-identical histories from different principals (tenants) resolve to
different ``conversation_id``s (P3 / P0-3 / A3). The hash functions themselves
are unchanged; isolation lives entirely in the map key. The store is also
bounded (TTL + max entries, FIFO eviction) to avoid unbounded memory growth
under long-running benchmarks.

Limitation (informatic): two distinct samples whose histories and assistant
outputs are byte-identical cannot be distinguished without an external id.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

_MAX_CONTENT_PER_MESSAGE = 4096

# Canonical hash of an empty message list — used as the parent lineage root
# before the first user message in a user-only opening turn.
_EMPTY_HISTORY_CANONICAL_HASH = hashlib.sha256(b"[]").hexdigest()

# Bounded-store defaults (P3 / P0-3 / A3): mirror InMemorySessionStore
# (moralstack/sdk/session_store.py) so a lineage entry and its governance
# session state expire on roughly the same horizon.
DEFAULT_CORRELATION_TTL_SECONDS = 3600
DEFAULT_MAX_CORRELATION_ENTRIES = 20_000


def _truncate(s: str) -> str:
    if len(s) <= _MAX_CONTENT_PER_MESSAGE:
        return s
    return s[:_MAX_CONTENT_PER_MESSAGE]


def _normalize_content(content: Any) -> str:
    """Normalize message content to a stable string for hashing."""
    if content is None:
        return ""
    if isinstance(content, str):
        return _truncate(content)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(json.dumps(block, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
                continue
            btype = str(block.get("type") or "")
            if btype == "text":
                parts.append(_truncate(str(block.get("text") or "")))
            else:
                parts.append(json.dumps(block, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        return _truncate("".join(parts))
    return _truncate(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def _canonical_message_record(msg: dict[str, Any]) -> dict[str, str]:
    role = str(msg.get("role", "") or "")
    return {"role": role, "content": _normalize_content(msg.get("content"))}


def canonical_history_hash(messages: list[dict[str, Any]]) -> str:
    """
    Deterministic SHA-256 (hex) of the full canonicalized message list.

    Uses sorted JSON for the outer list envelope so structure is stable.
    """
    normalized = [_canonical_message_record(m) for m in messages]
    blob = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def canonical_parent_history_hash(messages: list[dict[str, Any]]) -> str | None:
    """
    Hash of the conversation history *before* the current trailing user message.

    Returns ``None`` when the last message is not ``user`` (no COMPL-AI-style
    continuation edge) or when ``messages`` is empty.
    """
    if not messages:
        return None
    if (str(messages[-1].get("role") or "")) != "user":
        return None
    if len(messages) == 1:
        return _EMPTY_HISTORY_CANONICAL_HASH
    return canonical_history_hash(messages[:-1])


@dataclass
class _Entry:
    """Internal wrapper combining a resolved conversation_id with insertion time."""

    conversation_id: str
    inserted_at: float


class ConversationCorrelationStore:
    """
    Thread-safe, bounded in-memory mapping from ``(principal, canonical history
    hash)`` to ``conversation_id``.

    Bounded via TTL (lazy expiry on read) and a max-entries FIFO cap, mirroring
    ``moralstack.sdk.session_store.InMemorySessionStore``. See module docstring
    for the resolution algorithm and limitations.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_CORRELATION_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_CORRELATION_ENTRIES,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds}")
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._time_fn = time_fn
        self._lock = threading.RLock()
        # OrderedDict preserves insertion order for FIFO eviction.
        self._history_to_conversation: OrderedDict[tuple[str, str], _Entry] = OrderedDict()

    def resolve(self, messages: list[dict[str, Any]], *, principal: str = "") -> str:
        """
        Resolve (or mint) the ``conversation_id`` for a request history.

        Best-effort on the TTL/eviction lookup and insert paths (PROJECT_SPEC
        §5 invariant #6): this runs on the request path, before the proxy's
        fail-closed ``try`` block, so a failure in ``_get_id``/``_put_id``
        (e.g. from the injected clock or ``popitem`` during eviction) must
        never propagate out of ``resolve()``. On any such failure we log at
        debug and fall through to minting a fresh id, which is always
        returned regardless of whether the store lookup/insert succeeded.
        """
        request_hash = canonical_history_hash(messages)
        parent_hash = canonical_parent_history_hash(messages)
        request_key = (principal, request_hash)

        try:
            with self._lock:
                existing = self._get_id(request_key)
                if existing is not None:
                    return existing

                if parent_hash is not None:
                    parent_key = (principal, parent_hash)
                    parent_conversation_id = self._get_id(parent_key)
                    if parent_conversation_id is not None:
                        self._put_id(request_key, parent_conversation_id)
                        return parent_conversation_id
        except Exception:
            _LOG.debug(
                "Lookup lineage nello store di correlazione fallito (TTL/eviction); "
                "verrà generato un nuovo conversation_id.",
                exc_info=True,
            )

        conversation_id = f"msconv-{uuid.uuid4().hex[:16]}"
        try:
            with self._lock:
                self._put_id(request_key, conversation_id)
        except Exception:
            _LOG.debug(
                "Inserimento del nuovo conversation_id nello store di correlazione fallito; "
                "l'id generato viene comunque restituito.",
                exc_info=True,
            )
        return conversation_id

    def observe_completed_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        assistant_content: str,
        conversation_id: str,
        principal: str = "",
    ) -> None:
        """Register the completed history including the assistant reply for future turns."""
        if not conversation_id:
            return
        completed_messages = list(messages) + [{"role": "assistant", "content": assistant_content or ""}]
        completed_hash = canonical_history_hash(completed_messages)
        with self._lock:
            self._put_id((principal, completed_hash), conversation_id)

    def size(self) -> int:
        """Number of stored entries, including expired ones (lazy eviction)."""
        with self._lock:
            return len(self._history_to_conversation)

    def _get_id(self, key: tuple[str, str]) -> str | None:
        """Read under the lock, evicting the entry if expired. Hit path does not refresh TTL."""
        with self._lock:
            entry = self._history_to_conversation.get(key)
            if entry is None:
                return None
            if self._time_fn() - entry.inserted_at > self._ttl_seconds:
                self._history_to_conversation.pop(key, None)
                return None
            return entry.conversation_id

    def _put_id(self, key: tuple[str, str], conversation_id: str) -> None:
        """Insert/refresh an entry under the lock and enforce the max-entries FIFO cap."""
        with self._lock:
            if key in self._history_to_conversation:
                del self._history_to_conversation[key]
            self._history_to_conversation[key] = _Entry(
                conversation_id=conversation_id,
                inserted_at=self._time_fn(),
            )
            while len(self._history_to_conversation) > self._max_entries:
                self._history_to_conversation.popitem(last=False)
