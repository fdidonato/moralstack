"""
ResponseAssembler: costruisce la risposta finale.
Renderer deterministico: response_type determinato SOLO da decision.final_action.
"""

from __future__ import annotations

import logging
import time

from moralstack.core.types import PolicyLLMProtocol
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import RiskPolicyAction
from moralstack.orchestration.persistence_helpers import record_llm_call
from moralstack.orchestration.refusal_context import build_refusal_context
from moralstack.orchestration.safe_refusal_generator import (
    _detect_language_fallback,
    _iso_to_language_name,
    generate_llm_safe_refusal_detailed,
    resolve_refusal_domain_and_redirection,
)
from moralstack.orchestration.types import (
    ConvergenceOutcome,
    Decision,
    DeliberationState,
    FinalResponse,
    MetaAnalysis,
    PolicyOverlay,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
    RiskEstimationProtocol,
    risk_category_str,
)

# Same value used by deliberation_runner.SEQ_REFUSAL_OR_FINALIZE; duplicated
# here to avoid a cyclic import (deliberation_runner depends on this module).
_SEQ_REFUSAL_OR_FINALIZE = 6

_LOG = logging.getLogger(__name__)


class ResponseAssembler:
    """
    Assembla la risposta finale basata sullo stato deliberativo.
    Non decide il livello di sicurezza: la decisione è di competenza esclusiva di decide_action().
    """

    REFUSAL_TEMPLATE = """{reason}

{alternatives}"""

    # Language-agnostic: used only when reason needs enrichment (appended to short reason).
    # English as lingua franca for internal fallback; LLM-generated content preferred.
    REFUSAL_ENRICHMENT = (
        " Briefly explain why the request is problematic (risk/ethics) and "
        "suggest at least one safe alternative or redirection "
        "(e.g. rephrase the question, legitimate resources). "
        "Do not limit yourself to a single refusal sentence."
    )
    REFUSAL_MIN_REASON_LEN = 80

    def __init__(self, policy: PolicyLLMProtocol | None = None):
        self.policy = policy

    def assemble(
        self,
        request: ProcessedRequest,
        state: DeliberationState,
        decision: Decision,
        risk_score: float = 0.0,
        processing_time_ms: int = 0,
        constitution: object | None = None,
        risk_estimation: RiskEstimationProtocol | None = None,
        outcome: ConvergenceOutcome | None = None,
        decision_explanation: DecisionExplanation | None = None,
        constitution_store: object | None = None,
    ) -> FinalResponse:
        """
        Assembla la risposta finale. Renderer deterministico: il tipo di risposta
        (DIRECT / WITH_CAVEAT / FULL_REFUSAL) è determinato SOLO da decision.final_action.
        """
        predicted_action: str | None = None
        constitution_loaded_ok: bool | None = None
        if constitution is not None:
            constitution_loaded_ok = getattr(constitution, "constitution_loaded_ok", True)
        if risk_estimation is not None:
            rpa = getattr(risk_estimation, "risk_policy_action", None)
            predicted_action = getattr(rpa, "value", rpa) if rpa is not None else None

        risk_category = risk_category_str(risk_estimation) if risk_estimation is not None else ""
        intent_type_val: str | None = None
        if risk_estimation is not None:
            intent_type_val = (getattr(risk_estimation, "intent_type", "") or "").strip().lower() or None
        domain = request.get_domain() if hasattr(request, "get_domain") else None
        domain_overlay_val = (domain.strip() if isinstance(domain, str) and domain else None) or None
        request_id = getattr(request, "request_id", "") or ""

        metadata = ResponseMetadata.from_decision(
            decision=decision,
            request_id=request_id,
            risk_score=risk_score,
            processing_time_ms=processing_time_ms,
            risk_category=risk_category,
            decision_explanation=decision_explanation,
            deliberation_cycles=state.cycle,
            hindsight_score=state.hindsight_score,
            constitution_loaded_ok=constitution_loaded_ok,
            predicted_action=predicted_action,
            intent_type=intent_type_val,
            domain_overlay=domain_overlay_val,
            routing_reason_codes=list(getattr(decision, "reason_codes", None) or []),
            operational_risk=(
                getattr(
                    getattr(risk_estimation, "operational_risk", None),
                    "value",
                    getattr(risk_estimation, "operational_risk", ""),
                )
                if risk_estimation is not None
                else ""
            )
            or "",
            requested_instructions=(
                bool(getattr(risk_estimation, "requested_instructions", False)) if risk_estimation is not None else False
            ),
            intent_to_harm=bool(getattr(risk_estimation, "intent_to_harm", False)) if risk_estimation is not None else False,
            intent_operational=(
                bool(getattr(risk_estimation, "intent_operational", False)) if risk_estimation is not None else False
            ),
        )

        if decision.final_action == "REFUSE":
            out = self._make_refusal(
                request=request,
                state=state,
                decision=decision,
                metadata=metadata,
                risk_estimation=risk_estimation,
                constitution_store=constitution_store,
            )
            out.metadata.final_action = "REFUSE"
            out.metadata.path = decision.path
            if not getattr(out.metadata, "refusal_reason", "").strip():
                out.metadata.refusal_reason = (
                    state.last_critique.revision_guidance
                    if state.last_critique and hasattr(state.last_critique, "revision_guidance")
                    else ""
                ) or "[REFUSAL_FALLBACK]"
            out.metadata.must_refuse = True
            return out
        if decision.final_action == "SAFE_COMPLETE":
            # Content = draft only. No prepend of critic rationale (meta-layer leakage).
            # Policy overlay in metadata; no hardcoded caveat text (language-agnostic).
            content = state.draft_response
            metadata.predicted_action = RiskPolicyAction.ALLOW_WITH_CAVEAT.value
            metadata.final_action = "SAFE_COMPLETE"
            metadata.path = decision.path
            metadata.caveat_present = getattr(state, "safe_complete_caveat_present", True)
            metadata.safe_alternative_present = getattr(state, "safe_complete_safe_alternative_present", True)
            metadata.no_prescriptive_language = getattr(state, "safe_complete_no_prescriptive_language", True)
            policy_overlay = PolicyOverlay(
                caveat_type="domain_specific" if self._has_soft_violations(state) else "generic",
                principle_ids=state.triggered_principles,
            )
            meta_analysis = None
            if outcome and outcome.stop_reason == "CYCLES_EXHAUSTED" and state.last_critique:
                violations = getattr(state.last_critique, "violations", []) or []
                meta_analysis = MetaAnalysis(
                    critic_rationales=[getattr(v, "rationale", "") or "" for v in violations],
                    hindsight_score=state.hindsight_score,
                    stop_reason=outcome.stop_reason,
                )
            return FinalResponse(
                content=content,
                response_type=ResponseType.WITH_CAVEAT,
                metadata=metadata,
                policy_overlay=policy_overlay,
                meta_analysis=meta_analysis,
            )
        metadata.predicted_action = RiskPolicyAction.ALLOW.value
        metadata.final_action = "NORMAL_COMPLETE"
        metadata.path = decision.path
        return FinalResponse(
            content=state.draft_response,
            response_type=ResponseType.DIRECT,
            metadata=metadata,
        )

    def _has_soft_violations(self, state: DeliberationState) -> bool:
        if not state.critiques:
            return False
        last = state.critiques[-1]
        if hasattr(last, "violations"):
            for v in last.violations:
                if hasattr(v, "constraint_type") and v.constraint_type == "soft":
                    return True
        return False

    def _draft_is_valid_refusal(self, state: DeliberationState) -> bool:
        """
        True if the draft from deliberation is already a valid refusal (critic-approved as refusal).

        Requires: draft exists with sufficient length, critic present, zero violations,
        and critic decision is NOT PROCEED (if critic says PROCEED the draft is constructive
        content, not a refusal; we must generate a real refusal via policy.refuse()).
        """
        if not state.draft_response or len(state.draft_response.strip()) < 30:
            return False
        if not state.last_critique:
            return False
        violations = getattr(state.last_critique, "violations", None) or []
        if len(violations) > 0:
            return False
        critic_decision = (getattr(state.last_critique, "decision", "") or "").strip().upper()
        if critic_decision == "PROCEED":
            return False
        return True

    def _make_refusal(
        self,
        request: ProcessedRequest,
        state: DeliberationState,
        decision: Decision,
        metadata: ResponseMetadata,
        risk_estimation: RiskEstimationProtocol | None = None,
        constitution_store: object | None = None,
    ) -> FinalResponse:
        """Generate REFUSE content from shared refusal generator."""
        # DISABILITO: Il draft_response è la risposta originale rejettata, NON un refusal valido
        # Non dovremmo mai usare il draft per REFUSE - deve sempre generare nuovo refusal
        # if self._draft_is_valid_refusal(state):
        #     content = state.draft_response.strip()
        #     if len(content) > 20:
        #         reason = (state.last_critique.revision_guidance or "").strip() if state.last_critique else ""
        #         metadata.predicted_action = RiskPolicyAction.DENY.value
        #         metadata.must_refuse = True
        #         metadata.refusal_reason = reason or content[:200] or "[REFUSAL_POLICY]"
        #         return FinalResponse(
        #             content=content,
        #             response_type=ResponseType.FULL_REFUSAL,
        #             metadata=metadata,
        #         )

        # Costruisci una guidance reale a partire dai dati disponibili,
        # non affidarti alla revision_guidance del critic che è vuota su REFUSE.
        _critique_guidance = ""
        if state.last_critique and hasattr(state.last_critique, "revision_guidance"):
            _critique_guidance = (state.last_critique.revision_guidance or "").strip()

        # Se il critic non ha prodotto guidance (caso REFUSE), usa il rationale
        # del risk estimator come base per la guidance al refusal LLM.
        if not _critique_guidance and risk_estimation is not None:
            _critique_guidance = (getattr(risk_estimation, "rationale", "") or "").strip()

        # Fallback finale: almeno il reason_code è informativo
        if not _critique_guidance and state.has_critical_violations:
            _critique_guidance = "Request classified as clearly harmful based on policy evaluation."

        reason = _critique_guidance or "[REFUSAL_FALLBACK]"

        if self.policy is not None:
            try:
                detected_iso = getattr(risk_estimation, "detected_language", None) or "" if risk_estimation else ""
                explicit_lang = (
                    _iso_to_language_name(detected_iso) if detected_iso else _detect_language_fallback(request.prompt)
                )
                resolved_domain, refusal_redirection = resolve_refusal_domain_and_redirection(
                    request_prompt=request.prompt,
                    request_domain=request.get_domain() if hasattr(request, "get_domain") else None,
                    detected_domain=getattr(risk_estimation, "detected_domain", None) if risk_estimation else None,
                    risk_signals=list(getattr(decision, "risk_signals", None) or []),
                    constitution_store=constitution_store,
                )
                risk_cat_str = risk_category_str(risk_estimation) if risk_estimation is not None else ""
                refusal_context = build_refusal_context(
                    risk_estimation=risk_estimation,
                    decision=decision,
                    domain=resolved_domain,
                    refusal_redirection=refusal_redirection,
                    risk_score=getattr(metadata, "risk_score", None),
                    risk_category=risk_cat_str,
                    developer_contract=getattr(request, "developer_contract", None),
                    conversation_history=getattr(request, "conversation_history", None),
                )
                _refusal_t0 = time.time()
                refusal_result = generate_llm_safe_refusal_detailed(
                    user_prompt=request.prompt,
                    risk_category=risk_cat_str,
                    policy_reason_codes=list(getattr(decision, "reason_codes", None) or []),
                    language=explicit_lang or "English",
                    domain=resolved_domain,
                    llm_client=self.policy,
                    rationale=reason if reason else None,
                    refusal_redirection=refusal_redirection,
                    refusal_context=refusal_context,
                )
                _refusal_duration_ms = (time.time() - _refusal_t0) * 1000.0
                content = refusal_result.text
                # Persist the synthetic refusal prompts so they appear in the UI
                # / markdown export. Without this, only request.prompt would be
                # logged (set by the deliberation_runner downstream), which hides
                # the actual refusal-LLM input from observability.
                try:
                    record_llm_call(
                        None,
                        None,
                        {
                            "phase": "refusal",
                            "module": "orchestration",
                            "action": (
                                "refuse (deliberative, retried_no_leak)"
                                if refusal_result.leak_retried
                                else "refuse (deliberative)"
                            ),
                            "duration_ms": _refusal_duration_ms,
                            "prompt": refusal_result.user_prompt,
                            "system_prompt": refusal_result.system_prompt,
                            "raw_response": content,
                            "attempts": refusal_result.attempts,
                            "sequence_in_cycle": _SEQ_REFUSAL_OR_FINALIZE,
                            "token_usage_json": refusal_result.token_usage.to_json(),
                        },
                    )
                except Exception as _persist_e:
                    _LOG.warning(
                        "response_assembler: persisting refusal LLM call failed: %s",
                        _persist_e,
                    )
                if len(content.strip()) > 20:
                    metadata.predicted_action = RiskPolicyAction.DENY.value
                    metadata.must_refuse = True
                    metadata.refusal_reason = (reason or content[:200] or "").strip() or "[REFUSAL_POLICY]"
                    metadata.refusal_domain = resolved_domain
                    metadata.refusal_redirection_source = (
                        "domain_overlay"
                        if (refusal_redirection and resolved_domain != "general")
                        else ("refusal_context" if refusal_context.safe_redirection_guidance else "none")
                    )
                    metadata.safe_refusal_focus = refusal_context.safe_refusal_focus
                    return FinalResponse(
                        content=content,
                        response_type=ResponseType.FULL_REFUSAL,
                        metadata=metadata,
                    )
            except Exception as e:
                request_id = getattr(request, "request_id", "") or ""
                _LOG.warning(
                    "refusal_llm_enrichment failed request_id=%s error_type=%s error=%s; using non-enriched refusal",
                    request_id,
                    type(e).__name__,
                    e,
                )

        # Do not enrich structural markers (language-agnostic fallback).
        is_marker = reason.strip().startswith("[") and reason.strip().endswith("]")
        if not is_marker and len(reason) < self.REFUSAL_MIN_REASON_LEN:
            reason = reason + self.REFUSAL_ENRICHMENT
        if not is_marker:
            sentences = [s.strip() for s in reason.replace("!", ".").replace("?", ".").split(".") if s.strip()]
            if len(sentences) < 2 and reason:
                reason = reason.rstrip(".") + ". " + self.REFUSAL_ENRICHMENT.strip()

        alternatives: list[str] = []
        if state.last_critique and hasattr(state.last_critique, "revision_guidance"):
            g = (state.last_critique.revision_guidance or "").lower()
            for prefix in ("suggest:", "alternative:"):
                if prefix in g:
                    idx = (state.last_critique.revision_guidance or "").lower().find(prefix)
                    rest = (state.last_critique.revision_guidance or "")[idx + len(prefix) :].strip()
                    if rest:
                        alternatives.append(rest.split(".")[0].strip() + ("." if not rest.endswith(".") else ""))
                        break
        content = self.format_refusal(reason, alternatives)
        metadata.predicted_action = RiskPolicyAction.DENY.value
        metadata.must_refuse = True
        metadata.refusal_reason = (reason or "").strip() or "[REFUSAL_POLICY]"
        return FinalResponse(
            content=content,
            response_type=ResponseType.FULL_REFUSAL,
            metadata=metadata,
        )

    def format_refusal(self, reason: str, alternatives: list[str]) -> str:
        """Formatta un messaggio di rifiuto. Language-agnostic: no hardcoded natural language."""
        if alternatives:
            alt_text = "\n".join(f"- {a}" for a in alternatives)
        else:
            alt_text = "[REFUSAL_ALTERNATIVES]"
        return self.REFUSAL_TEMPLATE.format(reason=reason, alternatives=alt_text).strip()
