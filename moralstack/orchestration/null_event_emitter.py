"""No-op event emitter for standalone runs and tests without observability sinks."""

from __future__ import annotations

from typing import Any


class NullEventEmitter:
    def emit_orchestration_event(self, **kwargs: Any) -> None:
        return None

    def emit_llm_call(self, **kwargs: Any) -> None:
        return None

    def emit_decision_trace(self, **kwargs: Any) -> None:
        return None
