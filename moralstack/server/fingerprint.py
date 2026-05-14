"""
Deterministic conversation fingerprint helper for the server proxy.

When the client does not pass ``X-Moralstack-Conversation-Id`` or
``extra_body.moralstack_conversation_id``, the HTTP proxy resolves a stable
process-local id via :class:`moralstack.server.conversation_correlation.ConversationCorrelationStore`.

``compute_conversation_fingerprint`` hashes a *lineage stem*: all messages from
the start of the list through and including the first ``user`` message (or the
full list when no ``user`` is present). That stem stays stable as COMPL-AI-style
clients append ``assistant`` replies and further ``user`` turns, so the
fingerprint string is useful for diagnostics and tests without being mistaken for
the authoritative ``conversation_id`` (which uses ``msconv-`` ids from the
correlation store).

Per design v1.3 §4.3 (updated for multi-turn OpenAI-compatible clients).
"""

from __future__ import annotations

from typing import Any

from moralstack.server.conversation_correlation import canonical_history_hash


def _stem_messages_through_first_user(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return messages from the start through the first ``user`` role, inclusive."""
    stem: list[dict[str, Any]] = []
    for msg in messages:
        stem.append(msg)
        if str(msg.get("role") or "") == "user":
            return stem
    return list(messages)


def compute_conversation_fingerprint(messages: list[dict[str, Any]] | None) -> str:
    """
    Compute a stable diagnostic fingerprint from the opening lineage stem.

    Args:
        messages: OpenAI-style list of ``{"role": ..., "content": ...}``.

    Returns:
        A short fingerprint string ``msf-<16hex>`` when messages are present,
        or ``""`` when the input is empty/None. This value is stable across
        turns for clients that resend full history with the same opening stem.
    """
    if not messages:
        return ""

    stem = _stem_messages_through_first_user(messages)
    digest = canonical_history_hash(stem)
    return f"msf-{digest[:16]}"
