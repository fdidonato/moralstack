"""REFUSE path: LLM refusal generation and observability emission (extracted from OrchestrationController)."""

from __future__ import annotations

import json
import time

from moralstack.core.types import PolicyLLMProtocol
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import RiskPolicyAction
from moralstack.orchestration.diagnostics import orch_debug_log
from moralstack.orchestration.event_emitter import EventEmitter
from moralstack.orchestration.overlay_policy import get_constitution_safe
from moralstack.orchestration.refusal_context import build_refusal_context
from moralstack.orchestration.safe_refusal_generator import (
    _detect_language_fallback,
    _iso_to_language_name,
    generate_llm_safe_refusal_detailed,
    resolve_refusal_domain_and_redirection,
)
from moralstack.orchestration.trace import Trace
from moralstack.orchestration.types import (
    ConstitutionStoreProtocol,
    Decision,
    FinalResponse,
    OrchestratorResult,
    PathTakenType,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
    RiskEstimationProtocol,
    risk_category_str,
)


class RefusalHandler:
    """Builds REFUSE responses and emits related observability records."""

    def __init__(
        self,
        policy: PolicyLLMProtocol | None,
        constitution_store: ConstitutionStoreProtocol | None,
        event_emitter: EventEmitter,
    ) -> None:
        self.policy = policy
        self.constitution_store = constitution_store
        self._events = event_emitter

    def handle(
        self,
        request: ProcessedRequest,
        decision: Decision,
        explanation: DecisionExplanation,
        risk_estimation: RiskEstimationProtocol,
        risk_score: float,
        start_time: float,
        trace: Trace,
    ) -> OrchestratorResult:
        request_id = request.request_id
        orch_debug_log(
            "orchestrator.py:process",
            "early return REFUSE (FAST_PATH)",
            {"decision.path": decision.path, "deliberation_cycles": 0},
            "H-early-refuse",
            request_id=request_id,
        )
        constitution = get_constitution_safe(self.constitution_store, request.get_domain())
        processing_time_ms = int((time.time() - start_time) * 1000)
        path_taken_refuse: PathTakenType = "fast" if decision.path == "FAST_PATH" else "deliberative"
        risk_cat_str = risk_category_str(risk_estimation)
        detected_iso = getattr(risk_estimation, "detected_language", None) or ""
        language = _iso_to_language_name(detected_iso) if detected_iso else _detect_language_fallback(request.prompt)
        domain, refusal_redirection = resolve_refusal_domain_and_redirection(
            request_prompt=request.prompt,
            request_domain=request.get_domain(),
            detected_domain=getattr(risk_estimation, "detected_domain", None),
            risk_signals=list(getattr(decision, "risk_signals", None) or []),
            constitution_store=self.constitution_store,
        )
        rationale = getattr(risk_estimation, "rationale", None) or ""

        refusal_context = build_refusal_context(
            risk_estimation=risk_estimation,
            decision=decision,
            domain=domain,
            refusal_redirection=refusal_redirection,
            risk_score=risk_score,
            risk_category=risk_cat_str,
            developer_contract=getattr(request, "developer_contract", None),
            conversation_history=getattr(request, "conversation_history", None),
        )

        _refusal_t0 = time.time()
        refusal_result = generate_llm_safe_refusal_detailed(
            user_prompt=request.prompt,
            risk_category=risk_cat_str,
            policy_reason_codes=list(decision.reason_codes),
            language=language,
            domain=domain,
            llm_client=self.policy,
            rationale=rationale if rationale else None,
            refusal_redirection=refusal_redirection,
            refusal_context=refusal_context,
        )
        _refusal_duration_ms = (time.time() - _refusal_t0) * 1000.0
        refusal_content = refusal_result.text

        metadata = ResponseMetadata.from_decision(
            decision=decision,
            request_id=request.request_id,
            risk_score=risk_score,
            processing_time_ms=processing_time_ms,
            risk_category=risk_cat_str,
            decision_explanation=explanation,
            constitution_loaded_ok=(getattr(constitution, "constitution_loaded_ok", None) if constitution else None),
            predicted_action=RiskPolicyAction.DENY.value,
            early_stop_reason="REFUSE",
            must_refuse=True,
            refusal_reason="[REFUSAL_HIGH_RISK]",
            refusal_domain=domain,
            refusal_redirection_source=(
                "domain_overlay"
                if (refusal_redirection and domain != "general")
                else ("refusal_context" if refusal_context.safe_redirection_guidance else "none")
            ),
            safe_refusal_focus=refusal_context.safe_refusal_focus,
            operational_risk=(
                getattr(
                    getattr(risk_estimation, "operational_risk", None),
                    "value",
                    getattr(risk_estimation, "operational_risk", ""),
                )
                or ""
            ),
            requested_instructions=bool(getattr(risk_estimation, "requested_instructions", False)),
            intent_to_harm=bool(getattr(risk_estimation, "intent_to_harm", False)),
            intent_operational=bool(getattr(risk_estimation, "intent_operational", False)),
        )
        result = OrchestratorResult(
            response=FinalResponse(content=refusal_content, response_type=ResponseType.FULL_REFUSAL, metadata=metadata),
            request_id=request.request_id,
            path_taken=path_taken_refuse,
            path=decision.path,
            total_cycles=0,
            converged=False,
        )
        self._events.emit_llm_call(
            cycle=0,
            phase="refusal",
            module="orchestration",
            action="refuse (fast_path)",
            duration_ms=_refusal_duration_ms,
            prompt=refusal_result.user_prompt,
            system_prompt=refusal_result.system_prompt,
            raw_response=refusal_content,
            attempts=refusal_result.attempts,
            sequence_in_cycle=6,
        )
        try:
            self._events.emit_decision_trace(
                request_id=request.request_id,
                stage="RESPONSE",
                sequence=3,
                trace_json=json.dumps(
                    {
                        "path": decision.path,
                        "final_action": "REFUSE",
                        "total_cycles": 0,
                        "response_content": refusal_content,
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            pass
        trace.response_type = getattr(ResponseType.FULL_REFUSAL, "value", "full_refusal")
        trace.deliberation_cycles_actual = 0
        trace.modules_called = set()
        trace.converged = False
        result.trace = trace
        return result
