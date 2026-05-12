"""
Deterministic conversation fingerprint for the server proxy.

When the client does not pass `extra_body.moralstack_conversation_id`,
the server derives a fingerprint from the first 3 messages of the request
(system + first user + first assistant typically). Two HTTP calls that
continue the same conversation share the same prefix, so they yield the
same fingerprint.

Per design v1.3 §4.3.
"""

from __future__ import annotations

import hashlib
from typing import Any

_MAX_CONTENT_PER_MESSAGE = 4096  # 4KB cap for stability on long conversations
_PREFIX_SIZE = 3
_HASH_PREFIX_LEN = 16


def compute_conversation_fingerprint(messages: list[dict[str, Any]] | None) -> str:
    """
    Compute a stable conversation fingerprint from the message prefix.

    Args:
        messages: OpenAI-style list of {"role": ..., "content": ...}.

    Returns:
        A short fingerprint string of the form "msf-<16hex>" when messages are
        present, or "" when the input is empty/None. The "msf-" prefix makes
        the value easy to recognize in logs.
    """
    if not messages:
        return ""

    prefix_parts: list[str] = []
    for msg in messages[:_PREFIX_SIZE]:
        role = str(msg.get("role", "") or "")
        content = str(msg.get("content", "") or "")[:_MAX_CONTENT_PER_MESSAGE]
        prefix_parts.append(f"{role}:{content}")

    blob = "\n".join(prefix_parts).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:_HASH_PREFIX_LEN]
    return f"msf-{digest}"
