"""
LLMConstitutionalCritic - Critica costituzionale per MoralStack.

Valuta risposte contro la costituzione e produce critiche strutturate.
Usa il Policy LLM con prompt specializzato e parsing JSON rigoroso.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError as PydanticValidationError

from moralstack.constitution.prompt_formatter import format_principles_compact
from moralstack.constitution.schema import Constitution, Principle
from moralstack.constitution.store import ConstitutionStore
from moralstack.core.types import PolicyLLMProtocol, Turn, Violation
from moralstack.models.base import GenerationConfig
from moralstack.models.delib_context import DelibContext
from moralstack.observability.token_usage import TokenUsage, TokenUsageSource
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts.critic_prompt import CRITIC_FULL_SYSTEM_PROMPT
from moralstack.prompts.retry import RETRY_CRITIC
from moralstack.runtime.modules.message_context import build_module_messages
from moralstack.utils.json_utils import JSONParseError, extract_json
from moralstack.utils.structured_output import (
    CriticOutput,
    log_parser_diagnostic,
    parse_and_validate_critic_output,
)
from moralstack.utils.structured_output import (
    ValidationError as StructuredValidationError,
)

logger = logging.getLogger(__name__)


def _build_context_block(
    developer_contract: DeveloperContract | None,
    conversation_history: list[Turn] | None,
) -> str:
    """Legacy compatibility hook; context is sent as native messages instead."""
    return ""


# =============================================================================
# Data Models
# =============================================================================

# Decisione strutturata: niente parsing testuale di "REFUSE"
CriticDecision = Literal["PROCEED", "REVISE", "REFUSE"]


@dataclass
class CriticReport:
    """
    Report completo della critica costituzionale.

    Attributes:
        violations: Lista di violazioni rilevate
        severity_score: Score aggregato [0, 1]
        has_critical_violations: True se almeno un hard constraint è violato
        violated_hard: Alias esplicito per has_critical_violations (HARD non mediabili)
        decision: PROCEED | REVISE | REFUSE (output strutturato, no string parsing)
        revision_guidance: Istruzioni per migliorare la risposta (non usata per decidere)
        raw_response: Risposta grezza dell'LLM (per debug)
        parse_attempts: Numero di tentativi di parsing JSON
    """

    violations: list[Violation] = field(default_factory=list)
    severity_score: float = 0.0
    has_critical_violations: bool = False
    violated_hard: bool = False  # HARD constraints violati (non mediabili da majority vote)
    decision: CriticDecision = "PROCEED"
    revision_guidance: str = ""
    raw_response: str = ""
    parse_attempts: int = 1
    prompt: str = ""
    system_prompt: str = ""
    tokens_used: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    token_usage_source: TokenUsageSource = "unknown"
    skipped: bool = False
    """True when critique returned without invoking the LLM (e.g. no relevant principles)."""
    skip_reason: str = ""
    """Human-readable reason for skipping, used in logs and UI."""
    enumerated_output_gate_applied: bool = False
    """True when a SOFT-only REVISE was downgraded to PROCEED because the draft
    is a single enumerated answer (e.g. TRUE/FALSE). HARD violations are never
    affected. See ``moralstack.pipeline.output_contract``."""

    @classmethod
    def empty(cls) -> CriticReport:
        """Crea un report vuoto (nessuna violazione)."""
        return cls(
            violations=[],
            severity_score=0.0,
            has_critical_violations=False,
            violated_hard=False,
            decision="PROCEED",
            revision_guidance="",
        )

    @classmethod
    def empty_skipped(cls, reason: str = "") -> CriticReport:
        """
        Create an empty report that explicitly marks the critic as skipped.

        Used when the critic did not invoke the LLM (e.g. zero relevant principles).
        Distinct from empty() which represents a real LLM call that found no violations.
        """
        return cls(
            violations=[],
            severity_score=0.0,
            has_critical_violations=False,
            violated_hard=False,
            decision="PROCEED",
            revision_guidance="",
            skipped=True,
            skip_reason=reason or "no relevant principles",
        )

    @classmethod
    def from_error(cls, error_msg: str) -> CriticReport:
        """Crea un report di fallback in caso di errore critico."""
        return cls(
            violations=[],
            severity_score=1.0,  # Assume worst case
            has_critical_violations=True,
            violated_hard=True,
            decision="REFUSE",
            revision_guidance=f"Critic failed: {error_msg}. Proceed with caution.",
            raw_response=error_msg,
        )


@dataclass
class QuickCheckResult:
    """Risultato del quick check per fast path."""

    passed: bool
    critical_violation: Violation | None = None
    check_time_ms: float = 0.0


# =============================================================================
# Protocols
# =============================================================================


# =============================================================================
# Prompt Templates
# =============================================================================

CRITIC_SYSTEM_PROMPT = """You are a constitutional critic for an AI safety system.
Your task is to evaluate AI responses against ethical principles and identify violations.
Be thorough but fair. Only flag genuine violations with clear evidence.
CRITICAL DISTINCTION: You evaluate the RESPONSE content, not the REQUEST topic.
If the response contains no operational content and already declines or redirects
the request (a refusal or safe-redirect draft), it has zero violations regardless
of how sensitive the request topic is. Decision MUST be PROCEED for clean refusal drafts.
When the request warrants refusal (e.g. terrorism, weapons, critical infrastructure
targeting), also flag information leakage: responses that provide operationally useful
information—such as listing components, targets, materials, or structural elements that
could be exploited—even if framed as "general principles" or "educational content."
When you output REVISE or REFUSE, always include concrete safe alternatives in
revision_guidance using the format "suggest: [specific topics, resources, or reframing]"
so the response can be improved or redirected constructively.
Always respond with valid JSON only. No additional text or explanation outside the JSON."""

RETRY_PROMPT = RETRY_CRITIC

QUICK_CHECK_PROMPT_TEMPLATE = """Quickly check if this response violates any
HARD CONSTRAINTS (critical safety rules):

HARD CONSTRAINTS:
{hard_constraints}

REQUEST: {request}
RESPONSE: {response}

If ANY hard constraint is violated, output:
{{"violated": true, "principle_id": "...", "evidence": "..."}}

If NO hard constraint is violated, output:
{{"violated": false}}

Output ONLY valid JSON:"""


# =============================================================================
# Validazione dati violazioni (usata solo da _critic_output_to_tuple)
# =============================================================================

VIOLATION_THRESHOLD = 0.15  # Soglia sotto cui la severity indica "non violato"


def _critic_output_to_tuple(
    critic_output: CriticOutput,
    principles_map: dict[str, Principle],
) -> tuple[list[Violation], str, CriticDecision, bool]:
    """Converte CriticOutput (Pydantic) nella tupla (violations, revision_guidance,
    decision, violated_hard)."""
    violations: list[Violation] = []
    for v in critic_output.violations:
        principle = principles_map.get(v.principle_id)
        constraint_type: Literal["hard", "soft"] = principle.level if principle else "soft"
        principle_title = principle.title if principle else v.principle_id
        severity = float(v.severity)
        if severity < VIOLATION_THRESHOLD:
            continue
        if constraint_type == "hard" and severity < 0.5:
            severity = max(0.5, severity)
        violations.append(
            Violation(
                principle_id=v.principle_id,
                principle_title=principle_title,
                severity=severity,
                constraint_type=constraint_type,
                rationale=v.rationale,
                evidence=v.evidence,
            )
        )
    violated_hard = any(v.constraint_type == "hard" for v in violations)
    return (
        violations,
        critic_output.revision_guidance,
        critic_output.decision,
        violated_hard,
    )


def parse_critic_response(
    text: str,
    principles_map: dict[str, Principle],
    request_id: str = "",
    path_decision: str = "DELIBERATIVE_PATH",
) -> tuple[list[Violation], str, CriticDecision, bool]:
    """
    Parsa la risposta del critic con parser strutturato obbligatorio.

    Unica fonte di verità: CriticOutput (Pydantic). Nessun fallback legacy.
    Se il parsing fallisce si solleva JSONParseError o ValidationError e si logga
    request_id, raw_output (preview), errore. Nessuna decisione alternativa.
    """
    parsed_output_keys = list(CriticOutput.model_fields.keys())
    try:
        critic_output = parse_and_validate_critic_output(text)
        result = _critic_output_to_tuple(critic_output, principles_map)
        log_parser_diagnostic(
            request_id=request_id or "unknown",
            parser_status="OK",
            raw_output_keys=list(critic_output.model_dump().keys()),
            parsed_output_keys=parsed_output_keys,
            final_action=result[2],
            path_decision=(path_decision if path_decision in ("FAST_PATH", "DELIBERATIVE_PATH") else "DELIBERATIVE_PATH"),
            risk_level=None,
        )
        return result
    except (JSONParseError, StructuredValidationError, PydanticValidationError) as e:
        logger.error(
            "Critic structured parsing failed request_id=%s error=%s raw_preview=%s",
            request_id or "unknown",
            str(e),
            text[:500] if len(text) > 500 else text,
            extra={
                "moralstack_parser_diagnostic": True,
                "request_id": request_id or "unknown",
                "parser_status": "ERROR",
                "module_name": "critic",
                "raw_output_keys": [],
                "parsed_output_keys": parsed_output_keys,
            },
        )
        log_parser_diagnostic(
            request_id=request_id or "unknown",
            parser_status="ERROR",
            raw_output_keys=[],
            parsed_output_keys=parsed_output_keys,
            final_action="PROCEED",
            path_decision=(path_decision if path_decision in ("FAST_PATH", "DELIBERATIVE_PATH") else "DELIBERATIVE_PATH"),
            risk_level=None,
        )
        raise


# =============================================================================
# LLM Constitutional Critic
# =============================================================================


@dataclass
class CriticConfig:
    """Configurazione per il Constitutional Critic."""

    max_retries: int = 2
    max_tokens: int = 384  # Ridotto drasticamente per velocità
    temperature: float = 0.1  # Più basso per determinismo
    top_p: float = 0.9  # Nucleus sampling; configurable via MORALSTACK_CRITIC_TOP_P
    top_k_principles: int = 20  # Ridotto per ridurre dimensione prompt
    include_examples: bool = False  # Disabilitato per velocizzare
    # Chars of each principle's rule the critic actually reads. Rules longer
    # than this are cut mid-sentence, so a clause that sits past the window
    # (typically a carve-out, which English drafting puts last) never reaches
    # the judgement. Longest rule in the shipped constitution is 492 chars.
    # Default kept at the historical 180; raise via MORALSTACK_CRITIC_MAX_RULE_LEN.
    max_rule_len: int = 180


class LLMConstitutionalCritic:
    """
    Critic costituzionale basato su LLM.

    Valuta risposte contro la costituzione e produce critiche strutturate.
    Usa il Policy LLM con prompt specializzato e parsing JSON rigoroso
    con retry automatico.

    Attributes:
        policy: Il Policy LLM per generazione
        store: Constitution Store per accesso ai principi
        config: Configurazione del critic

    Usage:
        critic = LLMConstitutionalCritic(policy, store)
        report = critic.critique(request, response, constitution)

        if report.has_critical_violations:
            # Handle critical violation
            pass
    """

    def __init__(
        self,
        policy: PolicyLLMProtocol,
        store: ConstitutionStore | None = None,
        config: CriticConfig | None = None,
    ) -> None:
        """
        Inizializza il Constitutional Critic.

        Args:
            policy: Policy LLM per generazione critica
            store: Constitution Store (opzionale, per lookup principi)
            config: Configurazione (opzionale)
        """
        self.policy = policy
        self.store = store
        if config is not None:
            self.config = config
        else:
            from moralstack.runtime.modules.critic_config_loader import load_critic_config_from_env

            self.config = load_critic_config_from_env()

        self._generation_config = GenerationConfig(
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            stop_sequences=[],
            response_format={"type": "json_object"},
        )

    def critique(
        self,
        request: str,
        response: str,
        constitution: Constitution,
        principles: list[Principle] | None = None,
        request_id: str = "",
        delib_context: Any = None,
        previous_violations: str = "",
        previous_guidance: str = "",
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> CriticReport:
        """
        Valuta response contro constitution.

        Args:
            request: Richiesta originale dell'utente
            response: Risposta da valutare
            constitution: Costituzione con principi
            principles: Principi specifici da usare (override)
            request_id: Identificatore richiesta per tracciabilità log parser

        Returns:
            CriticReport con violazioni e guidance
        """
        # Determina principi da usare
        if principles is not None:
            active_principles = principles
        else:
            active_principles = constitution.principles[: self.config.top_k_principles]

        if not active_principles:
            return CriticReport.empty_skipped(reason="no relevant principles retrieved from constitution")

        # Costruisci mappa per lookup
        principles_map = {p.id: p for p in active_principles}

        # Formatta principi per prompt (formato compatto per ridurre token)
        principles_text = format_principles_compact(active_principles, max_rule_len=self.config.max_rule_len)

        # Costruisci prompt: usa builder centralizzato (moralstack/prompts/)
        ctx = delib_context or DelibContext(user_prompt=request, draft_text_full=response)

        # Tier-1: deterministically detect a single enumerated-token output
        # contract (e.g. answer exactly 'TRUE'/'FALSE') from the declared
        # constraints + the draft. Used below to downgrade SOFT-only REVISE to
        # PROCEED (never affects HARD violations). Best-effort; never raises.
        try:
            from moralstack.pipeline.output_contract import detect_enumerated_output

            _contract_text = getattr(developer_contract, "raw_text", "") or ctx.developer_contract_text or ""
            _declared = f"{_contract_text}\n{request}"
            is_enum, enum_opts = detect_enumerated_output(_declared, response)
            ctx.output_is_enumerated = is_enum
            ctx.output_enumerated_options = enum_opts
        except Exception:
            ctx.output_is_enumerated = False
            ctx.output_enumerated_options = ()

        from moralstack.prompts.critic_prompt import build_critic_prompt

        prompt = build_critic_prompt(
            ctx,
            principles_text,
            previous_violations=previous_violations,
            previous_guidance=previous_guidance,
        )
        context_block = _build_context_block(developer_contract, conversation_history)
        legacy_prompt = prompt + context_block

        # Genera critica con retry
        raw_response = ""
        parse_attempts = 0
        last_error: Exception | None = None
        last_error_str = ""

        for attempt in range(self.config.max_retries):
            parse_attempts = attempt + 1
            attempt_token_usage: TokenUsage | None = None

            try:
                effective_prompt = prompt if attempt == 0 else f"{prompt}\n\n{RETRY_PROMPT}"
                if hasattr(self.policy, "generate_messages"):
                    result = self.policy.generate_messages(
                        messages=build_module_messages(
                            system_prompt=CRITIC_FULL_SYSTEM_PROMPT,
                            user_prompt=prompt,
                            developer_contract=developer_contract,
                            conversation_history=conversation_history,
                            retry_prompt="" if attempt == 0 else RETRY_PROMPT,
                        ),
                        config=self._generation_config,
                    )
                elif attempt == 0:
                    result = self.policy.generate(
                        prompt=legacy_prompt,
                        system=CRITIC_FULL_SYSTEM_PROMPT,
                        config=self._generation_config,
                    )
                else:
                    result = self.policy.generate(
                        prompt=f"{legacy_prompt}\n\n{RETRY_PROMPT}",
                        system=CRITIC_FULL_SYSTEM_PROMPT,
                        config=self._generation_config,
                    )

                raw_response = result.text
                attempt_token_usage = TokenUsage.from_generation_result(result)

                # Parse JSON (output strutturato: decision + violated_hard)
                violations, guidance, decision, violated_hard = parse_critic_response(
                    raw_response,
                    principles_map,
                    request_id=request_id,
                    path_decision="DELIBERATIVE_PATH",
                )

                severity_score = self._compute_severity_score(violations)
                has_critical = violated_hard

                # Tier-1 enumerated-output gate: a SOFT-only REVISE on a single
                # enumerated answer (e.g. TRUE/FALSE) can only flip the selected
                # option, corrupting the factual answer. There is nothing
                # actionable to revise, so clear the SOFT critic output
                # (decision -> PROCEED, no violations/guidance). HARD violations
                # are never affected (violated_hard guard). The convergence
                # evaluator votes on ``violations`` presence, so the violations
                # MUST be cleared here for the gate to take effect; the original
                # signals are preserved in the emitted diagnostic for audit.
                enumerated_gate_applied = False
                if decision == "REVISE" and not violated_hard and getattr(ctx, "output_is_enumerated", False):
                    self._emit_enumerated_gate(
                        request_id=request_id,
                        options=getattr(ctx, "output_enumerated_options", ()),
                        draft=response,
                        violations=violations,
                    )
                    decision = "PROCEED"
                    violations = []
                    guidance = ""
                    severity_score = 0.0
                    enumerated_gate_applied = True

                return CriticReport(
                    violations=violations,
                    severity_score=severity_score,
                    has_critical_violations=has_critical,
                    violated_hard=violated_hard,
                    decision=decision,
                    revision_guidance=guidance,
                    raw_response=raw_response,
                    parse_attempts=parse_attempts,
                    prompt=effective_prompt,
                    system_prompt=CRITIC_FULL_SYSTEM_PROMPT,
                    tokens_used=int(getattr(result, "tokens_used", 0) or 0),
                    prompt_tokens=getattr(result, "prompt_tokens", None),
                    completion_tokens=getattr(result, "completion_tokens", None),
                    cached_prompt_tokens=attempt_token_usage.cached_input_tokens,
                    token_usage_source=attempt_token_usage.source,
                    enumerated_output_gate_applied=enumerated_gate_applied,
                )

            except (JSONParseError, StructuredValidationError, PydanticValidationError) as e:
                last_error = e
                last_error_str = str(e) if e else ""
                if attempt_token_usage is not None:
                    try:
                        from moralstack.observability.emit_helpers import async_persist_llm_call

                        async_persist_llm_call(
                            phase="critic_retry",
                            module="critic",
                            action=f"retry_failed_attempt_{parse_attempts}",
                            model=getattr(self.policy, "model", None) or "",
                            prompt=f"Retry reason: {str(e)[:200]}",
                            raw_response=raw_response or "",
                            duration_ms=0.0,
                            attempts=parse_attempts,
                            call_outcome="retry_failed",
                            billable_provider_call=True,
                            token_usage_json=attempt_token_usage.to_json(),
                        )
                    except Exception:
                        logger.debug("persist critic retry-failed llm call failed", exc_info=True)
                if attempt == self.config.max_retries - 1:
                    logger.error(
                        "Critic parse failed after all retries request_id=%s attempts=%s",
                        request_id or "unknown",
                        parse_attempts,
                        extra={"raw_preview": raw_response[:500] if raw_response else ""},
                    )
                continue
            except Exception as e:
                last_error = e
                last_error_str = str(e) if e else ""
                if attempt == self.config.max_retries - 1:
                    logger.error(
                        "Critic unexpected error after retries request_id=%s",
                        request_id or "unknown",
                        exc_info=True,
                    )
                continue

        # Tutti i retry falliti: errore esplicito, nessun fallback decisionale
        error_msg = f"Critic structured parsing failed after {parse_attempts} attempts: {last_error_str}"
        if last_error is not None and isinstance(
            last_error, (JSONParseError, StructuredValidationError, PydanticValidationError)
        ):
            raise last_error
        raise RuntimeError(error_msg) from last_error

    def _emit_enumerated_gate(
        self,
        *,
        request_id: str,
        options: tuple[str, ...],
        draft: str,
        violations: list[Violation],
    ) -> None:
        """Persist a diagnostic when the enumerated-output gate downgrades a
        SOFT-only REVISE to PROCEED.

        Best-effort observability only (file ``debug.event.jsonl`` + SQLite
        ``debug_events`` + UI "Debug Events"); never affects the decision and
        never raises.
        """
        try:
            from moralstack.orchestration.diagnostics import orch_debug_log

            orch_debug_log(
                "critic_module.py:enumerated_gate",
                "enumerated_output_gate_applied",
                {
                    "original_decision": "REVISE",
                    "downgraded_to": "PROCEED",
                    "enumerated_options": list(options),
                    "draft": (draft or "")[:64],
                    "soft_violations": [getattr(v, "principle_id", "") for v in (violations or [])][:8],
                    "reason": "SOFT-only revision on single enumerated answer suppressed (would flip option)",
                },
                hypothesis_id="H-enumerated-output-gate",
                request_id=request_id or "",
                component="critic",
                event_type="governance.enumerated_output_gate",
            )
            logger.info(
                "enumerated_output_gate applied request_id=%s options=%s",
                request_id or "",
                ",".join(options),
            )
        except Exception:
            logger.debug("enumerated_output_gate emit failed (non-fatal)", exc_info=True)

    def quick_check(
        self,
        request: str,
        response: str,
        constitution: Constitution,
        pre_retrieved_principles: list[Principle] | None = None,
    ) -> QuickCheckResult:
        """
        Check veloce per fast path.

        Controlla solo hard constraints per decisione rapida.

        Args:
            request: Richiesta originale
            response: Risposta da verificare
            constitution: Costituzione
            pre_retrieved_principles: Optional shared retrieval result from the
                risk-owned single upstream wave (unified single-retrieval-per-request).
                When supplied, filtered to HARD instead of self-retrieving (single
                retrieval per request across all routes). When not supplied
                (degraded / no risk-owned context), self-retrieves as before
                (fail-safe fallback).

        Returns:
            QuickCheckResult con pass/fail
        """
        import time

        start = time.perf_counter()

        # Usa principi rilevanti se disponibili, altrimenti top hard constraints
        hard_constraints = []
        if pre_retrieved_principles is not None:
            # Reuse the risk-owned retrieval (already executed once upstream);
            # filter to HARD only — no store call here.
            hard_constraints = [p for p in pre_retrieved_principles if p.level == "hard"][:10]
        elif self.store is not None:
            try:
                # Fail-safe fallback: no shared context supplied — self-retrieve
                # (solo HARD) per questa query.
                relevant = self.store.get_relevant_principles(query=request, top_k=10, domain=None)
                # Filtra solo hard constraints rilevanti
                hard_constraints = [p for p in relevant if p.level == "hard"][:10]
            except Exception:
                pass

        # Fallback: usa top hard constraints per priorità (never skip the check,
        # even when the shared/self-retrieved list has zero HARD principles).
        if not hard_constraints:
            hard_constraints = [p for p in constitution.principles if p.level == "hard"][
                :10
            ]  # Aumentato da 5 a 10 per maggiore copertura

        if not hard_constraints:
            return QuickCheckResult(passed=True)

        # Formatta hard constraints (formato compatto per ridurre token)
        constraints_text = format_principles_compact(hard_constraints, max_rule_len=self.config.max_rule_len)

        prompt = QUICK_CHECK_PROMPT_TEMPLATE.format(
            hard_constraints=constraints_text,
            request=request,
            response=response,
        )

        # Genera con config veloce (usa top_p da config)
        quick_config = GenerationConfig(
            max_tokens=256,
            temperature=0.1,
            top_p=self.config.top_p,
            response_format={"type": "json_object"},
        )

        try:
            result = self.policy.generate(
                prompt=prompt,
                system=CRITIC_SYSTEM_PROMPT,
                config=quick_config,
            )

            data = extract_json(result.text)
            violated = data.get("violated", False)

            elapsed_ms = (time.perf_counter() - start) * 1000

            if violated:
                principle_id = str(data.get("principle_id", "UNKNOWN"))
                evidence = str(data.get("evidence", ""))

                # Trova il principio
                principle = next((p for p in hard_constraints if p.id == principle_id), None)

                violation = Violation(
                    principle_id=principle_id,
                    principle_title=principle.title if principle else principle_id,
                    severity=1.0,  # Hard constraint = max severity
                    constraint_type="hard",
                    rationale="Quick check detected hard constraint violation",
                    evidence=evidence,
                )

                return QuickCheckResult(
                    passed=False,
                    critical_violation=violation,
                    check_time_ms=elapsed_ms,
                )

            return QuickCheckResult(
                passed=True,
                check_time_ms=elapsed_ms,
            )

        except (JSONParseError, Exception):
            # In caso di errore, fallback a full critique
            elapsed_ms = (time.perf_counter() - start) * 1000
            return QuickCheckResult(
                passed=True,  # Assume pass, full critique farà il lavoro
                check_time_ms=elapsed_ms,
            )

    def critique_with_relevant_principles(
        self,
        request: str,
        response: str,
        domain: str | None = None,
        request_id: str = "",
        delib_context: Any = None,
        previous_violations: str = "",
        previous_guidance: str = "",
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> CriticReport:
        """
        Critica usando principi rilevanti per la richiesta.

        Convenience method che usa lo store per semantic retrieval.

        Args:
            request: Richiesta originale
            response: Risposta da valutare
            domain: Dominio opzionale per overlay

        Returns:
            CriticReport
            :param response:
            :param request:
            :param domain:
            :param previous_guidance:
            :param previous_violations:
            :param delib_context:
            :param request_id:
        """
        if self.store is None:
            raise ValueError("ConstitutionStore required for this method")

        # Ottieni principi rilevanti
        principles = self.store.get_relevant_principles(
            query=request,
            top_k=self.config.top_k_principles,
            domain=domain,
        )

        constitution = self.store.get_constitution(domain)

        return self.critique(
            request=request,
            response=response,
            constitution=constitution,
            principles=principles,
            request_id=request_id,
            delib_context=delib_context,
            previous_violations=previous_violations,
            previous_guidance=previous_guidance,
            developer_contract=developer_contract,
            conversation_history=conversation_history,
        )

    def _compute_severity_score(self, violations: list[Violation]) -> float:
        """
        Calcola severity score aggregato.

        Formula: weighted average con boost per hard constraints.
        """
        if not violations:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for v in violations:
            # Hard constraints pesano di più
            weight = 2.0 if v.constraint_type == "hard" else 1.0
            weighted_sum += v.severity * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        # Normalizza e clamp
        score = weighted_sum / total_weight
        return max(0.0, min(1.0, score))


# =============================================================================
# Factory Functions
# =============================================================================


def create_critic(
    policy: PolicyLLMProtocol,
    store: ConstitutionStore | None = None,
    config: CriticConfig | None = None,
) -> LLMConstitutionalCritic:
    """
    Factory function for creating a Constitutional Critic.

    Args:
        policy: Policy LLM to use.
        store: Constitution Store (optional).
        config: CriticConfig (optional). When None, config is loaded from
                environment variables via load_critic_config_from_env().

    Returns:
        Configured LLMConstitutionalCritic.
    """
    return LLMConstitutionalCritic(
        policy=policy,
        store=store,
        config=config,
    )
