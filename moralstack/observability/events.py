"""
EventEnvelope and canonical event_type constants for MoralStack observability.

All telemetry passes through EventEnvelope. The 16 canonical event_types map
directly to the existing SQLite tables (no schema changes required).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Canonical event_type constants
# ---------------------------------------------------------------------------

# Lifecycle — runs
EVENT_RUN_STARTED = "run.started"
EVENT_RUN_ENDED = "run.ended"

# Lifecycle — requests
EVENT_REQUEST_UPSERTED = "request.upserted"
EVENT_REQUEST_DOMAIN_UPDATED = "request.domain_updated"
EVENT_REQUEST_RESPONSE_UPDATED = "request.response_updated"

# Deliberation telemetry
EVENT_LLM_CALL = "llm.call"
EVENT_ORCHESTRATION_EVENT = "orchestration.event"
EVENT_DECISION_TRACE = "decision.trace"
EVENT_DEBUG_EVENT = "debug.event"

# Multi-turn / conversation lifecycle
EVENT_CONVERSATION_STATE_UPDATED = "conversation.state_updated"

# Step 13 — multi-turn observability extensions
EVENT_REQUEST_META_UPDATED = "request.meta_updated"
EVENT_LEDGER_LOOKUP = "ledger.lookup"
EVENT_LEDGER_STORE = "ledger.store"
EVENT_SESSION_STORE_GET = "session_store.get"
EVENT_SESSION_STORE_PUT = "session_store.put"
EVENT_PROXY_REQUEST_FINALIZED = "proxy.request_finalized"

ALL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_RUN_STARTED,
        EVENT_RUN_ENDED,
        EVENT_REQUEST_UPSERTED,
        EVENT_REQUEST_DOMAIN_UPDATED,
        EVENT_REQUEST_RESPONSE_UPDATED,
        EVENT_LLM_CALL,
        EVENT_ORCHESTRATION_EVENT,
        EVENT_DECISION_TRACE,
        EVENT_DEBUG_EVENT,
        EVENT_CONVERSATION_STATE_UPDATED,
        # Step 13 multi-turn observability
        EVENT_REQUEST_META_UPDATED,
        EVENT_LEDGER_LOOKUP,
        EVENT_LEDGER_STORE,
        EVENT_SESSION_STORE_GET,
        EVENT_SESSION_STORE_PUT,
        EVENT_PROXY_REQUEST_FINALIZED,
    }
)


# ---------------------------------------------------------------------------
# EventEnvelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventEnvelope:
    """
    Typed wrapper for all MoralStack observability events.

    Multi-turn fields (session_id, turn_number, parent_event_id) map to the
    existing requests.conversation_id / requests.turn_index /
    requests.parent_request_id columns — no schema changes needed.
    """

    event_id: str
    event_type: str
    timestamp_ms: int
    run_id: str | None
    request_id: str | None
    cycle: int | None
    session_id: str | None = None  # -> requests.conversation_id
    turn_number: int | None = None  # -> requests.turn_index
    parent_event_id: str | None = None  # -> requests.parent_request_id
    audit_level: str = "turn"  # "turn" | "session" | "export"
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialisable dict (payload converted to plain dict)."""
        d = asdict(self)
        # asdict recurses into Mapping — but payload might already be dict
        return d


def make_envelope(
    event_type: str,
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    cycle: int | None = None,
    session_id: str | None = None,
    turn_number: int | None = None,
    parent_event_id: str | None = None,
    audit_level: str = "turn",
    payload: Mapping[str, Any] | None = None,
) -> EventEnvelope:
    """Factory for EventEnvelope, auto-populating event_id and timestamp_ms."""
    return EventEnvelope(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        timestamp_ms=int(time.time() * 1000),
        run_id=run_id,
        request_id=request_id,
        cycle=cycle,
        session_id=session_id,
        turn_number=turn_number,
        parent_event_id=parent_event_id,
        audit_level=audit_level,
        payload=payload or {},
    )
