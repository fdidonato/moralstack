"""
Observability context variables: run_id, request_id, cycle.

Thread-safe via contextvars. Migrated from moralstack.persistence.context.
"""

from __future__ import annotations

from contextvars import ContextVar

_run_id: ContextVar[str | None] = ContextVar("moralstack_run_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("moralstack_request_id", default=None)
_cycle: ContextVar[int | None] = ContextVar("moralstack_cycle", default=None)


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
