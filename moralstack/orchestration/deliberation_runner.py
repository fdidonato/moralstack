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

from moralstack.constitution.retrieval_result import warn_missing_retrieve_once
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.delib_context import DelibContext
from moralstack.models.risk.categories import OperationalRisk, RiskCategory, RiskPolicyAction
from moralstack.observability.context import set_current_cycle
from moralstack.observability.emit_helpers import persist_orchestration_event
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
    CONTEXT_SHAPE_RECORDED,
    CONVERGENCE_EVALUATED,
    CRITIC_SHORT_CIRCUIT_TRIGGERED,
    CRITIC_SKIPPED,
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
from moralstack.orchestration.system_prompt_resolver import effective_system_for_request
from moralstack.orchestration.types import (
    ConvergenceOutcome,
    Decision,
    DecisionType,
    DeliberationDependencies,
    DeliberationState,
    DraftProvenance,
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
from moralstack.runtime.trace.decision_trace import DecisionTrace, append_decision_trace, normalize_trace_fields
from moralstack.runtime.trace.trace_stages import CYCLE_SUMMARY, REQUEST_ANALYSIS_CONTEXT

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


def _context_shape_payload(request: ProcessedRequest, module: str) -> dict[str, Any]:
    """Build a compact context-shape payload for deliberative modules."""
    ctx = request.conversation_context
    history = list(getattr(request, "conversation_history", None) or [])
    prior_available = getattr(ctx, "prior_turn_count", len(history)) if ctx is not None else len(history)
    used = min(int(prior_available or 0), 3)
    if ctx is not None:
        payload = ctx.context_shape_metadata(
            module=module,
            context_mode="role_serialized_truncated" if prior_available > used else "role_serialized_full",
            prior_used=used,
            history_truncation="last_3" if prior_available > used else "none",
            history_truncated_count=max(0, prior_available - used),
        )
    else:
        payload = {
            "module": module,
            "context_mode": "role_serialized_truncated" if len(history) > used else "role_serialized_full",
            "raw_message_count": 0,
            "system_message_count": 0,
            "developer_message_count": 0,
            "prior_user_available": sum(1 for t in history if getattr(t, "role", "") == "user"),
            "prior_assistant_available": sum(1 for t in history if getattr(t, "role", "") == "assistant"),
            "prior_turn_count": len(history),
            "prior_turns_used": used,
            "history_truncation": "last_3" if len(history) > used else "none",
            "history_truncated_count": max(0, len(history) - used),
            "contains_full_native_messages": False,
            "developer_contract_included": getattr(request, "developer_contract", None) is not None,
            "final_user_included": bool(getattr(request, "prompt", "")),
            "history_source": "legacy_conversation_history" if history else "none",
        }
    if ctx is not None:
        payload["message_sections"] = ctx.observability_message_sections()
    else:
        payload["message_sections"] = {
            "system_messages": [],
            "developer_messages": (
                [getattr(getattr(request, "developer_contract", None), "raw_text", "") or ""]
                if getattr(request, "developer_contract", None) is not None
                else []
            ),
            "history_messages": [
                {"role": getattr(t, "role", "") or "unknown", "content": getattr(t, "content", "") or ""}
                for t in history[-3:]
            ],
            "final_user_message": getattr(request, "prompt", "") or "",
        }
    return payload


def _emit_context_shape(request: ProcessedRequest, module: str, cycle: int) -> None:
    try:
        persist_orchestration_event(
            cycle=cycle,
            stage="context",
            component=module,
            event_type=CONTEXT_SHAPE_RECORDED,
            decision="recorded",
            status="ok",
            payload=_context_shape_payload(request, module),
        )
    except Exception:
        _LOG.debug("emit CONTEXT_SHAPE_RECORDED failed for %s", module, exc_info=True)


def _policy_llm_model_for_action(policy: Any, action: str) -> str:
    """Effective OpenAI model name for policy generate vs rewrite (rewrite may use MORALSTACK_POLICY_REWRITE_MODEL)."""
    if policy is None:
        return ""
    if action == "rewrite":
        rw = getattr(policy, "rewrite_model", None)
        if rw is not None:
            return str(rw)
    m = getattr(policy, "model", None)
    return str(m) if m is not None else ""


def _module_model(module: Any) -> str:
    """Return the OpenAI model name used by a cognitive module (critic, simulator, …).

    Each module stores its inner ``OpenAIPolicy`` as ``self.policy``; fall back
    to ``module.model`` if present.
    """
    if module is None:
        return ""
    inner = getattr(module, "policy", None)
    if inner is not None:
        m = getattr(inner, "model", None)
        if m is not None:
            return str(m)
    m = getattr(module, "model", None)
    return str(m) if m is not None else ""


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

# Constants for retrieval query enrichment.
# These bound the size of the enriched query passed to the domain prefilter.
_RETRIEVAL_QUERY_MAX_CONTRACT_CHARS = 1500
_RETRIEVAL_QUERY_MAX_HISTORY_TURNS = 3
_RETRIEVAL_QUERY_MAX_HISTORY_CHARS_PER_TURN = 200


def _build_enriched_retrieval_query(request: ProcessedRequest) -> str:
    """
    Build a semantically-complete query for the constitution retriever.

    When the user prompt is a short payload (e.g. a password or a short command)
    invoked under a developer contract, the prompt alone is not enough for the
    domain prefilter to classify the request. This helper composes a query that
    includes:
      1. The developer_contract text (truncated to ~1500 chars)
      2. The last 3 turns of conversation history (truncated per-turn)
      3. The user prompt

    Resulting query is what the LLM-based domain prefilter sees.
    """
    parts: list[str] = []

    contract = getattr(request, "developer_contract", None)
    if contract is not None:
        contract_text = (getattr(contract, "raw_text", "") or "").strip()
        if contract_text:
            if len(contract_text) > _RETRIEVAL_QUERY_MAX_CONTRACT_CHARS:
                contract_text = contract_text[:_RETRIEVAL_QUERY_MAX_CONTRACT_CHARS] + "..."
            parts.append(f"CONTRACT:\n{contract_text}")

    history = getattr(request, "conversation_history", None) or []
    if history:
        recent = list(history)[-_RETRIEVAL_QUERY_MAX_HISTORY_TURNS:]
        history_lines: list[str] = []
        for turn in recent:
            role = (getattr(turn, "role", None) or "").strip() or "user"
            content = (getattr(turn, "content", None) or "").strip()
            if not content:
                continue
            if len(content) > _RETRIEVAL_QUERY_MAX_HISTORY_CHARS_PER_TURN:
                content = content[:_RETRIEVAL_QUERY_MAX_HISTORY_CHARS_PER_TURN] + "..."
            history_lines.append(f"[{role}]: {content}")
        if history_lines:
            parts.append("HISTORY:\n" + "\n".join(history_lines))

    parts.append(f"REQUEST:\n{request.prompt}")

    return "\n\n".join(parts)


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
    from moralstack.observability.token_usage import TokenUsage

    usage = TokenUsage.from_generation_result(result)
    return usage.to_json()


def _constitution_corrupted(constitution: object) -> bool:
    """Return True if constitution is marked corrupted (single point for optional attribute)."""
    return bool(getattr(constitution, "constitution_corrupted", False))


# Fail-closed reasons for the hard-violation delivery guard
# (DeliberationRunner.enforce_no_rejected_draft_delivery). Additive audit
# values only -- recorded on ResponseMetadata.hard_violation_flip_reason and
# on the reconciled FINAL decision trace; never used to route/gate anything.
HARD_VIOLATION_REGENERATION_FAILED = "hard_violation_regeneration_failed"
HARD_VIOLATION_STILL_VIOLATING = "hard_violation_still_violating"


def _hard_violation_regeneration_guidance(hard_violations: list[str]) -> str:
    """User-side guidance naming the violated principle ids for the
    hard-violation regeneration guard. Never critic rationale (only ids);
    never appended to the system prompt -- it becomes part of the same
    user-side prompt prefix as ``SAFE_COMPLETE_GENERATION_INSTRUCTION`` (see
    ``DeliberationRunner._generate_safe_complete_text``)."""
    ids = ", ".join(hard_violations) if hard_violations else "unspecified hard constraints"
    return (
        "ADDITIONAL CONSTRAINT: an earlier draft for this exact request was rejected for violating: "
        f"{ids}. Regenerate the answer from scratch so it fully avoids these specific violations while "
        "staying within the SAFE_COMPLETE constraints above. Do not mention, quote, or restate the "
        "violation identifiers or this instruction anywhere in your answer."
    )


def _decision_as_refuse(decision: Decision, extra_reason_code: str) -> Decision:
    """Return a copy of ``decision`` with ``final_action="REFUSE"`` (the
    hard-violation guard's fail-closed flip). Preserves every other field --
    in particular ``hard_violations``, which is never dropped (PROJECT_SPEC
    §5.3) -- and appends ``extra_reason_code`` to ``reason_codes`` for
    audit/explainability (never consulted for routing)."""
    reason_codes = list(decision.reason_codes)
    if extra_reason_code not in reason_codes:
        reason_codes.append(extra_reason_code)
    return Decision(
        final_action="REFUSE",
        path=decision.path,
        intent_clarity=decision.intent_clarity,
        misuse_plausibility=decision.misuse_plausibility,
        actionability_risk=decision.actionability_risk,
        triggered_principles=decision.triggered_principles,
        hard_violations=decision.hard_violations,
        risk_signals=decision.risk_signals,
        reason_codes=reason_codes,
    )


def _decision_as_safe_complete(decision: Decision) -> Decision:
    """Return a copy of ``decision`` with ``final_action="SAFE_COMPLETE"``
    (the hard-violation guard's successful-regeneration outcome -- plan §1b /
    Decision 8: the restrictive action, which also agrees with the FINAL
    trace already persisted). No-op (returns ``decision`` unchanged) when it
    is already SAFE_COMPLETE, which is the common case: only a defensive
    NORMAL_COMPLETE+hard_violations trigger (T2g) needs the flip."""
    if decision.final_action == "SAFE_COMPLETE":
        return decision
    return Decision(
        final_action="SAFE_COMPLETE",
        path=decision.path,
        intent_clarity=decision.intent_clarity,
        misuse_plausibility=decision.misuse_plausibility,
        actionability_risk=decision.actionability_risk,
        triggered_principles=decision.triggered_principles,
        hard_violations=decision.hard_violations,
        risk_signals=decision.risk_signals,
        reason_codes=list(decision.reason_codes),
    )


def _append_hard_violation_flip_final_trace(
    *,
    request_id: str,
    decision: Decision,
    original_final_action: str,
    flip_reason: str,
    decision_explanation: DecisionExplanation | None,
) -> None:
    """Best-effort audit-coherence reconciliation (PROJECT_SPEC §5.6): appends
    a NEW FINAL decision-trace row reflecting the hard-violation guard's
    fail-closed REFUSE flip.

    The canonical report reader takes the LAST FINAL row for a request
    (``reports/markdown_export.py:610-614``); this appends -- it never
    rewrites the SAFE_COMPLETE/NORMAL_COMPLETE FINAL row already persisted
    inside ``decide_action``/``_handle_hard_violations``. Trace emission
    only: the decision flip itself already happened before this is called,
    and never depends on this succeeding. Never raises.
    """
    if not request_id:
        return
    try:
        trace = DecisionTrace(request_id=request_id)
        trace.stage = "FINAL"
        trace.sequence = 2
        trace.path = decision.path
        trace.final_action = "REFUSE"
        trace.hard_violation_codes = list(decision.hard_violations)
        trace.hard_violation_source = "hard_violation_guard_fail_closed"
        trace.decision_reason = flip_reason
        trace.reason_codes = list(decision.reason_codes)
        if decision_explanation is not None:
            trace.risk_score = decision_explanation.risk_score
            trace.risk_category = decision_explanation.risk_category
            trace.activated_signals = list(decision_explanation.activated_signals)
            trace.overlay_applied = decision_explanation.overlay_applied or ""
            trace.domain_overlay = decision_explanation.overlay_applied or ""
        trace.why_not_refuse = f"hard_violation_guard flipped to REFUSE: {flip_reason}"
        trace.stage_payload = {
            "original_final_action": original_final_action,
            "flip_reason": flip_reason,
        }
        normalize_trace_fields(trace)
        append_decision_trace(trace)
    except Exception:
        _LOG.debug("hard_violation_guard: FINAL trace reconciliation failed", exc_info=True)


def _decision_explanation_for_hard_violation_flip(
    original: DecisionExplanation | None,
    decision: Decision,
    flip_reason: str,
) -> DecisionExplanation:
    """Rebuild the ``DecisionExplanation`` passed to ``assemble`` after the
    hard-violation guard's fail-closed flip to REFUSE.

    Without this, the caller would pass the pre-flip explanation (built by
    ``decide_action`` for the SAFE_COMPLETE/NORMAL_COMPLETE decision it
    actually returned) straight through to ``ResponseAssembler.assemble``.
    ``ResponseMetadata.from_decision`` (``types.py``) prioritizes
    ``decision_explanation.reason_codes`` / ``why_not_*`` / ``winning_rule``
    over the (correctly flipped) ``decision.reason_codes`` whenever a
    ``decision_explanation`` is supplied -- so a stale explanation would
    silently reproduce the exact audit-incoherence defect this guard exists
    to prevent: ``metadata.final_action == "REFUSE"`` while
    ``metadata.why_not_safe_complete`` / ``metadata.reason_codes`` still
    describe the original SAFE_COMPLETE decision.

    ``risk_score`` / ``risk_category`` / ``activated_signals`` /
    ``overlay_applied`` are properties of the request, not of the decision
    reasoning, so they are carried over unchanged from ``original`` when
    available.

    ``why_not_refuse`` follows the existing (if slightly quirky)
    `_build_why_not` convention (``decision_service.py``): when the actual
    action IS REFUSE, that field holds "why REFUSE" text, not "why not
    REFUSE" -- and it is what the UI surfaces for REFUSE rows
    (``ui/templates/request.html:108-109``, ``conversation.html:216``), so it
    must not be left blank.
    """
    base = original
    hv_str = ", ".join(decision.hard_violations) if decision.hard_violations else "none"
    return DecisionExplanation(
        request_id=(base.request_id if base is not None else "") or "",
        final_action="REFUSE",
        risk_score=(base.risk_score if base is not None else 0.0),
        risk_category=(base.risk_category if base is not None else ""),
        activated_signals=(list(base.activated_signals) if base is not None else []),
        overlay_applied=((base.overlay_applied if base is not None else "") or ""),
        winning_rule="hard_violation_guard_fail_closed",
        reason_codes=list(decision.reason_codes),
        why_not_refuse=(
            f"REFUSE chosen: hard-violation delivery guard flipped to REFUSE ({flip_reason}); "
            f"hard_violations=[{hv_str}]."
        ),
        why_not_safe_complete=(
            f"SAFE_COMPLETE not selected: hard-violation delivery guard flipped to REFUSE ({flip_reason})."
        ),
        why_not_normal_complete=(
            f"NORMAL_COMPLETE not selected: hard_violations=[{hv_str}] required at least SAFE_COMPLETE, "
            f"which the guard could not deliver ({flip_reason})."
        ),
    )


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
        self._executor: ThreadPoolExecutor | None = None

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

    def retrieval_top_k_for_request(self) -> int:
        """
        Align single-shot retrieval with critic.critique_with_relevant_principles (top_k_principles).

        Public accessor: also used by the controller to compute the unified
        ``max(risk_top_k, critic_top_k)`` retrieval top_k for the single upstream wave.
        """
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
        top_k = self.retrieval_top_k_for_request()
        try:
            t0 = time.time()
            started_ms = int(t0 * 1000)
            enriched_query = _build_enriched_retrieval_query(request)
            retrieve_fn = getattr(self.constitution_store, "retrieve", None)
            if callable(retrieve_fn):
                result = retrieve_fn(
                    query=enriched_query,
                    top_k=top_k,
                    domain=request.get_domain(),
                    retrieval_phase="deliberation_retrieval",
                )
                relevant = list(result.principles)
                retrieval_debug: dict[str, Any] = dict(result.debug_info)
                # ConstitutionRetriever.retrieve() is the single source of truth
                # for this marker; setdefault is a defence for third-party
                # stores/test doubles that implement retrieve() without
                # stamping it themselves (plan §6 point 5).
                retrieval_debug.setdefault("domain_channel", "retrieve")
            else:
                # Fail-open guard (Codex diff review, blocking 1): a legacy
                # store without retrieve() must still get the ENRICHED query —
                # not the raw prompt critique_with_relevant_principles falls
                # back to (critic_module.py:773) — and must still yield a
                # RequestAnalysisContext, so use_precomputed (:2907) stays True
                # and the critic keeps the precomputed path.
                warn_missing_retrieve_once(self.constitution_store)
                relevant = list(
                    self.constitution_store.get_relevant_principles(
                        query=enriched_query,
                        top_k=top_k,
                        domain=request.get_domain(),
                    )
                )
                retrieval_debug = {"domain_channel": "fallback_no_retrieve"}
            t1 = time.time()
            constitution = get_constitution_safe(self.constitution_store, request.get_domain())
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
        reuse_targets: list[str] | None = None,
    ) -> None:
        """Single REQUEST_ANALYSIS_CONTEXT trace at end of deliberation with reuse_targets populated.

        ``reuse_targets`` is request-scoped (lives on the caller's
        ``DeliberationState``, never on ``self``) and is passed in explicitly
        by ``run_deliberative_path``. Defaults to ``None``/empty so existing
        direct-call sites that predate this parameter keep working.
        """
        if request_analysis is None:
            return
        try:
            relevant = list(request_analysis.relevant_principles)
            relevant_principles_detail = [{"id": p.id, "title": p.title or "", "level": p.level or "soft"} for p in relevant]
            rd = request_analysis.retrieval_metadata
            reuse_targets = list(reuse_targets or [])
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
                "domain_channel": rd.get("domain_channel"),
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
        draft_provenance: DraftProvenance | None = None,
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
                    # Defensive output protection for any supplied draft. Normal
                    # speculative drafts are already protected at generation, so
                    # this is a no-op for them; it closes the gap for
                    # compliance-regenerated drafts delivered through this path.
                    content = self._output_protector.validate(speculative_draft).cleaned
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
                            system=effective_system_for_request(
                                base=self._protected_system_prompt, request=request, mode="normal"
                            ),
                            overrides=getattr(request, "generation_overrides", None),
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
                        effective_system_for_request(base=self._protected_system_prompt, request=request, mode="normal"),
                    )
                    record_llm_call(
                        self.logger,
                        None,
                        {
                            "cycle": 0,
                            "phase": "policy_generate",
                            "module": "policy",
                            "action": "generate (benign_fast_path)",
                            "model": _policy_llm_model_for_action(self.policy, "generate"),
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
            # Delivered content is the reused speculative draft when one was
            # supplied (the `if speculative_draft:` branch above); otherwise a
            # fresh policy generate produced it.
            internal_draft_reused=bool(speculative_draft),
            # Benign is NOT a reuse-`llm_call` emitter (Codex round-5): its
            # upstream provenance is carried here (FINAL metadata) plus the
            # speculative row already persisted `module="upstream_speculative"`.
            # Additive/gated: `draft_provenance is None` (internal mode, or a
            # DCCL compliance-regenerated draft) leaves these at the
            # `ResponseMetadata` defaults — byte-identical to today.
            draft_origin=(draft_provenance.origin if draft_provenance is not None and speculative_draft else "internal"),
            draft_model=(draft_provenance.model if draft_provenance is not None and speculative_draft else ""),
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
        content = self._generate_safe_complete_text(request, risk_estimation)
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

    def _generate_safe_complete_text(
        self,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol,
        *,
        extra_guidance: str = "",
    ) -> str:
        """Generate + validate SAFE_COMPLETE text: the caveat instruction as a
        user-side prompt prefix (never appended to the system prompt -- Step
        10 / design v1.3 section 3.7), the protected system prompt, one
        ``policy.generate`` call, output-protector validation.

        Extracted from ``run_safe_complete_path`` so the hard-violation
        delivery guard (``enforce_no_rejected_draft_delivery``) can reuse the
        exact same generation for its regeneration step. ``extra_guidance``
        (default ``""``) is appended after the standard SAFE_COMPLETE caveat
        instruction, still user-side only. With ``extra_guidance == ""`` the
        composed prompt, system prompt and the persisted ``record_llm_call``
        payload are byte-identical to before this extraction.
        """
        safe_system = effective_system_for_request(base=self._protected_system_prompt, request=request, mode="normal")
        safe_caveat = SAFE_COMPLETE_GENERATION_INSTRUCTION
        if extra_guidance:
            safe_caveat = safe_caveat + "\n\n" + extra_guidance
        if self.policy is None:
            return f"[SAFE_COMPLETE mock: {request.prompt[:50]}...]"
        try:
            start_gen = time.time()
            prompt_text = resolve_prompt_with_language(
                request.prompt,
                risk_estimation.detected_language or "",
                request.prompt,
            )
            safe_prompt = safe_caveat + "\n\n" + prompt_text
            try:
                result = self.policy.generate(
                    prompt=safe_prompt,
                    system=safe_system,
                    overrides=getattr(request, "generation_overrides", None),
                )
            except TypeError:
                result = self.policy.generate(safe_prompt)
            elapsed = (time.time() - start_gen) * 1000
            response_text = _policy_text(result)
            protection_result = self._output_protector.validate(response_text)
            content = protection_result.cleaned
            prompt_used = _policy_prompt_used(result, safe_prompt)
            system_used = _policy_system_used(result, safe_system)
            record_llm_call(
                self.logger,
                None,
                {
                    "cycle": 0,
                    "phase": "policy_generate",
                    "module": "policy",
                    "action": "generate (safe_complete_path)",
                    "model": _policy_llm_model_for_action(self.policy, "generate"),
                    "started_at": int(start_gen * 1000),
                    "duration_ms": elapsed,
                    "prompt": prompt_used,
                    "system_prompt": system_used or "",
                    "raw_response": response_text,
                    "sequence_in_cycle": SEQ_POLICY,
                    "token_usage_json": result.token_usage_json(),
                },
            )
            return content
        except Exception as e:
            raise GenerationError(f"Generation failed: {e}")

    def enforce_no_rejected_draft_delivery(
        self,
        request: ProcessedRequest,
        state: DeliberationState,
        decision: Decision,
        *,
        risk_estimation: RiskEstimationProtocol,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
        delib_context: DelibContext | None = None,
        decision_explanation: DecisionExplanation | None = None,
    ) -> tuple[Decision, str, str]:
        """§5.3 hard-signal-supremacy guard, called at both post-``decide_action``
        delivery sites (this runner's ``_build_deliberative_result`` and the
        controller's ``_route_deliberative``) right before ``assemble``.

        Trigger: ``decision.hard_violations`` non-empty and
        ``decision.final_action != "REFUSE"`` -- covers every route that can
        reach the delivery point with a critic-rejected draft still sitting in
        ``state.draft_response`` (SAFE_COMPLETE via any of the three
        ``_handle_hard_violations`` branches, and defensively a
        NORMAL_COMPLETE+hard_violations decision -- the gating relabel that
        used to produce that combination is closed by
        ``safe_complete_gating``'s §1b no-op, but this trigger does not
        depend on that).

        No-op (returns ``decision`` unchanged, no LLM call, ``state``
        untouched) when the trigger does not fire -- this is what keeps the
        ~99% of requests without hard violations byte-identical (T4).

        On trigger: regenerates under SAFE_COMPLETE governance (naming the
        violated principle ids as user-side guidance only -- never critic
        rationale), then re-validates exactly once with a DIRECT
        ``self.critic.critique(...)`` call. Deliberately NOT the ``_critique``
        wrapper: it swallows every exception (:3101-3113-ish, see
        ``DeliberationRunner._critique``) -- a re-validation that cannot fail
        is not a validation -- and it enforces a 90%-of-``timeout_ms`` guard
        (see ``DeliberationRunner._critique``) that would fire routinely this
        late in a full deliberation and silently turn "regenerate and
        deliver" into "refuse" for reasons unrelated to the content itself.
        Delivers the regenerated text as SAFE_COMPLETE if the critic clears
        it; fails closed to REFUSE otherwise (regeneration empty, regeneration
        raises, the critic still reports a hard violation, the critic itself
        raises, or the critic could not actually run -- ``skipped=True``, no
        relevant principles -- since that is not a real validation either).

        Mutates ``state.draft_response`` in place only on the successful
        (SAFE_COMPLETE) branch: ``ResponseAssembler``'s REFUSE branch never
        reads ``state.draft_response`` (the only code path that did is
        disabled, ``response_assembler.py:262-264``), so there is nothing to
        clear on fail-closed.

        Never mutates ``state.last_critique`` / ``state.critiques``: the
        decision this guard receives is already final, and overwriting the
        critique that produced it would leave the assembled metadata (built
        from ``state``) describing a critique that never gated anything.

        Returns ``(decision, original_final_action, flip_reason)``.
        ``original_final_action`` / ``flip_reason`` are ``""`` unless this
        call flipped the decision to REFUSE, in which case they carry the
        pre-flip action and one of ``HARD_VIOLATION_REGENERATION_FAILED`` /
        ``HARD_VIOLATION_STILL_VIOLATING`` for the caller to record as
        additive audit metadata on ``ResponseMetadata`` -- never used to gate
        anything. A best-effort reconciled FINAL decision trace is appended
        internally on the fail-closed branch (never on the no-op or success
        branch).
        """
        if not decision.hard_violations or decision.final_action == "REFUSE":
            return decision, "", ""

        request_id = request.request_id or ""
        pre_flip_action = decision.final_action
        hard_violations = list(decision.hard_violations)

        def _fail_closed(reason: str) -> tuple[Decision, str, str]:
            flipped = _decision_as_refuse(decision, reason)
            _append_hard_violation_flip_final_trace(
                request_id=request_id,
                decision=flipped,
                original_final_action=pre_flip_action,
                flip_reason=reason,
                decision_explanation=decision_explanation,
            )
            return flipped, pre_flip_action, reason

        try:
            guidance = _hard_violation_regeneration_guidance(hard_violations)
            regenerated_text = self._generate_safe_complete_text(request, risk_estimation, extra_guidance=guidance)
        except Exception as e:
            _LOG.warning(
                "hard_violation_guard: regeneration failed request_id=%s error_type=%s error=%s",
                request_id,
                type(e).__name__,
                e,
            )
            return _fail_closed(HARD_VIOLATION_REGENERATION_FAILED)

        if not regenerated_text or not regenerated_text.strip():
            _LOG.warning("hard_violation_guard: regeneration produced empty text request_id=%s", request_id)
            return _fail_closed(HARD_VIOLATION_REGENERATION_FAILED)

        try:
            critique = self._critique_hard_violation_regeneration(
                request,
                regenerated_text,
                constitution=constitution,
                request_analysis=request_analysis,
                delib_context=delib_context,
            )
        except Exception as e:
            # Deliberately NOT a swallow: an exception here fails closed
            # (PROJECT_SPEC §5.6 -- this call is governance, not telemetry).
            # It is never treated as "no violation found".
            _LOG.warning(
                "hard_violation_guard: re-critique raised request_id=%s error_type=%s error=%s",
                request_id,
                type(e).__name__,
                e,
            )
            return _fail_closed(HARD_VIOLATION_STILL_VIOLATING)

        critic_decision = (getattr(critique, "decision", "") or "").strip().upper()
        still_violating = (
            bool(getattr(critique, "violated_hard", False))
            or critic_decision == "REFUSE"
            # A skipped critique (e.g. zero relevant principles) never ran the
            # LLM -- it is not a real re-validation, so it cannot clear the
            # regenerated text either.
            or bool(getattr(critique, "skipped", False))
        )
        if still_violating:
            return _fail_closed(HARD_VIOLATION_STILL_VIOLATING)

        state.draft_response = regenerated_text
        return _decision_as_safe_complete(decision), "", ""

    def _critique_hard_violation_regeneration(
        self,
        request: ProcessedRequest,
        regenerated_text: str,
        *,
        constitution: Any | None,
        request_analysis: RequestAnalysisContext | None,
        delib_context: DelibContext | None,
    ) -> Any:
        """Single direct ``self.critic.critique(...)`` call re-validating the
        hard-violation guard's regenerated text (never the ``_critique``
        wrapper -- see ``enforce_no_rejected_draft_delivery``).

        Mirrors the strength of the critique that rejected the original
        draft: the precomputed-principles form (sliced to
        ``retrieval_top_k_for_request()``) when ``request_analysis`` is
        available, otherwise the retrieval form -- never a weaker variant.
        Persists the call with a distinguishable ``action`` so it is visible
        in ``llm_calls`` (T6) without being mistaken for a normal
        deliberation-cycle critique. Does not mutate ``state`` -- the caller
        owns applying (or not) the result.
        """
        if self.critic is None:
            # No graceful skip here (unlike `_critique`): the caller treats
            # this raise as "cannot re-validate" and fails closed to REFUSE.
            # In practice unreachable -- the trigger's hard_violations always
            # came from a real critique -- but explicit and mypy-narrowing.
            raise RuntimeError("hard_violation_guard: no critic configured; cannot re-validate")
        const_for_precomputed: Any | None = None
        use_precomputed = request_analysis is not None
        if use_precomputed and request_analysis is not None:
            const_for_precomputed = request_analysis.constitution or constitution
            if const_for_precomputed is None and self.constitution_store is not None:
                const_for_precomputed = get_constitution_safe(self.constitution_store, request.get_domain())
            if const_for_precomputed is None:
                use_precomputed = False

        start = time.time()
        if use_precomputed and request_analysis is not None and const_for_precomputed is not None:
            critic_top_k = self.retrieval_top_k_for_request()
            sliced_principles = list(request_analysis.relevant_principles)[:critic_top_k]
            critique = self.critic.critique(
                request.prompt,
                regenerated_text,
                const_for_precomputed,
                principles=sliced_principles,
                request_id=request.request_id or "",
                delib_context=delib_context,
                developer_contract=request.developer_contract,
                conversation_history=request.conversation_history,
            )
        else:
            resolved_constitution = constitution
            if resolved_constitution is None and self.constitution_store is not None:
                resolved_constitution = get_constitution_safe(self.constitution_store, request.get_domain())
            critique = self.critic.critique(
                request.prompt,
                regenerated_text,
                resolved_constitution,
                request_id=request.request_id or "",
                delib_context=delib_context,
                developer_contract=request.developer_contract,
                conversation_history=request.conversation_history,
            )
        elapsed = (time.time() - start) * 1000
        nv = len(getattr(critique, "violations", None) or [])
        rg = critique.revision_guidance[:100] if getattr(critique, "revision_guidance", "") else "N/A"
        response_text = f"Violations: {nv}, Guidance: {rg}"
        critic_model = _module_model(self.critic)
        prompt_text = f"CRITIQUE\nPrompt: {request.prompt}\nResponse: {regenerated_text}"
        record_llm_call(
            self.logger,
            {
                "module": "critic",
                "action": "critique (hard_violation_revalidation)",
                "prompt": prompt_text,
                "response": response_text,
                "duration_ms": elapsed,
                "model": critic_model,
            },
            {
                "phase": "critic",
                "module": "critic",
                "action": "critique (hard_violation_revalidation)",
                "model": critic_model,
                "started_at": int(start * 1000),
                "duration_ms": elapsed,
                "prompt": getattr(critique, "prompt", None) or prompt_text,
                "system_prompt": getattr(critique, "system_prompt", ""),
                "raw_response": getattr(critique, "raw_response", "") or "",
                "parsed_json": None,
                "parsed_summary_json": response_text,
                "attempts": getattr(critique, "parse_attempts", 1),
                "sequence_in_cycle": SEQ_CRITIC,
                "token_usage_json": _token_usage_json_from_result(critique),
                "billable_provider_call": True,
            },
        )
        return critique

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
        request_analysis: RequestAnalysisContext | None = None,
        draft_provenance: DraftProvenance | None = None,
    ) -> OrchestratorResult:
        """Path veloce: genera draft + quick check costituzionale;
        se fallisce passa a deliberative.

        ``request_analysis``: risk-owned retrieval context (single upstream wave).
        When supplied, ``quick_check`` filters the shared principles to HARD
        instead of self-retrieving (still falls back to the constitution's own
        HARD constraints if the filtered shared list has zero HARD principles);
        forwarded unchanged to the quick-check-failed ``run_deliberative_path``
        escalation so the fallback path does not retrieve a second time either.

        ``draft_provenance``: non-None only when ``speculative_draft`` is the
        speculative draft actually produced by an upstream generator (opt-in
        `generation="upstream_then_verify"`); default None = internal, keeping
        every persisted row byte-identical to today.
        """
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
                    state._draft_verbatim_reuse = True
                    reuse_module = "upstream_speculative" if draft_provenance is not None else "policy"
                    reuse_model = (
                        draft_provenance.model
                        if draft_provenance is not None
                        else _policy_llm_model_for_action(self.policy, "generate")
                    )
                    record_llm_call(
                        self.logger,
                        {
                            "module": reuse_module,
                            "action": "generate (speculative-reuse," " fast_path)",
                            "prompt": request.prompt[:200],
                            "response": speculative_draft[:200],
                            "duration_ms": 0.0,
                            "model": reuse_model,
                        },
                        {
                            "cycle": 0,
                            "phase": "policy_generate",
                            "module": reuse_module,
                            "action": "generate (speculative-reuse," " fast_path)",
                            "model": reuse_model,
                            "duration_ms": 0.0,
                            "prompt": request.prompt[:200],
                            "raw_response": speculative_draft[:200],
                            "sequence_in_cycle": SEQ_POLICY,
                            "billable_provider_call": False,
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
                            system=effective_system_for_request(
                                base=self._protected_system_prompt, request=request, mode="normal"
                            ),
                            overrides=getattr(request, "generation_overrides", None),
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
                                "billable_provider_call": False,
                            },
                        )
                    state.draft_response = protection_result.cleaned
                    prompt_used = _policy_prompt_used(
                        result,
                        prompt_text,
                    )
                    system_used = _policy_system_used(
                        result,
                        effective_system_for_request(base=self._protected_system_prompt, request=request, mode="normal"),
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
                            "model": _policy_llm_model_for_action(self.policy, "generate"),
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
                pre_retrieved_principles = (
                    list(request_analysis.relevant_principles) if request_analysis is not None else None
                )
                quick_result = self.critic.quick_check(
                    request.prompt,
                    state.draft_response,
                    constitution,
                    pre_retrieved_principles,
                )
                if not quick_result.passed:
                    _delib_provenance = draft_provenance if speculative_draft else None
                    state_delib, risk_score, outcome = self.run_deliberative_path(
                        request,
                        risk_estimation,
                        start_time,
                        constitution=constitution,
                        speculative_draft=state.draft_response,
                        request_analysis=request_analysis,
                        draft_provenance=_delib_provenance,
                    )
                    return self._build_deliberative_result(
                        request,
                        state_delib,
                        risk_score,
                        start_time,
                        risk_estimation,
                        outcome=outcome,
                        constitution=constitution,
                        draft_provenance=_delib_provenance,
                        request_analysis=request_analysis,
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
            draft_provenance=draft_provenance if speculative_draft else None,
        )
        if getattr(response.metadata, "final_action", "") == "REFUSE" or response.response_type == ResponseType.FULL_REFUSAL:
            # NOTE: the refusal LLM call (with full system+user prompt) is
            # persisted by ResponseAssembler.assemble itself, so it is visible
            # in observability (UI, markdown export). Here we only persist the
            # decision trace marking the RESPONSE stage.
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
        # Reaching here means quick_check passed (or was skipped): the draft set
        # above is delivered as final content. If a speculative draft was
        # supplied it was reused verbatim — no second policy generate.
        # (The quick_check-failed branch returns earlier via the deliberative
        # path, where a fresh deliberation produces the answer.)
        if getattr(response, "metadata", None) is not None:
            response.metadata.internal_draft_reused = bool(speculative_draft)
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
        draft_provenance: DraftProvenance | None = None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> OrchestratorResult:
        """Helper: costruisce OrchestratorResult da state (usato da run_fast_path
        quando quick_check fallisce). ``draft_provenance``: forwarded to
        ``assemble`` (fast-path -> deliberative escalation; see run_fast_path).
        ``request_analysis``: threaded from ``run_fast_path`` (in scope at its
        caller, ``:1049`` area) so the hard-violation delivery guard below can
        mirror the precomputed-principles critique form; this route never had
        it before this guard."""
        from moralstack.orchestration.decision_service import decide_action

        decision1, explanation1 = decide_action(
            request,
            risk_estimation,
            state.last_critique,
            state.simulations[-1] if state.simulations else None,
            state.hindsight,
            append_pre_policy_trace=False,
            risk_thresholds=getattr(getattr(self, "config", None), "risk_thresholds", None),
            regulated_informational_normal_complete=getattr(
                getattr(self, "config", None), "regulated_informational_normal_complete", False
            ),
        )
        processing_time = int((time.time() - start_time) * 1000)
        if constitution is None and self.constitution_store is not None:
            constitution = get_constitution_safe(self.constitution_store, request.get_domain())
        if constitution is not None and _constitution_corrupted(constitution):
            risk_score = 1.0
        converged = outcome.converged if outcome is not None else (state.decision == DecisionType.CONVERGED)
        decision1, hv_original_action, hv_flip_reason = self.enforce_no_rejected_draft_delivery(
            request,
            state,
            decision1,
            risk_estimation=risk_estimation,
            constitution=constitution,
            request_analysis=request_analysis,
            decision_explanation=explanation1,
        )
        if hv_flip_reason:
            # See _decision_explanation_for_hard_violation_flip: explanation1
            # still describes the pre-flip decision, and assemble()'s
            # ResponseMetadata.from_decision would otherwise prioritize its
            # stale reason_codes/why_not_*/winning_rule over decision1's
            # (correctly flipped) fields.
            explanation1 = _decision_explanation_for_hard_violation_flip(explanation1, decision1, hv_flip_reason)
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
            draft_provenance=draft_provenance,
        )
        if hv_flip_reason:
            response.metadata.original_final_action = hv_original_action
            response.metadata.hard_violation_flip_reason = hv_flip_reason
        if getattr(response.metadata, "final_action", "") == "REFUSE" or response.response_type == ResponseType.FULL_REFUSAL:
            # See FAST_PATH branch above: the refusal LLM call is persisted by
            # ResponseAssembler.assemble itself; here only the RESPONSE-stage
            # decision trace is recorded.
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
        request_analysis: RequestAnalysisContext | None = None,
        draft_provenance: DraftProvenance | None = None,
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
            draft_provenance: Non-None only when `speculative_draft` is the
                speculative draft actually produced by an upstream generator
                (opt-in `generation="upstream_then_verify"`). Forwarded to the
                cycle-1 reuse in `_generate_or_revise`; default None = internal,
                byte-identical to today.
            request_analysis: Risk-owned retrieval context supplied by the controller
                (single upstream wave). Authoritative even when
                ``relevant_principles`` is empty — an empty successful retrieval is
                not degraded and must not trigger a second retrieval. Only when this
                is ``None`` does the runner fall back to its own retrieval
                (``_try_build_request_analysis_context``), which also emits
                ``RELEVANT_PRINCIPLES_RETRIEVED`` (the controller already emitted it
                when it supplied a successful context, so the two emit sites are
                mutually exclusive per request).
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
        state = DeliberationState(cycle=0)
        # Pre-set speculative draft for cycle 1 when safe to do so.
        # constrained_generation uses a different system prompt so the
        # speculative draft (generated with the base prompt) is not suitable.
        _effective_draft_provenance: DraftProvenance | None = None
        if speculative_draft and not constrained_generation:
            state.draft_response = sanitize_policy_output(
                speculative_draft,
            )
            _effective_draft_provenance = draft_provenance
        risk_score = risk_estimation.score
        max_cycles = self._effective_max_cycles(risk_estimation)
        # Constrained generation (clearly_harmful): the policy is already instructed to
        # produce a refusal. A second rewrite cycle cannot improve a refusal — perspectives
        # feedback ("add concrete examples") would push toward operational content that
        # constrained_generation explicitly forbids. Cap to 1 cycle for full determinism.
        if constrained_generation:
            max_cycles = 1
        # Request-scoped retrieval: single get_relevant_principles + constitution for downstream reuse.
        # (state._request_analysis_reuse_targets starts empty via its dataclass
        # default_factory — no explicit reset needed here.)
        # Controller-supplied context is authoritative even when empty (successful
        # zero-principle retrieval is not degraded); fall back to this runner's own
        # retrieval only when the controller did not supply one (no risk estimator /
        # no store / retrieval raised — see run_deliberative_path docstring).
        if request_analysis is None and self.constitution_store is not None:
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
                draft_provenance=_effective_draft_provenance,
                start_time=start_time,
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
            reuse_targets=state._request_analysis_reuse_targets,
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
        draft_provenance: DraftProvenance | None = None,
        start_time: float | None = None,
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
            draft_provenance=draft_provenance,
        )

        delib_context, computed_max_cycles = self._build_delib_context(
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
                risk_estimation=risk_estimation,
                max_cycles=max_cycles,
                constitution=constitution,
                request_analysis=request_analysis,
                start_time=start_time,
            )
        else:
            state = self._run_critique_simulate_perspectives_sequential(
                state,
                request,
                delib_context=delib_context,
                risk_estimation=risk_estimation,
                max_cycles=max_cycles,
                constitution=constitution,
                request_analysis=request_analysis,
                start_time=start_time,
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

        state = self._apply_hindsight_if_needed(state, request, delib_context, max_cycles=max_cycles, start_time=start_time)

        return self._finalize_cycle(state, max_cycles, risk_estimation=risk_estimation)

    def _build_delib_context(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        risk_estimation: RiskEstimationProtocol | None,
        request_analysis: RequestAnalysisContext | None = None,
    ) -> tuple[DelibContext | None, int]:
        """Build DelibContext and compute effective max_cycles."""
        delib_context = None
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
        return delib_context, max_cycles

    def _apply_hindsight_if_needed(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        delib_context: DelibContext | None,
        *,
        max_cycles: int = 1,
        start_time: float | None = None,
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
                state = self._evaluate_hindsight(state, request, delib_context=delib_context, start_time=start_time)
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
        gate: SimulatorGateDecision,
        emit_gate_decision: bool = True,
        start_time: float | None = None,
    ) -> DeliberationState:
        """Execute simulator or record explicit skip; updates observability fields on state."""
        state._simulator_gate_reason_codes = list(gate.reason_codes)
        if emit_gate_decision:
            self._emit_simulator_gate_decision_event(state=state, gate=gate)
        if not self.config.enable_simulation or self.simulator is None:
            return state
        if gate.should_run:
            t0 = time.time()
            state = self._simulate(state, request, delib_context=delib_context, start_time=start_time)
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
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
        start_time: float | None = None,
    ) -> DeliberationState:
        state = self._critique(
            state,
            request,
            delib_context=delib_context,
            constitution=constitution,
            request_analysis=request_analysis,
            start_time=start_time,
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
                gate=gate,
                start_time=start_time,
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
            state = self._evaluate_perspectives(state, request, delib_context=delib_context, start_time=start_time)
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
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
        start_time: float | None = None,
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
                risk_estimation=risk_estimation,
                max_cycles=max_cycles,
                constitution=constitution,
                request_analysis=request_analysis,
                start_time=start_time,
            )

        return self._run_critic_gated_parallel(
            state,
            request,
            delib_context=delib_context,
            risk_estimation=risk_estimation,
            max_cycles=max_cycles,
            constitution=constitution,
            request_analysis=request_analysis,
            start_time=start_time,
        )

    def _run_critic_gated_parallel(
        self,
        state: DeliberationState,
        request: ProcessedRequest,
        *,
        delib_context: DelibContext | None = None,
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
        start_time: float | None = None,
    ) -> DeliberationState:
        """Original two-stage approach: critic runs first as a gate, then
        simulator + perspectives run in parallel only if no hard violation."""
        state = self._critique(
            state,
            request,
            delib_context=delib_context,
            constitution=constitution,
            request_analysis=request_analysis,
            start_time=start_time,
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
                gate=gate,
                start_time=start_time,
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
                start_time=start_time,
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
        risk_estimation: RiskEstimationProtocol | None = None,
        max_cycles: int = 2,
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
        start_time: float | None = None,
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
                constitution=constitution,
                request_analysis=request_analysis,
                start_time=start_time,
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
                gate=gate_sim,
                emit_gate_decision=False,
                start_time=start_time,
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
                start_time=start_time,
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
        # The critic ran on the `sc` fork (state_critic): its reuse-targets
        # append (inside _critique) lands there, not on `state` — merge it
        # back explicitly (fork() alone only copies it at fork time, before
        # the critic runs).
        state._request_analysis_reuse_targets = list(sc._request_analysis_reuse_targets)
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
                    system=effective_system_for_request(base=self._protected_system_prompt, request=request, mode="normal"),
                    overrides=getattr(request, "generation_overrides", None),
                )
            except TypeError:
                result = self.policy.rewrite(user_prompt_with_lang, state.draft_response, guidance)
            elapsed = (time.time() - start) * 1000
            response_text = _policy_text(result)
            protection_result = self._output_protector.validate(response_text)
            state.draft_response = sanitize_policy_output(protection_result.cleaned)
            # A soft-revision rewrite just overwrote `draft_response`: it is no
            # longer the verbatim joined draft, regardless of origin (mirrors
            # the same guard in `_generate_or_revise` at :2834).
            state._draft_verbatim_reuse = False
            state.soft_revision_applied = True
            state.soft_revision_guidance_used = guidance
            prompt_used = _policy_prompt_used(result, user_prompt_with_lang)
            system_used = _policy_system_used(
                result,
                effective_system_for_request(base=self._protected_system_prompt, request=request, mode="normal"),
            )
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
        draft_provenance: DraftProvenance | None = None,
    ) -> DeliberationState:
        if self.policy is None:
            state.draft_response = f"[Mock response to: {request.prompt[:50]}...]"
            state._draft_verbatim_reuse = False
            return state
        # Speculative draft already present from parallel generation:
        # skip redundant LLM call in cycle 1.
        if state.cycle == 1 and state.draft_response:
            reuse_module = "upstream_speculative" if draft_provenance is not None else "policy"
            reuse_model = (
                draft_provenance.model
                if draft_provenance is not None
                else _policy_llm_model_for_action(self.policy, "generate")
            )
            record_llm_call(
                self.logger,
                {
                    "module": reuse_module,
                    "action": "generate (speculative-reuse)",
                    "prompt": request.prompt,
                    "response": state.draft_response,
                    "duration_ms": 0.0,
                    "model": reuse_model,
                },
                {
                    "cycle": 1,
                    "phase": "policy_generate",
                    "module": reuse_module,
                    "action": "generate (speculative-reuse)",
                    "model": reuse_model,
                    "duration_ms": 0.0,
                    "prompt": request.prompt,
                    "system_prompt": "[orchestration] Reused completed speculative draft; no second policy LLM call.",
                    "raw_response": state.draft_response,
                    "sequence_in_cycle": SEQ_POLICY,
                    "call_kind": "speculative_reuse",
                    "billable_provider_call": False,
                },
            )
            # `state.draft_response` is still exactly the joined speculative
            # draft (no generate/rewrite has run yet) — gates draft-provenance
            # attribution in ResponseAssembler.assemble. Cleared below whenever
            # a subsequent generate/rewrite overwrites `draft_response`.
            state._draft_verbatim_reuse = True
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
                        "billable_provider_call": False,
                    },
                )
                return state
        try:
            start = time.time()
            # Step 10 / design v1.3 section 3.7: do NOT append CONSTRAINED_GENERATION_INSTRUCTION to system.
            # The constraint is now injected user-side as a prompt prefix.
            system_prompt = effective_system_for_request(base=self._protected_system_prompt, request=request, mode="normal")
            constrained_caveat = CONSTRAINED_GENERATION_INSTRUCTION if constrained_generation else ""
            policy_user_prompt: str
            if state.cycle == 1 or not state.draft_response:
                action = "generate"
                prompt_text = resolve_prompt_with_language(request.prompt, det_iso, request.prompt)
                policy_user_prompt = constrained_caveat + "\n\n" + prompt_text if constrained_caveat else prompt_text
                try:
                    result = self.policy.generate(
                        prompt=policy_user_prompt,
                        system=system_prompt,
                        overrides=getattr(request, "generation_overrides", None),
                    )
                except TypeError:
                    result = self.policy.generate(policy_user_prompt)
            else:
                action = "rewrite"
                guidance = pre_rewrite_guidance or ""
                prompt_text = f"REVISIONE\nPrompt originale: {request.prompt}\nGuidance: {guidance}"
                user_prompt_with_lang = resolve_prompt_with_language(request.prompt, det_iso, request.prompt)
                policy_user_prompt = (
                    constrained_caveat + "\n\n" + user_prompt_with_lang if constrained_caveat else user_prompt_with_lang
                )
                rewrite_system = system_prompt
                try:
                    result = self.policy.rewrite(
                        policy_user_prompt,
                        state.draft_response,
                        guidance,
                        system=rewrite_system,
                        overrides=getattr(request, "generation_overrides", None),
                    )
                except TypeError:
                    result = self.policy.rewrite(policy_user_prompt, state.draft_response, guidance)
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
                        "billable_provider_call": False,
                    },
                )
            state.draft_response = sanitize_policy_output(protection_result.cleaned)
            # A fresh internal generate/rewrite just overwrote `draft_response`:
            # it is no longer the verbatim joined draft, regardless of origin.
            state._draft_verbatim_reuse = False
            prompt_used = _policy_prompt_used(result, policy_user_prompt)
            system_used = _policy_system_used(
                result,
                effective_system_for_request(base=self._protected_system_prompt, request=request, mode="normal"),
            )
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
        constitution: Any | None = None,
        request_analysis: RequestAnalysisContext | None = None,
        start_time: float | None = None,
    ) -> DeliberationState:
        if self.critic is None or (self.constitution_store is None and constitution is None):
            return state
        try:
            if start_time is not None:
                elapsed = (time.time() - start_time) * 1000
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
            # A supplied request_analysis is authoritative even when empty (a
            # successful zero-principle retrieval): gate on presence of the
            # context, never on len(relevant_principles) > 0, so a legitimately
            # empty result is used as-is and never triggers a second retrieval
            # via critique_with_relevant_principles.
            use_precomputed = request_analysis is not None and getattr(self.critic, "critique", None) is not None
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
                # Never widen the critic beyond its own configured top_k, even when
                # the unified retrieval top_k (max(risk_top_k, critic_top_k)) was larger.
                critic_top_k = self.retrieval_top_k_for_request()
                sliced_principles = list(precomputed_analysis.relevant_principles)[:critic_top_k]
                critique = self.critic.critique(
                    request.prompt,
                    state.draft_response,
                    const_for_precomputed,
                    principles=sliced_principles,
                    request_id=request.request_id or "",
                    delib_context=delib_context,
                    previous_violations=prev_violations,
                    previous_guidance=prev_guidance,
                    developer_contract=request.developer_contract,
                    conversation_history=request.conversation_history,
                )
                if "critic" not in state._request_analysis_reuse_targets:
                    state._request_analysis_reuse_targets.append("critic")
                try:
                    persist_orchestration_event(
                        cycle=state.cycle,
                        stage="deliberation",
                        component="critic",
                        event_type=RELEVANT_PRINCIPLES_REUSED,
                        decision=str(len(sliced_principles)),
                        status="ok",
                        payload={
                            "reuse_target": "critic",
                            "principles_count": len(sliced_principles),
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
                    previous_violations=prev_violations,
                    previous_guidance=prev_guidance,
                    developer_contract=request.developer_contract,
                    conversation_history=request.conversation_history,
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
                    previous_violations=prev_violations,
                    previous_guidance=prev_guidance,
                    developer_contract=request.developer_contract,
                    conversation_history=request.conversation_history,
                )
            elapsed = (time.time() - start) * 1000
            nv = len(critique.violations)
            rg = (critique.revision_guidance[:100]) if critique.revision_guidance else "N/A"
            response_text = f"Violations: {nv}, Guidance: {rg}"
            is_skipped = bool(getattr(critique, "skipped", False))
            skip_reason = getattr(critique, "skip_reason", "") or ""
            if is_skipped:
                try:
                    persist_orchestration_event(
                        cycle=state.cycle,
                        stage="deliberation",
                        component="critic",
                        event_type=CRITIC_SKIPPED,
                        decision="skipped",
                        status="ok",
                        payload={
                            "reason": skip_reason,
                            "cycle": state.cycle,
                        },
                    )
                except Exception:
                    _LOG.debug("emit CRITIC_SKIPPED failed", exc_info=True)
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
                    "prompt": (
                        f"[SKIPPED] {skip_reason}" if is_skipped else (getattr(critique, "prompt", None) or prompt_text)
                    ),
                    "system_prompt": getattr(critique, "system_prompt", ""),
                    "raw_response": getattr(critique, "raw_response", "") or "",
                    "parsed_json": None,
                    "parsed_summary_json": (f"SKIPPED: {skip_reason}" if is_skipped else response_text),
                    "attempts": getattr(critique, "parse_attempts", 1),
                    "sequence_in_cycle": SEQ_CRITIC,
                    "token_usage_json": _token_usage_json_from_result(critique),
                    "call_kind": "skipped" if is_skipped else None,
                    "call_outcome": "skipped" if is_skipped else None,
                    "cache_status": "not_invoked" if is_skipped else None,
                    "related_event_id": None,
                    "billable_provider_call": not is_skipped,
                },
            )
            _emit_context_shape(request, "critic", state.cycle)
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
        start_time: float | None = None,
    ) -> DeliberationState:
        if self.simulator is None:
            return state
        try:
            if start_time is not None:
                elapsed = (time.time() - start_time) * 1000
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
                developer_contract=request.developer_contract,
                conversation_history=request.conversation_history,
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
            from_cache = bool(getattr(simulation, "from_cache", False))
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
                            "context_shape": _context_shape_payload(request, "simulator"),
                        }
                    ),
                    "attempts": getattr(simulation, "parse_attempts", 1),
                    "sequence_in_cycle": SEQ_SIMULATOR,
                    "token_usage_json": _token_usage_json_from_result(simulation),
                    "billable_provider_call": not from_cache,
                    "cache_status": "hit" if from_cache else None,
                },
            )
            _emit_context_shape(request, "simulator", state.cycle)
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
        start_time: float | None = None,
    ) -> DeliberationState:
        if self.hindsight is None:
            return state
        try:
            if start_time is not None:
                elapsed = (time.time() - start_time) * 1000
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
                developer_contract=request.developer_contract,
                conversation_history=request.conversation_history,
            )
            elapsed = (time.time() - start) * 1000
            hindsight_model = _module_model(self.hindsight)
            from_cache = bool(getattr(hindsight_result, "from_cache", False))
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
                    "parsed_summary_json": json.dumps({"context_shape": _context_shape_payload(request, "hindsight")}),
                    "attempts": getattr(hindsight_result, "parse_attempts", 1),
                    "sequence_in_cycle": SEQ_HINDSIGHT,
                    "token_usage_json": _token_usage_json_from_result(hindsight_result),
                    "billable_provider_call": not from_cache,
                    "cache_status": "hit" if from_cache else None,
                },
            )
            _emit_context_shape(request, "hindsight", state.cycle)
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
        start_time: float | None = None,
    ) -> DeliberationState:
        if self.perspectives is None:
            return state
        try:
            if start_time is not None:
                elapsed = (time.time() - start_time) * 1000
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
                developer_contract=request.developer_contract,
                conversation_history=request.conversation_history,
            )
            elapsed = (time.time() - start) * 1000
            raw_resp = "\n---\n".join(result.raw_responses or []) if getattr(result, "raw_responses", None) else ""
            prompts_list = getattr(result, "prompts", []) or []
            system_list = getattr(result, "system_prompts", []) or []
            persp_model = _module_model(self.perspectives)
            from_cache = bool(getattr(result, "from_cache", False))
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
                    "parsed_summary_json": json.dumps({"context_shape": _context_shape_payload(request, "perspectives")}),
                    "sequence_in_cycle": SEQ_PERSPECTIVES,
                    "token_usage_json": _token_usage_json_from_result(result),
                    "billable_provider_call": not from_cache,
                    "cache_status": "hit" if from_cache else None,
                },
            )
            _emit_context_shape(request, "perspectives", state.cycle)
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
