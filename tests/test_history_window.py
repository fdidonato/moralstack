"""The history window shown to governance modules, and its cache fingerprint.

The judging modules (critic, simulator, hindsight) see a compressed view of the
conversation: last N turns, M characters each. Until 2026-09-09 both N and M
were hardcoded in two files that had no way of knowing about each other —
`message_context.build_module_messages` (what the module is shown) and
`cache.build_context_fingerprint` (what keys the module's cache).

That pairing is a correctness constraint, not a style preference: if a module is
shown more content than the fingerprint hashes, two conversations identical in
their first M characters and different afterwards collide on one cache entry and
the second is served the first one's result. The tests below pin the two to the
same source, so raising the window cannot silently leave the fingerprint behind.

Defaults are the historical values, so an unset environment changes nothing.
"""

from __future__ import annotations

import pytest

from moralstack.core.types import Turn
from moralstack.runtime.modules.message_context import build_module_messages, message_sections
from moralstack.utils.cache import build_context_fingerprint
from moralstack.utils.history_window import (
    DEFAULT_MAX_CHARS_PER_TURN,
    DEFAULT_MAX_TURNS,
    ENV_MAX_CHARS_PER_TURN,
    ENV_MAX_TURNS,
    history_max_chars_per_turn,
    history_max_turns,
    recent_turns,
    truncate_turn_content,
)


def _turns(n: int, length: int = 500) -> list[Turn]:
    return [Turn(role="user" if i % 2 == 0 else "assistant", content=f"{i:02d}" + "x" * (length - 2)) for i in range(n)]


class TestDefaultsAreTheHistoricalValues:
    """An unset environment must reproduce the pre-2026-09-09 behaviour exactly."""

    def test_defaults(self, monkeypatch):
        monkeypatch.delenv(ENV_MAX_CHARS_PER_TURN, raising=False)
        monkeypatch.delenv(ENV_MAX_TURNS, raising=False)
        assert (DEFAULT_MAX_CHARS_PER_TURN, DEFAULT_MAX_TURNS) == (200, 3)
        assert history_max_chars_per_turn() == 200
        assert history_max_turns() == 3

    def test_module_messages_keep_three_turns_of_two_hundred_chars(self, monkeypatch):
        monkeypatch.delenv(ENV_MAX_CHARS_PER_TURN, raising=False)
        monkeypatch.delenv(ENV_MAX_TURNS, raising=False)
        msgs = build_module_messages(system_prompt="s", user_prompt="u", conversation_history=_turns(5))
        history = [m for m in msgs if m["role"] in {"user", "assistant"}][:-1]
        assert len(history) == 3
        assert all(len(m["content"]) == 200 for m in history)


class TestEnvironmentOverrides:
    @pytest.mark.parametrize("chars,turns", [(500, 2), (1000, 5), (50, 1)])
    def test_window_follows_the_environment(self, monkeypatch, chars, turns):
        monkeypatch.setenv(ENV_MAX_CHARS_PER_TURN, str(chars))
        monkeypatch.setenv(ENV_MAX_TURNS, str(turns))
        msgs = build_module_messages(system_prompt="s", user_prompt="u", conversation_history=_turns(8, length=2000))
        history = [m for m in msgs if m["role"] in {"user", "assistant"}][:-1]
        assert len(history) == turns
        assert all(len(m["content"]) == chars for m in history)

    @pytest.mark.parametrize("bad", ["", "   ", "0", "-5", "abc", "3.5"])
    def test_malformed_values_fall_back_instead_of_raising(self, monkeypatch, bad):
        # A malformed env var must never take the pipeline down.
        monkeypatch.setenv(ENV_MAX_CHARS_PER_TURN, bad)
        monkeypatch.setenv(ENV_MAX_TURNS, bad)
        assert history_max_chars_per_turn() == DEFAULT_MAX_CHARS_PER_TURN
        assert history_max_turns() == DEFAULT_MAX_TURNS


class TestFingerprintTracksTheWindow:
    """The correctness constraint: hash at least what the module is shown."""

    def test_fingerprint_separates_conversations_that_differ_inside_the_window(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_CHARS_PER_TURN, "500")
        a = [Turn(role="user", content="A" * 200 + "left" + "z" * 100)]
        b = [Turn(role="user", content="A" * 200 + "right" + "z" * 100)]
        # The two differ only past character 200 — the pre-change cut.
        assert a[0].content[:200] == b[0].content[:200]
        assert build_context_fingerprint(conversation_history=a) != build_context_fingerprint(conversation_history=b)

    def test_module_view_and_fingerprint_read_the_same_window(self, monkeypatch):
        for chars, turns in ((200, 3), (600, 2), (1500, 4)):
            monkeypatch.setenv(ENV_MAX_CHARS_PER_TURN, str(chars))
            monkeypatch.setenv(ENV_MAX_TURNS, str(turns))
            history = _turns(6, length=3000)
            msgs = build_module_messages(system_prompt="s", user_prompt="u", conversation_history=history)
            shown = [m["content"] for m in msgs if m["role"] in {"user", "assistant"}][:-1]
            hashed = [truncate_turn_content(t.content) for t in recent_turns(history)]
            # Byte-for-byte: what the module sees is what the fingerprint hashes.
            assert shown == hashed

    def test_a_turn_beyond_the_window_does_not_change_the_fingerprint(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_TURNS, "2")
        base = _turns(2, length=50)
        with_older = [Turn(role="user", content="older turn, outside the window")] + base
        assert build_context_fingerprint(conversation_history=base) == build_context_fingerprint(
            conversation_history=with_older
        )


class TestObservabilityRecordStaysComplete:
    """The audit record must not inherit the truncation.

    `message_sections` is the only source from which a replay can rebuild what a
    module was shown — reconstructing the critic's real input from the DB already
    depends on it. Truncating it would make that impossible.
    """

    def test_history_content_is_stored_in_full(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_CHARS_PER_TURN, "200")
        history = _turns(3, length=1500)
        sections = message_sections(conversation_history=history)
        assert [len(h["content"]) for h in sections["history_messages"]] == [1500, 1500, 1500]

    def test_turn_count_follows_the_window(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_TURNS, "2")
        sections = message_sections(conversation_history=_turns(5, length=10))
        assert len(sections["history_messages"]) == 2


def test_no_history_is_still_an_empty_fingerprint():
    assert build_context_fingerprint(conversation_history=None) == ""
    assert build_context_fingerprint(conversation_history=[]) == ""
    assert recent_turns(None) == []
