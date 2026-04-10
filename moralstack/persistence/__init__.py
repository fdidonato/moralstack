"""
MoralStack Persistence — deprecated; use moralstack.observability instead.

This package is kept as a backwards-compatible alias. All symbols re-export
from moralstack.observability and moralstack.observability.sinks.sqlite_sink.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "moralstack.persistence is deprecated; use moralstack.observability instead.",
    DeprecationWarning,
    stacklevel=2,
)

from moralstack.observability.config import (  # noqa: E402, F401
    get_db_path,
    get_ui_credentials,
)
from moralstack.observability.config import (  # noqa: E402
    get_observability_mode as get_persist_mode,
)
from moralstack.observability.context import (  # noqa: E402, F401
    get_current_cycle,
    get_current_request_id,
    get_current_run_id,
    set_current_cycle,
    set_current_request_id,
    set_current_run_id,
)
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402, F401
    SqliteUnitOfWork,
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
from moralstack.observability.write_queue import ObservabilityWriteQueue as PersistenceWriteQueue  # noqa: E402, F401
from moralstack.persistence.db import (  # noqa: E402, F401
    get_all_runs,
    get_debug_events_for_request,
    get_decision_traces_for_request,
    get_llm_calls_for_request,
    get_orchestration_events_for_request,
    get_request,
    get_requests_for_run,
    get_run,
)
from moralstack.persistence.default import DefaultPersistence  # noqa: E402, F401
from moralstack.persistence.null import NullPersistence  # noqa: E402, F401
from moralstack.persistence.port import PersistencePort  # noqa: E402, F401
from moralstack.persistence.sink import (  # noqa: E402, F401
    persist_debug_event,
    persist_debug_events_batch,
    persist_decision_trace,
    persist_decision_traces_batch,
    persist_llm_call,
    persist_llm_calls_batch,
    persist_orchestration_event,
    persist_orchestration_events_batch,
)
from moralstack.persistence.write_queue import (  # noqa: E402, F401
    async_persist_debug_event,
    async_persist_decision_trace,
    async_persist_llm_call,
    get_write_queue,
)

__all__ = [
    "PersistencePort",
    "NullPersistence",
    "DefaultPersistence",
    "SqliteUnitOfWork",
    "get_db_path",
    "get_persist_mode",
    "get_ui_credentials",
    "set_current_run_id",
    "get_current_run_id",
    "set_current_request_id",
    "get_current_request_id",
    "set_current_cycle",
    "get_current_cycle",
    "init_db",
    "create_run",
    "end_run",
    "upsert_request",
    "get_llm_calls_for_request",
    "get_decision_traces_for_request",
    "get_orchestration_events_for_request",
    "get_debug_events_for_request",
    "get_run",
    "get_request",
    "get_requests_for_run",
    "get_all_runs",
    "delete_run",
    "delete_request",
    "invalidate_exports_cache",
    "update_request_response",
    "insert_llm_calls_batch",
    "insert_decision_traces_batch",
    "insert_orchestration_events_batch",
    "insert_debug_events_batch",
    "persist_llm_call",
    "persist_decision_trace",
    "persist_debug_event",
    "persist_llm_calls_batch",
    "persist_decision_traces_batch",
    "persist_debug_events_batch",
    "persist_orchestration_event",
    "persist_orchestration_events_batch",
    "PersistenceWriteQueue",
    "get_write_queue",
    "async_persist_llm_call",
    "async_persist_decision_trace",
    "async_persist_debug_event",
]
