"""
OrchestrationController: solo coordinamento alto livello.
process() governa il flusso in base a decision.path; nessuna logica interna complessa.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace as _dc_replace
from functools import partial
from typing import Any, cast

from moralstack.compliance.types import ComplianceVerdict
from moralstack.core.types import (
    CriticProtocol,
    HindsightProtocol,
    PerspectiveEnsembleProtocol,
    PolicyLLMProtocol,
    RiskEstimatorProtocol,
    SimulatorProtocol,
)
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.reason_codes import policy_reason_codes_to_reason_codes
from moralstack.models.risk import OperationalRisk, RiskCategory, RiskEstimation
from moralstack.observability.context import (
    set_current_session_id,
    set_current_turn_number,
)
from moralstack.observability.conversation_events import emit_conversation_state_updated
from moralstack.orchestration.conversation_state import ConversationGovernanceState, TurnDecisionSummary
from moralstack.orchestration.conversational_fast_path import ConversationalFastPathRunner
from moralstack.orchestration.decision_logger import log_decision_explanation
from moralstack.orchestration.decision_service import decide_action
from moralstack.orchestration.default_event_emitter import DefaultEventEmitter
from moralstack.orchestration.deliberation_override import evaluate_deliberation_override
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.diagnostics import (
    DiagnosticsLayer,
    log_deliberation_inconsistency,
    orch_debug_log,
)
from moralstack.orchestration.domain_exclusion import generate_domain_exclusion_response
from moralstack.orchestration.event_emitter import EventEmitter
from moralstack.orchestration.language_resolver import resolve_prompt_with_language
from moralstack.orchestration.ledger import CachedDecision, LedgerResult, SemanticDecisionLedger
from moralstack.orchestration.orchestration_event_taxonomy import (
    COMPLIANCE_LAYER_STARTED,
    COMPLIANCE_LAYER_VERDICT_MATCH,
    COMPLIANCE_LAYER_VERDICT_NO_CONTRACT,
    COMPLIANCE_LAYER_VERDICT_NO_MATCH,
    COMPLIANCE_LAYER_VERDICT_SAFETY_OVERRIDE,
    CONVERSATION_CONTEXT_ATTACHED,
    CONVERSATION_STATE_UPDATED,
    LEDGER_FAST_PATH_APPLIED,
    LEDGER_FAST_PATH_NOT_APPLIED,
    MODULE_DEFERRED_TO_COMPLIANCE,
    SPECULATIVE_STARTED,
)
from moralstack.orchestration.overlay_policy import (
    OVERLAY_SENSITIVE_RISK_FLOOR,
    apply_risk_floor_if_sensitive,
    get_constitution_safe,
    is_domain_excluded,
    is_overlay_sensitive,
)
from moralstack.orchestration.path_router import get_route, is_hard_signal_refuse
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.refusal_handler import RefusalHandler
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.safe_complete_gating import apply_safe_complete_gating
from moralstack.orchestration.speculative_overlap import SpeculativeOverlapHandle
from moralstack.orchestration.system_prompt_resolver import effective_system_for_request
from moralstack.orchestration.trace import Trace
from moralstack.orchestration.trace_lifecycle import (
    TraceLifecycle,
    fill_trace_from_result,
    log_trace_event,
)
from moralstack.orchestration.types import (
    ConstitutionStoreProtocol,
    ConvergenceOutcome,
    Decision,
    DeliberationDependencies,
    FailSafeException,
    FinalResponse,
    LoggerProtocol,
    MoralStackError,
    OrchestratorConfig,
    OrchestratorResult,
    OrchestratorTimeoutError,
    OutputProtectorProtocol,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
    RiskEstimationError,
    RiskEstimationProtocol,
    risk_category_str,
)
from moralstack.persistence.null import NullPersistence
from moralstack.persistence.port import PersistencePort
from moralstack.runtime.trace.decision_trace import DecisionTrace, append_decision_trace, normalize_trace_fields
from moralstack.sdk.session_store import SessionStoreProtocol

_LOG = logging.getLogger(__name__)


def _as_risk_protocol(r: RiskEstimation) -> RiskEstimationProtocol:
    """Cast RiskEstimation to RiskEstimationProtocol (structural match at runtime)."""
    return cast(RiskEstimationProtocol, r)


def _normalize_runtime_domain(domain: str | None) -> str | None:
    """
    Normalize a runtime domain value before propagating it as request domain_overlay.

    `core` is a retrieval-only pseudo-domain (constitutional baseline) and must
    never become a runtime/applicative domain. Returns None for empty/whitespace
    values or for "core".
    """
    if not domain:
        return None
    value = str(domain).strip()
    if not value or value == "core":
        return None
    return value


class OrchestrationController:
    """
    Coordinamento alto livello: stima rischio, decide path, delega a runner/assembler/diagnostics.
    Path governa il flusso; nessun early return aggiuntivo.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        policy: PolicyLLMProtocol | None,
        risk_estimator: RiskEstimatorProtocol | None,
        critic: CriticProtocol | None,
        simulator: SimulatorProtocol | None,
        hindsight: HindsightProtocol | None,
        perspectives: PerspectiveEnsembleProtocol | None,
        constitution_store: ConstitutionStoreProtocol | None,
        output_protector: OutputProtectorProtocol,
        protected_system_prompt: str,
        logger: LoggerProtocol | None = None,
        persistence: PersistencePort | None = None,
        event_emitter: EventEmitter | None = None,
        *,
        ledger: SemanticDecisionLedger | None = None,
        session_store: SessionStoreProtocol | None = None,
    ) -> None:
        self.config = config
        self._persistence = persistence if persistence is not None else NullPersistence()
        self._events: EventEmitter = event_emitter if event_emitter is not None else DefaultEventEmitter()
        self.policy = policy
        self.risk_estimator = risk_estimator
        self.critic = critic
        self.simulator = simulator
        self.hindsight = hindsight
        self.perspectives = perspectives
        self.constitution_store = constitution_store
        self._output_protector = output_protector
        self._protected_system_prompt = protected_system_prompt
        self.logger = logger

        self.assembler = ResponseAssembler(policy)
        self._trace_lock = threading.Lock()
        self.execution_trace: dict[str, dict[str, Any]] = {}
        self._diagnostics = DiagnosticsLayer(self._trace_lock, self.execution_trace)
        deps = DeliberationDependencies(
            policy=policy,
            critic=critic,
            simulator=simulator,
            hindsight=hindsight,
            perspectives=perspectives,
            constitution_store=constitution_store,
            output_protector=output_protector,
        )
        self._runner = DeliberationRunner(
            config=config,
            deps=deps,
            protected_system_prompt=protected_system_prompt,
            logger=logger,
            assembler=self.assembler,
        )
        self._parser_diagnostic_handlers: dict[str, logging.Handler] = {}
        self._trace_lifecycle = TraceLifecycle(
            self._trace_lock,
            self.execution_trace,
            self._parser_diagnostic_handlers,
        )
        self._refusal_handler = RefusalHandler(
            policy=policy,
            constitution_store=constitution_store,
            event_emitter=self._events,
        )
        self._ledger: SemanticDecisionLedger | None = ledger
        self._session_store: SessionStoreProtocol | None = session_store
        self._fast_path_runner = ConversationalFastPathRunner()

    def set_logger(self, logger: LoggerProtocol | None) -> None:
        """Set the logger for tracking LLM calls (propagated to runner)."""
        self.logger = logger
        self._runner.logger = logger

    def _lookup_cached_decision(
        self,
        *,
        prompt: str,
        contract_hash: str,
        posture: str,
        domain: str | None,
        intent_clarity: str,
        request_type: str,
        turn_index: int,
    ) -> LedgerResult | None:
        """
        Consult the SemanticDecisionLedger for a cached decision matching the current
        (prompt, contract, posture, domain, intent_clarity, request_type, turn_index)
        context.

        Returns:
            LedgerResult when the ledger is configured, None otherwise.
            The caller decides what to do with hits/misses; in Step 6 the result is
            recorded for observability only (no routing change).
        """
        if self._ledger is None:
            return None
        return self._ledger.lookup(
            prompt=prompt,
            contract_hash=contract_hash,
            posture=posture,
            domain=domain,
            intent_clarity=intent_clarity,
            request_type=request_type,
            turn_index=turn_index,
        )

    def _store_decision_in_ledger(
        self,
        *,
        prompt: str,
        contract_hash: str,
        posture: str,
        domain: str | None,
        decision: Decision,
        risk_score: float,
        request_type: str,
        turn_index: int,
    ) -> bool:
        """
        Persist the produced decision into the SemanticDecisionLedger for future reuse.

        Returns:
            True when the entry was stored; False when the ledger is None or its
            internal skip rules tripped (e.g. turn_index < 1, posture ESCALATED).
        """
        if self._ledger is None:
            return False
        cached = CachedDecision(
            final_action=decision.final_action,
            risk_score=risk_score,
            governance_posture=posture,
            winning_rule=getattr(decision, "path", "") or "",
            decision_reason=", ".join(getattr(decision, "reason_codes", []) or []),
            reason_codes=tuple(getattr(decision, "reason_codes", []) or []),
            triggered_principles=tuple(getattr(decision, "triggered_principles", []) or []),
        )
        return self._ledger.store(
            prompt=prompt,
            contract_hash=contract_hash,
            posture=posture,
            domain=domain,
            decision=cached,
            intent_clarity=getattr(decision, "intent_clarity", "HIGH"),
            request_type=request_type,
            turn_index=turn_index,
        )

    def _compute_governance_posture(
        self,
        *,
        decision: Decision,
        overlay_sensitive: bool,
        hard_signal_refuse: bool,
    ) -> str:
        """
        Map the runtime signals to the governance posture used as ledger key component.

        Returns:
            'ESCALATED' when the request triggered a hard refusal signal.
            'ELEVATED' when the domain overlay is sensitive (but no hard signal).
            'NORMAL' otherwise.

        Reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §5.4.
        """
        if hard_signal_refuse and decision.final_action == "REFUSE":
            return "ESCALATED"
        if overlay_sensitive:
            return "ELEVATED"
        return "NORMAL"

    def _attach_trace_and_return(
        self,
        result: OrchestratorResult,
        request: ProcessedRequest,
        call_ctx: ProcessCallContext,
    ) -> OrchestratorResult:
        out = self._diagnostics.attach_trace_and_return(result, request, self.execution_trace)
        self._apply_conversation_metadata_to_result(out, request, call_ctx)
        return out

    def _apply_conversation_metadata_to_result(
        self,
        result: OrchestratorResult,
        request: ProcessedRequest,
        call_ctx: ProcessCallContext,
    ) -> None:
        """Stamp optional conversation linkage and updated governance state (no routing impact).

        Step 13: when the caller provides a ``conversation_id`` (even without
        an inbound ``state_in`` — e.g. turn 0 of a brand-new conversation),
        the controller now produces a ``conversation_governance_state_out``
        seeded from an empty :class:`ConversationGovernanceState`. This is a
        pure observability widening: it ensures the canonical
        ``conversation.state_updated`` envelope is emitted for every turn of
        a tracked conversation, so the audit timeline never skips turn 0.
        ``conversation_state_provided`` still reflects the input contract
        accurately (True only when a real state_in was supplied).
        """
        cid = call_ctx.conversation_id
        tid = call_ctx.turn_index
        pid = call_ctx.parent_request_id
        state_in = call_ctx.conversation_state
        if cid is not None:
            result.conversation_id = cid
        if tid is not None:
            result.turn_index = tid
        if pid is not None:
            result.parent_request_id = pid
        if call_ctx.compliance_verdict is not None:
            result.compliance_verdict = call_ctx.compliance_verdict
        should_build_state_out = state_in is not None or cid is not None
        if should_build_state_out:
            result.conversation_state_provided = state_in is not None
            base = state_in if isinstance(state_in, ConversationGovernanceState) else ConversationGovernanceState()
            merged = base.with_turn_metadata(
                conversation_id=cid if cid is not None else base.conversation_id,
                turn_index=tid if tid is not None else base.turn_index,
            )
            dom = request.get_domain() if hasattr(request, "get_domain") else None
            updated = merged.update_from_processing_result(
                request_id=getattr(request, "request_id", "") or "",
                domain=dom,
            )
            # --- v0.4 multi-turn: extend state_out with new governance fields ---
            updated = self._extend_state_out_v04(
                state=updated,
                request=request,
                result=result,
                call_ctx=call_ctx,
            )
            # --- end v0.4 multi-turn ---
            result.conversation_governance_state_out = updated
            result.conversation_state_updated = True
        if call_ctx.conversation_events_emitted:
            return
        call_ctx.conversation_events_emitted = True
        has_link = cid is not None or tid is not None or pid is not None or state_in is not None
        if has_link:
            self._events.emit_orchestration_event(
                cycle=0,
                stage="orchestration",
                component="conversation",
                event_type=CONVERSATION_CONTEXT_ATTACHED,
                decision="attached",
                status="ok",
                sequence=0,
                payload={
                    "conversation_id": cid,
                    "turn_index": tid,
                    "parent_request_id": pid,
                    "conversation_state_provided": state_in is not None,
                },
            )
        if result.conversation_state_updated and result.conversation_governance_state_out is not None:
            self._events.emit_orchestration_event(
                cycle=0,
                stage="orchestration",
                component="conversation",
                event_type=CONVERSATION_STATE_UPDATED,
                decision="updated",
                status="ok",
                sequence=1,
                payload={
                    "summary": result.conversation_governance_state_out.to_summary_dict(),
                },
            )
            # Step 13 — also emit the canonical conversation.state_updated event so
            # JSONL captures the full snapshot and SQLite persists a queryable row
            # in conversation_states. The orchestration_event above is kept for
            # backward compatibility (it carries only a summary payload).
            self._emit_canonical_conversation_state_updated(result=result, request=request, call_ctx=call_ctx)
        # --- v0.4 multi-turn: persist decision into the ledger for next turn ---
        # Done at the very end so any exception during routing already aborted the flow.
        self._maybe_store_in_ledger(request=request, result=result, call_ctx=call_ctx)
        # --- end v0.4 multi-turn ---

    def _emit_canonical_conversation_state_updated(
        self,
        *,
        result: OrchestratorResult,
        request: ProcessedRequest,
        call_ctx: ProcessCallContext,
    ) -> None:
        """
        Emit the canonical ``conversation.state_updated`` event so JSONL and
        SQLite capture the full state transition for the current turn.

        Step 13 multi-turn observability: complements the orchestration_event
        emitted above (which only carries a summary), giving us a queryable
        ``conversation_states`` row plus a complete JSONL envelope. Best-effort:
        any failure is debug-logged and never breaks the response flow.
        """
        try:
            state_out = result.conversation_governance_state_out
            state_in = call_ctx.conversation_state
            cid = call_ctx.conversation_id
            tid = call_ctx.turn_index
            request_id = getattr(request, "request_id", "") or ""

            # Risk score and final action from the produced metadata.
            metadata = getattr(getattr(result, "response", None), "metadata", None)
            final_action = getattr(metadata, "final_action", None) if metadata is not None else None
            risk_score: float | None = None
            if metadata is not None:
                rs = getattr(metadata, "risk_score", None)
                try:
                    risk_score = float(rs) if rs is not None else None
                except (ValueError, TypeError):
                    risk_score = None

            posture = state_out.last_governance_posture if isinstance(state_out, ConversationGovernanceState) else None

            ledger_lookup = call_ctx.ledger_lookup
            was_cached = bool(call_ctx.ledger_hit_applied)
            cached_from_turn: int | None = None
            if ledger_lookup is not None and getattr(ledger_lookup, "is_hit", False):
                cached_from_turn = getattr(ledger_lookup, "from_turn", None)

            refresh_required = call_ctx.refresh_required
            refresh_reason = call_ctx.refresh_reason

            emit_conversation_state_updated(
                run_id=None,  # falls back to current run_id from context
                request_id=request_id,
                conversation_id=cid if isinstance(cid, str) and cid else None,
                turn_index=tid if isinstance(tid, int) else None,
                state_in=state_in,
                state_out=state_out,
                final_action=final_action,
                risk_score=risk_score,
                posture=posture,
                was_cached=was_cached if was_cached else None,
                cached_from_turn=cached_from_turn,
                refresh_required=refresh_required if isinstance(refresh_required, bool) else None,
                refresh_reason=refresh_reason if isinstance(refresh_reason, str) and refresh_reason else None,
            )
        except Exception:
            _LOG.debug("_emit_canonical_conversation_state_updated failed", exc_info=True)

    def _extend_state_out_v04(
        self,
        *,
        state: ConversationGovernanceState,
        request: ProcessedRequest,
        result: OrchestratorResult,
        call_ctx: ProcessCallContext,
    ) -> ConversationGovernanceState:
        """
        Extend the outbound governance state with v0.4 multi-turn fields:
        - last_developer_contract_hash (from request.developer_contract)
        - last_governance_posture (derived from the produced decision)
        - turn_decisions_summary (append a TurnDecisionSummary for this turn)
        """
        was_cached = bool(call_ctx.ledger_hit_applied)
        contract_hash = ""
        developer_contract = getattr(request, "developer_contract", None)
        if developer_contract is not None:
            contract_hash = getattr(developer_contract, "contract_hash", "") or ""

        final_action = ""
        risk_score = 0.0
        metadata = getattr(getattr(result, "response", None), "metadata", None)
        if metadata is not None:
            final_action = getattr(metadata, "final_action", "") or ""
            risk_score = float(getattr(metadata, "risk_score", 0.0) or 0.0)

        # Step 14.8: derive posture from the SAME signal the lookup uses, namely
        # `is_overlay_sensitive(constitution_store, domain)`. Previously this
        # branch read `state.active_overlay`, but that field is never populated
        # by the controller (update_from_processing_result is called without
        # `overlay=`), so the elif branch was always False and the store wrote
        # posture="NORMAL" even for sensitive overlays — while the lookup,
        # using _compute_governance_posture with the correct overlay_sensitive
        # flag, wrote posture="ELEVATED". The asymmetry produced a different
        # LedgerKey for store and lookup, making cache hits structurally
        # impossible on any sensitive overlay (legal, medical, mental_health,
        # journalism, financial, healthcare, emergency, cybersecurity,
        # children, political, environment).
        posture = "NORMAL"
        if final_action == "REFUSE" and len(state.last_hard_constraints_triggered) > 0:
            posture = "ESCALATED"
        else:
            domain = request.get_domain() if hasattr(request, "get_domain") else None
            if is_overlay_sensitive(self.constitution_store, domain):
                posture = "ELEVATED"

        # Append a TurnDecisionSummary for the current turn.
        winning_rule = ""
        if metadata is not None:
            winning_rule = str(getattr(metadata, "decision_path", None) or getattr(metadata, "path", None) or "").strip()
        turn_idx_for_summary = state.turn_index if isinstance(state.turn_index, int) else 0
        new_summary = TurnDecisionSummary(
            turn_index=turn_idx_for_summary,
            final_action=final_action or "NORMAL_COMPLETE",
            risk_score=risk_score,
            winning_rule=winning_rule,
            was_cached=was_cached,
        )

        return _dc_replace(
            state,
            last_developer_contract_hash=contract_hash or state.last_developer_contract_hash,
            last_governance_posture=posture,
            turn_decisions_summary=state.turn_decisions_summary + (new_summary,),
        )

    def _maybe_store_in_ledger(
        self,
        *,
        request: ProcessedRequest,
        result: OrchestratorResult,
        call_ctx: ProcessCallContext,
    ) -> None:
        """
        Persist the produced decision into the ledger when the runtime has one
        configured and the conversation context permits caching. No-op when the
        ledger is None.

        The ledger's internal skip rules handle the rest (turn_index < 1, ESCALATED).
        """
        if self._ledger is None:
            return
        if call_ctx.conversation_id is None:
            return
        turn_index = call_ctx.turn_index
        if not isinstance(turn_index, int):
            return

        contract_hash = ""
        developer_contract = getattr(request, "developer_contract", None)
        if developer_contract is not None:
            contract_hash = getattr(developer_contract, "contract_hash", "") or ""

        state_out = getattr(result, "conversation_governance_state_out", None)
        posture = "NORMAL"
        if isinstance(state_out, ConversationGovernanceState):
            posture = state_out.last_governance_posture or "NORMAL"

        metadata = getattr(getattr(result, "response", None), "metadata", None)
        final_action = ""
        risk_score = 0.0
        decision_path = ""
        if metadata is not None:
            final_action = getattr(metadata, "final_action", "") or ""
            risk_score = float(getattr(metadata, "risk_score", 0.0) or 0.0)
            decision_path = str(getattr(metadata, "decision_path", None) or getattr(metadata, "path", None) or "").strip()

        if not final_action:
            # No decision produced (early failure); skip.
            return

        # Build a CachedDecision directly (we don't have the Decision object here, just metadata).
        decision_reason = ""
        if metadata is not None:
            decision_reason = str(
                getattr(metadata, "winning_decision_reason", None) or getattr(metadata, "decision_reason", None) or ""
            ).strip()
        reason_codes: tuple[str, ...] = ()
        triggered: tuple[str, ...] = ()
        if metadata is not None:
            rc = getattr(metadata, "reason_codes", None)
            if rc is not None:
                reason_codes = tuple(rc)
            tp = getattr(metadata, "triggered_principles", None)
            if tp is not None:
                triggered = tuple(tp)

        cached = CachedDecision(
            final_action=final_action,
            risk_score=risk_score,
            governance_posture=posture,
            winning_rule=decision_path,
            decision_reason=decision_reason,
            reason_codes=reason_codes,
            triggered_principles=triggered,
        )

        domain = request.get_domain() if hasattr(request, "get_domain") else None
        # Step 14.3: prefer values captured at lookup time (stored in
        # ProcessCallContext by the lookup block in process()). This
        # guarantees the store writes the SAME intent fields the lookup used,
        # so the secondary intent check at the next turn doesn't reject the
        # cache hit with reason='intent_divergence'. Fallback to metadata
        # only when the ctx is missing the value (e.g. ledger disabled at
        # lookup but enabled at store, which shouldn't happen but is safe to
        # handle).
        intent_clarity = "HIGH"
        request_type = ""
        ctx_clarity = call_ctx.ledger_intent_clarity
        ctx_request_type = call_ctx.ledger_request_type
        if isinstance(ctx_clarity, str) and ctx_clarity:
            intent_clarity = ctx_clarity
        if isinstance(ctx_request_type, str):
            request_type = ctx_request_type
        # Fallback to metadata when ctx didn't carry the lookup-time values.
        # NB: ResponseMetadata has no `request_type` field today — this branch
        # exists only for forward compatibility / safety.
        if not intent_clarity or intent_clarity == "HIGH":
            if metadata is not None:
                meta_clarity = getattr(metadata, "intent_clarity", None)
                if isinstance(meta_clarity, str) and meta_clarity:
                    intent_clarity = meta_clarity
        if not request_type and metadata is not None:
            meta_request_type = getattr(metadata, "request_type", None)
            if isinstance(meta_request_type, str):
                request_type = meta_request_type

        try:
            self._ledger.store(
                prompt=request.prompt,
                contract_hash=contract_hash,
                posture=posture,
                domain=domain,
                decision=cached,
                intent_clarity=intent_clarity,
                request_type=request_type,
                turn_index=turn_index,
            )
        except Exception as e:
            # The ledger is best-effort; a store failure must never break the response flow.
            orch_debug_log(
                "orchestrator.py:_maybe_store_in_ledger",
                "ledger store failed (non-fatal)",
                {"error": str(e)},
                "H-ledger-store-fail",
                request_id=getattr(request, "request_id", "") or "",
            )

    def _emit_risk_assessment_trace(
        self,
        request_id: str,
        risk_proto: RiskEstimationProtocol,
        risk_score: float,
    ) -> None:
        """
        Observability-only: persist RISK_ASSESSMENT decision trace after calibration (no routing impact).
        """
        try:
            rc = getattr(risk_proto, "risk_category", None)
            rc_str = getattr(rc, "value", str(rc or "")).strip().lower() if rc is not None else ""
            op = getattr(risk_proto, "operational_risk", None)
            op_str = str(getattr(op, "value", op) or "").strip()
            rpa = getattr(risk_proto, "risk_policy_action", None)
            rpa_str = str(getattr(rpa, "value", rpa) or "").strip()
            sigs = list(getattr(risk_proto, "semantic_signals", None) or [])
            dom = getattr(risk_proto, "detected_domain", None)
            dom_str = (str(dom).strip() if dom is not None else "") or ""
            raw_domains = list(getattr(risk_proto, "detected_domains_raw", None) or [])
            selection_reason = str(getattr(risk_proto, "domain_selection_reason", "") or "").strip()
            em = str(getattr(risk_proto, "estimation_mode", "") or "").strip()
            dt = DecisionTrace(
                request_id=request_id,
                stage="RISK_ASSESSMENT",
                sequence=-10,
                risk_score=float(risk_score),
                risk_category=rc_str,
                operational_risk=op_str,
                intent_to_harm=bool(getattr(risk_proto, "intent_to_harm", False)),
                requested_instructions=bool(getattr(risk_proto, "requested_instructions", False)),
                intent_operational=bool(getattr(risk_proto, "intent_operational", False)),
                estimation_mode=em,
            )
            dt.stage_payload = {
                "risk_policy_action": rpa_str,
                "detected_domain": dom_str or None,
                "detected_domains_raw": raw_domains,
                "selected_domain": dom_str or None,
                "selection_reason": selection_reason or None,
                "activated_signals": sigs,
                # Semantic intent flags (LLM-judged, language-agnostic).
                # Surfaced for full traceability in DB + UI; downstream
                # consumers (e.g. decision_service) may use them but absence
                # must not break parsing — defaults are False.
                "stated_personal_bias": bool(getattr(risk_proto, "stated_personal_bias", False)),
                "seeks_norm_circumvention": bool(getattr(risk_proto, "seeks_norm_circumvention", False)),
                "q13_protected_class_targeting": bool(getattr(risk_proto, "q13_protected_class_targeting", False)),
            }
            normalize_trace_fields(dt)
            append_decision_trace(dt)
        except Exception:
            _LOG.debug("emit RISK_ASSESSMENT trace failed", exc_info=True)

    def _emit_deliberation_aggregate_trace(
        self,
        *,
        request_id: str,
        state: Any,
        outcome: Any,
        risk_score: float,
    ) -> None:
        """Emit a DELIBERATION_AGGREGATE decision trace with full
        deliberation summary data (perspectives, convergence, sim metrics).
        Complements the FINAL trace for file_only audit completeness."""
        try:
            perspectives = getattr(state, "perspectives", None) or []
            pw_approval: float | None = None
            if perspectives:
                ap = [float(getattr(p, "approval_score", 0.0) or 0.0) for p in perspectives]
                pw_approval = sum(ap) / max(len(ap), 1)

            sem_harm: float | None = None
            sims = getattr(state, "simulations", None) or []
            if sims:
                last_sim = sims[-1]
                sem_harm = float(getattr(last_sim, "semantic_expected_harm", 0.0) or 0.0)

            lc = getattr(state, "last_critique", None)
            violations_count = 0
            critic_decision = ""
            if lc is not None:
                viol = getattr(lc, "violations", None) or []
                violations_count = len(viol)
                critic_decision = (getattr(lc, "decision", "") or "").strip().upper()

            hindsight_score: float | None = None
            hs = getattr(state, "hindsight", None)
            if hs is not None:
                hindsight_score = float(getattr(hs, "score", 0.0) or 0.0)

            conv_snap = getattr(state, "_convergence_evaluation_snapshot", None)
            if not isinstance(conv_snap, dict):
                conv_snap = {}

            payload = {
                "total_cycles": getattr(state, "cycle", 0),
                "converged": getattr(outcome, "converged", False) if outcome else False,
                "convergence_reason": getattr(outcome, "stop_reason", "") if outcome else "",
                "convergence_reason_codes": list(conv_snap.get("convergence_reason_codes") or []),
                "perspectives_count": len(perspectives),
                "perspectives_weighted_approval": pw_approval,
                "semantic_expected_harm": sem_harm,
                "critic_decision": critic_decision,
                "critic_violations_count": violations_count,
                "hindsight_score": hindsight_score,
                "early_convergence_considered": conv_snap.get("early_convergence_considered"),
                "early_convergence_accepted": conv_snap.get("early_convergence_accepted"),
            }
            dt = DecisionTrace(
                request_id=request_id,
                stage="DELIBERATION_AGGREGATE",
                sequence=3,
                risk_score=risk_score,
            )
            dt.stage_payload = payload
            normalize_trace_fields(dt)
            append_decision_trace(dt)
        except Exception:
            _LOG.debug("emit DELIBERATION_AGGREGATE trace failed", exc_info=True)

    def _estimate_risk(self, request: ProcessedRequest) -> RiskEstimation:
        if self.risk_estimator is None:
            return RiskEstimation(score=0.5, confidence=0.5, risk_category=RiskCategory.SENSITIVE)
        try:
            start = time.time()

            # Pass developer contract (system prompt) and conversation history so the
            # risk estimator can evaluate the request in its full conversational context.
            # Without this, payloads whose meaning depends on the system prompt (e.g.
            # an authentication token expected by the deployer) are mis-classified as
            # encoded/obfuscated and refused. See compl-ai llm_rules-benign Q74.
            contract_text: str | None = None
            dc = getattr(request, "developer_contract", None)
            if dc is not None:
                contract_text = getattr(dc, "raw_text", None)

            history_dicts: list[dict[str, str]] | None = None
            history_turns = getattr(request, "conversation_history", None) or []
            if history_turns:
                history_dicts = [
                    {
                        "role": getattr(t, "role", "") or "",
                        "content": getattr(t, "content", "") or "",
                    }
                    for t in history_turns
                ]

            # The protocol exposes only `estimate(prompt)`. Concrete implementations
            # accept optional kwargs; pass them only when meaningful so the byte-equivalent
            # single-turn fast path is preserved.
            try:
                result = self.risk_estimator.estimate(  # type: ignore[call-arg]
                    request.prompt,
                    developer_contract_text=contract_text,
                    conversation_history=history_dicts,
                )
            except TypeError:
                # Defensive fallback: an estimator implementation that does not yet
                # accept the new kwargs (e.g. a test double) still works.
                result = self.risk_estimator.estimate(request.prompt)

            elapsed = (time.time() - start) * 1000
            if self.logger and hasattr(self.logger, "log_call"):
                response_text = (
                    f"Risk: {result.score:.2f}, Category: {result.risk_category.value}"
                    if hasattr(result, "score")
                    else str(result)
                )
                self.logger.log_call(
                    module="risk_estimator",
                    action="estimate",
                    prompt=request.prompt,
                    response=response_text,
                    duration_ms=elapsed,
                )
            return cast(RiskEstimation, result)
        except Exception as e:
            raise RiskEstimationError(f"Risk estimation failed: {e}")

    def _speculative_generate(
        self,
        request: ProcessedRequest,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Generate a speculative draft in parallel with risk estimation.

        Uses the base system prompt and fallback language detection (the risk
        estimator's ``detected_language`` is not available yet).

        Returns ``(draft_text_or_None, persist_kwargs_or_None)``. Persistence is deferred
        until join/abandon so ``call_outcome`` (used/discarded) can be set correctly.
        """
        if self.policy is None:
            return None, None
        try:
            prompt_text = resolve_prompt_with_language(
                request.prompt,
                "",
                request.prompt,
            )
            start = time.time()
            try:
                result = self.policy.generate(
                    prompt=prompt_text,
                    system=effective_system_for_request(base=self._protected_system_prompt, request=request, mode="normal"),
                )
            except TypeError:
                result = self.policy.generate(prompt_text)
            elapsed = (time.time() - start) * 1000
            response_text = getattr(result, "text", None) or str(result)
            protection = self._output_protector.validate(response_text)
            prompt_used = getattr(result, "prompt_used", None) or prompt_text
            system_used = getattr(result, "system_used", None) or effective_system_for_request(
                base=self._protected_system_prompt, request=request, mode="normal"
            )
            policy_model = getattr(self.policy, "model", None)
            policy_model_str = str(policy_model) if policy_model is not None else None
            persist_kwargs: dict[str, Any] = {
                "cycle": 0,
                "phase": "speculative_generate",
                "module": "policy",
                "action": "generate (speculative)",
                "model": policy_model_str,
                "started_at": int(start * 1000),
                "duration_ms": elapsed,
                "prompt": prompt_used,
                "system_prompt": system_used or "",
                "raw_response": response_text,
                "sequence_in_cycle": 0,
                "call_kind": "speculative",
            }
            return protection.cleaned, persist_kwargs
        except Exception as e:
            _LOG.warning(
                "Speculative generation failed, will regenerate: %s",
                e,
            )
            return None, None

    def _run_speculative_overlap(
        self,
        request: ProcessedRequest,
    ) -> SpeculativeOverlapHandle:
        """Run risk estimation and speculative draft generation in parallel.

        Waits only for risk estimation before returning. Speculative work continues
        in the background; join via ``SpeculativeOverlapHandle.join_for_consumer`` or
        ``abandon`` when the route does not consume the draft.

        If risk estimation raises, the exception propagates after shutting down the executor.

        Uses ``contextvars.copy_context()`` so that persistence context
        (run_id, request_id, cycle) is available inside the worker threads.
        """
        import contextvars
        from concurrent.futures import ThreadPoolExecutor

        ctx_risk = contextvars.copy_context()
        ctx_spec = contextvars.copy_context()

        executor = ThreadPoolExecutor(max_workers=2)
        spec_started_at_ms = int(time.time() * 1000)
        risk_fut = executor.submit(ctx_risk.run, self._estimate_risk, request)
        spec_fut = executor.submit(
            ctx_spec.run,
            self._speculative_generate,
            request,
        )
        try:
            self._events.emit_orchestration_event(
                stage="orchestration",
                component="speculative",
                event_type=SPECULATIVE_STARTED,
                decision="started",
                status="ok",
                started_at=spec_started_at_ms,
                payload={
                    "speculative_mode": True,
                    "model": str(getattr(self.policy, "model", "") or ""),
                },
            )
        except Exception:
            _LOG.debug("emit SPECULATIVE_STARTED failed", exc_info=True)
        try:
            risk_estimation = risk_fut.result()
        except Exception:
            try:
                executor.shutdown(wait=False)
            except Exception:
                pass
            raise
        return SpeculativeOverlapHandle(
            risk_estimation=risk_estimation,
            spec_future=spec_fut,
            executor=executor,
            spec_started_at_ms=spec_started_at_ms,
            event_emitter=self._events,
        )

    @staticmethod
    def _nonblocking_speculative_draft(spec_handle: SpeculativeOverlapHandle | None) -> str:
        """Return speculative draft text only if the background future already completed."""
        if spec_handle is None:
            return ""
        spec_future = spec_handle._spec_future  # noqa: SLF001 — orchestration-internal join handle
        if not spec_future.done():
            return ""
        try:
            draft, _meta = spec_future.result(timeout=0)
        except Exception:
            return ""
        return draft or ""

    def _run_dccl_evaluation(
        self,
        request: ProcessedRequest,
        speculative_draft: str,
        call_ctx: ProcessCallContext,
    ) -> None:
        """
        Invoke DCCL after speculative overlap risk wait.

        Verdict is stored on ``call_ctx.compliance_verdict``. When decision is MATCH
        with a validated speculative draft, ``process()`` routes to the compliance
        fast-path (Commit 3); otherwise the standard pipeline continues unchanged.
        """
        compliance_verdict = None
        try:
            from moralstack.compliance.dccl import DeveloperContractComplianceLayer
            from moralstack.compliance.types import ComplianceDecision
            from moralstack.persistence.sink import persist_orchestration_event

            compliance_layer = DeveloperContractComplianceLayer(policy=self.policy)

            persist_orchestration_event(
                request_id=request.request_id,
                stage="compliance_layer",
                component="dccl",
                event_type=COMPLIANCE_LAYER_STARTED,
                decision="started",
                status="ok",
                payload={
                    "has_contract": getattr(request, "developer_contract", None) is not None,
                    "has_structured_rules": bool(
                        getattr(getattr(request, "developer_contract", None), "structured_rules", ()) or ()
                    ),
                    "evaluation_path_preference": compliance_layer._evaluation_path,
                },
            )

            compliance_verdict = compliance_layer.evaluate(
                request=request,
                speculative_draft=speculative_draft or "",
                risk_estimation=None,
            )

            event_map = {
                ComplianceDecision.MATCH: COMPLIANCE_LAYER_VERDICT_MATCH,
                ComplianceDecision.NO_MATCH: COMPLIANCE_LAYER_VERDICT_NO_MATCH,
                ComplianceDecision.SAFETY_OVERRIDE: COMPLIANCE_LAYER_VERDICT_SAFETY_OVERRIDE,
                ComplianceDecision.NO_CONTRACT: COMPLIANCE_LAYER_VERDICT_NO_CONTRACT,
            }
            event_type = event_map.get(compliance_verdict.decision, COMPLIANCE_LAYER_VERDICT_NO_MATCH)

            persist_orchestration_event(
                request_id=request.request_id,
                stage="compliance_layer",
                component="dccl",
                event_type=event_type,
                decision=compliance_verdict.decision.value,
                status="ok",
                duration_ms=compliance_verdict.duration_ms,
                payload={
                    "matched_rule_id": (
                        compliance_verdict.matched_rule.rule_id if compliance_verdict.matched_rule else None
                    ),
                    "matched_rule_summary": (
                        compliance_verdict.matched_rule.rule_summary if compliance_verdict.matched_rule else None
                    ),
                    "safety_override_reason": compliance_verdict.safety_override_reason,
                    "confidence": compliance_verdict.confidence,
                    "evaluation_path": compliance_verdict.evaluation_path.value,
                    "speculative_draft_validated": compliance_verdict.speculative_draft_validated,
                    "rationale_excerpt": (compliance_verdict.rationale[:300] if compliance_verdict.rationale else ""),
                    "contract_hash": compliance_verdict.contract_hash,
                },
            )
        except Exception as e:
            _LOG.warning("DCCL evaluation failed (non-fatal): %s", e, exc_info=True)
            compliance_verdict = None

        call_ctx.compliance_verdict = compliance_verdict

    def _route_compliance_match(
        self,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol,
        speculative_draft: str,
        start_time: float,
        trace: Trace,
        call_ctx: ProcessCallContext,
        spec_handle: SpeculativeOverlapHandle | None,
    ) -> OrchestratorResult:
        """
        Compliance fast-path: DCCL recognized deployer-authorized rule execution.

        Produces NORMAL_COMPLETE from the validated speculative draft, skipping risk
        routing, deliberation, critic, simulator, and perspectives. Emits
        MODULE_DEFERRED_TO_COMPLIANCE for each skipped module (audit).
        """

        cv = call_ctx.compliance_verdict
        matched_rule_id = cv.matched_rule.rule_id if (cv and cv.matched_rule) else None

        orch_debug_log(
            "controller.py:_route_compliance_match",
            "entering compliance fast-path (DCCL MATCH)",
            {
                "matched_rule_id": matched_rule_id,
                "evaluation_path": cv.evaluation_path.value if cv else None,
                "confidence": cv.confidence if cv else None,
            },
            "H-compliance-match",
            request_id=request.request_id or "",
        )

        if spec_handle is not None:
            try:
                spec_handle.abandon("compliance_match", "COMPLIANCE_MATCH")
            except Exception:
                _LOG.debug("spec_handle.abandon failed in compliance fast-path", exc_info=True)

        for module_name in ("risk_router", "critic", "simulator", "perspectives", "deliberation"):
            try:
                self._events.emit_orchestration_event(
                    request_id=request.request_id or "",
                    stage="compliance_layer",
                    component=module_name,
                    event_type=MODULE_DEFERRED_TO_COMPLIANCE,
                    decision="deferred",
                    status="ok",
                    payload={
                        "module": module_name,
                        "reason": "compliance_layer_match",
                        "matched_rule_id": matched_rule_id,
                        "cycle": 0,
                        "deferred_outcome_summary": ("skipped: request is authorized contract rule execution"),
                    },
                )
            except Exception:
                _LOG.debug("emit MODULE_DEFERRED_TO_COMPLIANCE failed for %s", module_name, exc_info=True)

        decision = self._build_compliance_decision(request, risk_estimation, cv)
        decision_explanation = self._build_compliance_decision_explanation(request, cv)

        result = self._runner.run_benign_fast_path(
            request=request,
            risk_estimation=risk_estimation,
            start_time=start_time,
            decision=decision,
            decision_explanation=decision_explanation,
            speculative_draft=speculative_draft,
        )

        if call_ctx.compliance_verdict is not None:
            result.compliance_verdict = call_ctx.compliance_verdict

        try:
            self._emit_compliance_decision_trace(request, cv, risk_estimation)
        except Exception:
            _LOG.debug("emit compliance decision trace failed", exc_info=True)

        fill_trace_from_result(trace, result)
        result.trace = trace
        return self._diagnostics.ensure_final_action_and_return(
            result,
            request,
            start_time,
            "compliance_fast_path",
            partial(self._attach_trace_and_return, call_ctx=call_ctx),
        )

    def _build_compliance_decision(
        self,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol,
        cv: ComplianceVerdict | None,
    ) -> Decision:
        """Build a NORMAL_COMPLETE Decision for a compliance match."""
        return Decision(
            final_action="NORMAL_COMPLETE",
            path="COMPLIANCE_FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=["COMPLIANCE_LAYER_MATCH"],
        )

    def _build_compliance_decision_explanation(
        self,
        request: ProcessedRequest,
        cv: ComplianceVerdict | None,
    ) -> DecisionExplanation | None:
        """Build a DecisionExplanation documenting the compliance match."""
        try:
            return DecisionExplanation(
                request_id=request.request_id or "",
                final_action="NORMAL_COMPLETE",
                risk_score=cv.confidence if cv else 0.0,
                risk_category="benign",
                reason_codes=["COMPLIANCE_LAYER_MATCH"],
                winning_rule="compliance_layer_match",
                why_not_refuse=(
                    "The deployer explicitly authorized this behavior via the developer "
                    "contract, and the output is not safety-restricted."
                ),
                why_not_safe_complete=(
                    "Contract execution does not require caveats; the deployer authorized " "the direct response."
                ),
            )
        except Exception:
            _LOG.debug("build compliance decision explanation failed", exc_info=True)
            return None

    def _emit_compliance_decision_trace(
        self,
        request: ProcessedRequest,
        cv: ComplianceVerdict | None,
        risk_estimation: RiskEstimationProtocol,
    ) -> None:
        """Emit a decision trace at the COMPLIANCE_LAYER stage."""
        try:
            dt = DecisionTrace(
                request_id=request.request_id or "",
                stage="COMPLIANCE_LAYER",
                sequence=-5,
                risk_score=float(getattr(risk_estimation, "score", 0.1) or 0.1),
            )
            dt.stage_payload = {
                "compliance_decision": cv.decision.value if cv else "NO_CONTRACT",
                "matched_rule_id": cv.matched_rule.rule_id if (cv and cv.matched_rule) else None,
                "matched_rule_summary": cv.matched_rule.rule_summary if (cv and cv.matched_rule) else None,
                "evaluation_path": cv.evaluation_path.value if cv else "skipped",
                "confidence": cv.confidence if cv else 0.0,
                "speculative_draft_validated": cv.speculative_draft_validated if cv else False,
                "contract_hash": cv.contract_hash if cv else "",
            }
            normalize_trace_fields(dt)
            append_decision_trace(dt)
        except Exception:
            _LOG.debug("emit COMPLIANCE_LAYER decision trace failed", exc_info=True)

    def _handle_timeout(self, request: ProcessedRequest, error_msg: str, start_time: float) -> OrchestratorResult:
        processing_time = int((time.time() - start_time) * 1000)
        response = FinalResponse(
            content=("[SYSTEM_TIMEOUT]" if self.config.safe_response_on_error else f"Timeout: {error_msg}"),
            response_type=ResponseType.FULL_REFUSAL,
            metadata=ResponseMetadata.for_system_error(
                processing_time, getattr(request, "request_id", "") or "", principle="SYSTEM.TIMEOUT"
            ),
        )
        return OrchestratorResult(
            response=response,
            request_id=request.request_id,
            path_taken="deliberative",
            path="DELIBERATIVE_PATH",
            total_cycles=0,
            converged=False,
            error=error_msg,
            errors=[error_msg] if error_msg else None,
        )

    def _handle_error(self, request: ProcessedRequest, error: MoralStackError, start_time: float) -> OrchestratorResult:
        processing_time = int((time.time() - start_time) * 1000)
        response = FinalResponse(
            content=("[SYSTEM_ERROR]" if self.config.safe_response_on_error else f"Error: {str(error)}"),
            response_type=ResponseType.FULL_REFUSAL,
            metadata=ResponseMetadata.for_system_error(
                processing_time, getattr(request, "request_id", "") or "", principle="SYSTEM.ERROR"
            ),
        )
        return OrchestratorResult(
            response=response,
            request_id=request.request_id,
            path_taken="fast",
            path="DELIBERATIVE_PATH",
            total_cycles=0,
            converged=False,
            error=str(error),
            errors=[str(error)],
        )

    def _route_refuse(
        self,
        request: ProcessedRequest,
        decision: Decision,
        explanation: DecisionExplanation,
        risk_estimation: RiskEstimationProtocol,
        risk_score: float,
        start_time: float,
        trace: Trace,
        *,
        call_ctx: ProcessCallContext,
    ) -> OrchestratorResult:
        result = self._refusal_handler.handle(
            request,
            decision,
            explanation,
            risk_estimation,
            risk_score,
            start_time,
            trace,
        )
        return self._diagnostics.ensure_final_action_and_return(
            result,
            request,
            start_time,
            "REFUSE",
            partial(self._attach_trace_and_return, call_ctx=call_ctx),
        )

    def _route_domain_excluded(
        self,
        request: ProcessedRequest,
        excluded_domain: str,
        start_time: float,
        trace: Trace,
        *,
        call_ctx: ProcessCallContext,
    ) -> OrchestratorResult:
        """Early exit when the detected domain has excluded=true. One LLM call for message."""
        content = generate_domain_exclusion_response(
            domain=excluded_domain,
            user_prompt=request.prompt,
            llm_client=self.policy,
        )
        processing_time_ms = int((time.time() - start_time) * 1000)
        metadata = ResponseMetadata.for_domain_excluded(processing_time_ms, request.request_id, excluded_domain)
        dt = DecisionTrace(
            request_id=request.request_id,
            stage="DOMAIN_EXCLUDED",
            sequence=1,
            excluded_domain=excluded_domain,
            domain_excluded=True,
            final_action="REFUSE",
            path="DOMAIN_EXCLUDED",
            reason_codes=["domain_excluded"],
            winning_rule="domain_excluded",
        )
        append_decision_trace(dt)
        result = OrchestratorResult(
            response=FinalResponse(
                content=content,
                response_type=ResponseType.DOMAIN_EXCLUDED,
                metadata=metadata,
            ),
            request_id=request.request_id,
            path_taken="domain_excluded",
            path="DOMAIN_EXCLUDED",
            total_cycles=0,
            converged=False,
        )
        trace.domain_excluded = True
        result.trace = trace
        return self._diagnostics.ensure_final_action_and_return(
            result,
            request,
            start_time,
            "REFUSE",
            partial(self._attach_trace_and_return, call_ctx=call_ctx),
        )

    def _route_benign(
        self,
        request: ProcessedRequest,
        decision: Decision,
        explanation: DecisionExplanation,
        risk_estimation: RiskEstimationProtocol,
        start_time: float,
        trace: Trace,
        speculative_draft: str | None = None,
        *,
        call_ctx: ProcessCallContext,
    ) -> OrchestratorResult:
        request_id = request.request_id
        orch_debug_log(
            "orchestrator.py:process",
            "early return benign_fast_path",
            {"decision.path": decision.path, "deliberation_cycles": 0},
            "H-early-benign",
            request_id=request_id,
        )
        result = self._runner.run_benign_fast_path(
            request,
            risk_estimation,
            start_time,
            decision=decision,
            decision_explanation=explanation,
            speculative_draft=speculative_draft,
        )
        fill_trace_from_result(trace, result)
        result.trace = trace
        return self._diagnostics.ensure_final_action_and_return(
            result,
            request,
            start_time,
            "benign_fast_path",
            partial(self._attach_trace_and_return, call_ctx=call_ctx),
        )

    def _route_safe_complete(
        self,
        request: ProcessedRequest,
        decision: Decision,
        explanation: DecisionExplanation,
        risk_estimation: RiskEstimationProtocol,
        start_time: float,
        trace: Trace,
        *,
        call_ctx: ProcessCallContext,
    ) -> OrchestratorResult:
        request_id = request.request_id
        orch_debug_log(
            "orchestrator.py:process",
            "early return safe_complete_path (FAST_PATH)",
            {"decision.path": decision.path, "deliberation_cycles": 0},
            "H-early-safe",
            request_id=request_id,
        )
        result = self._runner.run_safe_complete_path(
            request,
            risk_estimation,
            start_time,
            decision=decision,
            decision_explanation=explanation,
        )
        fill_trace_from_result(trace, result)
        result.trace = trace
        return self._diagnostics.ensure_final_action_and_return(
            result,
            request,
            start_time,
            "safe_complete_path",
            partial(self._attach_trace_and_return, call_ctx=call_ctx),
        )

    def _route_fast_path(
        self,
        request: ProcessedRequest,
        decision: Decision,
        explanation: DecisionExplanation,
        risk_estimation: RiskEstimationProtocol,
        start_time: float,
        trace: Trace,
        speculative_draft: str | None = None,
        *,
        call_ctx: ProcessCallContext,
    ) -> OrchestratorResult:
        request_id = request.request_id
        orch_debug_log(
            "orchestrator.py:process",
            "taking _fast_path (no deliberative loop)",
            {"path_taken": "fast"},
            "H-fast",
            request_id=request_id,
        )
        constitution = get_constitution_safe(self.constitution_store, request.get_domain())
        result = self._runner.run_fast_path(
            request,
            risk_estimation,
            start_time,
            decision=decision,
            constitution=constitution,
            decision_explanation=explanation,
            speculative_draft=speculative_draft,
        )
        _fsnap = getattr(result, "convergence_snapshot", None)
        if isinstance(_fsnap, dict) and request_id:
            with self._trace_lock:
                if request_id in self.execution_trace:
                    self.execution_trace[request_id]["convergence_snapshot"] = dict(_fsnap)
        result.path_taken = "fast"
        fill_trace_from_result(trace, result)
        result.trace = trace
        return self._diagnostics.ensure_final_action_and_return(
            result,
            request,
            start_time,
            "fast_path",
            partial(self._attach_trace_and_return, call_ctx=call_ctx),
        )

    def _route_deliberative(
        self,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol,
        risk_category: RiskCategory | None,
        constrained_generation: bool,
        overlay_sensitive: bool,
        start_time: float,
        trace: Trace,
        pre_decision: Decision | None = None,
        speculative_draft: str | None = None,
        *,
        call_ctx: ProcessCallContext,
    ) -> OrchestratorResult:
        request_id = request.request_id
        orch_debug_log(
            "orchestrator.py:process",
            "taking _deliberative_path",
            {"path_taken": "deliberative"},
            "H-delib",
            request_id=request_id,
        )
        constitution = get_constitution_safe(self.constitution_store, request.get_domain())
        state, risk_score, outcome = self._runner.run_deliberative_path(
            request,
            risk_estimation,
            start_time,
            constrained_generation=constrained_generation,
            constitution=constitution,
            speculative_draft=speculative_draft,
        )
        _snap_raw = getattr(state, "_convergence_evaluation_snapshot", None)
        _convergence_snapshot = dict(_snap_raw) if isinstance(_snap_raw, dict) else None
        if _convergence_snapshot is not None and request_id:
            with self._trace_lock:
                if request_id in self.execution_trace:
                    self.execution_trace[request_id]["convergence_snapshot"] = _convergence_snapshot
        modules_called: set[str] = set()
        if state.critiques:
            modules_called.add("critic")
        if state.simulations:
            modules_called.add("simulator")
        if state.perspectives:
            modules_called.add("perspectives")
        if state.hindsight is not None:
            modules_called.add("hindsight")
        trace.deliberation_cycles_actual = state.cycle
        trace.modules_called = modules_called
        trace.converged = outcome.converged if outcome else False
        with self._trace_lock:
            if request_id and request_id in self.execution_trace:
                self.execution_trace[request_id].setdefault("events", []).append(
                    {
                        "event": "CONVERGENCE_ENFORCED",
                        "request_id": request_id,
                        "stop_reason": outcome.stop_reason,
                        "total_cycles": state.cycle,
                        "converged": outcome.converged,
                    }
                )
        if state.cycle == 0:
            logging.getLogger(__name__).warning(
                "[Orchestrator] _deliberative_path returns total_cycles=0 with "
                "path=DELIBERATIVE_PATH: no deliberative cycle executed. request_id=%s",
                getattr(request, "request_id", "") or "",
            )
        decision1, explanation1 = decide_action(
            request,
            risk_estimation,
            critic_result=state.last_critique,
            sim_result=state.simulations[-1] if state.simulations else None,
            hindsight_result=state.hindsight,
            append_pre_policy_trace=False,
            total_cycles=state.cycle,
            stop_reason=outcome.stop_reason if outcome else "",
            overlay_sensitive=overlay_sensitive,
            risk_thresholds=getattr(getattr(self, "config", None), "risk_thresholds", None),
        )
        # Execution path was deliberative; ensure decision.path is DELIBERATIVE_PATH for metadata/trace.
        if state.cycle > 0 and decision1.path != "DELIBERATIVE_PATH":
            decision1 = Decision(
                final_action=decision1.final_action,
                path="DELIBERATIVE_PATH",
                intent_clarity=decision1.intent_clarity,
                misuse_plausibility=decision1.misuse_plausibility,
                actionability_risk=decision1.actionability_risk,
                triggered_principles=decision1.triggered_principles,
                hard_violations=decision1.hard_violations,
                risk_signals=decision1.risk_signals,
                reason_codes=list(decision1.reason_codes),
            )
        # --- DELIBERATION OVERRIDE: unanimous modules can downgrade REFUSE to SAFE_COMPLETE ---
        override = evaluate_deliberation_override(
            pre_decision=pre_decision,
            post_decision=decision1,
            state=state,
            risk_estimation=risk_estimation,
            outcome=outcome,
        )
        if override is not None:
            orch_debug_log(
                "orchestrator.py:process",
                "DELIBERATION_OVERRIDE: REFUSE -> SAFE_COMPLETE (modules unanimous)",
                {
                    "original_action": decision1.final_action,
                    "override_action": override.final_action,
                    "critic_decision": getattr(state.last_critique, "decision", ""),
                    "avg_approval": (
                        sum(getattr(p, "approval_score", 0) for p in (state.perspectives or []))
                        / max(len(state.perspectives or []), 1)
                    ),
                    "sim_valence": getattr(
                        state.simulations[-1] if state.simulations else None,
                        "expected_valence",
                        None,
                    ),
                },
                "H-delib-override",
                request_id=request_id,
            )
            decision1 = override
        # --- CYCLES_EXHAUSTED conservative fallback ---
        # Upgrade NORMAL_COMPLETE → SAFE_COMPLETE solo se la categoria è intrinsecamente
        # sensibile (SENSITIVE / MORALLY_NUANCED), OPPURE se overlay_sensitive + rischio
        # non banale + il critic ha trovato almeno una soft violation nell'ultimo ciclo.
        # overlay_sensitive da solo NON è sufficiente: evita falsi upgrade su query benigne
        # in domini regolati (es. "sintomi influenza" su overlay medical).
        _cycles_soft_violation = bool(
            state.last_critique
            and any(
                getattr(v, "constraint_type", "") == "soft" for v in (getattr(state.last_critique, "violations", []) or [])
            )
        )
        if (
            outcome.stop_reason == "CYCLES_EXHAUSTED"
            and decision1.final_action == "NORMAL_COMPLETE"
            and not getattr(decision1, "hard_violations", None)
            and (
                risk_category in (RiskCategory.SENSITIVE, RiskCategory.MORALLY_NUANCED)
                or (overlay_sensitive and risk_score >= 0.45 and _cycles_soft_violation)
            )
        ):
            orch_debug_log(
                "orchestrator.py:process",
                "CYCLES_EXHAUSTED sensitive fallback: NORMAL_COMPLETE -> SAFE_COMPLETE",
                {
                    "risk_category": str(risk_category),
                    "overlay_sensitive": overlay_sensitive,
                    "stop_reason": outcome.stop_reason,
                },
                "H-cycles-exhausted-fallback",
                request_id=request_id,
            )
            decision1 = Decision(
                final_action="SAFE_COMPLETE",
                path="DELIBERATIVE_PATH",
                intent_clarity=decision1.intent_clarity,
                misuse_plausibility=decision1.misuse_plausibility,
                actionability_risk=decision1.actionability_risk,
                triggered_principles=decision1.triggered_principles,
                hard_violations=decision1.hard_violations,
                risk_signals=decision1.risk_signals,
                reason_codes=list(decision1.reason_codes) + ["cycles_exhausted_sensitive_fallback"],
            )
        decision1 = apply_safe_complete_gating(
            decision1,
            request,
            risk_estimation,
            request_id=request_id,
            overlay_sensitive=overlay_sensitive,
        )
        op_risk_post = getattr(risk_estimation, "operational_risk", OperationalRisk.NONE)
        if (
            outcome.stop_reason == "CYCLES_EXHAUSTED"
            and decision1.final_action == "REFUSE"
            and is_hard_signal_refuse(decision1, risk_estimation, op_risk_post)
        ):
            orch_debug_log(
                "orchestrator.py:_route_deliberative",
                "hard-signal REFUSE -> normalizing stop_reason",
                {
                    "old_stop_reason": outcome.stop_reason,
                    "new_stop_reason": "HARD_VIOLATION_STOP",
                    "risk_score": risk_score,
                    "reason_codes": list(getattr(decision1, "reason_codes", None) or []),
                    "activated_signals": list(getattr(decision1, "risk_signals", None) or []),
                },
                "H-hard-signal-stop-reason",
                request_id=request_id,
            )
            outcome = ConvergenceOutcome(
                should_continue=False,
                converged=False,
                stop_reason="HARD_VIOLATION_STOP",
                cycle=outcome.cycle,
                max_cycles=outcome.max_cycles,
            )
        # Update trace to reflect the FINAL decision (post-deliberation),
        # not the stale pre-deliberation values set at first decide_action() call.
        trace.decision_path = decision1.path
        trace.final_action = decision1.final_action
        processing_time = int((time.time() - start_time) * 1000)
        if constitution is not None and getattr(constitution, "constitution_corrupted", False):
            risk_score = 1.0
        expl_for_assembler = explanation1
        if decision1.final_action == "SAFE_COMPLETE" and "cycles_exhausted_sensitive_fallback" in getattr(
            decision1, "reason_codes", []
        ):
            expl_for_assembler = DecisionExplanation(
                request_id=request_id,
                final_action=decision1.final_action,
                risk_score=risk_score,
                risk_category=risk_category_str(risk_estimation),
                activated_signals=list(getattr(decision1, "risk_signals", []) or []),
                overlay_applied=(request.get_domain() or "") or "",
                winning_rule="cycles_exhausted_fallback",
                reason_codes=policy_reason_codes_to_reason_codes(getattr(decision1, "reason_codes", [])),
                why_not_refuse="Risk below refuse threshold or non-operational.",
                why_not_safe_complete="N/A; current action is SAFE_COMPLETE.",
                why_not_normal_complete=("Cycles exhausted; safe framing applied (not full NORMAL_COMPLETE)."),
            )
        log_decision_explanation(
            expl_for_assembler,
            request_id,
            hypothesis_id="H-decision-delib",
            risk_score=risk_score,
            risk_category=risk_category_str(risk_estimation),
            stop_reason=getattr(outcome, "stop_reason", "") or "",
        )
        response = self.assembler.assemble(
            request,
            state,
            decision1,
            risk_score=risk_score,
            processing_time_ms=processing_time,
            constitution=constitution,
            risk_estimation=risk_estimation,
            outcome=outcome,
            decision_explanation=expl_for_assembler,
            constitution_store=self.constitution_store,
        )
        self._emit_deliberation_aggregate_trace(
            request_id=request_id,
            state=state,
            outcome=outcome,
            risk_score=risk_score,
        )
        result = OrchestratorResult(
            response=response,
            request_id=request.request_id,
            path_taken=("deliberative_sensitive" if risk_category == RiskCategory.SENSITIVE else "deliberative"),
            path="DELIBERATIVE_PATH",
            total_cycles=state.cycle,
            converged=outcome.converged,
            errors=list(state.errors) if state.errors else None,
            convergence_snapshot=_convergence_snapshot,
        )
        fill_trace_from_result(trace, result, modules_called=modules_called)
        result.trace = trace
        return self._diagnostics.ensure_final_action_and_return(
            result,
            request,
            start_time,
            "deliberative_path",
            partial(self._attach_trace_and_return, call_ctx=call_ctx),
        )

    def process(
        self,
        request: ProcessedRequest | str,
        *,
        conversation_id: str | None = None,
        turn_index: int | None = None,
        parent_request_id: str | None = None,
        conversation_state: ConversationGovernanceState | None = None,
    ) -> OrchestratorResult:
        """
        Entry point principale. Il flusso è governato da decision.path.
        REFUSE/benign/SAFE_COMPLETE fanno early return solo se path != DELIBERATIVE_PATH.
        Optional conversation_* / conversation_state are persisted for linkage only;
        they do not change routing or request-level analysis when unset.
        """
        start_time = time.time()
        if isinstance(request, str):
            request = ProcessedRequest(prompt=request)
        request_id = request.request_id

        call_ctx = ProcessCallContext(
            conversation_id=conversation_id,
            turn_index=turn_index,
            parent_request_id=parent_request_id,
            conversation_state=conversation_state,
        )

        set_current_session_id(conversation_id)
        set_current_turn_number(turn_index)

        self._persistence.set_request_context(request_id)
        self._persistence.ensure_run_and_upsert_request(
            request_id=request_id,
            prompt=request.prompt,
            domain=request.get_domain() if hasattr(request, "get_domain") else None,
            conversation_id=conversation_id,
            turn_index=turn_index,
            parent_request_id=parent_request_id,
        )

        trace = self._trace_lifecycle.start_trace(request_id)

        spec_handle: SpeculativeOverlapHandle | None = None
        try:
            if self.config.enable_speculative_generation and self.policy is not None:
                spec_handle = self._run_speculative_overlap(request)
                risk_estimation = spec_handle.risk_estimation
            else:
                risk_estimation = self._estimate_risk(request)

            speculative_draft_for_dccl = self._nonblocking_speculative_draft(spec_handle)
            self._run_dccl_evaluation(request, speculative_draft_for_dccl, call_ctx)

            from moralstack.compliance.types import ComplianceDecision

            cv = call_ctx.compliance_verdict
            if (
                cv is not None
                and cv.decision == ComplianceDecision.MATCH
                and cv.speculative_draft_validated
                and speculative_draft_for_dccl.strip()
            ):
                try:
                    return self._route_compliance_match(
                        request=request,
                        risk_estimation=risk_estimation,
                        speculative_draft=speculative_draft_for_dccl,
                        start_time=start_time,
                        trace=trace,
                        call_ctx=call_ctx,
                        spec_handle=spec_handle,
                    )
                except Exception as e:
                    _LOG.warning(
                        "DCCL compliance fast-path failed (non-fatal), " "falling back to standard pipeline: %s",
                        e,
                        exc_info=True,
                    )

            risk_score = risk_estimation.score if hasattr(risk_estimation, "score") else 0.5
            risk_category = getattr(risk_estimation, "risk_category", None)
            constrained_generation = risk_category == RiskCategory.CLEARLY_HARMFUL
            op_risk = getattr(risk_estimation, "operational_risk", OperationalRisk.NONE)
            trace.risk_score = risk_score
            trace.risk_category = (
                risk_category.value if risk_category and hasattr(risk_category, "value") else str(risk_category or "")
            )
            trace.op_risk = str(op_risk)
            trace.raw_risk_estimator_output_snippet = (
                getattr(risk_estimation, "raw_response", "") or getattr(risk_estimation, "raw_response_snippet", "")
            )[:200]
            trace.used_fallback_parse = getattr(risk_estimation, "used_fallback_parse", False)
            log_trace_event(
                "after_risk",
                "orchestrator.process",
                trace,
                {"risk_score": risk_score, "op_risk": str(op_risk)},
            )

            # Persist domain (user overlay or risk-detected) for dashboard and export.
            # `core` is a retrieval-only pseudo-domain and is normalized away here so
            # it never becomes a runtime overlay (see _normalize_runtime_domain).
            _domain = request.get_domain() if hasattr(request, "get_domain") else None
            _domain = _domain or getattr(risk_estimation, "detected_domain", None)
            _domain = _normalize_runtime_domain(_domain)
            if _domain is not None:
                self._persistence.update_request_domain(request_id, _domain)

            if _domain and not request.get_domain():
                request.user_context.domain_overlay = _domain

            # Domain exclusion check: zero overhead if no domain excluded
            has_excluded = getattr(self.constitution_store, "has_excluded_domains", None)
            if self.constitution_store is not None and callable(has_excluded) and has_excluded():
                _detected = getattr(risk_estimation, "detected_domain", None)
                if _detected and is_domain_excluded(self.constitution_store, _detected):
                    if spec_handle is not None:
                        spec_handle.abandon("domain_excluded", "DOMAIN_EXCLUDED")
                    return self._route_domain_excluded(request, _detected, start_time, trace, call_ctx=call_ctx)

            # --- Overlay sensitivity: risk_score floor ---
            overlay_sensitive = is_overlay_sensitive(self.constitution_store, request.get_domain())
            _overlay_floor_override: float | None = None
            _active_constitution = get_constitution_safe(self.constitution_store, request.get_domain())
            _active_overlay = getattr(_active_constitution, "active_overlay", None) if _active_constitution else None
            if _active_overlay is not None:
                _overlay_floor_override = getattr(_active_overlay, "sensitive_risk_floor", None)
            original_risk = risk_score
            risk_score = apply_risk_floor_if_sensitive(
                risk_score, overlay_sensitive, overlay_floor_override=_overlay_floor_override
            )
            if overlay_sensitive and risk_score != original_risk:
                _effective_floor = (
                    _overlay_floor_override if _overlay_floor_override is not None else OVERLAY_SENSITIVE_RISK_FLOOR
                )
                orch_debug_log(
                    "orchestrator.py:process",
                    "overlay sensitive: risk_score floor applied",
                    {
                        "original_risk_score": original_risk,
                        "floored_to": _effective_floor,
                        "per_overlay_floor": _overlay_floor_override,
                    },
                    "H-overlay-sensitive-floor",
                    request_id=request_id,
                )

            risk_proto = _as_risk_protocol(risk_estimation)
            # Propaga il risk_score floored (applicato sopra per overlay sensitive) dentro
            # risk_proto, in modo che decide_action e _handle_informational_recovery vedano
            # lo score corretto (0.35 invece di 0.1) quando l'overlay ha alzato il floor.
            # RiskEstimation è frozen=True: usare dataclasses.replace() per creare una copia.
            if overlay_sensitive and risk_score != original_risk:
                risk_proto = cast(RiskEstimationProtocol, _dc_replace(risk_estimation, score=risk_score))
            self._emit_risk_assessment_trace(request_id, risk_proto, risk_score)
            decision, explanation = decide_action(
                request,
                risk_proto,
                overlay_sensitive=overlay_sensitive,
                risk_thresholds=getattr(getattr(self, "config", None), "risk_thresholds", None),
            )
            decision = apply_safe_complete_gating(
                decision,
                request,
                risk_proto,
                request_id=request_id,
                overlay_sensitive=overlay_sensitive,
            )
            trace.decision_path = decision.path
            trace.final_action = decision.final_action
            trace.deliberation_cycles_planned = self.config.max_deliberation_cycles
            log_trace_event(
                "after_decide_action",
                "orchestrator.process",
                trace,
                {"decision.path": decision.path, "decision.final_action": decision.final_action},
            )

            log_decision_explanation(explanation, request_id, hypothesis_id="H-decision")

            route, borderline_refuse, risk_policy_action = get_route(decision, risk_proto, risk_score, self.config, op_risk)
            hard_signal_refuse = is_hard_signal_refuse(decision, risk_proto, op_risk)

            # --- v0.4 multi-turn: ledger lookup (Step 6 observability + Step 7 cache-driven routing) ---
            # Done AFTER decide_action and after hard_signal_refuse so we key on the canonical
            # decision context. On cache hit (Step 7), decision and route may be patched to skip deliberation.
            if self._ledger is not None and conversation_id is not None:
                _contract_hash = ""
                _developer_contract = getattr(request, "developer_contract", None)
                if _developer_contract is not None:
                    _contract_hash = getattr(_developer_contract, "contract_hash", "") or ""
                _posture = self._compute_governance_posture(
                    decision=decision,
                    overlay_sensitive=overlay_sensitive,
                    hard_signal_refuse=hard_signal_refuse,
                )
                _request_type = getattr(risk_proto, "request_type", "") or ""
                _intent_clarity = getattr(decision, "intent_clarity", "HIGH") or "HIGH"
                _turn_for_lookup = turn_index if isinstance(turn_index, int) else 0
                _cached_lookup = self._lookup_cached_decision(
                    prompt=request.prompt,
                    contract_hash=_contract_hash,
                    posture=_posture,
                    domain=request.get_domain() if hasattr(request, "get_domain") else None,
                    intent_clarity=_intent_clarity,
                    request_type=_request_type,
                    turn_index=_turn_for_lookup,
                )
                if _cached_lookup is not None:
                    orch_debug_log(
                        "orchestrator.py:process",
                        "ledger lookup result",
                        {
                            "is_hit": _cached_lookup.is_hit,
                            "similarity": _cached_lookup.similarity,
                            "from_turn": _cached_lookup.from_turn,
                            "reason": _cached_lookup.reason,
                        },
                        "H-ledger-lookup",
                        request_id=request_id,
                    )
                call_ctx.ledger_lookup = _cached_lookup
                # Step 14.3: persist lookup-time intent fields so the
                # post-pipeline store uses the SAME values the lookup used.
                # Without this, store would write request_type="" (because
                # ResponseMetadata has no request_type field) and any future
                # lookup with the real request_type triggers intent_divergence
                # at the secondary check, blocking every cache hit.
                call_ctx.ledger_request_type = _request_type
                call_ctx.ledger_intent_clarity = _intent_clarity
                # --- v0.4 Step 7: apply cached decision when hit and safe to do so ---
                if _cached_lookup is not None and _cached_lookup.is_hit:
                    cached_action = (
                        _cached_lookup.cached_decision.final_action if _cached_lookup.cached_decision else "unknown"
                    )
                    if self._fast_path_runner.is_safe_to_apply(
                        ledger_result=_cached_lookup,
                        current_decision=decision,
                        current_route=route,
                    ):
                        decision, route = self._fast_path_runner.apply_cached_decision(
                            ledger_result=_cached_lookup,
                            current_decision=decision,
                        )
                        # Re-evaluate hard_signal_refuse on the patched decision so the
                        # downstream routing block sees a consistent state.
                        hard_signal_refuse = is_hard_signal_refuse(decision, risk_proto, op_risk)
                        trace.decision_path = decision.path
                        trace.final_action = decision.final_action
                        # Mark the conversation context so _extend_state_out_v04 can flag was_cached=True.
                        call_ctx.ledger_hit_applied = True
                        # Step 14.4 — canonical orchestration.event emission so the
                        # cache application is visible in the UI metro map and in
                        # offline log consumers. The orch_debug_log below is kept
                        # for low-level debugging through debug_events.
                        try:
                            self._events.emit_orchestration_event(
                                request_id=request_id,
                                cycle=0,
                                stage="fast_path",
                                component="ledger_fast_path_runner",
                                event_type=LEDGER_FAST_PATH_APPLIED,
                                decision="applied",
                                status="ok",
                                sequence=0,
                                reason_codes=["cached_decision_reused"],
                                payload={
                                    "from_turn": _cached_lookup.from_turn,
                                    "similarity": _cached_lookup.similarity,
                                    "cached_action": cached_action,
                                    "forced_route": route,
                                    "modules_skipped": [
                                        "critic",
                                        "simulator",
                                        "perspectives",
                                        "hindsight",
                                    ],
                                },
                            )
                        except Exception:
                            # Observability is best-effort; never break the pipeline.
                            pass
                        orch_debug_log(
                            "orchestrator.py:process",
                            "ledger cache hit APPLIED — deliberation will be skipped",
                            {
                                "final_action": decision.final_action,
                                "forced_route": route,
                                "from_turn": _cached_lookup.from_turn,
                                "similarity": _cached_lookup.similarity,
                            },
                            "H-ledger-hit-applied",
                            request_id=request_id,
                        )
                    else:
                        # Step 14.4 — emit the gate-rejected variant so the audit
                        # trail explains why deliberation ran even though the
                        # ledger had a candidate hit. ``gate_reason`` is derived
                        # from the documented contract of is_safe_to_apply:
                        # - cached REFUSE is always applied (we never reach
                        #   this branch when cached_action == "REFUSE");
                        # - non-deliberative routes are always applied
                        #   (we never reach this branch in that case);
                        # - otherwise the current run is in deliberation and
                        #   the cached non-REFUSE decision is rejected.
                        gate_reason = (
                            "current_route_requires_deliberation"
                            if route in ("deliberative", "deliberative_loop")
                            else "unknown_gate_rejection"
                        )
                        try:
                            self._events.emit_orchestration_event(
                                request_id=request_id,
                                cycle=0,
                                stage="fast_path",
                                component="ledger_fast_path_runner",
                                event_type=LEDGER_FAST_PATH_NOT_APPLIED,
                                decision="rejected",
                                status="ok",
                                sequence=0,
                                reason_codes=[gate_reason],
                                payload={
                                    "from_turn": _cached_lookup.from_turn,
                                    "similarity": _cached_lookup.similarity,
                                    "cached_action": cached_action,
                                    "current_action": decision.final_action,
                                    "current_route": route,
                                    "gate_reason": gate_reason,
                                },
                            )
                        except Exception:
                            pass
                        orch_debug_log(
                            "orchestrator.py:process",
                            "ledger cache hit FOUND but safety gate prevents application",
                            {
                                "cached_action": cached_action,
                                "current_action": decision.final_action,
                                "current_route": route,
                            },
                            "H-ledger-hit-skipped",
                            request_id=request_id,
                        )
                # --- end v0.4 Step 7 ---
            # --- end v0.4 multi-turn ledger lookup ---

            if borderline_refuse:
                orch_debug_log(
                    "orchestrator.py:process",
                    "REFUSE borderline → forcing deliberative loop",
                    {
                        "risk_score": risk_score,
                        "lower_bound": self.config.risk_thresholds.medium,
                        "upper_bound": self.config.borderline_refuse_upper,
                    },
                    "H-refuse-borderline",
                    request_id=request_id,
                )

            if (
                route == "refuse"
                and decision.final_action == "REFUSE"
                and decision.path != "DELIBERATIVE_PATH"
                and hard_signal_refuse
            ):
                activated_signals = list(getattr(decision, "risk_signals", None) or [])
                if not activated_signals:
                    activated_signals = list(getattr(risk_proto, "semantic_signals", None) or [])
                orch_debug_log(
                    "orchestrator.py:process",
                    "hard-signal REFUSE -> bypass deliberative loop",
                    {
                        "risk_score": risk_score,
                        "lower_bound": self.config.risk_thresholds.medium,
                        "upper_bound": self.config.borderline_refuse_upper,
                        "activated_signals": activated_signals,
                        "reason_codes": list(getattr(decision, "reason_codes", None) or []),
                    },
                    "H-hard-signal-bypass",
                    request_id=request_id,
                )

            if route == "refuse":
                if spec_handle is not None:
                    spec_handle.abandon("refuse_path", "refuse")
                return self._route_refuse(
                    request,
                    decision,
                    explanation,
                    risk_proto,
                    risk_score,
                    start_time,
                    trace,
                    call_ctx=call_ctx,
                )
            if route == "benign":
                speculative_draft = spec_handle.join_for_consumer("benign", "benign_fast_path") if spec_handle else None
                return self._route_benign(
                    request,
                    decision,
                    explanation,
                    risk_proto,
                    start_time,
                    trace,
                    speculative_draft=speculative_draft,
                    call_ctx=call_ctx,
                )
            if route == "safe_complete":
                if spec_handle is not None:
                    spec_handle.abandon("safe_complete_path", "safe_complete")
                return self._route_safe_complete(
                    request,
                    decision,
                    explanation,
                    risk_proto,
                    start_time,
                    trace,
                    call_ctx=call_ctx,
                )

            if route in ("fast_path", "deliberative"):
                orch_debug_log(
                    "orchestrator.py:process",
                    "branch risk_policy vs deliberative",
                    {
                        "risk_policy_action": risk_policy_action.value,
                        "risk_score": risk_score,
                        "threshold_low": getattr(self.config.risk_thresholds, "low", None),
                        "decision.path": decision.path,
                    },
                    "H-branch",
                    request_id=request_id,
                )

            if route == "fast_path":
                speculative_draft_fp = spec_handle.join_for_consumer("fast_path", "run_fast_path") if spec_handle else None
                return self._route_fast_path(
                    request,
                    decision,
                    explanation,
                    risk_proto,
                    start_time,
                    trace,
                    speculative_draft=speculative_draft_fp,
                    call_ctx=call_ctx,
                )

            speculative_draft_delib: str | None = None
            if spec_handle is not None:
                if constrained_generation:
                    spec_handle.abandon("constrained_generation_incompatible", "deliberative")
                else:
                    speculative_draft_delib = spec_handle.join_for_consumer(
                        "deliberative",
                        "run_deliberative_path",
                    )
            return self._route_deliberative(
                request,
                risk_proto,
                risk_category,
                constrained_generation,
                overlay_sensitive,
                start_time,
                trace,
                pre_decision=decision,
                speculative_draft=speculative_draft_delib,
                call_ctx=call_ctx,
            )

        except OrchestratorTimeoutError as e:
            return self._attach_trace_and_return(self._handle_timeout(request, str(e), start_time), request, call_ctx)
        except MoralStackError as e:
            return self._attach_trace_and_return(self._handle_error(request, e, start_time), request, call_ctx)
        except FailSafeException:
            orch_debug_log(
                "orchestrator.py:process",
                "early return FailSafeException",
                {"path": "DELIBERATIVE_PATH", "deliberation_cycles": 0},
                "H-except",
                request_id=request_id,
            )
            log_deliberation_inconsistency(
                getattr(request, "request_id", "") or "",
                "DELIBERATIVE_PATH",
                "",
                "FailSafeException",
                "none",
            )
            processing_time = int((time.time() - start_time) * 1000)
            return self._attach_trace_and_return(
                OrchestratorResult(
                    response=FinalResponse.safe_default(processing_time),
                    request_id=request.request_id,
                    path_taken="error",
                    path="ERROR_PATH",
                    total_cycles=0,
                    converged=False,
                    error="FailSafeException",
                ),
                request,
                call_ctx,
            )
        except (AssertionError, Exception) as e:
            orch_debug_log(
                "orchestrator.py:process",
                "early return Exception",
                {"path": "DELIBERATIVE_PATH", "deliberation_cycles": 0, "error": str(e)[:200]},
                "H-except",
                request_id=request_id,
            )
            log_deliberation_inconsistency(
                getattr(request, "request_id", "") or "",
                "DELIBERATIVE_PATH",
                "",
                "exception",
                "none",
            )
            processing_time = int((time.time() - start_time) * 1000)
            return self._attach_trace_and_return(
                OrchestratorResult(
                    response=FinalResponse.safe_default(processing_time),
                    request_id=request.request_id,
                    path_taken="error",
                    path="ERROR_PATH",
                    total_cycles=0,
                    converged=False,
                    error=str(e) if e else "unknown",
                ),
                request,
                call_ctx,
            )
        finally:
            if spec_handle is not None:
                spec_handle.shutdown_executor()
            self._trace_lifecycle.remove_parser_diagnostic_handler(request_id)
