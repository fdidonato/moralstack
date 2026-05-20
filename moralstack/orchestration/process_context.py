"""
Per-request call context for OrchestrationController.process().

This module holds mutable state that must not live on the controller instance,
because a single controller may process many requests concurrently (e.g. from
Starlette threadpool workers). A stack-local ProcessCallContext is created at
the start of each process() call and passed explicitly to helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from moralstack.compliance.types import ComplianceVerdict
from moralstack.orchestration.conversation_state import ConversationGovernanceState


@dataclass
class ProcessCallContext:
    """
    Per-call mutable context for OrchestrationController.process().

    Lifecycle: created at the top of process(), passed by reference to every
    helper that needs to read or extend it, garbage-collected when process()
    returns. Never store this on the controller instance.

    Replaces the previous self._conversation_process_ctx dict, which caused
    cross-request state leaks under concurrent execution.
    """

    conversation_id: str | None = None
    turn_index: int | None = None
    parent_request_id: str | None = None
    conversation_state: ConversationGovernanceState | None = None
    conversation_events_emitted: bool = False

    ledger_lookup: Any | None = None
    ledger_request_type: str | None = None
    ledger_intent_clarity: str | None = None
    ledger_hit_applied: bool = False

    # Reserved for future refresh signalling (previously read from ctx dict).
    refresh_required: bool | None = None
    refresh_reason: str | None = None

    compliance_verdict: ComplianceVerdict | None = None


__all__ = ["ProcessCallContext"]
