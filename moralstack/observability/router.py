"""
EventRouter: dispatches an EventEnvelope to the active sinks based on mode.

  db_only   -> sqlite_sink only
  file_only -> jsonl_sink only
  dual      -> sqlite_sink + jsonl_sink
"""

from __future__ import annotations

from typing import Sequence

from moralstack.observability.config import get_observability_mode
from moralstack.observability.events import EventEnvelope
from moralstack.observability.sinks.jsonl_sink import JsonlEventSink
from moralstack.observability.sinks.sqlite_sink import SqliteEventSink

# Module-level singletons (lazy init handled by ObservabilityService)
_sqlite_sink: SqliteEventSink | None = None
_jsonl_sink: JsonlEventSink | None = None


def _get_sqlite_sink() -> SqliteEventSink:
    global _sqlite_sink
    if _sqlite_sink is None:
        _sqlite_sink = SqliteEventSink()
    return _sqlite_sink


def _get_jsonl_sink() -> JsonlEventSink:
    global _jsonl_sink
    if _jsonl_sink is None:
        _jsonl_sink = JsonlEventSink()
    return _jsonl_sink


def route(envelope: EventEnvelope) -> None:
    """Dispatch a single envelope to the active sinks. Does not raise."""
    mode = get_observability_mode()
    if mode in ("db_only", "dual"):
        _get_sqlite_sink().write_envelope(envelope)
    if mode in ("file_only", "dual"):
        _get_jsonl_sink().write_envelope(envelope)


def route_batch(envelopes: Sequence[EventEnvelope]) -> None:
    """Dispatch a batch of envelopes to the active sinks. Does not raise."""
    if not envelopes:
        return
    mode = get_observability_mode()
    if mode in ("db_only", "dual"):
        _get_sqlite_sink().write_batch(envelopes)
    if mode in ("file_only", "dual"):
        _get_jsonl_sink().write_batch(envelopes)
