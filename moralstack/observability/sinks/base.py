"""
EventSink protocol: common interface for all observability sinks.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from moralstack.observability.events import EventEnvelope


class EventSink(Protocol):
    """Protocol for observability sinks (SQLite, JSONL, …)."""

    def write_envelope(self, envelope: EventEnvelope) -> None:
        """Write a single event. Must not raise; log on error."""
        ...

    def write_batch(self, envelopes: Sequence[EventEnvelope]) -> None:
        """Write multiple events atomically where possible. Must not raise."""
        ...

    def flush(self, timeout: float = 30.0) -> None:
        """Ensure all pending writes are committed. Best-effort."""
        ...

    def close(self) -> None:
        """Release resources. Called at shutdown."""
        ...
