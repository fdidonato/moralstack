"""
Async write queue for observability: fire-and-forget emit calls.

A single background thread drains a tagged FIFO queue. Envelope-native emits are
persisted in small SQLite/JSONL windows; legacy callable submissions still run
with the contextvars snapshot captured at submit() time.
"""

from __future__ import annotations

import contextvars
import logging
import os
import queue
import threading
import time
from typing import Any, Callable

from moralstack.observability.config import get_db_path, get_observability_mode
from moralstack.observability.context import get_current_request_id, get_current_run_id
from moralstack.observability.events import EVENT_DEBUG_EVENT, EventEnvelope, make_envelope
from moralstack.observability.router import WindowResult
from moralstack.observability.sinks.sqlite_sink import _get_connection

logger = logging.getLogger(__name__)

_SENTINEL = object()
_DROP_LOG_EVERY = 100
_DROP_LOG_INTERVAL_S = 5.0


class ObservabilityWriteQueue:
    """
    Background FIFO queue for ObservabilityService.emit() calls.
    - submit paths are O(1) non-blocking
    - the worker owns its persistent SQLite connection
    - envelope windows are written in FIFO order around legacy callables
    """

    def __init__(
        self,
        maxsize: int = 100_000,
        *,
        batch_max_items: int | None = None,
        batch_max_delay_ms: int | None = None,
    ) -> None:
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        self._batch_max_items = self._env_int("MORALSTACK_OBSERVABILITY_BATCH_MAX_ITEMS", batch_max_items or 500)
        self._batch_max_delay_ms = self._env_int(
            "MORALSTACK_OBSERVABILITY_BATCH_MAX_DELAY_MS",
            batch_max_delay_ms if batch_max_delay_ms is not None else 50,
        )
        self._stats_lock = threading.Lock()
        self._submitted_count = 0
        self._written_count = 0
        self._dropped_count = 0
        self._failed_count = 0
        self._finalize_failed_count = 0
        # Per-sink window counters: in dual mode a JSONL failure is counted
        # separately and never flips the (SQLite-driven) headline written/failed.
        self._sqlite_written_count = 0
        self._sqlite_failed_count = 0
        self._jsonl_written_count = 0
        self._jsonl_failed_count = 0
        self._last_error: str | None = None
        self._last_drop_log_at = 0.0
        self._pending_drop_marker_count = 0
        self._pending_drop_marker_run_id: str | None = None
        self._pending_drop_marker_request_id: str | None = None
        self._worker_connection_thread_id: int | None = None
        self._worker_connection_closed_thread_id: int | None = None
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="moralstack-obs-worker",
        )
        self._thread.start()

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return max(1, int(os.getenv(name, str(default))))
        except Exception:
            return max(1, default)

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Enqueue func(*args, **kwargs) with a snapshot of the current context."""
        ctx = contextvars.copy_context()
        self._enqueue(("callable", func, args, kwargs, ctx), units=1, label=getattr(func, "__name__", str(func)))

    def submit_envelope(self, envelope: EventEnvelope) -> None:
        """Enqueue one envelope for windowed persistence. Never raises."""
        if not isinstance(envelope, EventEnvelope):
            self._record_invalid_submission(1, "invalid envelope")
            return
        self._enqueue(("envelopes", [envelope]), units=1, label=envelope.event_type)

    def submit_batch(self, envelopes: list[EventEnvelope]) -> None:
        """Enqueue an envelope batch as one queue item. Never raises."""
        batch = list(envelopes or [])
        if not batch:
            return
        valid: list[EventEnvelope] = []
        invalid = 0
        for envelope in batch:
            if isinstance(envelope, EventEnvelope):
                valid.append(envelope)
            else:
                invalid += 1
        if invalid:
            self._record_invalid_submission(invalid, "invalid envelope in batch")
        if valid:
            self._enqueue(("envelopes", valid), units=len(valid), label=f"envelopes[{len(valid)}]")

    def flush(self, timeout: float = 30.0) -> None:
        """
        Wait until queued tasks finish or ``timeout`` elapses.

        Never raises. On timeout, logs a warning and returns even if work remains.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                logger.warning(
                    "observability: flush timed out with %d unfinished task(s)",
                    self._queue.unfinished_tasks,
                )
                return
            time.sleep(0.01)

    def shutdown(self, timeout: float = 30.0) -> None:
        """Drain the queue and stop the worker thread. Never raises."""
        try:
            if not self._thread.is_alive():
                return
            self._queue.put(_SENTINEL, timeout=max(0.01, timeout))
        except Exception as exc:
            self._set_last_error(f"shutdown sentinel enqueue failed: {exc}")
            logger.error("observability: shutdown sentinel enqueue failed: %s", exc)
            return
        try:
            self._thread.join(timeout=timeout)
        except Exception as exc:
            self._set_last_error(f"shutdown join failed: {exc}")
            logger.warning("observability: shutdown join failed: %s", exc)

    def stats(self) -> dict[str, Any]:
        """Return thread-safe queue counters."""
        with self._stats_lock:
            return {
                "submitted_count": self._submitted_count,
                "written_count": self._written_count,
                "dropped_count": self._dropped_count,
                "failed_count": self._failed_count,
                "sqlite_written_count": self._sqlite_written_count,
                "sqlite_failed_count": self._sqlite_failed_count,
                "jsonl_written_count": self._jsonl_written_count,
                "jsonl_failed_count": self._jsonl_failed_count,
                "finalize_failed": self._finalize_failed_count,
                "finalize_failed_count": self._finalize_failed_count,
                "last_error": self._last_error,
                "unfinished_tasks": self._queue.unfinished_tasks,
                "worker_alive": self._thread.is_alive(),
                "worker_connection_thread_id": self._worker_connection_thread_id,
                "worker_connection_closed_thread_id": self._worker_connection_closed_thread_id,
            }

    def record_finalize_failure(self, count: int = 1, error: str | None = None) -> None:
        """Count synchronous finalization failures from route_audit_sync()."""
        with self._stats_lock:
            self._finalize_failed_count += max(0, int(count or 0))
            if error:
                self._last_error = error

    def _enqueue(self, item: Any, *, units: int, label: str) -> None:
        with self._stats_lock:
            self._submitted_count += units
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._record_drop(units, label)

    def _record_invalid_submission(self, units: int, error: str) -> None:
        with self._stats_lock:
            self._submitted_count += units
            self._failed_count += units
            self._last_error = error
        logger.error("observability: invalid emit counted failed count=%d error=%s", units, error)

    def _record_drop(self, units: int, label: str) -> None:
        run_id = get_current_run_id()
        request_id = get_current_request_id()
        with self._stats_lock:
            self._dropped_count += units
            # Only feed the PERSISTED count-only marker when this drop has a
            # resolvable run_id — otherwise the units would later be persisted
            # under whatever run_id a subsequent drop happens to carry
            # (mis-attribution). The total dropped_count still counts every drop.
            if run_id:
                self._pending_drop_marker_count += units
                self._pending_drop_marker_run_id = run_id
                self._pending_drop_marker_request_id = request_id
            self._last_error = f"write_queue full, dropped {units} {label}"
            dropped_count = self._dropped_count
        now = time.monotonic()
        should_log = (
            dropped_count == units
            or dropped_count % _DROP_LOG_EVERY == 0
            or (now - self._last_drop_log_at >= _DROP_LOG_INTERVAL_S)
        )
        if should_log:
            self._last_drop_log_at = now
            logger.error(
                "observability: write_queue full (%d items), dropped=%d latest=%s",
                self._queue.maxsize,
                dropped_count,
                label,
            )

    def _set_last_error(self, error: str) -> None:
        with self._stats_lock:
            self._last_error = error

    def _record_window_result(self, result: WindowResult, *, count_stats: bool = True) -> None:
        if not count_stats:
            return
        with self._stats_lock:
            self._written_count += result.written
            self._failed_count += result.failed
            self._sqlite_written_count += result.sqlite_written
            self._sqlite_failed_count += result.sqlite_failed
            self._jsonl_written_count += result.jsonl_written
            self._jsonl_failed_count += result.jsonl_failed
            if result.error:
                self._last_error = result.error

    def _record_callable_result(self, *, failed: bool, error: str | None = None) -> None:
        with self._stats_lock:
            if failed:
                self._failed_count += 1
                if error:
                    self._last_error = error
            else:
                self._written_count += 1

    def _take_drop_marker(self) -> EventEnvelope | None:
        with self._stats_lock:
            count = self._pending_drop_marker_count
            run_id = self._pending_drop_marker_run_id
            request_id = self._pending_drop_marker_request_id
            if count <= 0 or not run_id:
                return None
            self._pending_drop_marker_count = 0
            self._pending_drop_marker_run_id = None
            self._pending_drop_marker_request_id = None
        return make_envelope(
            EVENT_DEBUG_EVENT,
            run_id=run_id,
            request_id=request_id,
            payload={"kind": "obs_queue_dropped", "count": count},
        )

    def _open_worker_connection(self) -> Any:
        mode = get_observability_mode()
        if mode not in ("db_only", "dual"):
            return None
        path = get_db_path()
        if not path:
            return None
        try:
            conn = _get_connection(path)
            conn.execute("PRAGMA synchronous=NORMAL")
            self._worker_connection_thread_id = threading.get_ident()
            return conn
        except Exception as exc:
            self._set_last_error(f"worker connection open failed: {exc}")
            logger.warning("observability: worker connection open failed: %s", exc)
            return None

    def _flush_window(self, window: list[EventEnvelope], conn: Any, *, count_stats: bool = True) -> None:
        if not window:
            return
        try:
            from moralstack.observability import router

            result = router.route_window(window, conn)
        except Exception as exc:
            logger.warning("observability: window route failed: %s", exc)
            result = WindowResult(failed=len(window), error=str(exc))
        self._record_window_result(result, count_stats=count_stats)

    def _flush_drop_marker(self, conn: Any) -> None:
        marker = self._take_drop_marker()
        if marker is not None:
            self._flush_window([marker], conn, count_stats=False)

    def _run_callable_item(self, item: tuple[Any, ...]) -> None:
        _, func, args, kwargs, ctx = item
        try:
            ctx.run(func, *args, **kwargs)
            self._record_callable_result(failed=False)
        except Exception as e:
            self._record_callable_result(failed=True, error=str(e))
            logger.warning(
                "observability: async write failed [%s]: %s",
                getattr(func, "__name__", "?"),
                e,
            )

    def _worker(self) -> None:
        conn: Any = None
        try:
            while True:
                item = self._queue.get()
                if item is _SENTINEL:
                    self._queue.task_done()
                    break

                conn = conn or self._open_worker_connection()
                window: list[EventEnvelope] = []
                window_task_count = 0
                stop_after_window = False
                end_window_after_callable = False
                started = time.monotonic()

                while True:
                    if item is _SENTINEL:
                        self._queue.task_done()
                        stop_after_window = True
                        break

                    tag = item[0] if isinstance(item, tuple) and item else None
                    if tag == "envelopes":
                        window.extend(item[1])
                        window_task_count += 1
                    elif tag == "callable":
                        if window:
                            self._flush_window(window, conn)
                            for _ in range(window_task_count):
                                self._queue.task_done()
                            self._flush_drop_marker(conn)
                            window = []
                            window_task_count = 0
                        self._run_callable_item(item)
                        self._queue.task_done()
                        self._flush_drop_marker(conn)
                        end_window_after_callable = True
                        break
                    else:
                        with self._stats_lock:
                            self._failed_count += 1
                            self._last_error = f"unknown queue item: {tag!r}"
                        self._queue.task_done()

                    if len(window) >= self._batch_max_items:
                        break
                    elapsed_ms = (time.monotonic() - started) * 1000.0
                    remaining = max(0.0, (self._batch_max_delay_ms - elapsed_ms) / 1000.0)
                    if remaining <= 0.0:
                        break
                    try:
                        item = self._queue.get(timeout=remaining)
                    except queue.Empty:
                        break

                if window:
                    self._flush_window(window, conn)
                    for _ in range(window_task_count):
                        self._queue.task_done()
                    self._flush_drop_marker(conn)
                if stop_after_window:
                    break
                if end_window_after_callable:
                    continue
        finally:
            try:
                if conn is not None:
                    conn.close()
                    self._worker_connection_closed_thread_id = threading.get_ident()
            except Exception as exc:
                self._set_last_error(f"worker connection close failed: {exc}")
