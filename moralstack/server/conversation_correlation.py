"""
Lineage-based conversation correlation for OpenAI-compatible HTTP clients.

Clients such as COMPL-AI ``llm_rules`` resend the full message history on every
turn without a stable ``conversation_id``. This module maps request histories to
a process-local conversation identifier using parent/child history hashes, and
records completed turns (including assistant replies) so the next request can
link back to the same conversation.

Limitation (informatic): two distinct samples whose histories and assistant
outputs are byte-identical cannot be distinguished without an external id.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any

_MAX_CONTENT_PER_MESSAGE = 4096

# Canonical hash of an empty message list — used as the parent lineage root
# before the first user message in a user-only opening turn.
_EMPTY_HISTORY_CANONICAL_HASH = hashlib.sha256(b"[]").hexdigest()


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


class ConversationCorrelationStore:
    """
    Thread-safe in-memory mapping from canonical history hashes to conversation_id.

    See module docstring for the resolution algorithm and limitations.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._history_to_conversation: dict[str, str] = {}

    def resolve(self, messages: list[dict[str, Any]]) -> str:
        request_hash = canonical_history_hash(messages)
        parent_hash = canonical_parent_history_hash(messages)

        with self._lock:
            if request_hash in self._history_to_conversation:
                return self._history_to_conversation[request_hash]

            if parent_hash is not None and parent_hash in self._history_to_conversation:
                conversation_id = self._history_to_conversation[parent_hash]
                self._history_to_conversation[request_hash] = conversation_id
                return conversation_id

            conversation_id = f"msconv-{uuid.uuid4().hex[:16]}"
            self._history_to_conversation[request_hash] = conversation_id
            return conversation_id

    def observe_completed_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        assistant_content: str,
        conversation_id: str,
    ) -> None:
        """Register the completed history including the assistant reply for future turns."""
        if not conversation_id:
            return
        completed_messages = list(messages) + [{"role": "assistant", "content": assistant_content or ""}]
        completed_hash = canonical_history_hash(completed_messages)
        with self._lock:
            self._history_to_conversation[completed_hash] = conversation_id
