"""
DeliberationRunner: gestisce cicli deliberativi e chiamate ai moduli
(critic, simulator, perspectives, hindsight, generation).
NON decide path; restituisce stato/risultato al controller.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal, Union, cast

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.delib_context import DelibContext
from moralstack.models.risk.categories import OperationalRisk, RiskCategory, RiskPolicyAction
from moralstack.observability.context import set_current_cycle
from moralstack.orchestration._policy_helpers import (
    CONSTRAINED_GENERATION_INSTRUCTION,
    SAFE_COMPLETE_GENERATION_INSTRUCTION,
    sanitize_policy_output,
)
from moralstack.orchestration.convergence import (
    build_raw_outcome_for_log,
    enforce_convergence_invariants,
    log_convergence_event,
)
from moralstack.orchestration.convergence_evaluator import ConvergenceEvaluator
from moralstack.orchestration.diagnostics import orch_debug_log
from moralstack.orchestration.guidance_builder import build_aggregated_guidance
from moralstack.orchestration.language_resolver import resolve_prompt_with_language
from moralstack.orchestration.orchestration_event_taxonomy import (
    AGGREGATED_GUIDANCE_EVALUATED,
    CONVERGENCE_EVALUATED,
    CRITIC_SHORT_CIRCUIT_TRIGGERED,
    EARLY_CONVERGENCE_ACCEPTED,
    EARLY_CONVERGENCE_REJECTED,
    PARALLEL_STRATEGY_SELECTED,
    RELEVANT_PRINCIPLES_RETRIEVED,
    RELEVANT_PRINCIPLES_REUSED,
    SIMULATOR_EXECUTED,
    SIMULATOR_GATE_DECISION,
    SIMULATOR_SKIPPED,
)
from moralstack.orchestration.overlay_policy import get_constitution_safe
from moralstack.orchestration.persistence_helpers import record_decision_trace, record_llm_call
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.types import (
    ConvergenceOutcome,
    Decision,
    DecisionType,
    DeliberationDependencies,
    DeliberationState,
    FinalResponse,
    GenerationError,
    LoggerProtocol,
    OrchestratorConfig,
    OrchestratorResult,
    OrchestratorTimeoutError,
    PolicyGenerationResultProtocol,
    ProcessedRequest,
    RequestAnalysisContext,
    ResponseMetadata,
    ResponseType,
    RiskEstimationProtocol,
    risk_category_str,
)
from moralstack.persistence.sink import persist_orchestration_event
from moralstack.runtime.trace.decision_trace import DecisionTrace, append_decision_trace, normalize_trace_fields
from moralstack.runtime.trace.trace_stages import CYCLE_SUMMARY, REQUEST_ANALYSIS_CONTEXT

# Matches CriticProtocol / module context_mode for thin vs full prompts.
DelibContextMode = Literal["full", "thin"]

_LOG = logging.getLogger(__name__)


def _emit_aggregated_guidance_observability(
    state: DeliberationState,
    guidance: str,
    telemetry: dict[str, Any],
) -> None:
    """Persist orchestration event and structured log for aggregated guidance (rewrite path)."""
    empty = not guidance.strip()
    reason_codes = [
        "AGGREGATED_GUIDANCE_EVALUATED",
        "REWRITE_SKIPPED_NO_SUBSTANTIVE_GUIDANCE" if empty else "REWRITE_GUIDANCE_READY",
    ]
    _LOG.info(
        "aggregated_guidance_evaluated cycle=%s empty=%s telemetry=%s",
        state.cycle,
        empty,
        telemetry,
    )
    try:
        persist_orchestration_event(
            cycle=state.cycle,
            stage="deliberation",
            component="guidance_builder",
            event_type=AGGREGATED_GUIDANCE_EVALUATED,
            decision="rewrite_skipped" if empty else "rewrite_prepared",
            status="skipped" if empty else "ok",
            reason_codes=reason_codes,
            inputs={
                "cycle": state.cycle,
                "draft_response_len": len(state.draft_response or ""),
                "critiques_count": len(state.critiques),
                "simulations_count": len(state.simulations),
                "perspectives_count": len(state.perspectives) if state.perspectives else 0,
            },
            outputs={
                "guidance_char_len": len(guidance),
                "guidance_empty": empty,
                "rewrite_decision": "skipped" if empty else "prepared",
            },
            payload={
                **telemetry,
                "guidance_char_len": len(guidance),
                "guidance_empty": empty,
                "short_summary": (
                    "No substantive guidance after signal filter; rewrite skipped."
                    if empty
                    else "Substantive guidance aggregated for policy rewrite."
                ),
            },
        )
    except Exception:
        _LOG.debug("emit AGGREGATED_GUIDANCE_EVALUATED failed", exc_info=True)


def _policy_llm_model_for_action(policy: Any, action: str) -> str | None:
    """Effective OpenAI model name for policy generate vs rewrite (rewrite may use MORALSTACK_POLICY_REWRITE_MODEL)."""
    if policy is None:
        return None
    if action == "rewrite":
        rw = getattr(policy, "rewrite_model", None)
        if rw is not None:
            return str(rw)
    m = getattr(policy, "model", None)
    return str(m) if m is not None else None


def _module_model(module: Any) -> str | None:
    """Return the OpenAI model name used by a cognitive module (critic, simulator, …).

    Each module stores its inner ``OpenAIPolicy`` as ``self.policy``; fall back
    to ``module.model`` if present.
    """
    if module is None:
        return None
    inner = getattr(module, "policy", None)
    if inner is not None:
        m = getattr(inner, "model", None)
        if m is not None:
            return str(m)
    m = getattr(module, "model", None)
    return str(m) if m is not None else None


# Logical order within a deliberation cycle for journey/report display (sequence_in_cycle).
SEQ_POLICY = 1
SEQ_CRITIC = 2
SEQ_SIMULATOR = 3
SEQ_PERSPECTIVES = 4
SEQ_HINDSIGHT = 5
SEQ_REFUSAL_OR_FINALIZE = 6

ParallelSchedulerStrategy = Literal["critic_gated", "full_parallel"]

_SCHEDULER_REASON_ORDER: tuple[str, ...] = (
    "PREVIOUS_HARD_VIOLATION",
    "INTENT_TO_HARM_TRUE",
    "OPERATIONAL_RISK_HIGH",
    "RISK_POLICY_ACTION_DENY",
    "HIGH_RISK_POSTURE",
    "REQUESTED_INSTRUCTIONS_SENSITIVE_POSTURE",
)


@dataclass(frozen=True)
class ParallelStrategySelection:
    """Risk-aware parallel module scheduling (execution only; no governance semantics)."""

    strategy: ParallelSchedulerStrategy
    reason_codes: tuple[str, ...]
    posture_summary: dict[str, Any]


@dataclass(frozen=True)
class SimulatorGateDecision:
    """Conservative simulator run vs skip (execution only; does not change governance)."""

    should_run: bool
    reason_codes: tuple[str, ...]
    diagnostics: dict[str, Any]


def _prior_cycle_hard_violation_critiques(state: DeliberationState) -> bool:
    """True if any critique from a prior cycle reported violated_hard (current cycle critique not yet run)."""
    for cr in state.critiques:
        if getattr(cr, "violated_hard", False):
            return True
    return False


def _emit_hindsight_diagnostic(
    *,
    outcome: str,
    request_id: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit hindsight path diagnostics via ``orch_debug_log``.

    Persistence follows ``MORALSTACK_PERSIST_MODE`` / ``get_persist_mode()``:
    ``db_only`` -> SQLite ``debug_events``; ``dual`` -> DB + ``.debug/debug.log``;
    ``file_only`` -> NDJSON file only. Does not raise.
    """
    try:
        from moralstack.observability.config import get_observability_mode

        data: dict[str, Any] = {
            "component": "hindsight_diagnostic",
            "outcome": outcome,
            "persist_mode": get_observability_mode(),
        }
        if extra:
            data.update(extra)
        orch_debug_log(
            "deliberation_runner.py:hindsight",
            "hindsight_diagnostic",
            data,
            hypothesis_id="H-hindsight-path",
            request_id=request_id or "",
        )
        _LOG.info(
            "hindsight_diagnostic outcome=%s request_id=%s persist_mode=%s",
            outcome,
            request_id or "",
            data.get("persist_mode", ""),
        )
    except Exception:
        _LOG.debug("hindsight_diagnostic emit failed", exc_info=True)


def _policy_text(result: PolicyGenerationResultProtocol) -> str:
    """Extract response text from policy generation result; fallback to str(result) for raw types."""
    return getattr(result, "text", str(result))


def _policy_prompt_used(result: PolicyGenerationResultProtocol, fallback: str) -> str:
    """Extract prompt_used from policy result; fallback if absent."""
    return getattr(result, "prompt_used", None) or fallback


def _policy_system_used(result: PolicyGenerationResultProtocol, fallback: str) -> str:
    """Extract system_used from policy result; fallback if absent."""
    return getattr(result, "system_used", None) or fallback


def _token_usage_json_from_result(result: Any) -> str | None:
    """Build token usage json from result-like objects used by deliberative modules."""
    tokens_used = int(getattr(result, "tokens_used", 0) or 0)
    prompt_tokens = getattr(result, "prompt_tokens", None)
    completion_tokens = getattr(result, "completion_tokens", None)
    if tokens_used == 0 and prompt_tokens is None and completion_tokens is None:
        return None
    return json.dumps(
        {
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": tokens_used,
        }
    )


def _constitution_corrupted(constitution: object) -> bool:
    """Return True if constitution is marked corrupted (single point for optional attribute)."""
    return bool(getattr(constitution, "constitution_corrupted", False))


class DeliberationRunner:
    """
    Esegue cicli deliberativi e path fast/benign/safe_complete.
    Non decide path; il controller governa il flusso.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        deps: DeliberationDependencies,
        protected_system_prompt: str,
        logger: LoggerProtocol | None,
        assembler: ResponseAssembler,
    ) -> None:
        self.config = config
        self.policy = deps.policy
        self.critic = deps.critic
        self.simulator = deps.simulator
        self.hindsight = deps.hindsight
        self.perspectives = deps.perspectives
        self.constitution_store = deps.constitution_store
        self._output_protector = deps.output_protector
        self._protected_system_prompt = protected_system_prompt
        self.logger = logger
        self.assembler = assembler
        self._convergence_evaluator = ConvergenceEvaluator(config)
        self._current_start_time: float = 0.0
        self._executor: ThreadPoolExecutor | None = None
        self._request_analysis_reuse_targets: list[str] = []

    def _effective_max_cycles(self, risk_estimation: RiskEstimationProtocol) -> int:
        risk_score = risk_estimation.score if hasattr(risk_estimation, "score") else 0.5
        if risk_score >= self.config.risk_thresholds.low:
            return int(self.config.max_deliberation_cycles)
        rc = getattr(risk_estimation, "risk_category", None)
        rc_val = getattr(rc, "value", str(rc or "")).strip().lower()
        if rc_val in ("sensitive", "morally_nuanced"):
            return int(self.config.max_deliberation_cycles)
        return 1

    def _get_executor(self) -> ThreadPoolExecutor:
        """Executor lazy-initialized per parallel module calls."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=3)
        return self._executor

    def close(self) -> None:
        """Chiude il ThreadPoolExecutor se presente."""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def _retrieval_top_k_for_request(self) -> int:
        """Align single-shot retrieval with critic.critique_with_relevant_principles (top_k_principles)."""
        if self.critic is not None:
            cfg = getattr(self.critic, "config", None)
            if cfg is not None:
                tk = getattr(cfg, "top_k_principles", None)
                if isinstance(tk, int) and tk > 0:
                    return tk
        return 10

    def _try_build_request_analysis_context(
        self,
        request: ProcessedRequest,
    ) -> RequestAnalysisContext | None:
        """Single constitution-store retrieval for relevant principles + constitution object (per request path)."""
        if self.constitution_store is None:
            return None
        request_id = request.request_id or ""
        top_k = self._retrieval_top_k_for_request()
        try:
            t0 = time.time()
            started_ms = int(t0 * 1000)
            relevant = self.constitution_store.get_relevant_principles(
                query=request.prompt,
                top_k=top_k,
                domain=request.get_domain(),
            )
            t1 = time.time()
            constitution = get_constitution_safe(self.constitution_store, request.get_domain())
            retrieval_debug: dict[str, Any] = {}
            try:
                gd = getattr(self.constitution_store, "get_debug_info", None)
                if callable(gd):
                    retrieval_debug = gd() or {}
            except Exception:
                retrieval_debug = {}
            pc = retrieval_debug.get("prefilter_cache_status")
            pc_str: str | None
            if isinstance(pc, str):
                pc_str = pc
            elif pc is None:
                pc_str = None
            else:
                pc_str = str(pc)
            return RequestAnalysisContext(
                relevant_principles=tuple(relevant),
                constitution=constitution,
                detected_domain=request.get_domain(),
                retrieval_metadata=dict(retrieval_debug),
                prefilter_cache_status=pc_str,
                retrieval_count=len(relevant),
                retrieval_duration_ms=round((t1 - t0) * 1000, 1),
                retrieval_started_at_ms=started_ms,
                retrieval_top_k=top_k,
            )
        except Exception as e:
            _LOG.warning(
                "get_relevant_principles failed request_id=%s error_type=%s error=%s",
                request_id,
                type(e).__name__,
                e,
            )
            return None

    def _record_retrieval_start_and_event(
        self,
        *,
        request_id: str,
        request: ProcessedRequest,
        request_analysis: RequestAnalysisContext,
    ) -> None:
        """Trace RELEVANT_PRINCIPLES + orchestration RELEVANT_PRINCIPLES_RETRIEVED (request-scoped retrieval)."""
        relevant = list(request_analysis.relevant_principles)
        principle_ids = [p.id for p in relevant]
        relevant_principles_detail = [{"id": p.id, "title": p.title or "", "level": p.level or "soft"} for p in relevant]
        retrieval_debug = request_analysis.retrieval_metadata
        record_decision_trace(
            request_id=request_id,
            stage="RELEVANT_PRINCIPLES",
            sequence=0,
            trace_json=json.dumps(
                {
                    "relevant_principle_ids": principle_ids,
                    "relevant_principles": relevant_principles_detail,
                    "domain": (request.get_domain() or "") or "",
                    "started_at": request_analysis.retrieval_started_at_ms,
                    "duration_ms": request_analysis.retrieval_duration_ms,
                    "parallel_retrieval": True,
                    "retrieval_top_k": request_analysis.retrieval_top_k,
                },
                ensure_ascii=False,
            ),
        )
        try:
            persist_orchestration_event(
                cycle=None,
                stage="retrieval",
                component="constitution",
                event_type=RELEVANT_PRINCIPLES_RETRIEVED,
                decision=str(len(relevant)),
                status="ok",
                duration_ms=request_analysis.retrieval_duration_ms,
                payload={
                    "principles_count": len(relevant),
                    "principle_ids": principle_ids,
                    "constitution_domain": (request.get_domain() or "") or "",
                    "prefilter_cache_status": retrieval_debug.get("prefilter_cache_status"),
                    "retrieval_count": len(relevant),
                    "retrieval_top_k": request_analysis.retrieval_top_k,
                    "source": "deliberation_runner",
                },
            )
        except Exception:
            _LOG.debug("emit RELEVANT_PRINCIPLES_RETRIEVED failed", exc_info=True)

    def _emit_request_analysis_context_finalize(
        self,
        *,
        request_id: str,
        request_analysis: RequestAnalysisContext | None,
        risk_estimation: RiskEstimationProtocol,
    ) -> None:
        """Single REQUEST_ANALYSIS_CONTEXT trace at end of deliberation with reuse_targets populated."""
        if request_analysis is None:
            return
        try:
            relevant = list(request_analysis.relevant_principles)
            relevant_principles_detail = [{"id": p.id, "title": p.title or "", "level": p.level or "soft"} for p in relevant]
            rd = request_analysis.retrieval_metadata
            reuse_targets = list(self._request_analysis_reuse_targets)
            rq = DecisionTrace(
                request_id=request_id,
                stage=REQUEST_ANALYSIS_CONTEXT,
                sequence=100,
                risk_score=float(getattr(risk_estimation, "score", 0.5) or 0.5),
            )
            rq.stage_payload = {
                "relevant_principles": relevant_principles_detail,
                "constitution_domain": (request_analysis.detected_domain or "") or "",
                "retrieval_count": request_analysis.retrieval_count,
                "reuse_targets": reuse_targets,
                "reuse_count": len(reuse_targets),
                "prefilter_cache_status": rd.get("prefilter_cache_status"),
                "prefilter_cache_reason": rd.get("prefilter_cache_invalidation_reason"),
                "prefilter_keywords_changed": rd.get("prefilter_keywords_changed"),
                "prefilter_keywords_fingerprint_prefix": rd.get("prefilter_keywords_fingerprint_prefix") or "",
                "parallel_retrieval": True,
                "request_scoped": True,
                "retrieval_duration_ms": request_analysis.retrieval_duration_ms,
                "retrieval_top_k": request_analysis.retrieval_top_k,
            }
            normalize_trace_fields(rq)
            append_decision_trace(rq)
        except Exception:
            _LOG.debug("emit REQUEST_ANALYSIS_CONTEXT finalize failed", exc_info=True)

    def run_benign_fast_path(
        self,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol,
        start_time: float,
        *,
        decision: Decision,
        decision_explanation: DecisionExplanation | None = None,
        speculative_draft: str | None = None,
    ) -> OrchestratorResult:
        """FAST PATH per operational_risk == NONE. Nessun modulo deliberativo."""
        from moralstack.orchestration.diagnostics import orch_debug_log

        orch_debug_log(
            "orchestrator.py:_benign_fast_path",
            "entering benign_fast_path",
            {"decision.path": decision.path, "deliberation_cycles": 0},
            "H-benign-entry",
            request_id=request.request_id or "",
        )
        if self.policy is not None:
            try:
                if speculative_draft:
                    content = speculative_draft
                    record_llm_call(
                        self.logger,
                        None,
                        {
                            "cycle": 0,
                            "phase": "policy_generate",
                            "module": "policy",
                            "action": "generate (speculative-reuse," " benign_fast_path)",
                            "model": _policy_llm_model_for_action(self.policy, "generate"),
                            "duration_ms": 0.0,
                            "prompt": request.prompt[:200],
                            "raw_response": content[:200],
                            "sequence_in_cycle": SEQ_POLICY,
                        },
                    )
                else:
                    prompt_text = resolve_prompt_with_language(
                        request.prompt,
                        risk_estimation.detected_language or "",
                        request.prompt,
                    )
                    start_gen = time.time()
                    try:
                        result = self.policy.generate(
                            prompt=prompt_text,
                            system=self._protected_system_prompt,
                        )
                    except TypeError:
                        result = self.policy.generate(prompt_text)
                    elapsed = (time.time() - start_gen) * 1000
                    response_text = _policy_text(result)
                    prompt_used = _policy_prompt_used(
                        result,
                        prompt_text,
                    )
                    system_used = _policy_system_used(
                        result,
                        self._protected_system_prompt or "",
                    )
                    record_llm_call(
                        self.logger,
                        None,
                        {
                            "cycle": 0,
                            "phase": "policy_generate",
                            "module": "policy",
                            "action": "generate (benign_fast_path)",
                            "started_at": int(start_gen * 1000),
                            "duration_ms": elapsed,
                            "prompt": prompt_used,
                            "system_prompt": system_used or "",
                            "raw_response": response_text,
                            "sequence_in_cycle": SEQ_POLICY,
                            "token_usage_json": result.token_usage_json(),
                        },
                    )
                    protection_result = self._output_protector.validate(response_text)
                    content = protection_result.cleaned
            except Exception as e:
                raise GenerationError(f"Generation failed: {e}")
        else:
            content = f"[Mock response to: {request.prompt[:50]}...]"
        processing_time_ms = int((time.time() - start_time) * 1000)
        risk_score = risk_estimation.score
        metadata = ResponseMetadata.from_decision(
            decision=decision,
            request_id=request.request_id or "",
            risk_score=risk_score,
            processing_time_ms=processing_time_ms,
            risk_category=risk_category_str(risk_estimation),
            decision_explanation=decision_explanation,
            predicted_action=RiskPolicyAction.ALLOW.value,
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
        response = FinalResponse(content=content, response_type=ResponseType.DIRECT, metadata=metadata)
        return OrchestratorResult(
            response=response,
            request_id=request.request_id,
            path_taken="fast",
            path=decision.path,
            total_cycles=0,
            converged=True,
        )

    def run_safe_complete_path(
        self,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol,
        start_time: float,
        *,
        decision: Decision,
        decision_explanation: DecisionExplanation | None = None,
    ) -> OrchestratorResult:
        """SAFE_COMPLETE path: genera con istruzioni caveat; nessun ciclo deliberativo."""
        from moralstack.orchestration.diagnostics import orch_debug_log

        orch_debug_log(
            "orchestrator.py:_safe_complete_path",
            "entering safe_complete_path",
            {"decision.path": decision.path, "deliberation_cycles": 0},
            "H-safe-entry",
            request_id=request.request_id or "",
        )
        safe_system = (self._protected_system_prompt or "") + "\n\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION
        if self.policy is not None:
            try:
                start_gen = time.time()
                prompt_text = resolve_prompt_with_language(
                    request.prompt,
                    risk_estimation.detected_language or "",
                    request.prompt,
                )
                try:
                    result = self.policy.generate(prompt=prompt_text, system=safe_system)
                except TypeError:
                    result = self.policy.generate(prompt_text)
                elapsed = (time.time() - start_gen) * 1000
                response_text = _policy_text(result)
                protection_result = self._output_protector.validate(response_text)
                content = protection_result.cleaned
                prompt_used = _policy_prompt_used(result, prompt_text)
                system_used = _policy_system_used(result, safe_system)
                record_llm_call(
                    self.logger,
                    None,
                    {
                        "cycle": 0,
                        "phase": "policy_generate",
                        "module": "policy",
                        "action": "generate (safe_complete_path)",
                        "started_at": int(start_gen * 1000),
                        "duration_ms": elapsed,
                        "prompt": prompt_used,
                        "system_prompt": system_used or "",
                        "raw_response": response_text,
                        "sequence_in_cycle": SEQ_POLICY,
                        "token_usage_json": result.token_usage_json(),
                    },
                )
            except Exception as e:
                raise GenerationError(f"Generation failed: {e}")
        else:
            content = f"[SAFE_COMPLETE mock: {request.prompt[:50]}...]"
        processing_time_ms = int((time.time() - start_time) * 1000)
        risk_score = risk_estimation.score
        domain = request.get_domain()
        intent_type = (risk_estimation.intent_type or "").strip().lower() or None
        domain_overlay_val = (domain.strip() if isinstance(domain, str) and domain else None) or None
        metadata = ResponseMetadata.from_decision(
            decision=decision,
            request_id=request.request_id or "",
            risk_score=risk_score,
            processing_time_ms=processing_time_ms,
            risk_category=risk_category_str(risk_estimation),
            decision_explanation=decision_explanation,
            predicted_action=RiskPolicyAction.ALLOW_WITH_CAVEAT.value,
            intent_type=intent_type,
            domain_overlay=domain_overlay_val,
            caveat_present=True,
            safe_alternative_present=True,
            no_prescriptive_language=True,
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
        response = FinalResponse(content=content, response_type=ResponseType.WITH_CAVEAT, metadata=metadata)
        return OrchestratorResult(
            response=response,
            request_id=request.request_id,
            path_taken="deliberative",
            path=decision.path,
            total_cycles=0,
            converged=True,
        )

    def run_fast_path(
        self,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol,
        start_time: float,
        *,
        decision: Decision,
        constitution: Any | None = None,
        decision_explanation: DecisionExplanation | None = None,
        speculative_draft: str | None = None,
    ) -> OrchestratorResult:
        """Path veloce: genera draft + quick check costituzionale;
        se fallisce passa a deliberative."""
        from moralstack.orchestration.diagnostics import orch_debug_log

        orch_debug_log(
            "orchestrator.py:_fast_path",
            "entering _fast_path",
            {"decision.path": decision.path},
            "H-fast-entry",
            request_id=request.request_id or "",
        )
        if constitution is None and self.constitution_store is not None:
            constitution = get_constitution_safe(
                self.constitution_store,
                request.get_domain(),
            )
        state = DeliberationState(cycle=0)
        if self.policy is not None:
            try:
                if speculative_draft:
                    state.draft_response = speculative_draft
                    reuse_model = _policy_llm_model_for_action(self.policy, "generate")
                    record_llm_call(
                        self.logger,
                        {
                            "module": "policy",
                            "action": "generate (speculative-reuse," " fast_path)",
                            "prompt": request.prompt[:200],
                            "response": speculative_draft[:200],
                            "duration_ms": 0.0,
                            "model": reuse_model,
                        },
                        {
                            "cycle": 0,
                            "phase": "policy_generate",
                            "module": "policy",
                            "action": "generate (speculative-reuse," " fast_path)",
                            "model": reuse_model,
                            "duration_ms": 0.0,
                            "prompt": request.prompt[:200],
                            "raw_response": speculative_draft[:200],
                            "sequence_in_cycle": SEQ_POLICY,
                        },
                    )
                else:
                    start_gen = time.time()
                    prompt_text = resolve_prompt_with_language(
                        request.prompt,
                        risk_estimation.detected_language or "",
                        request.prompt,
                    )
                    try:
                        result = self.policy.generate(
                            prompt=prompt_text,
                            system=self._protected_system_prompt,
                        )
                    except TypeError:
                        result = self.policy.generate(prompt_text)
                    elapsed = (time.time() - start_gen) * 1000
                    response_text = _policy_text(result)
                    protection_result = self._output_protector.validate(response_text)
                    if protection_result.had_leakage:
                        record_llm_call(
                            self.logger,
                            {
                                "module": "output_protection",
                                "action": "leakage_detected" " (fast_path)",
                                "prompt": "Type: " f"{protection_result.leakage_type}",
                                "response": "Cleaned from "
                                f"{len(response_text)} to "
                                f"{len(protection_result.cleaned)}"
                                " chars",
                                "duration_ms": 0.0,
                            },
                            {
                                "cycle": 0,
                                "phase": "output_protection",
                                "module": "output_protection",
                                "action": "leakage_detected" " (fast_path)",
                                "duration_ms": 0.0,
                                "raw_response": {
                                    "leakage_type": protection_result.leakage_type,
                                    "original_len": len(response_text),
                                    "cleaned_len": len(
                                        protection_result.cleaned,
                                    ),
                                    "had_leakage": True,
                                },
                                "sequence_in_cycle": SEQ_POLICY,
                            },
                        )
                    state.draft_response = protection_result.cleaned
                    prompt_used = _policy_prompt_used(
                        result,
                        prompt_text,
                    )
                    system_used = _policy_system_used(
                        result,
                        self._protected_system_prompt,
                    )
                    record_llm_call(
                        self.logger,
                        {
                            "module": "policy",
                            "action": "generate (fast_path)",
                            "prompt": request.prompt,
                            "response": state.draft_response,
                            "duration_ms": elapsed,
                        },
                        {
                            "cycle": 0,
                            "phase": "policy_generate",
                            "module": "policy",
                            "action": "generate (fast_path)",
                            "started_at": int(start_gen * 1000),
                            "duration_ms": elapsed,
                            "prompt": prompt_used,
                            "system_prompt": system_used or "",
                            "raw_response": response_text,
                            "sequence_in_cycle": SEQ_POLICY,
                            "token_usage_json": result.token_usage_json(),
                        },
                    )
            except Exception as e:
                raise GenerationError(f"Generation failed: {e}")
        else:
            state.draft_response = f"[Mock response to: {request.prompt[:50]}...]"
        if self.critic is not None and constitution is not None:
            try:
                quick_result = self.critic.quick_check(request.prompt, state.draft_response, constitution)
                if not quick_result.passed:
                    state_delib, risk_score, outcome = self.run_deliberative_path(
                        request,
                        risk_estimation,
                        start_time,
                        constitution=constitution,
                        speculative_draft=state.draft_response,
                    )
                    return self._build_deliberative_result(
                        request,
                        state_delib,
                        risk_score,
                        start_time,
                        risk_estimation,
                        outcome=outcome,
                        constitution=constitution,
                    )
            except Exception as e:
                rid = request.request_id or ""
                _LOG.warning(
                    "run_fast_path quick_check failed request_id=%s error_type=%s error=%s",
                    rid,
                    type(e).__name__,
                    e,
                )
        processing_time = int((time.time() - start_time) * 1000)
        risk_score = risk_estimation.score
        if constitution is not None and _constitution_corrupted(constitution):
            risk_score = 1.0
        response = self.assembler.assemble(
            request,
            state,
            decision,
            risk_score=risk_score,
            processing_time_ms=processing_time,
            constitution=constitution,
            risk_estimation=risk_estimation,
            decision_explanation=decision_explanation,
            constitution_store=self.constitution_store,
        )
        if getattr(response.metadata, "final_action", "") == "REFUSE" or response.response_type == ResponseType.FULL_REFUSAL:
            record_llm_call(
                self.logger,
                None,
                {
                    "cycle": state.cycle,
                    "phase": "refusal",
                    "module": "orchestration",
                    "action": "refuse (deliberative)",
                    "duration_ms": 0.0,
                    "prompt": request.prompt or "",
                    "raw_response": response.content,
                    "sequence_in_cycle": SEQ_REFUSAL_OR_FINALIZE,
                },
            )
            try:
                import json

                record_decision_trace(
                    request_id=request.request_id,
                    stage="RESPONSE",
                    sequence=3,
                    trace_json=json.dumps(
                        {
                            "path": "FAST_PATH",
                            "final_action": "REFUSE",
                            "total_cycles": 0,
                            "response_content": response.content,
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception:
                pass
        return OrchestratorResult(
            response=response,
            request_id=request.request_id,
            path_taken="fast",
            path="FAST_PATH",
            total_cycles=0,
            converged=True,
            errors=list(state.errors) if state.errors else None,
        )

    def _build_deliberative_result(
        self,
        request: ProcessedRequest,
        state: DeliberationState,
        risk_score: float,
        start_time: float,
        risk_estimation: RiskEstimationProtocol,
        outcome: ConvergenceOutcome | None = None,
        constitution: Any | None = None,
    ) -> OrchestratorResult:
        """Helper: costruisce OrchestratorResult da state (usato da run_fast_path
        quando quick_check fallisce)."""
        from moralstack.orchestration.decision_service import decide_action

        decision1, explanation1 = decide_action(
            request,
            risk_estimation,
            state.last_critique,
            state.simulations[-1] if state.simulations else None,
            state.hindsight,
            append_pre_policy_trace=False,
        )
        processing_time = int((time.time() - start_time) * 1000)
        if constitution is None and self.constitution_store is not None:
            constitution = get_constitution_safe(self.constitution_store, request.get_domain())
        if constitution is not None and _constitution_corrupted(constitution):
            risk_score = 1.0
        converged = outcome.converged if outcome is not None else (state.decision == DecisionType.CONVERGED)
        response = self.assembler.assemble(
            request,
            state,
            decision1,
            risk_score=risk_score,
            processing_time_ms=processing_time,
            constitution=constitution,
            risk_estimation=risk_estimation,
            decision_explanation=explanation1,
            constitution_store=self.constitution_store,
        )
        if getattr(response.metadata, "final_action", "") == "REFUSE" or response.response_type == ResponseType.FULL_REFUSAL:
            record_llm_call(
                self.logger,
                None,
                {
                    "cycle": state.cycle,
                    "phase": "refusal",
                    "module": "orchestration",
                    "action": "refuse (deliberative)",
                    "duration_ms": 0.0,
                    "prompt": request.prompt or "",
                    "raw_response": response.content,
                    "sequence_in_cycle": SEQ_REFUSAL_OR_FINALIZE,
                },
            )
            try:
                import json

                record_decision_trace(
                    request_id=request.request_id,
                    stage="RESPONSE",
                    sequence=3,
                    trace_json=json.dumps(
                        {
                            "path": "DELIBERATIVE_PATH",
                            "final_action": "REFUSE",
                            "total_cycles": state.cycle,
                            "response_content": response.content,
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception:
                pass
        _snap_raw = getattr(state, "_convergence_evaluation_snapshot", None)
        _conv_snap = dict(_snap_raw) if isinstance(_snap_raw, dict) else None
        return OrchestratorResult(
            response=response,
            request_id=request.request_id,
            path_taken="deliberative",
            path="DELIBERATIVE_PATH",
            total_cycles=state.cycle,
            converged=converged,
            errors=list(state.errors) if state.errors else None,
            convergence_snapshot=_conv_snap,
        )

    def _emit_cycle_summary_trace(
        self,
        *,
        request_id: str,
        state: DeliberationState,
        outcome: ConvergenceOutcome,
        max_cycles: int,
        risk_estimation: RiskEstimationProtocol,
    ) -> None:
        """Observability-only: one CYCLE_SUMMARY decision trace per deliberation cycle."""
        try:
            dyn_strat = getattr(state, "_parallel_scheduler_strategy", None)
            if self.config.parallel_module_calls:
                strat = str(dyn_strat) if isinstance(dyn_strat, str) else "parallel_modules"
            else:
                strat = "sequential_modules"
            sched_reasons = list(getattr(state, "_parallel_scheduler_reason_codes", None) or [])
            short_circuit = bool(getattr(state, "_critic_short_circuit", False))
            planned: list[str] = ["critic", "simulator", "perspectives"]
            if self.config.enable_hindsight:
                planned.append("hindsight")
            sim_ran_flag = getattr(state, "_simulator_ran_this_cycle", None)
            sim_gate_codes = list(getattr(state, "_simulator_gate_reason_codes", None) or [])
            sim_carry = bool(getattr(state, "_simulator_carry_forward", False))
            executed: list[str] = []
            if state.last_critique is not None:
                executed.append("critic")
            if sim_ran_flag is True:
                executed.append("simulator")
            elif sim_ran_flag is None and state.simulations and self.config.enable_simulation and self.simulator is not None:
                executed.append("simulator")
            if state.perspectives:
                executed.append("perspectives")
            if state.hindsight is not None:
                executed.append("hindsight")
            skipped: list[str] = []
            mod_sk = getattr(state, "modules_skipped", None)
            if isinstance(mod_sk, dict):
                skipped = [str(k) for k in mod_sk.keys()]
            sched_skip = getattr(state, "_scheduler_skipped_modules", None)
            if isinstance(sched_skip, list) and sched_skip:
                for m in sched_skip:
                    if m not in skipped:
                        skipped.append(m)
            if (
                sim_ran_flag is False
                and self.config.enable_simulation
                and self.simulator is not None
                and "simulator" not in skipped
            ):
                skipped.append("simulator")
            lc = state.last_critique
            critic_decision = (getattr(lc, "decision", "") or "").strip().upper() if lc is not None else ""
            violations_count = 0
            violated_hard = False
            if lc is not None:
                viol = getattr(lc, "violations", None) or []
                violations_count = len(viol)
                violated_hard = bool(getattr(lc, "violated_hard", False))
            sem_harm: float | None = None
            if state.simulations:
                last_sim = state.simulations[-1]
                sem_harm = float(getattr(last_sim, "semantic_expected_harm", 0.0) or 0.0)
            perspectives_weighted_approval: float | None = None
            if state.perspectives:
                ap = [float(getattr(p, "approval_score", 0.0) or 0.0) for p in state.perspectives]
                perspectives_weighted_approval = sum(ap) / max(len(ap), 1)
            conv_snap = getattr(state, "_convergence_evaluation_snapshot", None)
            if not isinstance(conv_snap, dict):
                conv_snap = {}
            delib_decision = state.decision.value if state.decision is not None else None
            early_considered = conv_snap.get("early_convergence_considered")
            early_accepted = conv_snap.get("early_convergence_accepted")
            conv_reason_codes = conv_snap.get("convergence_reason_codes") or []
            payload = {
                "cycle": state.cycle,
                "scheduler_strategy": strat,
                "scheduler_reason_codes": sched_reasons,
                "critic_short_circuit": short_circuit,
                "modules_planned": planned,
                "modules_executed": executed,
                "modules_skipped": skipped,
                "modules_cancelled": [],
                "critic_decision": critic_decision,
                "violations_count": violations_count,
                "violated_hard": violated_hard,
                "semantic_expected_harm": sem_harm,
                "simulator_gate_enabled": bool(self.config.enable_simulator_gating),
                "simulator_ran_this_cycle": sim_ran_flag,
                "simulator_gate_reason_codes": sim_gate_codes,
                "simulator_carry_forward": sim_carry,
                "perspectives_weighted_approval": perspectives_weighted_approval,
                "convergence_decision": outcome.stop_reason,
                "convergence_reason": outcome.stop_reason,
                "deliberation_decision": delib_decision,
                "early_convergence_considered": early_considered,
                "early_convergence_accepted": early_accepted,
                "convergence_reason_codes": list(conv_reason_codes),
                "next_action": "continue" if outcome.should_continue else "stop",
                "max_cycles": max_cycles,
            }
            dt = DecisionTrace(
                request_id=request_id,
                stage=CYCLE_SUMMARY,
                sequence=200 + int(state.cycle),
                risk_score=float(getattr(risk_estimation, "score", 0.5) or 0.5),
            )
            dt.sim_semantic_expected_harm = float(sem_harm or 0.0)
            if state.simulations:
                dt.sim_dominant_harm_types = list(getattr(state.simulations[-1], "dominant_harm_types", []) or [])
            dt.total_cycles = int(state.cycle)
            dt.stage_payload = payload
            normalize_trace_fields(dt)
            append_decision_trace(dt)
            ce_payload: dict[str, Any] = {
                "should_continue": outcome.should_continue,
                "converged": outcome.converged,
                "stop_reason": outcome.stop_reason,
                "cycle": state.cycle,
                "deliberation_decision": delib_decision,
                "critic_decision": critic_decision,
                "violations_count": violations_count,
                "violated_hard": violated_hard,
                "semantic_expected_harm": sem_harm,
                "perspectives_weighted_approval": perspectives_weighted_approval,
                "early_convergence_considered": early_considered,
                "early_convergence_accepted": early_accepted,
                "decision": str(outcome.stop_reason or ""),
                "reason_codes": list(conv_reason_codes),
            }
            persist_orchestration_event(
                cycle=state.cycle,
                stage="deliberation",
                component="convergence",
                event_type=CONVERGENCE_EVALUATED,
                decision=str(outcome.stop_reason or ""),
                status="ok" if outcome.converged else "continue",
                sequence=state.cycle,
                reason_codes=list(conv_reason_codes),
                inputs={
                    "cycle": state.cycle,
                    "max_cycles": max_cycles,
                    "risk_score": float(getattr(risk_estimation, "score", 0.5) or 0.5),
                    "critic_decision": critic_decision,
                    "violations_count": violations_count,
                },
                outputs={
                    "should_continue": outcome.should_continue,
                    "converged": outcome.converged,
                    "stop_reason": outcome.stop_reason,
                    "deliberation_decision": delib_decision,
                },
                payload=ce_payload,
            )
            if state.cycle == 1 and early_considered is True:
                if early_accepted is True:
                    persist_orchestration_event(
                        cycle=1,
                        stage="deliberation",
                        component="convergence",
                        event_type=EARLY_CONVERGENCE_ACCEPTED,
                        decision=str(delib_decision or ""),
                        status="ok",
                        sequence=state.cycle * 100 + 1,
                        reason_codes=list(conv_reason_codes),
                        payload={
                            "cycle": 1,
                            "reason_codes": list(conv_reason_codes),
                            "next_action": "stop" if not outcome.should_continue else "continue",
                            "deliberation_decision": delib_decision,
                            "evidence_summary": conv_snap.get("cycle1_evidence_summary") or {},
                            "stop_reason": outcome.stop_reason,
                        },
                    )
                elif early_accepted is False:
                    persist_orchestration_event(
                        cycle=1,
                        stage="deliberation",
                        component="convergence",
                        event_type=EARLY_CONVERGENCE_REJECTED,
                        decision=str(delib_decision or ""),
                        status="continue",
                        sequence=state.cycle * 100 + 2,
                        reason_codes=list(conv_reason_codes),
                        payload={
                            "cycle": 1,
                            "reason_codes": list(conv_reason_codes),
                            "deliberation_decision": delib_decision,
                            "evidence_not_strong_enough": list(conv_reason_codes),
                        },
                    )
        except Exception:
            _LOG.debug("emit CYCLE_SUMMARY trace failed", exc_info=True)

    def run_deliberative_path(
        self,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol,
        start_time: float,
        *,
        constrained_generation: bool = False,
        constitution: Any | None = None,
        speculative_draft: str | None = None,
    ) -> tuple[DeliberationState, float, ConvergenceOutcome]:
        """
        Esegue cicli deliberativi. Restituisce (state, risk_score, outcome) per assemblaggio.
        L'unica autorità sul loop è outcome post-enforcement: "continue"
        non sopravvive a cicli esauriti.

        Args:
            speculative_draft: Pre-generated draft from parallel overlap with
                risk estimation.  When provided *and* constrained_generation is
                False, the draft is used as the cycle-1 starting point,
                skipping the initial generation call.
        """
        from moralstack.orchestration.diagnostics import orch_debug_log

        if constitution is None and self.constitution_store is not None:
            constitution = get_constitution_safe(
                self.constitution_store,
                request.get_domain(),
            )
        request_id = request.request_id or ""
        orch_debug_log(
            "orchestrator.py:_deliberative_path",
            "entering _deliberative_path",
            {"request_id": request_id},
            "H-delib-entry",
            request_id=request_id,
        )
        self._current_start_time = start_time
        state = DeliberationState(cycle=0)
        # Pre-set speculative draft for cycle 1 when safe to do so.
        # constrained_generation uses a different system prompt so the
        # speculative draft (generated with the base prompt) is not suitable.
        if speculative_draft and not constrained_generation:
            state.draft_response = sanitize_policy_output(
                speculative_draft,
            )
        risk_score = risk_estimation.score
        max_cycles = self._effective_max_cycles(risk_estimation)
        # Constrained generation (clearly_harmful): the policy is already instructed to
        # produce a refusal. A second rewrite cycle cannot improve a refusal — perspectives
        # feedback ("add concrete examples") would push toward operational content that
        # constrained_generation explicitly forbids. Cap to 1 cycle for full determinism.
        if constrained_generation:
            max_cycles = 1
        # Request-scoped retrieval: single get_relevant_principles + constitution for downstream reuse.
        self._request_analysis_reuse_targets = []
        request_analysis: RequestAnalysisContext | None = None
        if self.constitution_store is not None:
            request_analysis = self._try_build_request_analysis_context(request)
            if request_analysis is not None:
                self._record_retrieval_start_and_event(
                    request_id=request_id,
                    request=request,
                    request_analysis=request_analysis,
                )
        orch_debug_log(
            "orchestrator.py:_deliberative_path",
            "before while loop",
            {"max_cycles": max_cycles, "state.cycle": state.cycle, "risk_score": risk_score},
            "H-delib-while",
            request_id=request_id,
        )
        last_outcome: ConvergenceOutcome | None = None
        while state.cycle < max_cycles:
            elapsed = (time.time() - start_time) * 1000
            if elapsed > self.config.timeout_ms:
                raise OrchestratorTimeoutError(f"Timeout after {elapsed:.0f}ms (max: {self.config.timeout_ms}ms)")
            remaining_time = (self.config.timeout_ms - elapsed) / 1000
            record_llm_call(
                self.logger,
                {
                    "module": "orchestrator",
                    "action": "pre_cycle_check",
                    "prompt": f"Tempo rimanente: {remaining_time:.1f}s | Ciclo {state.cycle + 1}/{max_cycles}",
                    "response": "",
                    "duration_ms": 0.0,
                },
                None,
            )
            state = self._deliberation_cycle(
                state,
                request,
                risk_estimation=risk_estimation,
                constrained_generation=constrained_generation,
                max_cycles=max_cycles,
                constitution=constitution,
                request_analysis=request_analysis,
            )
            raw = build_raw_outcome_for_log(state.cycle, max_cycles, state.decision)
            log_convergence_event("CONVERGENCE_RAW", request_id=request_id, **raw)
            outcome = enforce_convergence_invariants(state.cycle, max_cycles, state.decision)
            last_outcome = outcome
            log_convergence_event(
                "CONVERGENCE_ENFORCED",
                request_id=request_id,
                should_continue=outcome.should_continue,
                converged=outcome.converged,
                stop_reason=outcome.stop_reason,
                cycle=outcome.cycle,
                max_cycles=outcome.max_cycles,
            )
            self._emit_cycle_summary_trace(
                request_id=request_id,
                state=state,
                outcome=outcome,
                max_cycles=max_cycles,
                risk_estimation=risk_estimation,
            )
            if not outcome.should_continue:
                break
            elapsed = (time.time() - start_time) * 1000
            if elapsed > self.config.timeout_ms:
                raise OrchestratorTimeoutError(f"Timeout after {elapsed:.0f}ms (max: {self.config.timeout_ms}ms)")
        if last_outcome is None:
            last_outcome = enforce_convergence_invariants(state.cycle, max_cycles, state.decision)
        if (
            last_outcome.converged
            and state.decision == DecisionType.CONVERGED_WITH_SUGGESTIONS
            and self.config.enable_soft_revision
        ):
            state = self._soft_revision_pass(state, request, risk_estimation)
        log_convergence_event(
            "CONVERGENCE_EXIT",
            request_id=request_id,
            stop_reason=last_outcome.stop_reason,
            total_cycles=state.cycle,
            converged=last_outcome.converged,
        )
        orch_debug_log(
            "orchestrator.py:_deliberative_path",
            "exiting _deliberative_path",
            {
                "total_cycles": state.cycle,
                "converged": last_outcome.converged,
                "stop_reason": last_outcome.stop_reason,
            },
            "H-delib-exit",
            request_id=request_id,
        )
        self._emit_request_analysis_context_finalize(
            request_id=request_id,
            request_analysis=request_analysis,
            risk_estimation=risk_estimation,
        )
        return state, risk_score, last_outcome

    def _deliberation_cycle(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        risk_estimation: RiskEstimationProtocol | None = None,
        constrained_generation: bool = False,
        max_cycles: int = 1,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> DeliberationState:
        """Singolo ciclo deliberativo: generate/revisione, critique, simulate,
        perspectives, hindsight, decisione."""
        state.cycle += 1
        set_current_cycle(state.cycle)
        state._simulator_ran_this_cycle = None
        state._simulator_gate_reason_codes = []
        state._simulator_carry_forward = False
        state_info = (
            f"Ciclo #{state.cycle}\nDraft response length: "
            f"{len(state.draft_response)} chars\nCritiques: {len(state.critiques)}\n"
            f"Simulations: {len(state.simulations)}\n"
            f"Hindsight: {'Yes' if state.hindsight else 'No'}\n"
            f"Perspectives: {len(state.perspectives) if state.perspectives else 0}"
        )
        record_llm_call(
            self.logger,
            {
                "module": "orchestrator",
                "action": f"deliberation_cycle_{state.cycle}_start",
                "prompt": state_info,
                "response": "",
                "duration_ms": 0.0,
            },
            None,
        )
        state = self._generate_or_revise(
            state,
            request,
            risk_estimation=risk_estimation,
            constrained_generation=constrained_generation,
        )

        delib_context, context_mode, computed_max_cycles = self._build_delib_context(
            state, request, risk_estimation, request_analysis=request_analysis
        )
        if computed_max_cycles != max_cycles:
            orch_debug_log(
                "deliberation_runner.py:_deliberation_cycle",
                "max_cycles drift detected between loop and context",
                {
                    "authoritative_max_cycles": max_cycles,
                    "computed_max_cycles": computed_max_cycles,
                    "cycle": state.cycle,
                },
                hypothesis_id="H-max-cycles-drift",
                request_id=request.request_id or "",
            )

        if self.config.parallel_module_calls:
            state = self._run_critique_simulate_perspectives_parallel(
                state,
                request,
                delib_context=delib_context,
                context_mode=context_mode,
                risk_estimation=risk_estimation,
                max_cycles=max_cycles,
                constitution=constitution,
                request_analysis=request_analysis,
            )
        else:
            state = self._run_critique_simulate_perspectives_sequential(
                state,
                request,
                delib_context=delib_context,
                context_mode=context_mode,
                risk_estimation=risk_estimation,
                max_cycles=max_cycles,
                constitution=constitution,
                request_analysis=request_analysis,
            )
        # Constitutional override: perspectives cannot approve content
        # that violates HARD constraints
        self._apply_constitutional_perspective_override(state)
        if state.has_critical_violations or (
            state.last_critique is not None and getattr(state.last_critique, "violated_hard", False)
        ):
            if state.last_critique is not None and getattr(state.last_critique, "decision", None) == "REFUSE":
                state.decision = DecisionType.REFUSE
                return state
            state.decision = DecisionType.REVISE
            return state

        state = self._apply_hindsight_if_needed(
            state, request, delib_context, context_mode=context_mode, max_cycles=max_cycles
        )

        return self._finalize_cycle(state, max_cycles, risk_estimation=risk_estimation)

    def _build_delib_context(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol | None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> tuple[DelibContext | None, DelibContextMode, int]:
        """Build DelibContext for thin prompts in cycle 2+ and compute effective max_cycles."""
        delib_context = None
        context_mode: DelibContextMode = "full"
        if self.config.enable_thin_mode and state.cycle > 1:
            context_mode = "thin"
        if risk_estimation is not None:
            from moralstack.pipeline.context_builder import build_context

            prev_ctx = state._prev_delib_context
            delib_context = build_context(
                user_prompt=request.prompt,
                risk_result=risk_estimation,
                domain=request.get_domain(),
                draft_text=state.draft_response,
                prev_context=prev_ctx,
                cycle=state.cycle,
            )
            # Propagate simulator_domain_guidance from overlay (if available)
            if self.constitution_store is not None:
                _dc_constitution = get_constitution_safe(self.constitution_store, request.get_domain())
                if request_analysis is not None and request_analysis.constitution is not None:
                    _dc_constitution = request_analysis.constitution
                _dc_overlay = getattr(_dc_constitution, "active_overlay", None) if _dc_constitution else None
                if _dc_overlay is not None:
                    _guidance = getattr(_dc_overlay, "simulator_domain_guidance", "") or ""
                    if _guidance:
                        delib_context.simulator_domain_guidance = _guidance
            state._prev_delib_context = delib_context

        # risk_score = risk_estimation.score if risk_estimation is not None else 0.5
        max_cycles = self._effective_max_cycles(risk_estimation) if risk_estimation is not None else 1
        return delib_context, context_mode, max_cycles

    def _apply_hindsight_if_needed(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        delib_context: DelibContext | None,
        *,
        context_mode: DelibContextMode = "full",
        max_cycles: int = 1,
    ) -> DeliberationState:
        """Run hindsight evaluation when enabled, available, and not gated."""
        req_id = request.request_id or ""
        if self.config.enable_hindsight:
            if self.hindsight is None:
                record_llm_call(
                    self.logger,
                    {
                        "module": "orchestrator",
                        "action": "hindsight (SKIPPED)",
                        "prompt": "Hindsight non disponibile",
                        "response": "",
                        "duration_ms": 0.0,
                    },
                    None,
                )
                _emit_hindsight_diagnostic(
                    outcome="skipped_no_module",
                    request_id=req_id,
                    extra={"enable_hindsight": True},
                )
            elif not self._should_run_hindsight(state, state.cycle, max_cycles):
                record_llm_call(
                    self.logger,
                    {
                        "module": "orchestrator",
                        "action": "hindsight (GATED)",
                        "prompt": f"Hindsight skipped: not final cycle ({state.cycle}/{max_cycles})",
                        "response": "",
                        "duration_ms": 0.0,
                    },
                    None,
                )
                state._hindsight_skipped_reason = "not_final_cycle"
                _emit_hindsight_diagnostic(
                    outcome="gated_not_final_cycle",
                    request_id=req_id,
                    extra={
                        "state_cycle": state.cycle,
                        "max_cycles": max_cycles,
                        "enable_hindsight_gating": self.config.enable_hindsight_gating,
                    },
                )
            else:
                _emit_hindsight_diagnostic(
                    outcome="invoke_evaluate",
                    request_id=req_id,
                    extra={
                        "state_cycle": state.cycle,
                        "max_cycles": max_cycles,
                        "enable_hindsight_gating": self.config.enable_hindsight_gating,
                    },
                )
                state = self._evaluate_hindsight(state, request, delib_context=delib_context, context_mode=context_mode)
        else:
            record_llm_call(
                self.logger,
                {
                    "module": "orchestrator",
                    "action": "hindsight (DISABLED)",
                    "prompt": "Hindsight disabilitato in config",
                    "response": "",
                    "duration_ms": 0.0,
                },
                None,
            )
            _emit_hindsight_diagnostic(
                outcome="disabled_by_config",
                request_id=req_id,
                extra={"enable_hindsight": False},
            )
        return state

    def _finalize_cycle(
        self,
        state: DeliberationState,
        max_cycles: int,
        *,
        risk_estimation: RiskEstimationProtocol | None = None,
    ) -> DeliberationState:
        """Determine decision, clean up resources, and log cycle completion."""
        state.decision = self._convergence_evaluator.determine_decision(state, risk_estimation=risk_estimation)
        decision_str = state.decision.value if state.decision is not None else str(state.decision)
        cycles_exhausted_display = state.cycle >= max_cycles
        decision_converged = state.decision in (DecisionType.CONVERGED, DecisionType.CONVERGED_WITH_SUGGESTIONS)
        final_state = (
            f"End of cycle #{state.cycle}\nDecision: {decision_str}\n"
            f"Cycles exhausted: {cycles_exhausted_display}\n"
            f"Converging decision: {decision_converged}\n"
        )
        if state.hindsight:
            final_state += f"Hindsight score: {state.hindsight_score:.2f}\n"
        if state.critiques:
            last_crit = state.critiques[-1]
            nv = len(last_crit.violations)
            final_state += f"Last violations: {nv}"
        record_llm_call(
            self.logger,
            {
                "module": "orchestrator",
                "action": f"deliberation_cycle_{state.cycle}_complete",
                "prompt": final_state,
                "response": "",
                "duration_ms": 0.0,
            },
            None,
        )
        return state

    def _risk_posture_requires_simulator_run(
        self,
        risk_estimation: RiskEstimationProtocol,
    ) -> tuple[bool, str]:
        """Elevated request posture: always re-run simulator when in doubt."""
        rc = risk_estimation.risk_category
        rc_enum = rc if isinstance(rc, RiskCategory) else None
        rc_val = str(getattr(rc, "value", rc) or "").strip().lower()
        op = risk_estimation.operational_risk
        op_val = str(getattr(op, "value", op) or "").strip().upper()
        rpa = risk_estimation.risk_policy_action
        rpa_val = str(getattr(rpa, "value", rpa) or "").strip().upper()

        if bool(getattr(risk_estimation, "intent_to_harm", False)):
            return True, "HIGH_RISK_POSTURE_REQUIRE_RUN"
        if op_val == OperationalRisk.HIGH.value:
            return True, "HIGH_RISK_POSTURE_REQUIRE_RUN"
        if rpa_val == RiskPolicyAction.DENY.value:
            return True, "HIGH_RISK_POSTURE_REQUIRE_RUN"
        if rc_enum in (RiskCategory.POTENTIALLY_HARMFUL, RiskCategory.CLEARLY_HARMFUL):
            return True, "HIGH_RISK_POSTURE_REQUIRE_RUN"
        if rc_enum is None and rc_val in ("potentially_harmful", "clearly_harmful"):
            return True, "HIGH_RISK_POSTURE_REQUIRE_RUN"
        if bool(getattr(risk_estimation, "requested_instructions", False)) and (
            rc_enum in (RiskCategory.SENSITIVE, RiskCategory.POTENTIALLY_HARMFUL, RiskCategory.CLEARLY_HARMFUL)
            or rc_val in ("sensitive", "potentially_harmful", "clearly_harmful")
        ):
            return True, "HIGH_RISK_POSTURE_REQUIRE_RUN"
        return False, ""

    def _critique_blocks_simulator_skip(self, lc: Any) -> tuple[bool, str]:
        """Current-cycle critic must be clean to allow conservative skip."""
        if lc is None:
            return True, "CURRENT_CRITIC_MISSING_REQUIRE_RUN"
        if getattr(lc, "violated_hard", False):
            return True, "CURRENT_CRITIC_HARD_VIOLATION_REQUIRE_RUN"
        if bool(getattr(lc, "has_critical_violations", False)):
            return True, "CURRENT_CRITIC_CRITICAL_VIOLATION_REQUIRE_RUN"
        dec = (getattr(lc, "decision", "") or "").strip().upper()
        if dec == "REFUSE":
            return True, "CURRENT_CRITIC_REFUSE_REQUIRE_RUN"
        viol = getattr(lc, "violations", None) or []
        if len(viol) > 0:
            return True, "CURRENT_CRITIC_VIOLATIONS_PRESENT_REQUIRE_RUN"
        return False, ""

    def _parallel_precritic_allows_conservative_skip(
        self,
        risk_estimation: RiskEstimationProtocol,
        prev_sem: float,
    ) -> bool:
        """Allow skip without current critic only when prior-only signals are strong."""
        if prev_sem >= self.config.simulator_gate_skip_max_prior_semantic_harm:
            return False
        rc = risk_estimation.risk_category
        rc_enum = rc if isinstance(rc, RiskCategory) else None
        rc_val = str(getattr(rc, "value", rc) or "").strip().lower()
        if rc_enum not in (RiskCategory.BENIGN, RiskCategory.MORALLY_NUANCED) and rc_val not in (
            "benign",
            "morally_nuanced",
        ):
            return False
        if float(risk_estimation.score) >= self.config.risk_thresholds.medium:
            return False
        if bool(getattr(risk_estimation, "intent_to_harm", False)):
            return False
        op = risk_estimation.operational_risk
        op_val = str(getattr(op, "value", op) or "").strip().upper()
        if op_val == OperationalRisk.HIGH.value:
            return False
        return True

    def _evaluate_simulator_gate(
        self,
        state: DeliberationState,
        risk_estimation: RiskEstimationProtocol | None,
        delib_context: DelibContext | None,
        cycle: int,
        *,
        current_critique_available: bool,
    ) -> SimulatorGateDecision:
        """
        Conservative simulator gating: default to run; skip only with strong evidence.
        When `current_critique_available` is False (full parallel), critic-based skip checks are not used;
        skip is allowed only under stricter prior-only conditions.
        """
        diagnostics: dict[str, Any] = {
            "cycle": cycle,
            "current_critique_available": current_critique_available,
        }
        if not self.config.enable_simulator_gating:
            return SimulatorGateDecision(
                True,
                ("GATING_DISABLED_ALWAYS_RUN",),
                diagnostics,
            )
        if cycle <= 1:
            return SimulatorGateDecision(True, ("FIRST_CYCLE_REQUIRE_RUN",), diagnostics)

        if not state.simulations:
            return SimulatorGateDecision(True, ("NO_PRIOR_SIMULATION_REQUIRE_RUN",), diagnostics)

        if risk_estimation is None:
            return SimulatorGateDecision(True, ("INSUFFICIENT_EVIDENCE_REQUIRE_RUN",), diagnostics)

        prev_sim = state.simulations[-1]
        prev_sem = float(getattr(prev_sim, "semantic_expected_harm", 0.0) or 0.0)
        diagnostics["prior_semantic_expected_harm"] = prev_sem

        need_run, risk_code = self._risk_posture_requires_simulator_run(risk_estimation)
        if need_run:
            diagnostics["risk_posture"] = "elevated"
            return SimulatorGateDecision(True, (risk_code,), diagnostics)

        if prev_sem >= self.config.simulator_gate_semantic_harm_threshold:
            return SimulatorGateDecision(
                True,
                ("PRIOR_SEMANTIC_HARM_ELEVATED_REQUIRE_RUN",),
                diagnostics,
            )

        if prev_sem >= self.config.simulator_gate_skip_max_prior_semantic_harm:
            return SimulatorGateDecision(
                True,
                ("PRIOR_HARM_BORDERLINE_BAND_REQUIRE_RUN",),
                diagnostics,
            )

        delta_chars = 0
        if delib_context and delib_context.change_log:
            delta_chars = sum(len(c) for c in delib_context.change_log)
        diagnostics["candidate_delta_chars"] = delta_chars
        if delta_chars >= self.config.simulator_gate_delta_chars_threshold:
            return SimulatorGateDecision(
                True,
                ("CANDIDATE_CHANGED_MATERIAL_REQUIRE_RUN",),
                diagnostics,
            )

        risk_score = risk_estimation.score
        ar = risk_estimation.actionability_risk
        ar_val = getattr(ar, "value", str(ar or "")) if ar is not None else ""
        diagnostics["risk_score"] = risk_score
        diagnostics["actionability_risk"] = ar_val
        if 0.3 <= risk_score <= 0.7 and ar_val == "HIGH":
            return SimulatorGateDecision(
                True,
                ("BORDERLINE_ACTIONABILITY_HIGH_REQUIRE_RUN",),
                diagnostics,
            )

        if current_critique_available:
            lc = state.last_critique
            crit_need, crit_code = self._critique_blocks_simulator_skip(lc)
            if crit_need:
                diagnostics["critic_decision"] = (getattr(lc, "decision", "") or "").strip().upper() if lc else ""
                diagnostics["violations_count"] = len(getattr(lc, "violations", None) or [])
                return SimulatorGateDecision(True, (crit_code,), diagnostics)
        elif not self._parallel_precritic_allows_conservative_skip(risk_estimation, prev_sem):
            return SimulatorGateDecision(
                True,
                ("PARALLEL_PRECRITIC_INSUFFICIENT_SIGNAL_REQUIRE_RUN",),
                diagnostics,
            )

        skip_codes: list[str] = ["LOW_PRIOR_HARM_CONSERVATIVE_SKIP"]
        if current_critique_available:
            skip_codes.append("CRITIC_CLEAN_SKIP")
        else:
            skip_codes.append("PARALLEL_PRIOR_ONLY_SIGNAL")
        diagnostics["carry_forward_prior_simulation"] = True
        return SimulatorGateDecision(False, tuple(skip_codes), diagnostics)

    def _emit_simulator_gate_decision_event(
        self,
        *,
        state: DeliberationState,
        gate: SimulatorGateDecision,
    ) -> None:
        """Persist SIMULATOR_GATE_DECISION (best-effort)."""
        payload: dict[str, Any] = {
            "cycle": state.cycle,
            "should_run": gate.should_run,
            "reason_codes": list(gate.reason_codes),
        }
        if gate.diagnostics:
            payload.update(dict(gate.diagnostics))
        try:
            persist_orchestration_event(
                cycle=state.cycle,
                stage="deliberation",
                component="simulator",
                event_type=SIMULATOR_GATE_DECISION,
                decision="run" if gate.should_run else "skip",
                status="ok",
                sequence=state.cycle * 10 + 3,
                reason_codes=list(gate.reason_codes),
                payload=payload,
            )
        except Exception:
            _LOG.debug("emit SIMULATOR_GATE_DECISION failed", exc_info=True)

    def _emit_simulator_executed_event(
        self,
        *,
        state: DeliberationState,
        duration_ms: float,
        gate: SimulatorGateDecision,
    ) -> None:
        try:
            sim_out: dict[str, Any] = {"duration_ms": duration_ms}
            if state.simulations:
                last_s = state.simulations[-1]
                sim_out["semantic_expected_harm"] = float(getattr(last_s, "semantic_expected_harm", 0.0) or 0.0)
                sim_out["expected_valence"] = float(getattr(last_s, "expected_valence", 0.0) or 0.0)
            persist_orchestration_event(
                cycle=state.cycle,
                stage="deliberation",
                component="simulator",
                event_type=SIMULATOR_EXECUTED,
                decision="run",
                status="ok",
                sequence=state.cycle * 10 + 4,
                duration_ms=duration_ms,
                reason_codes=list(gate.reason_codes),
                inputs={
                    "cycle": state.cycle,
                    "draft_response_len": len(state.draft_response or ""),
                    "gate_reason_codes": list(gate.reason_codes),
                },
                outputs=sim_out,
                payload={
                    "cycle": state.cycle,
                    "duration_ms": duration_ms,
                    "gate_reason_codes": list(gate.reason_codes),
                },
            )
        except Exception:
            _LOG.debug("emit SIMULATOR_EXECUTED failed", exc_info=True)

    def _emit_simulator_skipped_event(
        self,
        *,
        state: DeliberationState,
        gate: SimulatorGateDecision,
    ) -> None:
        try:
            persist_orchestration_event(
                cycle=state.cycle,
                stage="deliberation",
                component="simulator",
                event_type=SIMULATOR_SKIPPED,
                decision="skip",
                status="ok",
                sequence=state.cycle * 10 + 4,
                reason_codes=list(gate.reason_codes),
                payload={
                    "cycle": state.cycle,
                    "reason_codes": list(gate.reason_codes),
                    "carry_forward_prior_simulation": bool(
                        gate.diagnostics.get("carry_forward_prior_simulation"),
                    ),
                },
            )
        except Exception:
            _LOG.debug("emit SIMULATOR_SKIPPED failed", exc_info=True)

    def _run_simulator_after_gate(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None,
        context_mode: DelibContextMode,
        gate: SimulatorGateDecision,
        emit_gate_decision: bool = True,
    ) -> DeliberationState:
        """Execute simulator or record explicit skip; updates observability fields on state."""
        state._simulator_gate_reason_codes = list(gate.reason_codes)
        if emit_gate_decision:
            self._emit_simulator_gate_decision_event(state=state, gate=gate)
        if not self.config.enable_simulation or self.simulator is None:
            return state
        if gate.should_run:
            t0 = time.time()
            state = self._simulate(state, request, delib_context=delib_context, context_mode=context_mode)
            elapsed = (time.time() - t0) * 1000
            state._simulator_ran_this_cycle = True
            state._simulator_carry_forward = False
            self._emit_simulator_executed_event(state=state, duration_ms=elapsed, gate=gate)
        else:
            state._simulator_ran_this_cycle = False
            state._simulator_carry_forward = True
            record_llm_call(
                self.logger,
                {
                    "module": "orchestrator",
                    "action": "simulate (GATED)",
                    "prompt": "Simulator skipped: carry forward previous result",
                    "response": "",
                    "duration_ms": 0.0,
                },
                None,
            )
            self._emit_simulator_skipped_event(state=state, gate=gate)
        return state

    def _should_run_hindsight(
        self,
        state: DeliberationState,
        cycle: int,
        max_cycles: int,
    ) -> bool:
        """Gating: run hindsight only in final cycle to reduce tokens."""
        if not self.config.enable_hindsight_gating:
            return True
        return cycle >= max_cycles

    def _run_critique_simulate_perspectives_sequential(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: DelibContextMode = "full",
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> DeliberationState:
        state = self._critique(
            state,
            request,
            delib_context=delib_context,
            context_mode=context_mode,
            constitution=constitution,
            request_analysis=request_analysis,
        )
        if self.config.enable_simulation and self.simulator is not None:
            gate = self._evaluate_simulator_gate(
                state,
                risk_estimation,
                delib_context,
                state.cycle,
                current_critique_available=True,
            )
            state = self._run_simulator_after_gate(
                state,
                request,
                delib_context=delib_context,
                context_mode=context_mode,
                gate=gate,
            )
        elif self.config.enable_simulation:
            record_llm_call(
                self.logger,
                {
                    "module": "orchestrator",
                    "action": "simulate (SKIPPED)",
                    "prompt": "Simulator non disponibile",
                    "response": "",
                    "duration_ms": 0.0,
                },
                None,
            )
        else:
            record_llm_call(
                self.logger,
                {
                    "module": "orchestrator",
                    "action": "simulate (DISABLED)",
                    "prompt": "Simulation disabilitata in config",
                    "response": "",
                    "duration_ms": 0.0,
                },
                None,
            )
        if self.config.enable_perspectives and self.perspectives is not None:
            state = self._evaluate_perspectives(state, request, delib_context=delib_context, context_mode=context_mode)
        elif self.config.enable_perspectives:
            record_llm_call(
                self.logger,
                {
                    "module": "orchestrator",
                    "action": "perspectives (SKIPPED)",
                    "prompt": "Perspectives non disponibile",
                    "response": "",
                    "duration_ms": 0.0,
                },
                None,
            )
        else:
            record_llm_call(
                self.logger,
                {
                    "module": "orchestrator",
                    "action": "perspectives (DISABLED)",
                    "prompt": "Perspectives disabilitato in config",
                    "response": "",
                    "duration_ms": 0.0,
                },
                None,
            )
        return state

    def _select_parallel_strategy(
        self,
        *,
        risk_estimation: RiskEstimationProtocol | None,
        state: DeliberationState,
    ) -> ParallelStrategySelection:
        """
        Conservative risk-aware choice between critic_gated and full_parallel.
        Uses only existing risk/cycle signals; does not affect governance semantics.
        """
        if risk_estimation is None:
            strat: ParallelSchedulerStrategy = (
                "full_parallel" if self.config.parallel_critic_with_modules else "critic_gated"
            )
            return ParallelStrategySelection(
                strategy=strat,
                reason_codes=("CONFIG_FALLBACK_NO_RISK_ESTIMATION",),
                posture_summary={},
            )

        rc = risk_estimation.risk_category
        rc_enum = rc if isinstance(rc, RiskCategory) else None
        rc_val = str(getattr(rc, "value", rc) or "").strip().lower()

        op = risk_estimation.operational_risk
        op_val = str(getattr(op, "value", op) or "").strip().upper()

        rpa = risk_estimation.risk_policy_action
        rpa_val = str(getattr(rpa, "value", rpa) or "").strip().upper()

        intent_harm = bool(getattr(risk_estimation, "intent_to_harm", False))
        req_ins = bool(getattr(risk_estimation, "requested_instructions", False))
        prior_hard = _prior_cycle_hard_violation_critiques(state)

        posture_summary: dict[str, Any] = {
            "risk_category": rc_val,
            "operational_risk": op_val,
            "intent_to_harm": intent_harm,
            "requested_instructions": req_ins,
            "risk_policy_action": rpa_val,
            "prior_hard_violation": prior_hard,
        }

        reason_set: set[str] = set()
        if prior_hard:
            reason_set.add("PREVIOUS_HARD_VIOLATION")
        if intent_harm:
            reason_set.add("INTENT_TO_HARM_TRUE")
        if op_val == OperationalRisk.HIGH.value:
            reason_set.add("OPERATIONAL_RISK_HIGH")
        if rpa_val == RiskPolicyAction.DENY.value:
            reason_set.add("RISK_POLICY_ACTION_DENY")
        if rc_enum in (RiskCategory.POTENTIALLY_HARMFUL, RiskCategory.CLEARLY_HARMFUL):
            reason_set.add("HIGH_RISK_POSTURE")
        elif rc_enum is None and rc_val in ("potentially_harmful", "clearly_harmful"):
            reason_set.add("HIGH_RISK_POSTURE")
        if req_ins and (
            rc_enum in (RiskCategory.SENSITIVE, RiskCategory.POTENTIALLY_HARMFUL, RiskCategory.CLEARLY_HARMFUL)
            or rc_val in ("sensitive", "potentially_harmful", "clearly_harmful")
        ):
            reason_set.add("REQUESTED_INSTRUCTIONS_SENSITIVE_POSTURE")

        if reason_set:
            ordered = tuple(r for r in _SCHEDULER_REASON_ORDER if r in reason_set)
            return ParallelStrategySelection(
                strategy="critic_gated",
                reason_codes=ordered,
                posture_summary=posture_summary,
            )
        return ParallelStrategySelection(
            strategy="full_parallel",
            reason_codes=("DEFAULT_LOWER_RISK_PARALLEL",),
            posture_summary=posture_summary,
        )

    def _emit_parallel_strategy_selected_event(
        self,
        *,
        state: DeliberationState,
        selection: ParallelStrategySelection,
    ) -> None:
        """Persist PARALLEL_STRATEGY_SELECTED for audit (best-effort)."""
        payload: dict[str, Any] = {
            "cycle": state.cycle,
            "selected_strategy": selection.strategy,
            "reason_codes": list(selection.reason_codes),
        }
        if selection.posture_summary:
            payload["posture"] = dict(selection.posture_summary)
        try:
            persist_orchestration_event(
                cycle=state.cycle,
                stage="deliberation",
                component="runner",
                event_type=PARALLEL_STRATEGY_SELECTED,
                decision=selection.strategy,
                status="ok",
                sequence=state.cycle * 10 + 1,
                reason_codes=list(selection.reason_codes),
                payload=payload,
            )
        except Exception:
            _LOG.debug("emit PARALLEL_STRATEGY_SELECTED failed", exc_info=True)

    def _run_critique_simulate_perspectives_parallel(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: DelibContextMode = "full",
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> DeliberationState:
        # Pre-import prompt modules to avoid deadlock when threads import concurrently
        import moralstack.prompts.critic_prompt  # noqa: F401
        import moralstack.prompts.perspectives_prompt  # noqa: F401
        import moralstack.prompts.simulator_prompt  # noqa: F401

        state._critic_short_circuit = False
        state._scheduler_skipped_modules = []

        if self.config.enable_dynamic_parallel_scheduler:
            selection = self._select_parallel_strategy(risk_estimation=risk_estimation, state=state)
        else:
            legacy: Literal["full_parallel", "critic_gated"] = (
                "full_parallel" if self.config.parallel_critic_with_modules else "critic_gated"
            )
            selection = ParallelStrategySelection(
                strategy=legacy,
                reason_codes=("LEGACY_STATIC_PARALLEL_CRITIC_CONFIG",),
                posture_summary={},
            )

        state._parallel_scheduler_strategy = selection.strategy
        state._parallel_scheduler_reason_codes = list(selection.reason_codes)
        self._emit_parallel_strategy_selected_event(state=state, selection=selection)

        if selection.strategy == "full_parallel":
            return self._run_full_parallel_evaluation(
                state,
                request,
                delib_context=delib_context,
                context_mode=context_mode,
                risk_estimation=risk_estimation,
                max_cycles=max_cycles,
                constitution=constitution,
                request_analysis=request_analysis,
            )

        return self._run_critic_gated_parallel(
            state,
            request,
            delib_context=delib_context,
            context_mode=context_mode,
            risk_estimation=risk_estimation,
            max_cycles=max_cycles,
            constitution=constitution,
            request_analysis=request_analysis,
        )

    def _run_critic_gated_parallel(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: DelibContextMode = "full",
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> DeliberationState:
        """Original two-stage approach: critic runs first as a gate, then
        simulator + perspectives run in parallel only if no hard violation."""
        state = self._critique(
            state,
            request,
            delib_context=delib_context,
            context_mode=context_mode,
            constitution=constitution,
            request_analysis=request_analysis,
        )
        if state.has_critical_violations or getattr(state.last_critique, "violated_hard", False):
            state._critic_short_circuit = True
            downstream: list[str] = []
            if self.config.enable_simulation and self.simulator is not None:
                downstream.append("simulator")
            if self.config.enable_perspectives and self.perspectives is not None:
                downstream.append("perspectives")
            state._scheduler_skipped_modules = list(downstream)
            lc = state.last_critique
            crit_dec = (getattr(lc, "decision", "") or "").strip().upper() if lc is not None else ""
            viol_n = 0
            if lc is not None:
                viol_n = len(getattr(lc, "violations", None) or [])
            try:
                persist_orchestration_event(
                    cycle=state.cycle,
                    stage="deliberation",
                    component="critic",
                    event_type=CRITIC_SHORT_CIRCUIT_TRIGGERED,
                    decision=crit_dec or "HARD_VIOLATION",
                    status="short_circuit",
                    sequence=state.cycle * 10 + 2,
                    payload={
                        "cycle": state.cycle,
                        "critic_decision": crit_dec,
                        "violations_count": viol_n,
                        "violated_hard": bool(getattr(lc, "violated_hard", False)),
                        "downstream_modules_skipped": downstream,
                    },
                )
            except Exception:
                _LOG.debug("emit CRITIC_SHORT_CIRCUIT_TRIGGERED failed", exc_info=True)
            return state

        n_errors_after_critic = len(state.errors)
        state2 = state.fork()
        state3 = state.fork()

        def do_simulate(
            s: DeliberationState,
            r: ProcessedRequest,
        ) -> DeliberationState:
            if not self.config.enable_simulation or self.simulator is None:
                return s
            gate = self._evaluate_simulator_gate(
                s,
                risk_estimation,
                delib_context,
                s.cycle,
                current_critique_available=True,
            )
            return self._run_simulator_after_gate(
                s,
                r,
                delib_context=delib_context,
                context_mode=context_mode,
                gate=gate,
            )

        def do_perspectives(
            s: DeliberationState,
            r: ProcessedRequest,
        ) -> DeliberationState:
            if not self.config.enable_perspectives or self.perspectives is None:
                return s
            return self._evaluate_perspectives(
                s,
                r,
                delib_context=delib_context,
                context_mode=context_mode,
            )

        ctx2 = contextvars.copy_context()
        ctx3 = contextvars.copy_context()
        executor = self._get_executor()
        fut2 = executor.submit(ctx2.run, do_simulate, state2, request)
        fut3 = executor.submit(ctx3.run, do_perspectives, state3, request)
        s2, s3 = fut2.result(), fut3.result()
        state.simulations = s2.simulations
        state.perspectives = s3.perspectives
        state._perspectives_aggregation = s3._perspectives_aggregation
        state.errors = list(state.errors) + list(s2.errors[n_errors_after_critic:]) + list(s3.errors[n_errors_after_critic:])
        state._simulator_ran_this_cycle = getattr(s2, "_simulator_ran_this_cycle", None)
        state._simulator_carry_forward = bool(getattr(s2, "_simulator_carry_forward", False))
        state._simulator_gate_reason_codes = list(getattr(s2, "_simulator_gate_reason_codes", None) or [])
        return state

    def _run_full_parallel_evaluation(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: DelibContextMode = "full",
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> DeliberationState:
        """Full parallel: critic, simulator, and perspectives all run
        concurrently. On hard violation the sim/persp results are discarded,
        paying extra LLM calls but saving wall-clock time in the common case
        (no hard violation). Decision quality is identical: the convergence
        logic sees exactly the same module outputs."""
        n_errors_before = len(state.errors)
        state_critic = state.fork()
        state_sim = state.fork()
        state_persp = state.fork()

        gate_sim = self._evaluate_simulator_gate(
            state_sim,
            risk_estimation,
            delib_context,
            state.cycle,
            current_critique_available=False,
        )
        self._emit_simulator_gate_decision_event(state=state, gate=gate_sim)
        state._simulator_gate_reason_codes = list(gate_sim.reason_codes)

        def do_critique(
            s: DeliberationState,
            r: ProcessedRequest,
        ) -> DeliberationState:
            return self._critique(
                s,
                r,
                delib_context=delib_context,
                context_mode=context_mode,
                constitution=constitution,
                request_analysis=request_analysis,
            )

        def do_simulate(
            s: DeliberationState,
            r: ProcessedRequest,
        ) -> DeliberationState:
            if not self.config.enable_simulation or self.simulator is None:
                return s
            return self._run_simulator_after_gate(
                s,
                r,
                delib_context=delib_context,
                context_mode=context_mode,
                gate=gate_sim,
                emit_gate_decision=False,
            )

        def do_perspectives(
            s: DeliberationState,
            r: ProcessedRequest,
        ) -> DeliberationState:
            if not self.config.enable_perspectives or self.perspectives is None:
                return s
            return self._evaluate_perspectives(
                s,
                r,
                delib_context=delib_context,
                context_mode=context_mode,
            )

        ctx_c = contextvars.copy_context()
        ctx_s = contextvars.copy_context()
        ctx_p = contextvars.copy_context()
        executor = self._get_executor()
        fut_c = executor.submit(ctx_c.run, do_critique, state_critic, request)
        fut_s = executor.submit(ctx_s.run, do_simulate, state_sim, request)
        fut_p = executor.submit(ctx_p.run, do_perspectives, state_persp, request)

        sc = fut_c.result()
        ss = fut_s.result()
        sp = fut_p.result()

        # Always merge critic results
        state.critiques = sc.critiques
        state.errors = list(state.errors) + list(sc.errors[n_errors_before:])

        # Propagate critic signals into delib_context (matches sequential path)
        if delib_context is not None and state.last_critique is not None:
            critique = state.last_critique
            delib_context.critic_decision = getattr(critique, "decision", "") or ""
            delib_context.critic_violated_hard = bool(getattr(critique, "violated_hard", False))
            if getattr(critique, "violations", None):
                delib_context.critic_violations_summary = "; ".join(
                    f"{v.principle_id}:{getattr(v, 'severity', 0)}" for v in critique.violations[:5]
                )

        hard_violation = state.has_critical_violations or getattr(
            state.last_critique,
            "violated_hard",
            False,
        )
        if hard_violation:
            # Discard sim/persp results — critic authority prevails.
            # Wall-clock time was not wasted (parallel execution).
            return state

        # No hard violation: merge sim + persp results
        state.simulations = ss.simulations
        state.perspectives = sp.perspectives
        state._perspectives_aggregation = sp._perspectives_aggregation
        state.errors = list(state.errors) + list(ss.errors[n_errors_before:]) + list(sp.errors[n_errors_before:])
        state._simulator_ran_this_cycle = getattr(ss, "_simulator_ran_this_cycle", None)
        state._simulator_carry_forward = bool(getattr(ss, "_simulator_carry_forward", False))
        state._simulator_gate_reason_codes = list(getattr(ss, "_simulator_gate_reason_codes", None) or [])
        return state

    def _apply_constitutional_perspective_override(self, state: DeliberationState) -> None:
        """Applica override costituzionale sulle prospettive quando il Critic
        rileva violazioni HARD."""
        from moralstack.orchestration.types import EnsembleResultProtocol
        from moralstack.runtime.modules.perspective_module import (
            EnsembleResult,
            PerspectiveAggregation,
            apply_constitutional_override,
        )

        aggregation = state._perspectives_aggregation
        critic_result = state.last_critique
        if aggregation is not None and critic_result is not None:
            concrete = cast(Union[PerspectiveAggregation, EnsembleResult], aggregation)
            state._perspectives_aggregation = cast(
                EnsembleResultProtocol, apply_constitutional_override(concrete, critic_result)
            )

    def _soft_revision_pass(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol | None = None,
    ) -> DeliberationState:
        """Single rewrite pass that incorporates pending soft suggestions without re-running the
        full deliberative cycle (no critic, no simulator, no hindsight)."""
        if self.policy is None:
            return state
        guidance = build_aggregated_guidance(state)
        if not guidance.strip():
            return state
        det_iso = (risk_estimation.detected_language or "") if risk_estimation is not None else ""
        user_prompt_with_lang = resolve_prompt_with_language(request.prompt, det_iso, request.prompt)
        try:
            start = time.time()
            try:
                result = self.policy.rewrite(
                    user_prompt_with_lang,
                    state.draft_response,
                    guidance,
                    system=self._protected_system_prompt,
                )
            except TypeError:
                result = self.policy.rewrite(user_prompt_with_lang, state.draft_response, guidance)
            elapsed = (time.time() - start) * 1000
            response_text = _policy_text(result)
            protection_result = self._output_protector.validate(response_text)
            state.draft_response = sanitize_policy_output(protection_result.cleaned)
            state.soft_revision_applied = True
            state.soft_revision_guidance_used = guidance
            prompt_used = _policy_prompt_used(result, user_prompt_with_lang)
            system_used = _policy_system_used(result, self._protected_system_prompt)
            soft_model = _policy_llm_model_for_action(self.policy, "rewrite")
            record_llm_call(
                self.logger,
                {
                    "module": "policy",
                    "action": "soft_revision",
                    "prompt": f"Guidance: {guidance[:200]}",
                    "response": state.draft_response[:200],
                    "duration_ms": elapsed,
                    "model": soft_model,
                },
                {
                    "phase": "soft_revision",
                    "module": "policy",
                    "action": "soft_revision",
                    "model": soft_model,
                    "started_at": int(start * 1000),
                    "duration_ms": elapsed,
                    "prompt": prompt_used,
                    "system_prompt": system_used or "",
                    "raw_response": response_text,
                    "sequence_in_cycle": SEQ_POLICY,
                    "token_usage_json": result.token_usage_json(),
                },
            )
        except Exception as e:
            _LOG.warning("Soft revision failed, keeping original draft: %s", e)
            state.errors.append(f"Soft revision failed: {e}")
        return state

    def _generate_or_revise(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        risk_estimation: RiskEstimationProtocol | None = None,
        constrained_generation: bool = False,
    ) -> DeliberationState:
        if self.policy is None:
            state.draft_response = f"[Mock response to: {request.prompt[:50]}...]"
            return state
        # Speculative draft already present from parallel generation:
        # skip redundant LLM call in cycle 1.
        if state.cycle == 1 and state.draft_response:
            reuse_model = _policy_llm_model_for_action(self.policy, "generate")
            record_llm_call(
                self.logger,
                {
                    "module": "policy",
                    "action": "generate (speculative-reuse)",
                    "prompt": request.prompt[:200],
                    "response": state.draft_response[:200],
                    "duration_ms": 0.0,
                    "model": reuse_model,
                },
                {
                    "cycle": 1,
                    "phase": "policy_generate",
                    "module": "policy",
                    "action": "generate (speculative-reuse)",
                    "model": reuse_model,
                    "duration_ms": 0.0,
                    "prompt": request.prompt[:200],
                    "raw_response": state.draft_response[:200],
                    "sequence_in_cycle": SEQ_POLICY,
                },
            )
            return state
        det_iso = (risk_estimation.detected_language or "") if risk_estimation is not None else ""
        pre_rewrite_guidance: str | None = None
        pre_rewrite_telemetry: dict[str, Any] | None = None
        will_rewrite = not (state.cycle == 1 or not state.draft_response)
        if will_rewrite:
            pre_rewrite_telemetry = {}
            pre_rewrite_guidance = build_aggregated_guidance(state, telemetry=pre_rewrite_telemetry)
            _emit_aggregated_guidance_observability(state, pre_rewrite_guidance, pre_rewrite_telemetry)
            if not pre_rewrite_guidance.strip():
                rw_model = _policy_llm_model_for_action(self.policy, "rewrite")
                record_llm_call(
                    self.logger,
                    {
                        "module": "policy",
                        "action": "rewrite (SKIPPED_EMPTY_GUIDANCE)",
                        "prompt": request.prompt[:200],
                        "response": (state.draft_response[:200] if state.draft_response else ""),
                        "duration_ms": 0.0,
                        "model": rw_model,
                    },
                    {
                        "phase": "policy_rewrite",
                        "module": "policy",
                        "action": "rewrite (SKIPPED_EMPTY_GUIDANCE)",
                        "model": rw_model,
                        "started_at": int(time.time() * 1000),
                        "duration_ms": 0.0,
                        "prompt": request.prompt[:200],
                        "system_prompt": "",
                        "raw_response": "",
                        "sequence_in_cycle": SEQ_POLICY,
                        "cycle": state.cycle,
                    },
                )
                return state
        try:
            start = time.time()
            if state.cycle == 1 or not state.draft_response:
                action = "generate"
                prompt_text = resolve_prompt_with_language(request.prompt, det_iso, request.prompt)
                system_prompt = self._protected_system_prompt
                if constrained_generation:
                    system_prompt = (system_prompt or "") + "\n\n" + CONSTRAINED_GENERATION_INSTRUCTION
                try:
                    result = self.policy.generate(prompt=prompt_text, system=system_prompt)
                except TypeError:
                    result = self.policy.generate(prompt_text)
            else:
                action = "rewrite"
                guidance = pre_rewrite_guidance or ""
                prompt_text = f"REVISIONE\nPrompt originale: {request.prompt}\nGuidance: {guidance}"
                user_prompt_with_lang = resolve_prompt_with_language(request.prompt, det_iso, request.prompt)
                # Propagate constrained_generation to rewrite: defense-in-depth.
                # Fix C (max_cycles cap) prevents reaching this branch when
                # constrained_generation=True, but this ensures the constraint
                # is enforced even if that guard is ever relaxed.
                rewrite_system = self._protected_system_prompt
                if constrained_generation:
                    rewrite_system = (rewrite_system or "") + "\n\n" + CONSTRAINED_GENERATION_INSTRUCTION
                try:
                    result = self.policy.rewrite(
                        user_prompt_with_lang,
                        state.draft_response,
                        guidance,
                        system=rewrite_system,
                    )
                except TypeError:
                    result = self.policy.rewrite(user_prompt_with_lang, state.draft_response, guidance)
            elapsed = (time.time() - start) * 1000
            response_text = _policy_text(result)
            protection_result = self._output_protector.validate(response_text)
            if protection_result.had_leakage:
                record_llm_call(
                    self.logger,
                    {
                        "module": "output_protection",
                        "action": "leakage_detected",
                        "prompt": f"Type: {protection_result.leakage_type}",
                        "response": f"Cleaned from {len(response_text)} to {len(protection_result.cleaned)} chars",
                        "duration_ms": 0.0,
                    },
                    {
                        "phase": "output_protection",
                        "module": "output_protection",
                        "action": "leakage_detected",
                        "duration_ms": 0.0,
                        "raw_response": json.dumps(
                            {
                                "leakage_type": protection_result.leakage_type,
                                "original_len": len(response_text),
                                "cleaned_len": len(protection_result.cleaned),
                                "had_leakage": True,
                            }
                        ),
                        "sequence_in_cycle": SEQ_POLICY,
                    },
                )
            state.draft_response = sanitize_policy_output(protection_result.cleaned)
            prompt_used = _policy_prompt_used(result, prompt_text)
            system_used = _policy_system_used(result, self._protected_system_prompt)
            policy_model_label = _policy_llm_model_for_action(self.policy, action)
            record_llm_call(
                self.logger,
                {
                    "module": "policy",
                    "action": action,
                    "prompt": prompt_text,
                    "response": state.draft_response,
                    "duration_ms": elapsed,
                    "model": policy_model_label,
                },
                {
                    "phase": "policy_generate" if action == "generate" else "policy_rewrite",
                    "module": "policy",
                    "action": action,
                    "model": policy_model_label,
                    "started_at": int(start * 1000),
                    "duration_ms": elapsed,
                    "prompt": prompt_used,
                    "system_prompt": system_used or "",
                    "raw_response": response_text,
                    "sequence_in_cycle": SEQ_POLICY,
                    "token_usage_json": result.token_usage_json(),
                },
            )
        except Exception as e:
            state.errors.append(f"Generation error: {e}")
            if not state.draft_response:
                raise GenerationError(f"Cannot generate response: {e}")
        return state

    def _critique(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: DelibContextMode = "full",
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> DeliberationState:
        if self.critic is None or (self.constitution_store is None and constitution is None):
            return state
        try:
            if hasattr(self, "_current_start_time"):  # set by run_deliberative_path
                elapsed = (time.time() - self._current_start_time) * 1000
                elapsed_ratio = elapsed / self.config.timeout_ms
                if elapsed_ratio > 0.90:
                    raise OrchestratorTimeoutError(
                        f"Timeout approaching before critique: {elapsed:.0f}ms / "
                        f"{self.config.timeout_ms}ms ({elapsed_ratio * 100:.1f}%)"
                    )
            start = time.time()
            prompt_text = f"CRITIQUE\nPrompt: {request.prompt}\nResponse: {state.draft_response}"
            prev_violations = ""
            prev_guidance = ""
            if state.last_critique:
                if state.last_critique.violations:
                    prev_violations = "; ".join(
                        f"{v.principle_id}: {v.rationale[:80]}" for v in state.last_critique.violations[:3]
                    )
                prev_guidance = (state.last_critique.revision_guidance or "") if state.last_critique else ""
            has_critique_with_principles = getattr(self.critic, "critique_with_relevant_principles", None) is not None
            use_precomputed = (
                request_analysis is not None
                and len(request_analysis.relevant_principles) > 0
                and getattr(self.critic, "critique", None) is not None
            )
            precomputed_analysis = request_analysis if request_analysis is not None else None
            const_for_precomputed: Any | None = None
            if use_precomputed and precomputed_analysis is not None:
                const_for_precomputed = precomputed_analysis.constitution
                if const_for_precomputed is None:
                    const_for_precomputed = constitution
                if const_for_precomputed is None and self.constitution_store is not None:
                    const_for_precomputed = get_constitution_safe(self.constitution_store, request.get_domain())
                if const_for_precomputed is None:
                    use_precomputed = False
            if use_precomputed and const_for_precomputed is not None and precomputed_analysis is not None:
                critique = self.critic.critique(
                    request.prompt,
                    state.draft_response,
                    const_for_precomputed,
                    principles=list(precomputed_analysis.relevant_principles),
                    request_id=request.request_id or "",
                    delib_context=delib_context,
                    context_mode=context_mode,
                    previous_violations=prev_violations,
                    previous_guidance=prev_guidance,
                )
                if "critic" not in self._request_analysis_reuse_targets:
                    self._request_analysis_reuse_targets.append("critic")
                try:
                    persist_orchestration_event(
                        cycle=state.cycle,
                        stage="deliberation",
                        component="critic",
                        event_type=RELEVANT_PRINCIPLES_REUSED,
                        decision=str(len(precomputed_analysis.relevant_principles)),
                        status="ok",
                        payload={
                            "reuse_target": "critic",
                            "principles_count": len(precomputed_analysis.relevant_principles),
                            "cycle": state.cycle,
                            "request_scoped": True,
                        },
                    )
                except Exception:
                    _LOG.debug("emit RELEVANT_PRINCIPLES_REUSED failed", exc_info=True)
            elif has_critique_with_principles and getattr(self.critic, "store", None) is not None:
                critique = self.critic.critique_with_relevant_principles(
                    request=request.prompt,
                    response=state.draft_response,
                    domain=request.get_domain(),
                    request_id=request.request_id or "",
                    delib_context=delib_context,
                    context_mode=context_mode,
                    previous_violations=prev_violations,
                    previous_guidance=prev_guidance,
                )
            else:
                if constitution is None and self.constitution_store is not None:
                    constitution = get_constitution_safe(self.constitution_store, request.get_domain())
                critique = self.critic.critique(
                    request.prompt,
                    state.draft_response,
                    constitution,
                    request_id=request.request_id or "",
                    delib_context=delib_context,
                    context_mode=context_mode,
                    previous_violations=prev_violations,
                    previous_guidance=prev_guidance,
                )
            elapsed = (time.time() - start) * 1000
            nv = len(critique.violations)
            rg = (critique.revision_guidance[:100]) if critique.revision_guidance else "N/A"
            response_text = f"Violations: {nv}, Guidance: {rg}"
            critic_model = _module_model(self.critic)
            record_llm_call(
                self.logger,
                {
                    "module": "critic",
                    "action": "critique",
                    "prompt": prompt_text,
                    "response": response_text,
                    "duration_ms": elapsed,
                    "model": critic_model,
                },
                {
                    "phase": "critic",
                    "module": "critic",
                    "action": "critique",
                    "model": critic_model,
                    "started_at": int(start * 1000),
                    "duration_ms": elapsed,
                    "prompt": getattr(critique, "prompt", None) or prompt_text,
                    "system_prompt": getattr(critique, "system_prompt", ""),
                    "raw_response": getattr(critique, "raw_response", ""),
                    "parsed_summary_json": response_text,
                    "attempts": getattr(critique, "parse_attempts", 1),
                    "sequence_in_cycle": SEQ_CRITIC,
                    "token_usage_json": _token_usage_json_from_result(critique),
                },
            )
            state.critiques.append(critique)
            # Propagate critic signals into DelibContext for downstream modules
            if delib_context is not None:
                delib_context.critic_decision = getattr(critique, "decision", "") or ""
                delib_context.critic_violated_hard = bool(getattr(critique, "violated_hard", False))
                if critique.violations:
                    delib_context.critic_violations_summary = "; ".join(
                        f"{v.principle_id}:{getattr(v, 'severity', 0)}" for v in critique.violations[:5]
                    )
        except Exception as e:
            state.errors.append(f"Critique error: {e}")
            record_llm_call(
                self.logger,
                {
                    "module": "critic",
                    "action": "critique (ERROR)",
                    "prompt": f"Prompt: {request.prompt}",
                    "response": f"ERROR: {e}",
                    "duration_ms": 0.0,
                },
                None,
            )
        return state

    def _simulate(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: DelibContextMode = "full",
    ) -> DeliberationState:
        if self.simulator is None:
            return state
        try:
            if hasattr(self, "_current_start_time"):  # set by run_deliberative_path
                elapsed = (time.time() - self._current_start_time) * 1000
                elapsed_ratio = elapsed / self.config.timeout_ms
                if elapsed_ratio > self.config.skip_optional_modules_threshold:
                    raise OrchestratorTimeoutError(
                        f"Timeout approaching before simulation: {elapsed:.0f}ms / "
                        f"{self.config.timeout_ms}ms ({elapsed_ratio * 100:.1f}%)"
                    )
                if elapsed_ratio > self.config.soft_timeout_threshold:
                    record_llm_call(
                        self.logger,
                        {
                            "module": "orchestrator",
                            "action": "timeout_warning",
                            "prompt": (
                                f"Warning: Low time remaining for simulation: {self.config.timeout_ms - elapsed:.0f}ms"
                            ),
                            "response": "",
                            "duration_ms": 0.0,
                        },
                        None,
                    )
            start = time.time()
            simulation = self.simulator.simulate(
                request.prompt,
                state.draft_response,
                self.config.num_simulations,
                delib_context=delib_context,
                context_mode=context_mode,
            )
            elapsed = (time.time() - start) * 1000
            ev = simulation.expected_valence
            sem_harm = simulation.semantic_expected_harm
            dom_harms = simulation.dominant_harm_types or []
            worst = simulation.worst_harm
            response_text = (
                f"Consequences: "
                f"{len(simulation.consequences)}, "
                f"Expected valence: {ev:.2f}, Semantic harm: {sem_harm:.2f}, "
                f"Dominant harms: {dom_harms}, Worst harm: {worst}"
            )
            sim_model = _module_model(self.simulator)
            record_llm_call(
                self.logger,
                {
                    "module": "simulator",
                    "action": "simulate",
                    "prompt": f"SIMULATION\nPrompt: {request.prompt}\nResponse: {state.draft_response}",
                    "response": response_text,
                    "duration_ms": elapsed,
                    "model": sim_model,
                },
                {
                    "phase": "simulator",
                    "module": "simulator",
                    "action": "simulate",
                    "model": sim_model,
                    "started_at": int(start * 1000),
                    "duration_ms": elapsed,
                    "prompt": getattr(simulation, "prompt", ""),
                    "system_prompt": getattr(simulation, "system_prompt", ""),
                    "raw_response": getattr(simulation, "raw_response", ""),
                    "parsed_summary_json": json.dumps(
                        {
                            "consequences_count": len(simulation.consequences),
                            "expected_valence": ev,
                            "semantic_expected_harm": sem_harm,
                            "dominant_harm_types": dom_harms,
                            "worst_harm": worst,
                        }
                    ),
                    "attempts": getattr(simulation, "parse_attempts", 1),
                    "sequence_in_cycle": SEQ_SIMULATOR,
                    "token_usage_json": _token_usage_json_from_result(simulation),
                },
            )
            state.simulations.append(simulation)
            from moralstack.orchestration.diagnostics import orch_debug_log

            orch_debug_log(
                "simulator.semantic",
                "semantic harm aggregation",
                {
                    "expected_valence": ev,
                    "semantic_expected_harm": sem_harm,
                    "dominant_harm_types": dom_harms,
                    "worst_harm": worst,
                },
                request_id=request.request_id or "",
            )
        except Exception as e:
            state.errors.append(f"Simulation error: {e}")
            record_llm_call(
                self.logger,
                {
                    "module": "simulator",
                    "action": "simulate (ERROR)",
                    "prompt": f"Prompt: {request.prompt}",
                    "response": f"ERROR: {e}",
                    "duration_ms": 0.0,
                },
                None,
            )
        return state

    def _evaluate_hindsight(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: DelibContextMode = "full",
    ) -> DeliberationState:
        if self.hindsight is None:
            return state
        try:
            if hasattr(self, "_current_start_time"):  # set by run_deliberative_path
                elapsed = (time.time() - self._current_start_time) * 1000
                elapsed_ratio = elapsed / self.config.timeout_ms
                if elapsed_ratio > self.config.skip_optional_modules_threshold:
                    raise OrchestratorTimeoutError(
                        f"Timeout approaching before hindsight: {elapsed:.0f}ms / "
                        f"{self.config.timeout_ms}ms ({elapsed_ratio * 100:.1f}%)"
                    )
                if elapsed_ratio > self.config.soft_timeout_threshold:
                    record_llm_call(
                        self.logger,
                        {
                            "module": "orchestrator",
                            "action": "timeout_warning",
                            "prompt": f"Warning: Low time remaining for hindsight: {self.config.timeout_ms - elapsed:.0f}ms",
                            "response": "",
                            "duration_ms": 0.0,
                        },
                        None,
                    )
            start = time.time()
            consequences = []
            if state.simulations:
                last_sim = state.simulations[-1]
                consequences = last_sim.consequences

            hindsight_result = self.hindsight.evaluate(
                request.prompt,
                state.draft_response,
                consequences,
                delib_context=delib_context,
                context_mode=context_mode,
            )
            elapsed = (time.time() - start) * 1000
            hindsight_model = _module_model(self.hindsight)
            record_llm_call(
                self.logger,
                {
                    "module": "hindsight",
                    "action": "evaluate",
                    "prompt": f"HINDSIGHT\nPrompt: {request.prompt}\nResponse: {state.draft_response}",
                    "response": str(hindsight_result)[:200],
                    "duration_ms": elapsed,
                    "model": hindsight_model,
                },
                {
                    "phase": "hindsight",
                    "module": "hindsight",
                    "action": "evaluate",
                    "model": hindsight_model,
                    "started_at": int(start * 1000),
                    "duration_ms": elapsed,
                    "prompt": getattr(hindsight_result, "prompt", ""),
                    "system_prompt": getattr(hindsight_result, "system_prompt", ""),
                    "raw_response": getattr(hindsight_result, "raw_response", ""),
                    "attempts": getattr(hindsight_result, "parse_attempts", 1),
                    "sequence_in_cycle": SEQ_HINDSIGHT,
                    "token_usage_json": _token_usage_json_from_result(hindsight_result),
                },
            )
            state.hindsight = hindsight_result
            _emit_hindsight_diagnostic(
                outcome="evaluate_ok",
                request_id=request.request_id or "",
                extra={
                    "duration_ms": round(elapsed, 1),
                    "state_cycle": state.cycle,
                },
            )
        except Exception as e:
            state.errors.append(f"Hindsight error: {e}")
            record_llm_call(
                self.logger,
                {
                    "module": "hindsight",
                    "action": "evaluate (ERROR)",
                    "prompt": f"Prompt: {request.prompt}",
                    "response": f"ERROR: {e}",
                    "duration_ms": 0.0,
                },
                None,
            )
            err_extra: dict[str, Any] = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "state_cycle": state.cycle,
            }
            if isinstance(e, OrchestratorTimeoutError):
                if "before hindsight" in str(e):
                    diag_outcome = "evaluate_aborted_timeout_guard"
                else:
                    diag_outcome = "evaluate_failed_orchestrator_timeout"
            else:
                diag_outcome = "evaluate_failed"
            _emit_hindsight_diagnostic(
                outcome=diag_outcome,
                request_id=request.request_id or "",
                extra=err_extra,
            )
        return state

    def _evaluate_perspectives(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: DelibContextMode = "full",
    ) -> DeliberationState:
        if self.perspectives is None:
            return state
        try:
            if hasattr(self, "_current_start_time"):  # set by run_deliberative_path
                elapsed = (time.time() - self._current_start_time) * 1000
                elapsed_ratio = elapsed / self.config.timeout_ms
                if elapsed_ratio > self.config.skip_optional_modules_threshold:
                    raise OrchestratorTimeoutError(
                        f"Timeout approaching before perspectives: {elapsed:.0f}ms / "
                        f"{self.config.timeout_ms}ms ({elapsed_ratio * 100:.1f}%)"
                    )
                if elapsed_ratio > self.config.soft_timeout_threshold:
                    record_llm_call(
                        self.logger,
                        {
                            "module": "orchestrator",
                            "action": "timeout_warning",
                            "prompt": (
                                f"Warning: Low time remaining for perspectives: {self.config.timeout_ms - elapsed:.0f}ms"
                            ),
                            "response": "",
                            "duration_ms": 0.0,
                        },
                        None,
                    )
            start = time.time()
            result = self.perspectives.evaluate(
                request.prompt,
                state.draft_response,
                delib_context=delib_context,
                context_mode=context_mode,
            )
            elapsed = (time.time() - start) * 1000
            raw_resp = "\n---\n".join(result.raw_responses or []) if getattr(result, "raw_responses", None) else ""
            prompts_list = getattr(result, "prompts", []) or []
            system_list = getattr(result, "system_prompts", []) or []
            persp_model = _module_model(self.perspectives)
            record_llm_call(
                self.logger,
                {
                    "module": "perspectives",
                    "action": "evaluate",
                    "prompt": f"PERSPECTIVES\nPrompt: {request.prompt}\nResponse: {state.draft_response}",
                    "response": str(result)[:200],
                    "duration_ms": elapsed,
                    "model": persp_model,
                },
                {
                    "phase": "perspectives",
                    "module": "perspectives",
                    "action": "evaluate",
                    "model": persp_model,
                    "started_at": int(start * 1000),
                    "duration_ms": elapsed,
                    "prompt": "\n---\n".join(prompts_list) if prompts_list else "",
                    "system_prompt": "\n---\n".join(system_list) if system_list else "",
                    "raw_response": raw_resp,
                    "sequence_in_cycle": SEQ_PERSPECTIVES,
                    "token_usage_json": _token_usage_json_from_result(result),
                },
            )
            if getattr(result, "results", None):
                state.perspectives = result.results
            else:
                state.perspectives = [result]
            state._perspectives_aggregation = result
        except Exception as e:
            state.errors.append(f"Perspectives error: {e}")
            record_llm_call(
                self.logger,
                {
                    "module": "perspectives",
                    "action": "evaluate (ERROR)",
                    "prompt": f"Prompt: {request.prompt}",
                    "response": f"ERROR: {e}",
                    "duration_ms": 0.0,
                },
                None,
            )
        return state
