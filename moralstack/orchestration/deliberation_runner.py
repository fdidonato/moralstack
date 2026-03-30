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
from typing import Any

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.delib_context import DelibContext
from moralstack.models.risk import RiskPolicyAction
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
    ResponseMetadata,
    ResponseType,
    RiskEstimationProtocol,
    risk_category_str,
)
from moralstack.persistence.context import set_current_cycle

_LOG = logging.getLogger(__name__)


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
        from moralstack.persistence.config import get_persist_mode

        data: dict[str, Any] = {
            "component": "hindsight_diagnostic",
            "outcome": outcome,
            "persist_mode": get_persist_mode(),
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

    def _effective_max_cycles(self, risk_estimation: RiskEstimationProtocol) -> int:
        risk_score = risk_estimation.score if hasattr(risk_estimation, "score") else 0.5
        if risk_score >= self.config.risk_thresholds.low:
            return self.config.max_deliberation_cycles
        rc = getattr(risk_estimation, "risk_category", None)
        rc_val = getattr(rc, "value", str(rc or "")).strip().lower()
        if rc_val in ("sensitive", "morally_nuanced"):
            return self.config.max_deliberation_cycles
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
        return OrchestratorResult(
            response=response,
            request_id=request.request_id,
            path_taken="deliberative",
            path="DELIBERATIVE_PATH",
            total_cycles=state.cycle,
            converged=converged,
            errors=list(state.errors) if state.errors else None,
        )

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
        # Persist relevant principles identified at the start of deliberation
        # (parallel domain agents; used by critic and policy)
        if self.constitution_store is not None:
            try:
                t0 = time.time()
                relevant = self.constitution_store.get_relevant_principles(
                    query=request.prompt,
                    top_k=10,
                    domain=request.get_domain(),
                )
                t1 = time.time()
                principle_ids = [p.id for p in relevant]
                relevant_principles_detail = [
                    {"id": p.id, "title": p.title or "", "level": p.level or "soft"} for p in relevant
                ]
                record_decision_trace(
                    request_id=request_id,
                    stage="RELEVANT_PRINCIPLES",
                    sequence=0,
                    trace_json=json.dumps(
                        {
                            "relevant_principle_ids": principle_ids,
                            "relevant_principles": relevant_principles_detail,
                            "domain": (request.get_domain() or "") or "",
                            "started_at": int(t0 * 1000),
                            "duration_ms": round((t1 - t0) * 1000, 1),
                            "parallel_retrieval": True,
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception as e:
                _LOG.warning(
                    "get_relevant_principles failed request_id=%s error_type=%s error=%s",
                    request_id,
                    type(e).__name__,
                    e,
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
    ) -> DeliberationState:
        """Singolo ciclo deliberativo: generate/revisione, critique, simulate,
        perspectives, hindsight, decisione."""
        state.cycle += 1
        set_current_cycle(state.cycle)
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

        delib_context, context_mode, max_cycles = self._build_delib_context(state, request, risk_estimation)

        if self.config.parallel_module_calls:
            state = self._run_critique_simulate_perspectives_parallel(
                state,
                request,
                delib_context=delib_context,
                context_mode=context_mode,
                risk_estimation=risk_estimation,
                max_cycles=max_cycles,
                constitution=constitution,
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

        return self._finalize_cycle(state, max_cycles)

    def _build_delib_context(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol | None,
    ) -> tuple[DelibContext | None, str, int]:
        """Build DelibContext for thin prompts in cycle 2+ and compute effective max_cycles."""
        delib_context = None
        context_mode: str = "full"
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
        context_mode: str = "full",
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
    ) -> DeliberationState:
        """Determine decision, clean up resources, and log cycle completion."""
        state.decision = self._convergence_evaluator.determine_decision(state)
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

    def _should_run_simulator(
        self,
        state: DeliberationState,
        risk_estimation: RiskEstimationProtocol | None,
        delib_context: DelibContext | None,
        cycle: int,
        max_cycles: int,
    ) -> bool:
        """Gating: cycle 2+ skip simulator when safe to carry forward."""
        if not self.config.enable_simulator_gating or cycle <= 1:
            return True
        if not state.simulations:
            return True
        if risk_estimation is None:
            return True
        prev_sim = state.simulations[-1]
        sem_harm = prev_sim.semantic_expected_harm
        if sem_harm >= self.config.simulator_gate_semantic_harm_threshold:
            return True
        if delib_context and delib_context.change_log:
            delta_chars = sum(len(c) for c in delib_context.change_log)
            if delta_chars >= self.config.simulator_gate_delta_chars_threshold:
                return True
        risk_score = risk_estimation.score
        ar = risk_estimation.actionability_risk
        ar_val = getattr(ar, "value", str(ar or "")) if ar is not None else ""
        if 0.3 <= risk_score <= 0.7 and ar_val == "HIGH":
            return True
        return False

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
        context_mode: str = "full",
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
    ) -> DeliberationState:
        state = self._critique(
            state, request, delib_context=delib_context, context_mode=context_mode, constitution=constitution
        )
        if self.config.enable_simulation and self.simulator is not None:
            if self._should_run_simulator(
                state,
                risk_estimation,
                delib_context,
                state.cycle,
                max_cycles,
            ):
                state = self._simulate(state, request, delib_context=delib_context, context_mode=context_mode)
            else:
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

    def _run_critique_simulate_perspectives_parallel(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: str = "full",
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
    ) -> DeliberationState:
        # Pre-import prompt modules to avoid deadlock when threads import concurrently
        import moralstack.prompts.critic_prompt  # noqa: F401
        import moralstack.prompts.perspectives_prompt  # noqa: F401
        import moralstack.prompts.simulator_prompt  # noqa: F401

        if self.config.parallel_critic_with_modules:
            return self._run_full_parallel_evaluation(
                state,
                request,
                delib_context=delib_context,
                context_mode=context_mode,
                risk_estimation=risk_estimation,
                max_cycles=max_cycles,
                constitution=constitution,
            )

        return self._run_critic_gated_parallel(
            state,
            request,
            delib_context=delib_context,
            context_mode=context_mode,
            risk_estimation=risk_estimation,
            max_cycles=max_cycles,
            constitution=constitution,
        )

    def _run_critic_gated_parallel(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: str = "full",
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
    ) -> DeliberationState:
        """Original two-stage approach: critic runs first as a gate, then
        simulator + perspectives run in parallel only if no hard violation."""
        state = self._critique(
            state,
            request,
            delib_context=delib_context,
            context_mode=context_mode,
            constitution=constitution,
        )
        if state.has_critical_violations or getattr(state.last_critique, "violated_hard", False):
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
            if not self._should_run_simulator(
                s,
                risk_estimation,
                delib_context,
                s.cycle,
                max_cycles,
            ):
                return s
            return self._simulate(
                s,
                r,
                delib_context=delib_context,
                context_mode=context_mode,
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
        return state

    def _run_full_parallel_evaluation(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        context_mode: str = "full",
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
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
            )

        def do_simulate(
            s: DeliberationState,
            r: ProcessedRequest,
        ) -> DeliberationState:
            if not self.config.enable_simulation or self.simulator is None:
                return s
            if not self._should_run_simulator(
                s,
                risk_estimation,
                delib_context,
                s.cycle,
                max_cycles,
            ):
                return s
            return self._simulate(
                s,
                r,
                delib_context=delib_context,
                context_mode=context_mode,
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
        return state

    def _apply_constitutional_perspective_override(self, state: DeliberationState) -> None:
        """Applica override costituzionale sulle prospettive quando il Critic
        rileva violazioni HARD."""
        from moralstack.runtime.modules.perspective_module import apply_constitutional_override

        aggregation = state._perspectives_aggregation
        critic_result = state.last_critique
        if aggregation is not None and critic_result is not None:
            state._perspectives_aggregation = apply_constitutional_override(aggregation, critic_result)

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
        try:
            start = time.time()
            det_iso = risk_estimation.detected_language or ""
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
                guidance = build_aggregated_guidance(state)
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
        context_mode: str = "full",
        constitution: Any | None = None,
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
            if has_critique_with_principles and getattr(self.critic, "store", None) is not None:
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
        context_mode: str = "full",
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
        context_mode: str = "full",
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
        context_mode: str = "full",
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
