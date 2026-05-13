"""
MoralStack Observability — unified telemetry module.

Provides:
  obs           — process-wide ObservabilityService singleton (emit, flush, read_store)
  EventEnvelope — typed event wrapper
  make_envelope — factory helper
  EVENT_*       — canonical event_type constants
  context       — run_id / request_id / cycle contextvars

Quick start:
    from moralstack.observability import obs, make_envelope, EVENT_LLM_CALL
    obs.emit(make_envelope(EVENT_LLM_CALL, run_id=..., request_id=..., payload={...}))
"""

from moralstack.observability.config import (
    get_db_path,
    get_jsonl_dir,
    get_observability_mode,
    get_persist_mode,
    get_ui_credentials,
)
from moralstack.observability.context import (
    get_current_cycle,
    get_current_request_id,
    get_current_run_id,
    set_current_cycle,
    set_current_request_id,
    set_current_run_id,
)
from moralstack.observability.events import (
    ALL_EVENT_TYPES,
    EVENT_CONVERSATION_STATE_UPDATED,
    EVENT_DEBUG_EVENT,
    EVENT_DECISION_TRACE,
    EVENT_LEDGER_LOOKUP,
    EVENT_LEDGER_STORE,
    EVENT_LLM_CALL,
    EVENT_ORCHESTRATION_EVENT,
    EVENT_PROXY_REQUEST_FINALIZED,
    EVENT_REQUEST_DOMAIN_UPDATED,
    EVENT_REQUEST_META_UPDATED,
    EVENT_REQUEST_RESPONSE_UPDATED,
    EVENT_REQUEST_UPSERTED,
    EVENT_RUN_ENDED,
    EVENT_RUN_STARTED,
    EVENT_SESSION_STORE_GET,
    EVENT_SESSION_STORE_PUT,
    EventEnvelope,
    make_envelope,
)
from moralstack.observability.read_store import ReadStore, SqliteReadStore
from moralstack.observability.service import ObservabilityService, get_obs
from moralstack.observability.sinks.sqlite_sink import (
    SqliteUnitOfWork,
    create_run,
    delete_request,
    delete_run,
    end_run,
    init_db,
    insert_conversation_state_event,
    insert_debug_events_batch,
    insert_decision_traces_batch,
    insert_ledger_event,
    insert_llm_calls_batch,
    insert_orchestration_events_batch,
    insert_proxy_request_event,
    insert_session_store_event,
    invalidate_exports_cache,
    update_request_domain,
    update_request_meta,
    update_request_response,
    upsert_request,
)

# Process-wide singleton — primary entry point
obs: ObservabilityService = get_obs()

__all__ = [
    # Singleton
    "obs",
    "get_obs",
    "ObservabilityService",
    # Config
    "get_db_path",
    "get_persist_mode",
    "get_observability_mode",
    "get_jsonl_dir",
    "get_ui_credentials",
    # Context
    "get_current_run_id",
    "set_current_run_id",
    "get_current_request_id",
    "set_current_request_id",
    "get_current_cycle",
    "set_current_cycle",
    # Events
    "EventEnvelope",
    "make_envelope",
    "ALL_EVENT_TYPES",
    "EVENT_RUN_STARTED",
    "EVENT_RUN_ENDED",
    "EVENT_REQUEST_UPSERTED",
    "EVENT_REQUEST_DOMAIN_UPDATED",
    "EVENT_REQUEST_RESPONSE_UPDATED",
    "EVENT_LLM_CALL",
    "EVENT_ORCHESTRATION_EVENT",
    "EVENT_DECISION_TRACE",
    "EVENT_DEBUG_EVENT",
    "EVENT_CONVERSATION_STATE_UPDATED",
    # Step 13 multi-turn observability
    "EVENT_REQUEST_META_UPDATED",
    "EVENT_LEDGER_LOOKUP",
    "EVENT_LEDGER_STORE",
    "EVENT_SESSION_STORE_GET",
    "EVENT_SESSION_STORE_PUT",
    "EVENT_PROXY_REQUEST_FINALIZED",
    # Read store
    "ReadStore",
    "SqliteReadStore",
    # SQLite helpers (exposed for compatibility)
    "SqliteUnitOfWork",
    "init_db",
    "create_run",
    "end_run",
    "upsert_request",
    "update_request_response",
    "update_request_domain",
    "delete_run",
    "delete_request",
    "invalidate_exports_cache",
    "insert_llm_calls_batch",
    "insert_decision_traces_batch",
    "insert_orchestration_events_batch",
    "insert_debug_events_batch",
    # Step 13 multi-turn observability writers
    "update_request_meta",
    "insert_conversation_state_event",
    "insert_ledger_event",
    "insert_session_store_event",
    "insert_proxy_request_event",
]
