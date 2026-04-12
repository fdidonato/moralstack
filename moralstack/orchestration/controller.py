"""
OrchestrationController: solo coordinamento alto livello.
process() governa il flusso in base a decision.path; nessuna logica interna complessa.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace as _dc_replace
from typing import Any, cast

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
from moralstack.orchestration.conversation_state import ConversationGovernanceState
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
from moralstack.orchestration.orchestration_event_taxonomy import (
    CONVERSATION_CONTEXT_ATTACHED,
    CONVERSATION_STATE_UPDATED,
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
from moralstack.orchestration.refusal_handler import RefusalHandler
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.safe_complete_gating import apply_safe_complete_gating
from moralstack.orchestration.speculative_overlap import SpeculativeOverlapHandle
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

_LOG = logging.getLogger(__name__)


def _as_risk_protocol(r: RiskEstimation) -> RiskEstimationProtocol:
    """Cast RiskEstimation to RiskEstimationProtocol (structural match at runtime)."""
    return cast(RiskEstimationProtocol, r)


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
        self._conversation_process_ctx: dict[str, Any] | None = None
        self._refusal_handler = RefusalHandler(
            policy=policy,
            constitution_store=constitution_store,
            event_emitter=self._events,
        )

    def set_logger(self, logger: LoggerProtocol | None) -> None:
        """Set the logger for tracking LLM calls (propagated to runner)."""
        self.logger = logger
        self._runner.logger = logger

    def _attach_trace_and_return(self, result: OrchestratorResult, request: ProcessedRequest) -> OrchestratorResult:
        out = self._diagnostics.attach_trace_and_return(result, request, self.execution_trace)
        self._apply_conversation_metadata_to_result(out, request)
        return out

    def _apply_conversation_metadata_to_result(self, result: OrchestratorResult, request: ProcessedRequest) -> None:
        """Stamp optional conversation linkage and updated governance state (no routing impact)."""
        ctx = getattr(self, "_conversation_process_ctx", None)
        if not isinstance(ctx, dict):
            return
        cid = ctx.get("conversation_id")
        tid = ctx.get("turn_index")
        pid = ctx.get("parent_request_id")
        state_in = ctx.get("conversation_state")
        if cid is not None:
            result.conversation_id = cid
        if tid is not None:
            result.turn_index = tid
        if pid is not None:
            result.parent_request_id = pid
        if state_in is not None:
            result.conversation_state_provided = True
            base = state_in if isinstance(state_in, ConversationGovernanceState) else ConversationGovernanceState()
            merged = base.with_turn_metadata(
                conversation_id=cid if cid is not None else base.conversation_id,
                turn_index=tid if tid is not None else base.turn_index,
            )
            dom = request.get_domain() if hasattr(request, "get_domain") else None
            result.conversation_governance_state_out = merged.update_from_processing_result(
                request_id=getattr(request, "request_id", "") or "",
                domain=dom,
            )
            result.conversation_state_updated = True
        if ctx.get("_conversation_events_emitted"):
            return
        ctx["_conversation_events_emitted"] = True
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
            }
            normalize_trace_fields(dt)
            append_decision_trace(dt)
        except Exception:
            _LOG.debug("emit RISK_ASSESSMENT trace failed", exc_info=True)

    def _estimate_risk(self, request: ProcessedRequest) -> RiskEstimation:
        if self.risk_estimator is None:
            return RiskEstimation(score=0.5, confidence=0.5, risk_category=RiskCategory.SENSITIVE)
        try:
            start = time.time()
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
                    system=self._protected_system_prompt,
                )
            except TypeError:
                result = self.policy.generate(prompt_text)
            elapsed = (time.time() - start) * 1000
            response_text = getattr(result, "text", None) or str(result)
            protection = self._output_protector.validate(response_text)
            prompt_used = getattr(result, "prompt_used", None) or prompt_text
            system_used = getattr(result, "system_used", None) or self._protected_system_prompt
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
            lambda r, req: self._attach_trace_and_return(r, req),
        )

    def _route_domain_excluded(
        self,
        request: ProcessedRequest,
        excluded_domain: str,
        start_time: float,
        trace: Trace,
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
            lambda r, req: self._attach_trace_and_return(r, req),
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
            lambda r, req: self._attach_trace_and_return(r, req),
        )

    def _route_safe_complete(
        self,
        request: ProcessedRequest,
        decision: Decision,
        explanation: DecisionExplanation,
        risk_estimation: RiskEstimationProtocol,
        start_time: float,
        trace: Trace,
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
            lambda r, req: self._attach_trace_and_return(r, req),
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
            lambda r, req: self._attach_trace_and_return(r, req),
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
            lambda r, req: self._attach_trace_and_return(r, req),
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

        self._conversation_process_ctx = {
            "conversation_id": conversation_id,
            "turn_index": turn_index,
            "parent_request_id": parent_request_id,
            "conversation_state": conversation_state,
            "_conversation_events_emitted": False,
        }

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

            # Persist domain (user overlay or risk-detected) for dashboard and export
            _domain = request.get_domain() if hasattr(request, "get_domain") else None
            _domain = _domain or getattr(risk_estimation, "detected_domain", None)
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
                    return self._route_domain_excluded(request, _detected, start_time, trace)

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
            decision, explanation = decide_action(request, risk_proto, overlay_sensitive=overlay_sensitive)
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
            )

        except OrchestratorTimeoutError as e:
            return self._attach_trace_and_return(self._handle_timeout(request, str(e), start_time), request)
        except MoralStackError as e:
            return self._attach_trace_and_return(self._handle_error(request, e, start_time), request)
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
            )
        finally:
            self._conversation_process_ctx = None
            if spec_handle is not None:
                spec_handle.shutdown_executor()
            self._trace_lifecycle.remove_parser_diagnostic_handler(request_id)
