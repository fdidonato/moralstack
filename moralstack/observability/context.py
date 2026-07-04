"""
Observability context variables: run_id, request_id, cycle,
session_id (multi-turn conversation_id), turn_number.

Thread-safe via contextvars.
"""

from __future__ import annotations

from contextvars import ContextVar

_run_id: ContextVar[str | None] = ContextVar("moralstack_run_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("moralstack_request_id", default=None)
_cycle: ContextVar[int | None] = ContextVar("moralstack_cycle", default=None)
_session_id: ContextVar[str | None] = ContextVar("moralstack_session_id", default=None)
_turn_number: ContextVar[int | None] = ContextVar("moralstack_turn_number", default=None)


def set_current_run_id(run_id: str) -> None:
    """Sets the current run ID."""
    _run_id.set(run_id)


def get_current_run_id() -> str | None:
    """Returns the current run ID, or None."""
    return _run_id.get()


def set_current_request_id(request_id: str) -> None:
    """Sets the current request ID."""
    _request_id.set(request_id)


def get_current_request_id() -> str | None:
    """Returns the current request ID, or None."""
    return _request_id.get()


def set_current_cycle(cycle: int) -> None:
    """Sets the current deliberation cycle."""
    _cycle.set(cycle)


def get_current_cycle() -> int | None:
    """Returns the current cycle, or None."""
    return _cycle.get()


def set_current_session_id(session_id: str | None) -> None:
    """Sets the current session (conversation) ID."""
    _session_id.set(session_id)


def get_current_session_id() -> str | None:
    """Returns the current session (conversation) ID, or None."""
    return _session_id.get()


def set_current_turn_number(turn_number: int | None) -> None:
    """Sets the current turn number within a conversation."""
    _turn_number.set(turn_number)


def get_current_turn_number() -> int | None:
    """Returns the current turn number, or None."""
    return _turn_number.get()
