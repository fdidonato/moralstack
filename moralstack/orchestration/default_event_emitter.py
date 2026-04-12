"""Default EventEmitter: delegates to persistence_helpers (same behavior as direct sink/queue calls)."""

from __future__ import annotations

from typing import Any

from moralstack.orchestration.persistence_helpers import record_decision_trace, record_llm_call
from moralstack.persistence.sink import persist_orchestration_event


class DefaultEventEmitter:
    """Wraps current sink + async queue helpers — drop-in for production orchestration."""

    def emit_orchestration_event(self, **kwargs: Any) -> None:
        persist_orchestration_event(**kwargs)

    def emit_llm_call(self, **kwargs: Any) -> None:
        record_llm_call(None, None, kwargs)

    def emit_decision_trace(self, **kwargs: Any) -> None:
        record_decision_trace(**kwargs)
