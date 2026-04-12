"""Protocol for observability emission from the controller and collaborators."""

from __future__ import annotations

from typing import Any, Protocol


class EventEmitter(Protocol):
    """Abstraction for orchestration observability (events, LLM calls, decision traces)."""

    def emit_orchestration_event(self, **kwargs: Any) -> None:
        """Emit a single orchestration lifecycle / taxonomy event."""
        ...

    def emit_llm_call(self, **kwargs: Any) -> None:
        """Persist or record an LLM call (async queue in default implementation)."""
        ...

    def emit_decision_trace(self, **kwargs: Any) -> None:
        """Persist or record a decision trace entry."""
        ...
