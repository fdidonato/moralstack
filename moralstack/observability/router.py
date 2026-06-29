"""
EventRouter: dispatches an EventEnvelope to the active sinks based on mode.

  db_only   -> sqlite_sink only
  file_only -> jsonl_sink only
  dual      -> sqlite_sink + jsonl_sink
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from typing import Sequence

from moralstack.observability.config import get_db_path, get_observability_mode
from moralstack.observability.events import EventEnvelope
from moralstack.observability.sinks.jsonl_sink import JsonlEventSink
from moralstack.observability.sinks.sqlite_sink import SqliteEventSink, _get_connection

logger = logging.getLogger(__name__)


@dataclass
class WindowResult:
    written: int = 0
    failed: int = 0
    sqlite_written: int = 0
    sqlite_failed: int = 0
    jsonl_written: int = 0
    jsonl_failed: int = 0
    error: str | None = None


@dataclass
class RouteResult:
    written: int = 0
    failed: int = 0
    sqlite_written: int = 0
    sqlite_failed: int = 0
    jsonl_written: int = 0
    jsonl_failed: int = 0
    error: str | None = None


# Module-level singletons (lazy init handled by ObservabilityService)
_sqlite_sink: SqliteEventSink | None = None
_jsonl_sink: JsonlEventSink | None = None
_sqlite_write_lock = threading.RLock()


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


def _merge_window_results(
    *,
    mode: str,
    sqlite_result: WindowResult | None = None,
    jsonl_written: int = 0,
    jsonl_failed: int = 0,
    jsonl_error: str | None = None,
    total: int = 0,
) -> WindowResult:
    sqlite_written = sqlite_result.sqlite_written if sqlite_result is not None else 0
    sqlite_failed = sqlite_result.sqlite_failed if sqlite_result is not None else 0
    sqlite_error = sqlite_result.error if sqlite_result is not None else None
    error = sqlite_error or jsonl_error
    if mode == "file_only":
        written = jsonl_written
        failed = jsonl_failed
    elif mode == "dual":
        written = sqlite_written
        failed = sqlite_failed
    else:
        written = sqlite_written
        failed = sqlite_failed
    if mode in ("db_only", "dual") and sqlite_result is None:
        failed = total
        sqlite_failed = total
        error = error or "missing sqlite result"
    return WindowResult(
        written=written,
        failed=failed,
        sqlite_written=sqlite_written,
        sqlite_failed=sqlite_failed,
        jsonl_written=jsonl_written,
        jsonl_failed=jsonl_failed,
        error=error,
    )


def route_window(envelopes: Sequence[EventEnvelope], conn: sqlite3.Connection | None) -> WindowResult:
    """Dispatch a counted envelope window. Never raises."""
    batch = list(envelopes or [])
    if not batch:
        return WindowResult()
    mode = get_observability_mode()
    sqlite_result: WindowResult | None = None
    jsonl_written = 0
    jsonl_failed = 0
    jsonl_error: str | None = None
    try:
        if mode in ("db_only", "dual"):
            with _sqlite_write_lock:
                sqlite_result = _get_sqlite_sink().write_window(batch, conn)
        if mode in ("file_only", "dual"):
            jsonl_result = _get_jsonl_sink().write_window(batch)
            jsonl_written = jsonl_result.written
            jsonl_failed = jsonl_result.failed
            jsonl_error = jsonl_result.error
        return _merge_window_results(
            mode=mode,
            sqlite_result=sqlite_result,
            jsonl_written=jsonl_written,
            jsonl_failed=jsonl_failed,
            jsonl_error=jsonl_error,
            total=len(batch),
        )
    except Exception as exc:
        logger.error("observability: route_window failed count=%d: %s", len(batch), exc)
        return WindowResult(failed=len(batch), sqlite_failed=0, jsonl_failed=0, error=str(exc))


def _count_finalize_failure(result: RouteResult) -> None:
    failed = result.sqlite_failed + result.jsonl_failed
    if failed <= 0:
        failed = result.failed
    if failed <= 0:
        return
    try:
        from moralstack.observability.service import get_obs

        get_obs().record_finalize_failure(failed, result.error)
    except Exception:
        pass


def route_audit_sync(envelopes: Sequence[EventEnvelope]) -> RouteResult:
    """Synchronously route audit-critical envelopes with counted results. Never raises."""
    batch = list(envelopes or [])
    if not batch:
        return RouteResult()
    mode = get_observability_mode()
    conn: sqlite3.Connection | None = None
    sqlite_result: WindowResult | None = None
    jsonl_written = 0
    jsonl_failed = 0
    jsonl_error: str | None = None
    try:
        if mode in ("db_only", "dual"):
            path = get_db_path()
            if not path:
                sqlite_result = WindowResult(failed=len(batch), sqlite_failed=len(batch), error="missing sqlite db path")
            else:
                with _sqlite_write_lock:
                    conn = _get_connection(path)
                    try:
                        sqlite_result = _get_sqlite_sink().write_window(batch, conn)
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
        if mode in ("file_only", "dual"):
            jsonl_result = _get_jsonl_sink().write_window(batch)
            jsonl_written = jsonl_result.written
            jsonl_failed = jsonl_result.failed
            jsonl_error = jsonl_result.error
        merged = _merge_window_results(
            mode=mode,
            sqlite_result=sqlite_result,
            jsonl_written=jsonl_written,
            jsonl_failed=jsonl_failed,
            jsonl_error=jsonl_error,
            total=len(batch),
        )
        result = RouteResult(**merged.__dict__)
        if result.sqlite_failed or result.jsonl_failed:
            logger.error(
                "observability: route_audit_sync counted failure sqlite_failed=%d jsonl_failed=%d error=%s",
                result.sqlite_failed,
                result.jsonl_failed,
                result.error,
            )
            _count_finalize_failure(result)
        return result
    except Exception as exc:
        logger.error("observability: route_audit_sync failed count=%d: %s", len(batch), exc)
        result = RouteResult(failed=len(batch), error=str(exc))
        _count_finalize_failure(result)
        return result
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
