"""
SQLite persistence layer — re-export from moralstack.observability.

Deprecated: use moralstack.observability and moralstack.observability.read_store directly.
"""

from __future__ import annotations

from typing import Any

# Read-side helpers (standalone functions for backwards compat)
from moralstack.observability.read_store import SqliteReadStore as _SqliteReadStore

# Write-side helpers (schema, UoW, lifecycle, batch inserts)
from moralstack.observability.sinks.sqlite_sink import (  # noqa: F401
    SqliteUnitOfWork as SqliteUnitOfWork,
)
from moralstack.observability.sinks.sqlite_sink import (
    _get_connection as _get_connection,
)
from moralstack.observability.sinks.sqlite_sink import (
    create_run as create_run,
)
from moralstack.observability.sinks.sqlite_sink import (
    delete_request as delete_request,
)
from moralstack.observability.sinks.sqlite_sink import (
    delete_run as delete_run,
)
from moralstack.observability.sinks.sqlite_sink import (
    end_run as end_run,
)
from moralstack.observability.sinks.sqlite_sink import (
    init_db as init_db,
)
from moralstack.observability.sinks.sqlite_sink import (
    insert_debug_events_batch as insert_debug_events_batch,
)
from moralstack.observability.sinks.sqlite_sink import (
    insert_decision_traces_batch as insert_decision_traces_batch,
)
from moralstack.observability.sinks.sqlite_sink import (
    insert_llm_calls_batch as insert_llm_calls_batch,
)
from moralstack.observability.sinks.sqlite_sink import (
    insert_orchestration_events_batch as insert_orchestration_events_batch,
)
from moralstack.observability.sinks.sqlite_sink import (
    invalidate_exports_cache as invalidate_exports_cache,
)
from moralstack.observability.sinks.sqlite_sink import (
    update_request_domain as update_request_domain,
)
from moralstack.observability.sinks.sqlite_sink import (
    update_request_response as update_request_response,
)
from moralstack.observability.sinks.sqlite_sink import (
    upsert_request as upsert_request,
)

_rs = _SqliteReadStore()


def get_run(run_id: str) -> dict[str, Any] | None:
    return _rs.get_run(run_id)


def get_request(run_id: str, request_id: str) -> dict[str, Any] | None:
    return _rs.get_request(run_id, request_id)


def get_requests_for_run(run_id: str) -> list[dict[str, Any]]:
    return _rs.get_requests_for_run(run_id)


def get_llm_calls_for_request(run_id: str, request_id: str) -> list[dict[str, Any]]:
    return _rs.get_llm_calls_for_request(run_id, request_id)


def get_decision_traces_for_request(run_id: str, request_id: str) -> list[dict[str, Any]]:
    return _rs.get_decision_traces_for_request(run_id, request_id)


def get_orchestration_events_for_request(run_id: str, request_id: str) -> list[dict[str, Any]]:
    return _rs.get_orchestration_events_for_request(run_id, request_id)


def get_debug_events_for_request(run_id: str, request_id: str | None = None) -> list[dict[str, Any]]:
    return _rs.get_debug_events_for_request(run_id, request_id)


def get_all_runs(limit: int = 100) -> list[dict[str, Any]]:
    return _rs.get_all_runs(limit)


def get_models_used_for_run(run_id: str) -> dict[str, str]:
    return _rs.get_models_used_for_run(run_id)
