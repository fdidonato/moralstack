"""
JSONL event sink: writes one file per event_type under jsonl_dir.

Each line is a JSON-serialised EventEnvelope dict.
Files are opened in append mode; per-file locks prevent interleaved writes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Sequence

from moralstack.observability.config import get_jsonl_dir
from moralstack.observability.events import EventEnvelope

logger = logging.getLogger(__name__)


@dataclass
class JsonlWindowResult:
    written: int = 0
    failed: int = 0
    error: str | None = None


_file_locks: dict[str, threading.Lock] = {}
_locks_meta_lock = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    with _locks_meta_lock:
        if path not in _file_locks:
            _file_locks[path] = threading.Lock()
        return _file_locks[path]


class JsonlEventSink:
    """
    Implements EventSink by appending one JSON line per envelope to
    {jsonl_dir}/{event_type}.jsonl.
    """

    def __init__(self, jsonl_dir: str | None = None) -> None:
        self._jsonl_dir = jsonl_dir  # None → read from config at runtime

    def _dir(self) -> str:
        return self._jsonl_dir or get_jsonl_dir()

    def write_envelope(self, envelope: EventEnvelope) -> None:
        """Append one line. Does not raise."""
        try:
            self._write_line(envelope)
        except Exception as e:
            logger.warning(
                "observability[jsonl]: write_envelope failed event_type=%s: %s",
                envelope.event_type,
                e,
            )

    def write_batch(self, envelopes: Sequence[EventEnvelope]) -> None:
        """Append all envelopes. Does not raise."""
        by_type: dict[str, list[EventEnvelope]] = {}
        for ev in envelopes:
            by_type.setdefault(ev.event_type, []).append(ev)
        for event_type, batch in by_type.items():
            try:
                self._write_lines_for_type(event_type, batch)
            except Exception as e:
                logger.warning(
                    "observability[jsonl]: write_batch failed event_type=%s count=%d: %s",
                    event_type,
                    len(batch),
                    e,
                )

    def write_window(self, envelopes: Sequence[EventEnvelope]) -> JsonlWindowResult:
        """Append a result-counted window. Never raises."""
        written = 0
        failed = 0
        first_error: str | None = None
        for envelope in envelopes:
            try:
                self._write_line(envelope)
                written += 1
            except Exception as e:
                failed += 1
                if first_error is None:
                    first_error = str(e)
                logger.warning(
                    "observability[jsonl]: write_window failed event_type=%s: %s",
                    getattr(envelope, "event_type", "?"),
                    e,
                )
        return JsonlWindowResult(written=written, failed=failed, error=first_error)

    def flush(self, timeout: float = 30.0) -> None:
        """No-op: writes are synchronous."""

    def close(self) -> None:
        """No-op: files are opened/closed per write."""

    def _write_line(self, envelope: EventEnvelope) -> None:
        jsonl_dir = self._dir()
        path = os.path.join(jsonl_dir, f"{envelope.event_type}.jsonl")
        line = json.dumps(envelope.to_dict(), ensure_ascii=False) + "\n"
        lock = _get_file_lock(path)
        with lock:
            os.makedirs(jsonl_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

    def _write_lines_for_type(self, event_type: str, batch: list[EventEnvelope]) -> None:
        jsonl_dir = self._dir()
        path = os.path.join(jsonl_dir, f"{event_type}.jsonl")
        lines = "".join(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n" for ev in batch)
        lock = _get_file_lock(path)
        with lock:
            os.makedirs(jsonl_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(lines)
