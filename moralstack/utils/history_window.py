"""Shared window over the conversation history shown to governance modules.

The modules that *judge* a draft (critic, simulator, hindsight) receive a
compressed view of the conversation: the last N turns, each truncated to M
characters. The path that *generates* the delivered answer is deliberately not
affected — it receives the full history, so a governed answer is written with
the same context an ungoverned call would have had.

Why the truncation and the cache fingerprint must read the same values:
`build_context_fingerprint` keys the per-module caches on the history. If a
module were shown more content than the fingerprint hashes, two conversations
identical in their first M characters and different afterwards would collide on
one cache entry, and the second would be served the first one's result.
Widening the window without widening the fingerprint is therefore a
correctness bug, not a tuning choice.

Defaults are the historical hardcoded values, so setting nothing changes
nothing. Raising the window is a measurable experiment, not a free win: on the
COMPL-AI campaigns removing the truncation entirely for these three modules
costs about $0.9 per campaign, and a counterfactual on the critic showed it
does **not** change the hard-violation verdicts (6/6 either way) — it changes
the quality of the stated rationale, which the 200-character cut was mangling.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

ENV_MAX_CHARS_PER_TURN = "MORALSTACK_HISTORY_MAX_CHARS_PER_TURN"
ENV_MAX_TURNS = "MORALSTACK_HISTORY_MAX_TURNS"

DEFAULT_MAX_CHARS_PER_TURN = 200
DEFAULT_MAX_TURNS = 3


def _positive_int_from_env(env_var: str, default: int) -> int:
    """Read a positive int from the environment. Falls back to `default`.

    An unparseable or non-positive value is ignored with a warning rather than
    raising: a malformed env var must not take the pipeline down.
    """
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", env_var, raw, default)
        return default
    if value < 1:
        logger.warning("%s=%d must be >= 1; using %d", env_var, value, default)
        return default
    return value


def history_max_chars_per_turn() -> int:
    """Characters kept per history turn in the modules' view of the conversation."""
    return _positive_int_from_env(ENV_MAX_CHARS_PER_TURN, DEFAULT_MAX_CHARS_PER_TURN)


def history_max_turns() -> int:
    """Number of trailing history turns shown to the modules."""
    return _positive_int_from_env(ENV_MAX_TURNS, DEFAULT_MAX_TURNS)


def recent_turns(history: Iterable[Any] | None) -> list[Any]:
    """Last `history_max_turns()` turns of `history`, or [] when unusable.

    Accepts anything iterable: callers pass `list[Turn]`, but also plain dicts
    from the proxy path, and test doubles. A non-iterable value returns [] rather
    than raising — the history is context, never a reason to fail a request.
    """
    if not history:
        return []
    try:
        turns: list[Any] = list(history)
    except TypeError:
        return []
    return turns[-history_max_turns() :]


def truncate_turn_content(content: str | None) -> str:
    """Truncate one turn's content to `history_max_chars_per_turn()`."""
    return (content or "")[: history_max_chars_per_turn()]
