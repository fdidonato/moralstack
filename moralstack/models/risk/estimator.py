"""
LLMBasedRiskEstimator - Classificazione rischio etico per MoralStack.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import nullcontext
from typing import Any, Literal

from moralstack.core.types import PolicyLLMProtocol
from moralstack.observability.context import get_current_cycle as _get_cycle
from moralstack.observability.context import get_current_request_id as _get_request_id
from moralstack.observability.context import get_current_run_id as _get_run_id
from moralstack.observability.context import get_current_session_id as _get_session_id
from moralstack.observability.context import get_current_turn_number as _get_turn_number
from moralstack.observability.events import EVENT_LLM_CALL, EventEnvelope
from moralstack.observability.events import make_envelope as _make_envelope
from moralstack.observability.phase0_timing import emit_phase0_timing, phase0_timing_enabled
from moralstack.orchestration.types import RiskEstimationError
from moralstack.utils.llm_parse_contract import (
    parse_dict_with_contract,
)

from .calibration import merge_mini_estimator_results, parse_risk_dict
from .categories import (
    ActionabilityRisk,
    IntentClarity,
    MisusePlausibility,
    RiskCategory,
    RiskPolicyAction,
)
from .config_loader import (
    ENV_CATEGORIZE_BENIGN_THRESHOLD,
    ENV_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD,
    ENV_CATEGORIZE_SENSITIVE_THRESHOLD,
    ENV_CRISIS_CLAMP_HIGH,
    ENV_CRISIS_CLAMP_LOW,
    ENV_RULE_PREVIEW_LEN,
    ENV_TOP_K,
    ENV_TOP_P,
    get_risk_env_float,
    get_risk_env_int,
    load_risk_estimator_config_from_env,
)
from .parse_result import RiskParseResult
from .prompts import (
    INTENT_CONTEXT_PROMPT_TEMPLATE,
    INTENT_CONTEXT_SYSTEM_PROMPT,
    OPERATIONAL_RISK_PROMPT_TEMPLATE,
    OPERATIONAL_RISK_SYSTEM_PROMPT,
)
from .schema import RiskEstimation, RiskEstimatorConfig
from .signals.prompt_renderer import get_harm_signal_prompts
from .signals.registry import registry as signal_registry
from .utils import _intent_type_from_request_type

_RISK_LOG = logging.getLogger(__name__)


def _obs_route(envelope: EventEnvelope) -> None:
    """Enqueue one risk mini-call envelope. Never raises into risk estimation."""
    from moralstack.observability.service import get_obs

    get_obs().emit(envelope)


def _obs_route_batch(envelopes: list[EventEnvelope]) -> None:
    """Enqueue risk mini-call envelopes. Never raises into risk estimation."""
    from moralstack.observability.service import get_obs

    get_obs().emit_batch(envelopes)


_LOCAL_LLM_CALL_PAYLOAD_KEYS = (
    "phase",
    "module",
    "action",
    "model",
    "started_at",
    "duration_ms",
    "prompt",
    "system_prompt",
    "raw_response",
    "parsed_json",
    "parsed_summary_json",
    "token_usage_json",
    "attempts",
    "error",
    "sequence_in_cycle",
)


class _Phase0Timer:
    """Small scoped timer for temporary Phase 0 instrumentation."""

    def __init__(self, event: str, **fields: Any) -> None:
        self._event = event
        self._fields = fields
        self._started = 0.0

    def __enter__(self) -> "_Phase0Timer":
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        emit_phase0_timing(
            self._event,
            (time.perf_counter() - self._started) * 1000,
            error_type=getattr(exc_type, "__name__", None),
            **self._fields,
        )


def persist_llm_call(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    cycle: int | None = None,
    phase: str = "",
    module: str = "",
    action: str = "",
    model: str | None = None,
    started_at: int | None = None,
    duration_ms: float | None = None,
    prompt: str = "",
    system_prompt: str = "",
    raw_response: str = "",
    parsed_json: str | None = None,
    parsed_summary_json: str | None = None,
    token_usage_json: str | None = None,
    attempts: int | None = None,
    error: str | None = None,
    sequence_in_cycle: int | None = None,
    **kwargs: Any,
) -> bool:
    try:
        envelope = _build_local_llm_call_envelope(
            run_id=run_id,
            request_id=request_id,
            cycle=cycle,
            phase=phase,
            module=module,
            action=action,
            model=model,
            started_at=started_at,
            duration_ms=duration_ms,
            prompt=prompt,
            system_prompt=system_prompt,
            raw_response=raw_response,
            parsed_json=parsed_json,
            parsed_summary_json=parsed_summary_json,
            token_usage_json=token_usage_json,
            attempts=attempts,
            error=error,
            sequence_in_cycle=sequence_in_cycle,
        )
        if envelope is None:
            return False
        _obs_route(envelope)
        return True
    except Exception:
        return False


def _build_local_llm_call_envelope(
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    cycle: int | None = None,
    phase: str = "",
    module: str = "",
    action: str = "",
    model: str | None = None,
    started_at: int | None = None,
    duration_ms: float | None = None,
    prompt: str = "",
    system_prompt: str = "",
    raw_response: str = "",
    parsed_json: str | None = None,
    parsed_summary_json: str | None = None,
    token_usage_json: str | None = None,
    attempts: int | None = None,
    error: str | None = None,
    sequence_in_cycle: int | None = None,
) -> EventEnvelope | None:
    _run_id = run_id or _get_run_id()
    _request_id = request_id or _get_request_id()
    if not _run_id or not _request_id:
        return None
    _cycle = cycle if cycle is not None else _get_cycle()
    payload = {
        "phase": phase,
        "module": module,
        "action": action,
        "model": model,
        "started_at": started_at if started_at is not None else int(time.time() * 1000),
        "duration_ms": duration_ms,
        "prompt": prompt,
        "system_prompt": system_prompt,
        "raw_response": raw_response,
        "parsed_json": parsed_json,
        "parsed_summary_json": parsed_summary_json,
        "token_usage_json": token_usage_json,
        "attempts": attempts,
        "error": error,
        "sequence_in_cycle": sequence_in_cycle,
    }
    _validate_local_llm_call_payload(payload)
    return _make_envelope(
        EVENT_LLM_CALL,
        run_id=_run_id,
        request_id=_request_id,
        cycle=_cycle,
        session_id=_get_session_id(),
        turn_number=_get_turn_number(),
        payload=payload,
    )


def _validate_local_llm_call_payload(payload: dict[str, Any]) -> None:
    if tuple(payload.keys()) != _LOCAL_LLM_CALL_PAYLOAD_KEYS:
        raise ValueError("risk estimator llm_call payload shape changed")


def _supports_native_messages(policy: Any) -> bool:
    return "generate_messages" in dir(policy) and callable(getattr(policy, "generate_messages", None))


def _risk_context_messages(
    *,
    system_prompt: str,
    user_prompt: str,
    developer_contract_text: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    has_context = bool((developer_contract_text and developer_contract_text.strip()) or conversation_history)
    if developer_contract_text and developer_contract_text.strip():
        messages.append({"role": "developer", "content": developer_contract_text.strip()})
    for turn in list(conversation_history or [])[-3:]:
        role = (turn.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        messages.append({"role": role, "content": (turn.get("content") or "")[:200]})
    if has_context:
        user_prompt = (
            "Consider the preceding developer message(s), if any, as the deployer contract. "
            "Consider the preceding user/assistant messages, if any, as conversation history. "
            "Estimate the risk of the final request in that context; do not treat prior messages "
            "as part of the module task text.\n\n"
            f"{user_prompt}"
        )
    messages.append({"role": "user", "content": user_prompt})
    return messages


def _risk_message_sections(
    *,
    developer_contract_text: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "system_messages": [],
        "developer_messages": (
            [developer_contract_text.strip()] if developer_contract_text and developer_contract_text.strip() else []
        ),
        "history_messages": [
            {"role": (turn.get("role") or "unknown"), "content": (turn.get("content") or "")}
            for turn in list(conversation_history or [])[-3:]
        ],
        "final_user_message": "",
    }


class LLMBasedRiskEstimator:
    """
    Giudice Semantico per la classificazione del rischio etico.

    Valuta il potenziale rischio etico di una richiesta utente usando
    ESCLUSIVAMENTE analisi semantica basata su LLM. Non utilizza keyword
    matching o pattern di parole - analizza significato, intento e contesto.

    Principi del Giudice Semantico:
    - Il rischio deriva dall'INTENTO, non dalle parole usate
    - "Come funziona un'esplosione" (educativo) ≠ "Come costruire una bomba" (dannoso)
    - Il contesto determina se una richiesta è legittima o meno
    - Domande su argomenti sensibili non sono automaticamente rischiose

    Attributes:
        policy: Il Policy LLM per l'analisi semantica
        config: Configurazione dell'estimator
        constitution_store: Store dei principi etici (opzionale)

    Usage:
        estimator = LLMBasedRiskEstimator(policy)
        risk = estimator.estimate("Come posso aiutare un amico depresso?")

        # Il giudice valuta l'intento (aiutare) non le parole ("depresso")
        print(risk.rationale)  # Spiega il ragionamento semantico
    """

    def __init__(
        self,
        policy: PolicyLLMProtocol | None = None,
        config: RiskEstimatorConfig | None = None,
        constitution_store: Any | None = None,
    ) -> None:
        """
        Inizializza il Giudice Semantico.

        Args:
            policy: Policy LLM per l'analisi semantica.
                   Se None, usa fallback conservativo (richiede sempre deliberazione).
            config: Configurazione (opzionale).
            constitution_store: Constitution Store opzionale per arricchire l'analisi.
        """
        self.policy = policy
        self.config = config or load_risk_estimator_config_from_env()
        self.constitution_store = constitution_store
        # Env-derived params (read once; used in _semantic_analysis and categorize_from_score)
        self._top_p = get_risk_env_float(ENV_TOP_P, 0.9, 0.0, 1.0)
        self._top_k = get_risk_env_int(ENV_TOP_K, 10, 1)
        self._rule_preview_len = get_risk_env_int(ENV_RULE_PREVIEW_LEN, 200, 1)
        self._crisis_clamp_low = get_risk_env_float(ENV_CRISIS_CLAMP_LOW, 0.35, 0.0, 1.0)
        self._crisis_clamp_high = get_risk_env_float(ENV_CRISIS_CLAMP_HIGH, 0.65, 0.0, 1.0)
        self._categorize_benign = get_risk_env_float(ENV_CATEGORIZE_BENIGN_THRESHOLD, 0.2, 0.0, 1.0)
        self._categorize_sensitive = get_risk_env_float(ENV_CATEGORIZE_SENSITIVE_THRESHOLD, 0.5, 0.0, 1.0)
        self._categorize_clearly_harmful = get_risk_env_float(ENV_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD, 0.85, 0.0, 1.0)
        # Mini-estimator OpenAIPolicy reuse: keyed by model id on this estimator instance (thread-safe fill).
        self._mini_policy_pool: dict[str, Any] = {}
        self._mini_policy_pool_lock = threading.Lock()
        self._mini_policy_pool_hits: int = 0
        self._mini_policy_pool_misses: int = 0

    def get_pooling_diagnostics(self) -> dict[str, Any]:
        """
        Optional low-noise diagnostics for mini-estimator policy pooling.

        Not merged into main request UI by default; for advanced debug / benchmarks.
        """
        with self._mini_policy_pool_lock:
            models = list(self._mini_policy_pool.keys())
        return {
            "risk_mini_policy_pool_models": models,
            "risk_policy_pool_hits": self._mini_policy_pool_hits,
            "risk_policy_pool_misses": self._mini_policy_pool_misses,
        }

    def _policy_for_mini_estimator_model(self, target_model: str) -> Any:
        """
        Return a shared OpenAIPolicy for ``target_model`` on this estimator.

        Reuse scope: LLMBasedRiskEstimator instance only. Pool key is the model string
        (same as previous per-call ``OpenAIPolicy(model=...)``). Thread-safe for parallel minis.
        """
        from moralstack.models.policy import OpenAIPolicy

        key = str(target_model)
        with self._mini_policy_pool_lock:
            existing = self._mini_policy_pool.get(key)
            if existing is not None:
                self._mini_policy_pool_hits += 1
                return existing
            self._mini_policy_pool_misses += 1
            pol = OpenAIPolicy(model=target_model)
            if self.policy is not None and hasattr(self.policy, "tracker") and getattr(self.policy, "tracker", None):
                pol.set_cost_tracker(self.policy.tracker)
            self._mini_policy_pool[key] = pol
            return pol

    def estimate(
        self,
        prompt: str,
        *,
        developer_contract_text: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> RiskEstimation:
        """
        Stima il rischio etico di un prompt usando analisi semantica pura.

        Il Giudice Semantico analizza:
        1. L'INTENTO della richiesta (informativo, educativo, potenzialmente dannoso)
        2. Il CONTESTO implicito (chi potrebbe fare questa domanda e perché)
        3. Le CONSEGUENZE potenziali di una risposta

        NON considera:
        - Presenza di parole specifiche (no keyword matching)
        - Pattern lessicali superficiali

        Optional context (``developer_contract_text``, ``conversation_history``), when
        provided, is prepended to the request body sent to the three mini-estimators
        (intent, signals, operational) to disambiguate payloads whose meaning depends
        on the system prompt or prior turns.

        Args:
            prompt: Richiesta utente da valutare
            developer_contract_text: Deployer system prompt / developer contract text
            conversation_history: Prior turns as ``{"role", "content"}`` dicts

        Returns:
            RiskEstimation con score, categoria e ragionamento semantico
        """
        if not prompt or not prompt.strip():
            return RiskEstimation.benign(confidence=1.0, rationale="Empty request - no content to analyze")

        # Se non c'è LLM, usa fallback conservativo
        if self.policy is None:
            return self._fallback_estimate(prompt)

        # Analisi semantica via LLM (unica strategia)
        return self._semantic_analysis(
            prompt,
            developer_contract_text=developer_contract_text,
            conversation_history=conversation_history,
        )

    def _fallback_estimate(self, prompt: str) -> RiskEstimation:
        """
        Fallback conservativo quando LLM non è disponibile.

        Senza capacità di analisi semantica, il sistema assume che
        ogni richiesta richieda deliberazione per sicurezza.
        """
        return RiskEstimation(
            score=self.config.fallback_risk_score,
            confidence=self.config.fallback_confidence,
            risk_category=RiskCategory.SENSITIVE,
            semantic_signals=["NO_LLM_AVAILABLE"],
            rationale="Semantic analysis unavailable (no LLM). " "Conservatively requiring deliberation for safety.",
            used_fallback_parse=True,
        )

    def _build_generation_config(self) -> Any:
        """Build optional GenerationConfig for policy.generate. Returns None if unavailable."""
        try:
            from moralstack.models.policy import GenerationConfig

            return GenerationConfig(
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                top_p=self._top_p,
                # OpenAI Chat Completions: enforce a single JSON object (tolerant recovery still in extract_json).
                response_format={"type": "json_object"},
            )
        except ImportError as e:
            _RISK_LOG.debug("GenerationConfig unavailable: %s", e)
            return None

    def _get_principles_context(self, prompt: str) -> tuple[str, str | None]:
        """
        Retrieve and format relevant constitution principles for the prompt.

        Returns (context, runtime_domain). `runtime_domain` is derived from the
        DomainPrefilter's prefiltered_domains (single source of truth) — never
        from a separate LLM call. `core` is treated as a retrieval-only pseudo-
        domain and is never returned as runtime_domain.
        """
        if self.constitution_store is None:
            return "", None
        try:
            relevant_principles = self.constitution_store.get_relevant_principles(
                query=prompt, top_k=self._top_k, domain=None
            )

            runtime_domain: str | None = None
            try:
                get_debug_info = getattr(self.constitution_store, "get_debug_info", None)
                debug_info = get_debug_info() if callable(get_debug_info) else {}
                prefiltered_domains = list(debug_info.get("prefiltered_domains") or [])
                specific_domains = [
                    d.strip() for d in prefiltered_domains if isinstance(d, str) and d.strip() and d.strip() != "core"
                ]
                runtime_domain = specific_domains[0] if specific_domains else None
            except Exception:
                runtime_domain = None

            if not relevant_principles:
                return "", runtime_domain
            n_principles = self._top_k
            hard_principles = [p for p in relevant_principles[:n_principles] if p.level == "hard"]
            soft_principles = [p for p in relevant_principles[:n_principles] if p.level == "soft"]
            rpl = self._rule_preview_len
            hard_list = []
            for p in hard_principles:
                rule_preview = p.rule[:rpl] + "..." if len(p.rule) > rpl else p.rule
                hard_list.append(f"- {p.id} [HARD]: {p.title}\n  Rule: {rule_preview}")
            soft_list = []
            for p in soft_principles:
                rule_preview = p.rule[:rpl] + "..." if len(p.rule) > rpl else p.rule
                soft_list.append(f"- {p.id} [SOFT]: {p.title}\n  Rule: {rule_preview}")
            formatted_context = f"""

RELEVANT ETHICAL PRINCIPLES FROM CONSTITUTION (for context):

⚠️ HARD CONSTRAINTS:
{chr(10).join(hard_list) if hard_list else "  (None found)"}

ℹ️ SOFT NORMS:
{chr(10).join(soft_list) if soft_list else "  (None found)"}

IMPORTANT - SEMANTIC ANALYSIS GUIDELINES:
1. These principles are RELEVANT to the topic, but the request may NOT violate them.
2. Analyze the INTENT and MEANING, not the presence of specific words.
3. A question ABOUT a sensitive topic is NOT the same as REQUESTING harmful content.
4. Academic/educational discussions RESPECT principles by promoting understanding.
5. Only classify as harmful if the request ACTIVELY SEEKS TO CAUSE HARM:
   - Asking HOW TO harm, deceive, or manipulate someone → potentially harmful
   - Asking ABOUT ethics, psychology, or controversial topics → educational/sensitive"""
            return formatted_context, runtime_domain
        except Exception as e:
            _RISK_LOG.warning("principles retrieval failed, continuing without: %s", e)
            return "", None

    def _post_process_crisis(self, parsed: RiskParseResult) -> tuple[
        float,
        RiskCategory,
        list[str],
        RiskPolicyAction,
        IntentClarity,
        MisusePlausibility,
        ActionabilityRisk,
        str,
    ]:
        """
        Apply crisis/help-seeking post-processing: self-harm language without
        requested instructions or intent to harm → clamp score and set crisis_support.
        Returns (score, category, signals, risk_policy_action, intent_clarity,
        misuse_plausibility, actionability_risk, request_type_final).
        """
        request_type_override = ""
        score = parsed.score
        category = parsed.category
        signals = list(parsed.signals)
        risk_policy_action = parsed.risk_policy_action
        intent_clarity = parsed.intent_clarity
        misuse_plausibility = parsed.misuse_plausibility
        actionability_risk = parsed.actionability_risk
        if parsed.self_harm_language and not parsed.requested_instructions and not parsed.intent_to_harm:
            request_type_override = "crisis_support"
            category = RiskCategory.SENSITIVE
            risk_policy_action = RiskPolicyAction.DELIBERATE
            score = max(
                self._crisis_clamp_low,
                min(self._crisis_clamp_high, parsed.score),
            )
            intent_clarity = IntentClarity.HIGH
            misuse_plausibility = MisusePlausibility.LOW
            actionability_risk = ActionabilityRisk.LOW
            if not any("crisis_support" in str(s) for s in signals):
                signals = signals + ["request_type:crisis_support"]
        request_type_final = request_type_override or parsed.request_type or ""
        return (
            score,
            category,
            signals,
            risk_policy_action,
            intent_clarity,
            misuse_plausibility,
            actionability_risk,
            request_type_final,
        )

    def _to_risk_estimation(
        self,
        parsed: RiskParseResult,
        raw_response: str,
        score: float,
        category: RiskCategory,
        signals: list[str],
        risk_policy_action: RiskPolicyAction,
        intent_clarity: IntentClarity,
        misuse_plausibility: MisusePlausibility,
        actionability_risk: ActionabilityRisk,
        request_type_final: str,
        detected_domain: str | None = None,
        estimation_mode: str = "",
    ) -> RiskEstimation:
        """Build RiskEstimation from parsed result and post-processed values."""
        intent_type = _intent_type_from_request_type(
            request_type_final,
            intent_operational=parsed.intent_operational,
            risk_score=score,
        )
        return RiskEstimation(
            score=score,
            confidence=parsed.confidence,
            risk_category=category,
            domain_sensitivity=parsed.domain_sensitivity,
            operational_risk=parsed.operational_risk,
            semantic_signals=signals,
            risk_policy_action=risk_policy_action,
            rationale=parsed.rationale,
            raw_response=raw_response,
            intent_clarity=intent_clarity,
            misuse_plausibility=misuse_plausibility,
            actionability_risk=actionability_risk,
            harm_type=parsed.harm_type,
            request_type=request_type_final,
            intent_type=intent_type,
            intent_operational=parsed.intent_operational,
            requested_instructions=parsed.requested_instructions,
            intent_to_harm=parsed.intent_to_harm,
            detected_language=parsed.detected_language,
            detected_domain=detected_domain,
            estimation_mode=estimation_mode,
            stated_personal_bias=parsed.stated_personal_bias,
            seeks_norm_circumvention=parsed.seeks_norm_circumvention,
            q13_protected_class_targeting=parsed.q13_protected_class_targeting,
            q14_reputational_harm=parsed.q14_reputational_harm,
            q15_deceptive_online_campaign=parsed.q15_deceptive_online_campaign,
            q16_harassment_smear_campaign=parsed.q16_harassment_smear_campaign,
            q17_minor_exploitation=parsed.q17_minor_exploitation,
        )

    def _persist_mini_llm_call(
        self,
        *,
        system_prompt: str,
        prompt: str,
        raw_response: str,
        action: str,
        started_at: int | None = None,
        duration_ms: float,
        attempts: int,
        parse_contract: dict[str, Any] | None = None,
        token_usage_json: str | None = None,
        llm_model: str | None = None,
        sequence_in_cycle: int = -9,
        message_sections: dict[str, Any] | None = None,
    ) -> None:
        """Enqueue a single mini-estimator LLM call. Does not raise."""
        try:
            envelope = self._build_mini_llm_call_envelope(
                system_prompt=system_prompt,
                prompt=prompt,
                raw_response=raw_response,
                action=action,
                started_at=started_at,
                duration_ms=duration_ms,
                attempts=attempts,
                parse_contract=parse_contract,
                token_usage_json=token_usage_json,
                llm_model=llm_model,
                sequence_in_cycle=sequence_in_cycle,
                message_sections=message_sections,
            )
            if envelope is not None:
                _obs_route(envelope)
        except Exception as e:
            _RISK_LOG.debug("persist_mini_llm_call failed: %s", e)

    def _build_mini_llm_call_envelope(
        self,
        *,
        system_prompt: str,
        prompt: str,
        raw_response: str,
        action: str,
        started_at: int | None = None,
        duration_ms: float,
        attempts: int,
        parse_contract: dict[str, Any] | None = None,
        token_usage_json: str | None = None,
        llm_model: str | None = None,
        sequence_in_cycle: int = -9,
        message_sections: dict[str, Any] | None = None,
    ) -> EventEnvelope | None:
        import json as _json

        # Parallel mini-estimators may use pooled policies with a different model than self.policy;
        # pass llm_model from the caller when the effective OpenAI policy differs.
        if llm_model is not None:
            risk_model_str = llm_model or None
        else:
            risk_model = getattr(self.policy, "model", None) if self.policy else None
            risk_model_str = str(risk_model) if risk_model is not None else None
        summary: dict[str, Any] = {"mini_estimator": action, "estimation_mode": "parallel"}
        if parse_contract is not None:
            summary["parse_contract"] = parse_contract
        if message_sections:
            summary["message_sections"] = message_sections
        return _build_local_llm_call_envelope(
            cycle=0,
            phase="risk_estimation",
            module="risk_estimator",
            action=action,
            model=risk_model_str,
            started_at=started_at,
            duration_ms=duration_ms,
            prompt=prompt,
            system_prompt=system_prompt,
            raw_response=raw_response,
            parsed_summary_json=_json.dumps(summary, ensure_ascii=False),
            attempts=attempts,
            token_usage_json=token_usage_json,
            sequence_in_cycle=sequence_in_cycle,
        )

    def _persist_mini_llm_calls_batch(self, calls: list[dict[str, Any]]) -> None:
        """Enqueue mini-estimator rows as one best-effort async batch."""
        try:
            envelopes = [env for call in calls if (env := self._build_mini_llm_call_envelope(**call)) is not None]
            if envelopes:
                _obs_route_batch(envelopes)
        except Exception as e:
            _RISK_LOG.debug("persist_mini_llm_calls_batch failed: %s", e)

    def _parallel_mini_analysis(
        self,
        prompt: str,
        *,
        developer_contract_text: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> RiskEstimation:
        """
        Esegue 3 mini-chiamate LLM in parallelo via ThreadPoolExecutor.

        LLM 1 (estimate_intent): detected_language, intent_*, request_type, harm_type, rationale (intent)
        LLM 2 (estimate_signals): q1-q13, domain_sensitivity
        LLM 3 (estimate_operational): operational_risk, risk_score, confidence, misuse_plausibility, ..., rationale (risk)

        Aggrega i risultati con merge_mini_estimator_results() e li processa con
        la pipeline di calibrazione standard (parse_risk_dict).
        estimation_mode="parallel" è impostato nel RiskEstimation risultante.
        """
        from concurrent.futures import ThreadPoolExecutor

        if self.policy is None:
            raise RiskEstimationError("policy not set")

        _policy = self.policy  # narrowed non-optional for closure capture

        gen_config = self._build_generation_config()
        context, detected_domain = self._get_principles_context(prompt)

        intent_prompt = INTENT_CONTEXT_PROMPT_TEMPLATE.format(
            request=prompt,
            constitution_context=context,
        )
        harm_signal_system_prompt, harm_signal_user_template = get_harm_signal_prompts(signal_registry)
        signal_prompt = harm_signal_user_template.format(request=prompt)
        operational_prompt = OPERATIONAL_RISK_PROMPT_TEMPLATE.format(request=prompt)
        message_sections = _risk_message_sections(
            developer_contract_text=developer_contract_text,
            conversation_history=conversation_history,
        )

        _RISK_LOG.info(
            "risk_estimator parallel mini-models (config): intent=%s signals=%s operational=%s; "
            "main risk policy model=%s",
            self.config.intent_model,
            self.config.signals_model,
            self.config.operational_model,
            getattr(_policy, "model", None),
        )

        # Each future returns (
        #   data_dict, raw_response, duration_ms, attempts, started_at_ms, parse_contract, token_usage_json,
        #   resolved_model_str for observability,
        # )
        def _call_and_track(
            system_prompt: str, full_prompt: str, mini_name: str
        ) -> tuple[Any, str, float, int, int, Any, str | None, str | None]:
            from moralstack.utils.json_utils import JSONParseError

            # Determine the model for this mini-call
            target_model = None
            if mini_name == "estimate_intent":
                target_model = self.config.intent_model
            elif mini_name == "estimate_signals":
                target_model = self.config.signals_model
            elif mini_name == "estimate_operational":
                target_model = self.config.operational_model

            # Use dedicated policy if model override is present and different from main policy
            effective_policy = _policy
            if target_model and hasattr(_policy, "model") and _policy.model != target_model:
                try:
                    effective_policy = self._policy_for_mini_estimator_model(target_model)
                    _RISK_LOG.debug("mini_estimator[%s] using dedicated model: %s", mini_name, target_model)
                except Exception as e:
                    _RISK_LOG.warning(
                        "Failed to create dedicated policy for %s (%s): %s. Falling back to default.",
                        mini_name,
                        target_model,
                        e,
                    )
                    effective_policy = _policy

            resolved_for_obs = getattr(effective_policy, "model", None)
            if resolved_for_obs is None and target_model:
                resolved_for_obs = target_model
            resolved_model_str = str(resolved_for_obs) if resolved_for_obs is not None else None

            raw_response = ""
            for attempt in range(self.config.max_retries):
                try:
                    start_gen = time.time()
                    if (developer_contract_text or conversation_history) and _supports_native_messages(effective_policy):
                        result = effective_policy.generate_messages(
                            messages=_risk_context_messages(
                                system_prompt=system_prompt,
                                user_prompt=full_prompt,
                                developer_contract_text=developer_contract_text,
                                conversation_history=conversation_history,
                            ),
                            config=gen_config,
                        )
                    else:
                        result = effective_policy.generate(
                            prompt=full_prompt,
                            system=system_prompt,
                            config=gen_config,
                        )
                    elapsed_ms = (time.time() - start_gen) * 1000
                    raw_response = result.text if hasattr(result, "text") else str(result)
                    _RISK_LOG.info(
                        "mini_estimator[%s] raw_output (troncato): %s | elapsed_ms=%.0f",
                        mini_name,
                        (raw_response or "")[:200],
                        elapsed_ms,
                    )
                    data, p_contract = parse_dict_with_contract(raw_response, strict_json_requested=True)
                    _tu = result.token_usage_json() if hasattr(result, "token_usage_json") else None
                    return (
                        data,
                        raw_response,
                        elapsed_ms,
                        attempt + 1,
                        int(start_gen * 1000),
                        p_contract,
                        _tu,
                        resolved_model_str,
                    )
                except JSONParseError as e:
                    _RISK_LOG.warning(
                        "mini_estimator[%s] attempt %s/%s failed (JSONParseError): %s",
                        mini_name,
                        attempt + 1,
                        self.config.max_retries,
                        str(e),
                    )
                except Exception as e:
                    _RISK_LOG.warning(
                        "mini_estimator[%s] attempt %s/%s failed: %s",
                        mini_name,
                        attempt + 1,
                        self.config.max_retries,
                        str(e),
                    )
            raise RiskEstimationError(f"Mini estimator [{mini_name}] failed after {self.config.max_retries} attempts")

        with ThreadPoolExecutor(max_workers=3) as executor:
            fut1 = executor.submit(_call_and_track, INTENT_CONTEXT_SYSTEM_PROMPT, intent_prompt, "estimate_intent")
            fut2 = executor.submit(_call_and_track, harm_signal_system_prompt, signal_prompt, "estimate_signals")
            fut3 = executor.submit(
                _call_and_track, OPERATIONAL_RISK_SYSTEM_PROMPT, operational_prompt, "estimate_operational"
            )
            intent_data, intent_raw, intent_ms, intent_attempts, intent_started, intent_pc, intent_tu, intent_m_obs = (
                fut1.result()
            )
            signal_data, signal_raw, signal_ms, signal_attempts, signal_started, signal_pc, signal_tu, signal_m_obs = (
                fut2.result()
            )
            (
                operational_data,
                op_raw,
                op_ms,
                op_attempts,
                op_started,
                op_pc,
                op_tu,
                op_m_obs,
            ) = fut3.result()

        # Persist all 3 mini-estimator LLM calls for UI visibility
        persist_timer = (
            _Phase0Timer("risk_estimator.mini_persist", row_count=3) if phase0_timing_enabled() else nullcontext()
        )
        with persist_timer:
            self._persist_mini_llm_calls_batch(
                [
                    {
                        "system_prompt": INTENT_CONTEXT_SYSTEM_PROMPT,
                        "prompt": intent_prompt,
                        "raw_response": intent_raw,
                        "action": "estimate_intent",
                        "started_at": intent_started,
                        "duration_ms": intent_ms,
                        "attempts": intent_attempts,
                        "parse_contract": intent_pc,
                        "token_usage_json": intent_tu,
                        "llm_model": intent_m_obs,
                        "message_sections": message_sections,
                    },
                    {
                        "system_prompt": harm_signal_system_prompt,
                        "prompt": signal_prompt,
                        "raw_response": signal_raw,
                        "action": "estimate_signals",
                        "started_at": signal_started,
                        "duration_ms": signal_ms,
                        "attempts": signal_attempts,
                        "parse_contract": signal_pc,
                        "token_usage_json": signal_tu,
                        "llm_model": signal_m_obs,
                        "message_sections": message_sections,
                    },
                    {
                        "system_prompt": OPERATIONAL_RISK_SYSTEM_PROMPT,
                        "prompt": operational_prompt,
                        "raw_response": op_raw,
                        "action": "estimate_operational",
                        "started_at": op_started,
                        "duration_ms": op_ms,
                        "attempts": op_attempts,
                        "parse_contract": op_pc,
                        "token_usage_json": op_tu,
                        "llm_model": op_m_obs,
                        "message_sections": message_sections,
                    },
                ]
            )

        merged = merge_mini_estimator_results(intent_data, signal_data, operational_data)

        # ── Persist calibration guard event if the guard was triggered ─────────
        if merged.get("_calibration_guard_applied"):
            import json as _json

            guard_notes = merged.get("_calibration_guard_notes", [])
            guard_request_type = merged.get("_calibration_guard_request_type", "")
            guard_summary = "; ".join(guard_notes)
            guard_rationale = (
                f"Calibration guard applied [request_type={guard_request_type}]: {guard_summary}. "
                "Risk metrics capped because intent estimator confirmed benign/non-operational "
                "framing with no harm intent and no requested instructions."
            )
            _RISK_LOG.warning(
                "CALIBRATION_GUARD persisting synthetic trace event: request_type=%s notes=%s",
                guard_request_type,
                guard_notes,
            )
            self._persist_mini_llm_call(
                system_prompt="[calibration_guard] Automatic recalibration of risk metrics",
                prompt="<synthetic — no LLM call>",
                raw_response=_json.dumps(
                    {
                        "rationale": guard_rationale,
                        "guard_applied": True,
                        "request_type": guard_request_type,
                        "notes": guard_notes,
                        "caps_applied": {
                            "risk_score_max": 0.45,
                            "operational_risk_max": "LOW",
                            "risk_policy_action_max": "DELIBERATE",
                            "misuse_plausibility_max": "MEDIUM",
                            "actionability_risk_max": "MEDIUM",
                        },
                    }
                ),
                action="calibration_guard",
                duration_ms=0.0,
                attempts=1,
                sequence_in_cycle=-8,
            )

        parsed = parse_risk_dict(merged)

        _RISK_LOG.info(
            "parallel_mini_estimator [mode=parallel] parsed: score=%.2f category=%s "
            "risk_policy_action=%s lang=%s request_type=%s "
            "stated_personal_bias=%s seeks_norm_circumvention=%s q13=%s",
            parsed.score,
            getattr(parsed.category, "value", parsed.category),
            getattr(parsed.risk_policy_action, "value", parsed.risk_policy_action),
            parsed.detected_language,
            parsed.request_type,
            parsed.stated_personal_bias,
            parsed.seeks_norm_circumvention,
            parsed.q13_protected_class_targeting,
            extra={
                "estimation_mode": "parallel",
                "score": parsed.score,
                "category": getattr(parsed.category, "value", str(parsed.category)),
                "risk_policy_action": getattr(parsed.risk_policy_action, "value", parsed.risk_policy_action),
                "stated_personal_bias": parsed.stated_personal_bias,
                "seeks_norm_circumvention": parsed.seeks_norm_circumvention,
                "q13_protected_class_targeting": parsed.q13_protected_class_targeting,
            },
        )
        (
            score,
            category,
            signals,
            risk_policy_action,
            intent_clarity,
            misuse_plausibility,
            actionability_risk,
            request_type_final,
        ) = self._post_process_crisis(parsed)
        return self._to_risk_estimation(
            parsed,
            "",
            score,
            category,
            signals,
            risk_policy_action,
            intent_clarity,
            misuse_plausibility,
            actionability_risk,
            request_type_final,
            detected_domain=detected_domain,
            estimation_mode="parallel",
        )

    def _semantic_analysis(
        self,
        prompt: str,
        *,
        developer_contract_text: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> RiskEstimation:
        """
        Execute focused parallel mini-analysis.
        """
        try:
            return self._parallel_mini_analysis(
                prompt,
                developer_contract_text=developer_contract_text,
                conversation_history=conversation_history,
            )
        except Exception as e:
            _RISK_LOG.warning("parallel mini-analysis failed, using safe fallback estimate: %s", e)
            return RiskEstimation.from_error(str(e))

    def get_risk_level(self, estimation: RiskEstimation) -> Literal["low", "medium", "high"]:
        """
        Converte RiskEstimation in livello discreto.

        Args:
            estimation: Risultato della stima

        Returns:
            "low", "medium", o "high"
        """
        if estimation.score < self.config.low_threshold:
            return "low"
        elif estimation.score < self.config.medium_threshold:
            return "medium"
        else:
            return "high"

    def should_deliberate(self, estimation: RiskEstimation) -> bool:
        """
        Determina se è necessaria la deliberazione completa.

        Args:
            estimation: Risultato della stima

        Returns:
            True se serve deliberazione, False per fast path
        """
        return estimation.score >= self.config.low_threshold

    def categorize_from_score(self, score: float, is_moral_dilemma: bool = False) -> RiskCategory:
        """
        Mappa score numerico a categoria di rischio.

        Utile per garantire consistenza tra score e categoria.

        Args:
            score: Score di rischio [0, 1]
            is_moral_dilemma: Se True, forza categoria MORALLY_NUANCED per score 0.3-0.5

        Returns:
            RiskCategory corrispondente
        """
        if score < self._categorize_benign:
            return RiskCategory.BENIGN
        elif score < self.config.low_threshold:
            return RiskCategory.BENIGN
        elif score < self._categorize_sensitive:
            # Range low_threshold to categorize_sensitive: morally_nuanced or sensitive
            if is_moral_dilemma:
                return RiskCategory.MORALLY_NUANCED
            return RiskCategory.SENSITIVE
        elif score < self.config.medium_threshold:
            return RiskCategory.SENSITIVE
        elif score < self._categorize_clearly_harmful:
            return RiskCategory.POTENTIALLY_HARMFUL
        else:
            return RiskCategory.CLEARLY_HARMFUL

    def estimate_with_context(
        self,
        prompt: str,
        conversation_history: list[dict[str, str]] | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> RiskEstimation:
        """Legacy façade — see estimate() for the canonical entry point."""
        contract_text: str | None = None
        if user_context:
            contract_text = user_context.get("developer_contract")
        return self.estimate(
            prompt,
            developer_contract_text=contract_text,
            conversation_history=conversation_history,
        )


# =============================================================================
# Factory Functions
# =============================================================================


def create_risk_estimator(
    policy: PolicyLLMProtocol | None = None,
    low_threshold: float = 0.3,
    medium_threshold: float = 0.7,
    constitution_store: Any | None = None,
    config: RiskEstimatorConfig | None = None,
) -> LLMBasedRiskEstimator:
    """
    Factory function per creare un Giudice Semantico (Risk Estimator).

    Crea un classificatore di rischio basato ESCLUSIVAMENTE su analisi
    semantica. Non utilizza keyword matching.

    Args:
        policy: Policy LLM per l'analisi semantica.
                Se None, usa fallback conservativo (richiede sempre deliberazione).
        low_threshold: Soglia sotto la quale il rischio è considerato basso
        medium_threshold: Soglia sopra la quale serve deliberazione completa
        constitution_store: Store dei principi etici (opzionale)
        config: Config esplicita (opzionale); se None, carica da env.

    Returns:
        LLMBasedRiskEstimator configurato come giudice semantico

    Note:
        Senza un policy LLM, il sistema funziona in modo conservativo,
        richiedendo sempre deliberazione per ogni richiesta non vuota.
    """
    if config is None:
        base = load_risk_estimator_config_from_env()
        config = RiskEstimatorConfig(
            low_threshold=low_threshold,
            medium_threshold=medium_threshold,
            max_retries=base.max_retries,
            max_tokens=base.max_tokens,
            temperature=base.temperature,
            fallback_risk_score=base.fallback_risk_score,
            fallback_confidence=base.fallback_confidence,
            require_deliberation_on_fallback=base.require_deliberation_on_fallback,
            intent_model=base.intent_model,
            signals_model=base.signals_model,
            operational_model=base.operational_model,
        )
    else:
        config = RiskEstimatorConfig(
            low_threshold=low_threshold,
            medium_threshold=medium_threshold,
            max_retries=config.max_retries,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            fallback_risk_score=config.fallback_risk_score,
            fallback_confidence=config.fallback_confidence,
            require_deliberation_on_fallback=config.require_deliberation_on_fallback,
            intent_model=config.intent_model,
            signals_model=config.signals_model,
            operational_model=config.operational_model,
        )
    return LLMBasedRiskEstimator(
        policy=policy,
        config=config,
        constitution_store=constitution_store,
    )


def create_conservative_estimator(
    config: RiskEstimatorConfig | None = None,
) -> LLMBasedRiskEstimator:
    """
    Crea un estimator conservativo senza LLM.

    Utile per testing o quando non si vuole/può usare un LLM.
    Richiede sempre deliberazione per sicurezza.

    Args:
        config: Config esplicita (opzionale); se None, carica da env.

    Returns:
        LLMBasedRiskEstimator in modalità conservativa
    """
    if config is None:
        config = load_risk_estimator_config_from_env()
    return LLMBasedRiskEstimator(
        policy=None,
        config=config,
    )
