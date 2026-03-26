"""
Helpers for optional diagnostics logging and LLM call persistence.
Centralizes the repeated pattern: log_call (if logger present) + async_persist_llm_call.
Persistence is a top-level dependency; optional behavior is at runtime (get_persist_mode, NullPersistence).
"""

from __future__ import annotations

import logging
from typing import Any

from moralstack.persistence.write_queue import (
    async_persist_decision_trace,
    async_persist_llm_call,
)

_LOG = logging.getLogger(__name__)


def _get_log_call(logger: Any) -> Any:
    """Return logger.log_call if present (for optional diagnostics)."""
    return getattr(logger, "log_call", None) if logger is not None else None


def record_llm_call(
    logger: Any,
    diagnostics_payload: dict[str, Any] | None,
    persist_kwargs: dict[str, Any] | None,
) -> None:
    """
    Optionally log a diagnostics call and/or persist an LLM call.
    If diagnostics_payload is not None and logger has log_call, calls logger.log_call(**diagnostics_payload).
    If persist_kwargs is not None, calls async_persist_llm_call(**persist_kwargs). Runtime errors are logged, not raised.
    """
    if diagnostics_payload is not None:
        log_call = _get_log_call(logger)
        if logger is not None and log_call is not None:
            log_call(**diagnostics_payload)
    if persist_kwargs is not None:
        try:
            async_persist_llm_call(**persist_kwargs)
        except Exception as e:
            _LOG.warning(
                "persist_llm_call failed: %s %s",
                type(e).__name__,
                e,
            )


def record_decision_trace(
    request_id: str | None = None,
    **kwargs: Any,
) -> None:
    """
    Fire-and-forget persist of a decision trace. Uses async_persist_decision_trace.
    Runtime errors are logged (with request_id and stage if provided), not raised.
    """
    try:
        async_persist_decision_trace(**kwargs)
    except Exception as e:
        stage = kwargs.get("stage", "?")
        _LOG.warning(
            "persist_decision_trace %s failed request_id=%s error_type=%s error=%s",
            stage,
            request_id or "",
            type(e).__name__,
            e,
        )
