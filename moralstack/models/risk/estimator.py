"""
LLMBasedRiskEstimator - Classificazione rischio etico per MoralStack.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Literal

from moralstack.core.types import PolicyLLMProtocol
from moralstack.observability.context import get_current_cycle as _get_cycle
from moralstack.observability.context import get_current_request_id as _get_request_id
from moralstack.observability.context import get_current_run_id as _get_run_id
from moralstack.observability.context import get_current_session_id as _get_session_id
from moralstack.observability.context import get_current_turn_number as _get_turn_number
from moralstack.observability.events import EVENT_LLM_CALL
from moralstack.observability.events import make_envelope as _make_envelope
from moralstack.observability.router import route as _obs_route
from moralstack.orchestration.types import RiskEstimationError
from moralstack.utils.json_utils import JSONParseError
from moralstack.utils.llm_parse_contract import (
    build_failed_parse_contract,
    merge_parse_contract_into_summary,
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
    HARM_SIGNAL_PROMPT_TEMPLATE,
    HARM_SIGNAL_SYSTEM_PROMPT,
    INTENT_CONTEXT_PROMPT_TEMPLATE,
    INTENT_CONTEXT_SYSTEM_PROMPT,
    OPERATIONAL_RISK_PROMPT_TEMPLATE,
    OPERATIONAL_RISK_SYSTEM_PROMPT,
    RISK_PROMPT_TEMPLATE,
    RISK_SYSTEM_PROMPT,
)
from .schema import RiskEstimation, RiskEstimatorConfig
from .utils import _intent_type_from_request_type

_RISK_LOG = logging.getLogger(__name__)


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
    _run_id = run_id or _get_run_id()
    _request_id = request_id or _get_request_id()
    if not _run_id or not _request_id:
        return False
    _cycle = cycle if cycle is not None else _get_cycle()
    envelope = _make_envelope(
        EVENT_LLM_CALL,
        run_id=_run_id,
        request_id=_request_id,
        cycle=_cycle,
        session_id=_get_session_id(),
        turn_number=_get_turn_number(),
        payload={
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
        },
    )
    try:
        _obs_route(envelope)
        return True
    except Exception:
        return False


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

    def estimate(self, prompt: str) -> RiskEstimation:
        """
        Stima il rischio etico di un prompt usando analisi semantica pura.

        Il Giudice Semantico analizza:
        1. L'INTENTO della richiesta (informativo, educativo, potenzialmente dannoso)
        2. Il CONTESTO implicito (chi potrebbe fare questa domanda e perché)
        3. Le CONSEGUENZE potenziali di una risposta

        NON considera:
        - Presenza di parole specifiche (no keyword matching)
        - Pattern lessicali superficiali

        Args:
            prompt: Richiesta utente da valutare

        Returns:
            RiskEstimation con score, categoria e ragionamento semantico
        """
        if not prompt or not prompt.strip():
            return RiskEstimation.benign(confidence=1.0, rationale="Empty request - no content to analyze")

        # Se non c'è LLM, usa fallback conservativo
        if self.policy is None:
            return self._fallback_estimate(prompt)

        # Analisi semantica via LLM (unica strategia)
        return self._semantic_analysis(prompt)

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

    def _build_full_prompt(self, prompt: str) -> tuple[str, str | None]:
        """Build full prompt: base template + optional principles context. Returns (full_prompt, detected_domain)."""
        context, domain = self._get_principles_context(prompt)
        return RISK_PROMPT_TEMPLATE.format(request=prompt) + context, domain

    def _persist_risk_llm_call(
        self,
        *,
        started_at: int | None = None,
        duration_ms: float,
        prompt: str,
        raw_response: str,
        attempts: int,
        parsed_summary_json: str | None = None,
        error: str | None = None,
        token_usage_json: str | None = None,
    ) -> None:
        """Persist LLM call for risk estimation. Logs debug on ImportError."""
        risk_model = getattr(self.policy, "model", None) if self.policy else None
        risk_model_str = str(risk_model) if risk_model is not None else None
        try:
            persist_llm_call(
                cycle=0,
                phase="risk_estimation",
                module="risk_estimator",
                action="estimate",
                model=risk_model_str,
                started_at=started_at,
                duration_ms=duration_ms,
                prompt=prompt,
                system_prompt=RISK_SYSTEM_PROMPT,
                raw_response=raw_response,
                parsed_summary_json=parsed_summary_json,
                attempts=attempts,
                error=error,
                token_usage_json=token_usage_json,
            )
        except Exception as e:
            _RISK_LOG.debug("persist_llm_call failed: %s", e)

    def _call_llm_with_retry(self, full_prompt: str, gen_config: Any) -> tuple[str, RiskParseResult]:
        """
        Call policy LLM with retry loop. Returns (raw_response, parsed) on first successful parse.

        Structured JSON output is requested via GenerationConfig.response_format; tolerant extract_json
        remains available inside parse_dict_with_contract for parse metadata only.

        Raises RiskEstimationError if all retries fail (parse or generation error).
        """
        assert self.policy is not None, "policy must be set before calling _call_llm_with_retry"
        raw_response = ""
        for attempt in range(self.config.max_retries):
            _tu_json: str | None = None
            try:
                start_gen = time.time()
                result = self.policy.generate(
                    prompt=full_prompt,
                    system=RISK_SYSTEM_PROMPT,
                    config=gen_config,
                )
                elapsed_ms = (time.time() - start_gen) * 1000
                raw_response = result.text if hasattr(result, "text") else str(result)
                # Capture token usage immediately after generate so it's available
                # in both the success path and any subsequent except handler.
                _tu_json = result.token_usage_json() if hasattr(result, "token_usage_json") else None
                _RISK_LOG.info(
                    "risk_estimator raw_output (troncato): %s",
                    (raw_response or "")[:200],
                    extra={"raw_snippet_len": min(200, len(raw_response or ""))},
                )
                data, p_contract = parse_dict_with_contract(raw_response, strict_json_requested=True)
                parsed = parse_risk_dict(data)
                summary = merge_parse_contract_into_summary(
                    {
                        "estimation_mode": "monolithic",
                        "retry_count": attempt,
                        "parse_attempts": attempt + 1,
                    },
                    p_contract,
                )
                self._persist_risk_llm_call(
                    started_at=int(start_gen * 1000),
                    duration_ms=elapsed_ms,
                    prompt=full_prompt,
                    raw_response=raw_response,
                    attempts=attempt + 1,
                    parsed_summary_json=summary,
                    token_usage_json=_tu_json,
                )
                return raw_response, parsed
            except JSONParseError as e:
                _RISK_LOG.warning(
                    "risk_estimator parse attempt %s/%s failed (JSONParseError): %s | raw_response (truncated): %s",
                    attempt + 1,
                    self.config.max_retries,
                    str(e),
                    (raw_response or "")[:500],
                    exc_info=True,
                )
                fc = build_failed_parse_contract(strict_json_requested=True, message=str(e))
                summary = merge_parse_contract_into_summary(
                    {
                        "estimation_mode": "monolithic",
                        "retry_count": attempt,
                        "parse_attempts": attempt + 1,
                    },
                    fc,
                )
                self._persist_risk_llm_call(
                    started_at=int(start_gen * 1000),
                    duration_ms=elapsed_ms,
                    prompt=full_prompt,
                    raw_response=raw_response,
                    attempts=attempt + 1,
                    parsed_summary_json=summary,
                    error=str(e)[:1000],
                    token_usage_json=_tu_json,
                )
                continue
            except Exception as e:
                _RISK_LOG.warning(
                    "risk_estimator parse attempt %s/%s failed: %s | raw_response (truncated): %s",
                    attempt + 1,
                    self.config.max_retries,
                    str(e),
                    (raw_response or "")[:500],
                    exc_info=True,
                )
                continue
        _RISK_LOG.warning(
            "risk_estimator fallback: parse/retry failed after %s attempts",
            self.config.max_retries,
            extra={"used_fallback_parse": True},
        )
        raise RiskEstimationError(f"Semantic analysis failed after {self.config.max_retries} attempts")

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
    ) -> None:
        """Persist a single mini-estimator LLM call. Logs debug on ImportError."""
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
        try:
            persist_llm_call(
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
            )
        except Exception as e:
            _RISK_LOG.debug("persist_mini_llm_call failed: %s", e)

    def _parallel_mini_analysis(self, prompt: str) -> RiskEstimation:
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
        signal_prompt = HARM_SIGNAL_PROMPT_TEMPLATE.format(request=prompt)
        operational_prompt = OPERATIONAL_RISK_PROMPT_TEMPLATE.format(request=prompt)

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
            fut2 = executor.submit(_call_and_track, HARM_SIGNAL_SYSTEM_PROMPT, signal_prompt, "estimate_signals")
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
        self._persist_mini_llm_call(
            system_prompt=INTENT_CONTEXT_SYSTEM_PROMPT,
            prompt=intent_prompt,
            raw_response=intent_raw,
            action="estimate_intent",
            started_at=intent_started,
            duration_ms=intent_ms,
            attempts=intent_attempts,
            parse_contract=intent_pc,
            token_usage_json=intent_tu,
            llm_model=intent_m_obs,
        )
        self._persist_mini_llm_call(
            system_prompt=HARM_SIGNAL_SYSTEM_PROMPT,
            prompt=signal_prompt,
            raw_response=signal_raw,
            action="estimate_signals",
            started_at=signal_started,
            duration_ms=signal_ms,
            attempts=signal_attempts,
            parse_contract=signal_pc,
            token_usage_json=signal_tu,
            llm_model=signal_m_obs,
        )
        self._persist_mini_llm_call(
            system_prompt=OPERATIONAL_RISK_SYSTEM_PROMPT,
            prompt=operational_prompt,
            raw_response=op_raw,
            action="estimate_operational",
            started_at=op_started,
            duration_ms=op_ms,
            attempts=op_attempts,
            parse_contract=op_pc,
            token_usage_json=op_tu,
            llm_model=op_m_obs,
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

    def _monolithic_semantic_analysis(self, prompt: str) -> RiskEstimation:
        """
        Analisi semantica con singola chiamata LLM monolitica (fallback).

        Il giudice semantico valuta:
        - Intento della richiesta
        - Potenziali conseguenze
        - Contesto etico

        Nessun keyword matching - solo comprensione del significato.
        """
        if self.policy is None:
            raise RiskEstimationError("policy not set")
        try:
            gen_config = self._build_generation_config()
            full_prompt, detected_domain = self._build_full_prompt(prompt)
            raw_response, parsed = self._call_llm_with_retry(full_prompt, gen_config)
        except RiskEstimationError as e:
            return RiskEstimation.from_error(str(e))
        except Exception as e:
            return RiskEstimation.from_error(str(e))

        _RISK_LOG.info(
            "risk_estimator [mode=monolithic] parsed: score=%.2f category=%s "
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
                "estimation_mode": "monolithic",
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
            raw_response,
            score,
            category,
            signals,
            risk_policy_action,
            intent_clarity,
            misuse_plausibility,
            actionability_risk,
            request_type_final,
            detected_domain=detected_domain,
            estimation_mode="monolithic",
        )

    def _semantic_analysis(self, prompt: str) -> RiskEstimation:
        """
        Dispatch to parallel mini-analysis or monolithic fallback based on config.
        """
        if self.config.use_parallel_estimators:
            try:
                return self._parallel_mini_analysis(prompt)
            except Exception as e:
                _RISK_LOG.warning("parallel mini-analysis failed, falling back to monolithic: %s", e)
        return self._monolithic_semantic_analysis(prompt)

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
        """
        Stima rischio con contesto aggiuntivo.

        Versione estesa di estimate() che considera:
        - Storia della conversazione
        - Contesto utente (locale, permessi, dominio)

        Args:
            prompt: Richiesta utente
            conversation_history: Lista di turni precedenti
            user_context: Metadata utente (es. {"locale": "it-IT", "domain": "medical"})

        Returns:
            RiskEstimation con analisi contestualizzata
        """
        # Il contesto viene usato per arricchire l'analisi semantica dell'LLM
        # In futuro può influenzare soglie e pesi delle diverse categorie

        # Costruisci contesto testuale
        context_parts = []

        if conversation_history:
            history_summary = []
            for turn in conversation_history[-3:]:  # Ultimi 3 turni
                role = turn.get("role", "unknown")
                content = turn.get("content", "")[:200]  # Tronca
                history_summary.append(f"{role}: {content}")
            if history_summary:
                context_parts.append("RECENT HISTORY:\n" + "\n".join(history_summary))

        if user_context:
            context_info = []
            if "domain" in user_context:
                context_info.append(f"Domain: {user_context['domain']}")
            if "locale" in user_context:
                context_info.append(f"Locale: {user_context['locale']}")
            if context_info:
                context_parts.append("CONTEXT: " + ", ".join(context_info))

        # Arricchisci prompt se c'è contesto
        if context_parts:
            enriched_prompt = "\n".join(context_parts) + f"\n\nCURRENT REQUEST: {prompt}"
        else:
            enriched_prompt = prompt

        return self.estimate(enriched_prompt)


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
            use_parallel_estimators=base.use_parallel_estimators,
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
            use_parallel_estimators=config.use_parallel_estimators,
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
