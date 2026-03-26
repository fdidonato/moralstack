"""
Async write queue: fire-and-forget persistence per il path deliberativo.
Un thread background drena la queue FIFO; il context vars viene copiato
al momento del submit per garantire run_id/request_id corretti.
"""

import contextvars
import logging
import queue
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SENTINEL = object()


class PersistenceWriteQueue:
    """
    Background FIFO queue per persist_* calls.
    - submit() è O(1) non-blocking
    - Il worker thread processa in ordine, garantendo FK ordering
    - daemon=True: non blocca lo shutdown del processo
    """

    def __init__(self, maxsize: int = 100_000) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread = threading.Thread(target=self._worker, daemon=True, name="moralstack-persist-worker")
        self._thread.start()

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Accoda func(*args, **kwargs) con il context corrente. Non blocca mai."""
        ctx = contextvars.copy_context()
        try:
            self._queue.put_nowait((func, args, kwargs, ctx))
        except queue.Full:
            logger.warning(
                "persistence: write_queue piena (%d items), dropping %s",
                self._queue.maxsize,
                getattr(func, "__name__", str(func)),
            )

    def flush(self, timeout: float = 30.0) -> None:
        """Blocca finché la queue è vuota o scade il timeout. Chiamare dopo ogni request."""
        self._queue.join()

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                self._queue.task_done()
                break
            func, args, kwargs, ctx = item
            try:
                ctx.run(func, *args, **kwargs)
            except Exception as e:
                logger.warning(
                    "persistence: async write failed [%s]: %s",
                    getattr(func, "__name__", "?"),
                    e,
                )
            finally:
                self._queue.task_done()

    def shutdown(self, timeout: float = 30.0) -> None:
        """Drena la queue e termina il thread. Chiamare allo shutdown dell'app."""
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=timeout)


# --- Singleton thread-safe ---
_write_queue: PersistenceWriteQueue | None = None
_queue_lock = threading.Lock()


def get_write_queue() -> PersistenceWriteQueue:
    global _write_queue
    if _write_queue is None:
        with _queue_lock:
            if _write_queue is None:
                _write_queue = PersistenceWriteQueue()
    return _write_queue


# --- Async wrappers (drop-in replacement per il path deliberativo) ---


def async_persist_llm_call(**kwargs: Any) -> None:
    """Fire-and-forget persist_llm_call. Non blocca. Usa context corrente."""
    from moralstack.persistence.sink import persist_llm_call

    get_write_queue().submit(persist_llm_call, **kwargs)


def async_persist_decision_trace(**kwargs: Any) -> None:
    """Fire-and-forget persist_decision_trace."""
    from moralstack.persistence.sink import persist_decision_trace

    get_write_queue().submit(persist_decision_trace, **kwargs)


def async_persist_debug_event(**kwargs: Any) -> None:
    """Fire-and-forget persist_debug_event."""
    from moralstack.persistence.sink import persist_debug_event

    get_write_queue().submit(persist_debug_event, **kwargs)
