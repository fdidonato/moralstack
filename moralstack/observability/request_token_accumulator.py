"""Per-request in-process token usage accumulator (best-effort synchronous summary)."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass

from moralstack.observability.token_usage import TokenUsage

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 5000
_max_entries = _DEFAULT_MAX_ENTRIES
_lock = threading.Lock()
_store: OrderedDict[tuple[str, str], RequestTokenTotals] = OrderedDict()


@dataclass
class RequestTokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    missing_usage_count: int = 0
    estimated_usage_count: int = 0
    usage_may_be_incomplete: bool = False
    incomplete_reason: str | None = None


def record_llm_call_usage(run_id: str, request_id: str, token_usage_json: str | None) -> None:
    usage = TokenUsage.from_json(token_usage_json)
    with _lock:
        totals = _get_or_create(run_id, request_id)
        totals.input_tokens += usage.input_tokens
        totals.output_tokens += usage.output_tokens
        totals.total_tokens += usage.total_tokens
        totals.llm_call_count += 1
        if usage.source == "missing":
            totals.missing_usage_count += 1
        elif usage.source == "estimated":
            totals.estimated_usage_count += 1


def pop_request_token_usage(run_id: str, request_id: str) -> RequestTokenTotals | None:
    key = (run_id, request_id)
    with _lock:
        return _store.pop(key, None)


def mark_request_usage_partial(run_id: str, request_id: str, *, reason: str) -> None:
    with _lock:
        totals = _get_or_create(run_id, request_id)
        totals.usage_may_be_incomplete = True
        totals.incomplete_reason = reason


def finalize_and_persist(run_id: str, request_id: str) -> RequestTokenTotals | None:
    try:
        totals = pop_request_token_usage(run_id, request_id)
        if totals is None:
            totals = RequestTokenTotals()
        from moralstack.observability import obs
        from moralstack.observability.events import EVENT_REQUEST_TOKEN_USAGE_FINALIZED, make_envelope

        obs.emit(
            make_envelope(
                EVENT_REQUEST_TOKEN_USAGE_FINALIZED,
                run_id=run_id,
                request_id=request_id,
                payload={
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "total_tokens": totals.total_tokens,
                    "llm_call_count": totals.llm_call_count,
                    "missing_usage_count": totals.missing_usage_count,
                    "estimated_usage_count": totals.estimated_usage_count,
                    "usage_may_be_incomplete": totals.usage_may_be_incomplete,
                    "incomplete_reason": totals.incomplete_reason,
                },
            )
        )
        return totals
    except Exception:
        logger.debug("token accounting finalize_and_persist failed", exc_info=True)
        return None


def _get_or_create(run_id: str, request_id: str) -> RequestTokenTotals:
    key = (run_id, request_id)
    if key not in _store:
        _store[key] = RequestTokenTotals()
        while len(_store) > _max_entries:
            _store.popitem(last=False)
    else:
        _store.move_to_end(key)
    return _store[key]
