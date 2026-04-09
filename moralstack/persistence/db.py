"""
SQLite persistence layer — re-export from moralstack.observability.

Deprecated: use moralstack.observability and moralstack.observability.read_store directly.
"""

from __future__ import annotations

# Read-side helpers (standalone functions for backwards compat)
from moralstack.observability.read_store import SqliteReadStore as _SqliteReadStore

# Write-side helpers (schema, UoW, lifecycle, batch inserts)
from moralstack.observability.sinks.sqlite_sink import (  # noqa: F401
    SqliteUnitOfWork,
    _get_connection,
    create_run,
    delete_request,
    delete_run,
    end_run,
    init_db,
    insert_debug_events_batch,
    insert_decision_traces_batch,
    insert_llm_calls_batch,
    insert_orchestration_events_batch,
    invalidate_exports_cache,
    update_request_domain,
    update_request_response,
    upsert_request,
)

_rs = _SqliteReadStore()


def get_run(run_id: str):  # type: ignore[return]
    return _rs.get_run(run_id)


def get_request(run_id: str, request_id: str):  # type: ignore[return]
    return _rs.get_request(run_id, request_id)


def get_requests_for_run(run_id: str):  # type: ignore[return]
    return _rs.get_requests_for_run(run_id)


def get_llm_calls_for_request(run_id: str, request_id: str):  # type: ignore[return]
    return _rs.get_llm_calls_for_request(run_id, request_id)


def get_decision_traces_for_request(run_id: str, request_id: str):  # type: ignore[return]
    return _rs.get_decision_traces_for_request(run_id, request_id)


def get_orchestration_events_for_request(run_id: str, request_id: str):  # type: ignore[return]
    return _rs.get_orchestration_events_for_request(run_id, request_id)


def get_debug_events_for_request(run_id: str, request_id=None):  # type: ignore[return]
    return _rs.get_debug_events_for_request(run_id, request_id)


def get_all_runs(limit: int = 100):  # type: ignore[return]
    return _rs.get_all_runs(limit)


def get_models_used_for_run(run_id: str):  # type: ignore[return]
    return _rs.get_models_used_for_run(run_id)
