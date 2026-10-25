"""
LLMConstitutionalCritic - Constitutional critic for MoralStack.

Evaluates responses against the constitution and produces structured critiques.
Uses the Policy LLM with a specialized prompt and strict JSON parsing.
"""

from __future__ import annotations

import json
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
from moralstack.observability.wire import (
    generate_messages_for,
    generate_wire_summary,
    inline_wire_summary,
    safe_encode_wire,
)
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts.catalog import PromptContext, PromptSet, default_prompt_set
from moralstack.prompts.critic_prompt import CRITIC_FULL_SYSTEM_PROMPT  # noqa: F401 - default re-export, kept importable
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

# Structured decision: no textual parsing of "REFUSE"
CriticDecision = Literal["PROCEED", "REVISE", "REFUSE"]


@dataclass
class CriticReport:
    """
    Complete report of the constitutional critique.

    Attributes:
        violations: List of detected violations
        severity_score: Aggregated score [0, 1]
        has_critical_violations: True if at least one hard constraint is violated
        violated_hard: Explicit alias for has_critical_violations (HARD, non-negotiable)
        decision: PROCEED | REVISE | REFUSE (structured output, no string parsing)
        revision_guidance: Instructions to improve the response (not used to decide)
        raw_response: Raw LLM response (for debug)
        parse_attempts: Number of JSON parsing attempts
    """

    violations: list[Violation] = field(default_factory=list)
    severity_score: float = 0.0
    has_critical_violations: bool = False
    violated_hard: bool = False  # HARD constraints violated (not negotiable via majority vote)
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
    wire: dict[str, Any] | None = None
    """Exact messages sent by the call that produced this report (wire record v1, ``observability/wire.py``).
    Additive audit data; ``None`` when no model call was made or the record could not be built."""
    enumerated_output_gate_applied: bool = False
    """True when a SOFT-only REVISE was downgraded to PROCEED because the draft
    is a single enumerated answer (e.g. TRUE/FALSE). HARD violations are never
    affected. See ``moralstack.pipeline.output_contract``."""

    @classmethod
    def empty(cls) -> CriticReport:
        """Creates an empty report (no violations)."""
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
        """Creates a fallback report on critical error."""
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
    """Result of the quick check for the fast path."""

    passed: bool
    critical_violation: Violation | None = None
    check_time_ms: float = 0.0


class NoHardPrinciplesError(Exception):
    """quick_check found no HARD principle to check against (shared list,
    self-retrieval and constitution fallback all empty): no verdict, never a pass."""


# =============================================================================
# Protocols
# =============================================================================


# =============================================================================
# Prompt Templates
# =============================================================================

# Default re-exports of the packaged fragments (byte-identical to the former literals). Runtime code reads
# the module's own ``PromptSet`` (``self._prompt_set``), never these constants.
CRITIC_SYSTEM_PROMPT = default_prompt_set().text("critic.quick.system")

RETRY_PROMPT = RETRY_CRITIC

QUICK_CHECK_PROMPT_TEMPLATE = default_prompt_set().template("critic.quick.user")


# =============================================================================
# Violation data validation (used only by _critic_output_to_tuple)
# =============================================================================

VIOLATION_THRESHOLD = 0.15  # Threshold below which severity indicates "not violated"


def _critic_output_to_tuple(
    critic_output: CriticOutput,
    principles_map: dict[str, Principle],
) -> tuple[list[Violation], str, CriticDecision, bool]:
    """Converts CriticOutput (Pydantic) into the tuple (violations, revision_guidance,
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
    Parses the critic response with the mandatory structured parser.

    Single source of truth: CriticOutput (Pydantic). No legacy fallback.
    If parsing fails, JSONParseError or ValidationError is raised and the following is logged:
    request_id, raw_output (preview), error. No alternative decision.
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
    """Configuration for the Constitutional Critic."""

    max_retries: int = 2
    max_tokens: int = 384  # Drastically reduced for speed
    temperature: float = 0.1  # Lower for determinism
    top_p: float = 0.9  # Nucleus sampling; configurable via MORALSTACK_CRITIC_TOP_P
    top_k_principles: int = 20  # Reduced to shrink prompt size
    # Chars of each principle's rule the critic actually reads. Rules longer
    # than this are cut mid-sentence, so a clause that sits past the window
    # (typically a carve-out, which English drafting puts last) never reaches
    # the judgement. Longest rule in the shipped constitution is 492 chars, so
    # the default 512 reads every shipped rule in full; lower it via
    # MORALSTACK_CRITIC_MAX_RULE_LEN (180 = the historical window).
    max_rule_len: int = 512


class LLMConstitutionalCritic:
    """
    LLM-based constitutional critic.

    Evaluates responses against the constitution and produces structured critiques.
    Uses the Policy LLM with a specialized prompt and strict JSON parsing
    with automatic retry.

    Attributes:
        policy: The Policy LLM used for generation
        store: Constitution Store for access to the principles
        config: Critic configuration

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
        prompt_set: PromptSet | None = None,
    ) -> None:
        """
        Initializes the Constitutional Critic.

        Args:
            policy: Policy LLM for critique generation
            store: Constitution Store (optional, for principle lookup)
            config: Configuration (optional)
            prompt_set: PromptSet to read prompts and retries from (default: packaged set)
        """
        self.policy = policy
        self.store = store
        self._prompt_set = prompt_set or default_prompt_set()
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
        Evaluates response against constitution.

        Args:
            request: Original user request
            response: Response to evaluate
            constitution: Constitution with principles
            principles: Specific principles to use (override)
            request_id: Request identifier for parser log traceability

        Returns:
            CriticReport with violations and guidance
        """
        # Determine the principles to use
        if principles is not None:
            active_principles = principles
        else:
            active_principles = constitution.principles[: self.config.top_k_principles]

        if not active_principles:
            return CriticReport.empty_skipped(reason="no relevant principles retrieved from constitution")

        # Build map for lookup
        principles_map = {p.id: p for p in active_principles}

        # Format principles for prompt (compact format to reduce tokens)
        principles_text = format_principles_compact(active_principles, max_rule_len=self.config.max_rule_len)

        # Build prompt: use the centralized builder (moralstack/prompts/)
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
            prompt_set=self._prompt_set,
        )
        context_block = _build_context_block(developer_contract, conversation_history)
        legacy_prompt = prompt + context_block

        # Per-call system prompt: ``when`` conditions are evaluated against what this call carries. The default
        # set renders identically for every context. The rendered string is also what gets persisted.
        full_system_prompt = self._prompt_set.render(
            "critic.full.system", PromptContext.from_contract(developer_contract, conversation_history)
        )
        retry_prompt = self._prompt_set.text("critic.retry")

        # Generate critique with retry
        raw_response = ""
        parse_attempts = 0
        last_error: Exception | None = None
        last_error_str = ""

        for attempt in range(self.config.max_retries):
            parse_attempts = attempt + 1
            attempt_token_usage: TokenUsage | None = None
            sent: tuple[list[dict[str, str]], str] | None = None

            try:
                effective_prompt = prompt if attempt == 0 else f"{prompt}\n\n{retry_prompt}"
                if hasattr(self.policy, "generate_messages"):
                    messages = build_module_messages(
                        system_prompt=full_system_prompt,
                        user_prompt=prompt,
                        developer_contract=developer_contract,
                        conversation_history=conversation_history,
                        retry_prompt="" if attempt == 0 else retry_prompt,
                        prompt_set=self._prompt_set,
                    )
                    sent = (messages, "messages")
                    result = self.policy.generate_messages(
                        messages=messages,
                        config=self._generation_config,
                    )
                else:
                    legacy_user = legacy_prompt if attempt == 0 else f"{legacy_prompt}\n\n{retry_prompt}"
                    sent = (generate_messages_for(legacy_user, full_system_prompt), "generate")
                    result = self.policy.generate(
                        prompt=legacy_user,
                        system=full_system_prompt,
                        config=self._generation_config,
                    )

                raw_response = result.text
                attempt_token_usage = TokenUsage.from_generation_result(result)

                # Parse JSON (structured output: decision + violated_hard)
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
                    system_prompt=full_system_prompt,
                    tokens_used=int(getattr(result, "tokens_used", 0) or 0),
                    prompt_tokens=getattr(result, "prompt_tokens", None),
                    completion_tokens=getattr(result, "completion_tokens", None),
                    cached_prompt_tokens=attempt_token_usage.cached_input_tokens,
                    token_usage_source=attempt_token_usage.source,
                    wire=safe_encode_wire(
                        sent[0], prompt_col=effective_prompt, system_prompt_col=full_system_prompt, branch=sent[1]
                    ),
                    enumerated_output_gate_applied=enumerated_gate_applied,
                )

            except (JSONParseError, StructuredValidationError, PydanticValidationError) as e:
                last_error = e
                last_error_str = str(e) if e else ""
                if attempt_token_usage is not None:
                    try:
                        from moralstack.observability.emit_helpers import async_persist_llm_call

                        retry_reason = f"Retry reason: {str(e)[:200]}"
                        inline_wire = inline_wire_summary(sent, retry_reason)
                        async_persist_llm_call(
                            phase="critic_retry",
                            module="critic",
                            action=f"retry_failed_attempt_{parse_attempts}",
                            model=getattr(self.policy, "model", None) or "",
                            prompt=retry_reason,
                            parsed_summary_json=json.dumps(inline_wire) if inline_wire else None,
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

        # All retries failed: explicit error, no decision fallback
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

    def _persist_quick_check(
        self,
        result: Any,
        *,
        prompt: str,
        system_prompt: str,
        started_at_ms: int,
        duration_ms: float,
        summary: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Persist the fast-path quick-check call as an ``llm_calls`` row (cycle 0, after the fast draft).

        The row carries what was sent (``prompt`` / ``system_prompt``) and what the provider billed. Best-effort
        observability: it never raises and never changes the verdict (PROJECT_SPEC invariant 6).
        """
        try:
            import json

            from moralstack.observability.emit_helpers import async_persist_llm_call

            error_text: str | None = None
            if error is not None:
                # Built here, inside the swallowing helper: formatting the error must never raise into the request.
                error_text = str(error)[:200]
                summary = {"passed": None, "error": error_text, "error_type": type(error).__name__}

            # The summary is a JSON object, so the wire goes in it (unlike the full critique row). quick_check calls
            # ``policy.generate(prompt=prompt, system=system_prompt)``; the record never raises.
            summary = {
                **(summary or {}),
                **generate_wire_summary(prompt, system_prompt),
            }

            async_persist_llm_call(
                phase="critic",
                module="critic",
                action="quick_check",
                model=getattr(self.policy, "model", None) or "",
                started_at=started_at_ms,
                duration_ms=duration_ms,
                prompt=prompt,
                system_prompt=system_prompt,
                raw_response=getattr(result, "text", "") or "",
                parsed_summary_json=json.dumps(summary or {}, ensure_ascii=False),
                token_usage_json=TokenUsage.from_generation_result(result).to_json(),
                attempts=1,
                error=error_text,
                # The fast path runs in cycle 0 (no deliberation cycle is active); seq 2 = SEQ_CRITIC, i.e. right
                # after the fast draft (SEQ_POLICY=1) in the same cycle.
                cycle=0,
                sequence_in_cycle=2,
                billable_provider_call=True,
            )
        except Exception:
            logger.debug("persist critic quick_check llm call failed", exc_info=True)

    def quick_check(
        self,
        request: str,
        response: str,
        constitution: Constitution,
        pre_retrieved_principles: list[Principle] | None = None,
    ) -> QuickCheckResult:
        """
        Quick check for the fast path.

        Checks only hard constraints for a quick decision.

        Args:
            request: Original request
            response: Response to verify
            constitution: Constitution
            pre_retrieved_principles: Optional shared retrieval result from the
                risk-owned single upstream wave (unified single-retrieval-per-request).
                When supplied, filtered to HARD instead of self-retrieving (single
                retrieval per request across all routes). When not supplied
                (degraded / no risk-owned context), self-retrieves as before
                (fail-safe fallback).

        Returns:
            QuickCheckResult with pass/fail

        Raises:
            JSONParseError: the reply is unparseable, is not a JSON object, or has
                no ``violated`` key (no verdict).
            Exception: any provider/transport error from ``policy.generate``
                propagates unchanged. The caller must treat every raise as "no
                verdict", never as a pass (``passed=True`` is returned only after
                a parsed, falsy ``violated``).
            NoHardPrinciplesError: no HARD principle to check against (shared list,
                self-retrieval and constitution fallback all without HARD). No LLM
                call is made; this is "no verdict", never a pass.
        """
        import time

        start = time.perf_counter()

        # Use relevant principles if available, otherwise top hard constraints
        hard_constraints = []
        if pre_retrieved_principles is not None:
            # Reuse the risk-owned retrieval (already executed once upstream);
            # filter to HARD only — no store call here.
            hard_constraints = [p for p in pre_retrieved_principles if p.level == "hard"][:10]
        elif self.store is not None:
            try:
                # Fail-safe fallback: no shared context supplied — self-retrieve
                # (HARD only) for this query.
                relevant = self.store.get_relevant_principles(query=request, top_k=10, domain=None)
                # Filter only relevant hard constraints
                hard_constraints = [p for p in relevant if p.level == "hard"][:10]
            except Exception as e:
                logger.warning(
                    "quick_check self-retrieval failed error_type=%s; falling back to the constitution HARD principles",
                    type(e).__name__,
                    exc_info=e,
                )

        # Fallback: use top hard constraints by priority (never skip the check,
        # even when the shared/self-retrieved list has zero HARD principles).
        if not hard_constraints:
            hard_constraints = [p for p in constitution.principles if p.level == "hard"][
                :10
            ]  # Increased from 5 to 10 for wider coverage

        if not hard_constraints:
            raise NoHardPrinciplesError("quick_check found no HARD principle to check against")

        # Format hard constraints (compact format to reduce tokens)
        constraints_text = format_principles_compact(hard_constraints, max_rule_len=self.config.max_rule_len)

        prompt = self._prompt_set.format(
            "critic.quick.user",
            hard_constraints=constraints_text,
            request=request,
            response=response,
        )

        # Generate with fast config (uses top_p from config)
        quick_config = GenerationConfig(
            max_tokens=256,
            temperature=0.1,
            top_p=self.config.top_p,
            response_format={"type": "json_object"},
        )

        # No try/except: a provider/transport error, an unparseable reply, a non-dict
        # reply or a reply without the ``violated`` key is "no verdict" and must
        # propagate. Swallowing it as ``passed=True`` would fail OPEN on the fast
        # path, where no full critique runs afterwards.
        quick_system = self._prompt_set.text("critic.quick.system")
        started_at_ms = int(time.time() * 1000)
        result = self.policy.generate(
            prompt=prompt,
            system=quick_system,
            config=quick_config,
        )

        try:
            data = extract_json(result.text)
            if not isinstance(data, dict) or "violated" not in data:
                raise JSONParseError("quick_check reply carries no 'violated' verdict")
        except Exception as e:
            # The call was paid for but yields no verdict: record it, then propagate unchanged (fail closed).
            self._persist_quick_check(
                result,
                prompt=prompt,
                system_prompt=quick_system,
                started_at_ms=started_at_ms,
                duration_ms=(time.perf_counter() - start) * 1000,
                error=e,
            )
            raise
        violated = data["violated"]

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._persist_quick_check(
            result,
            prompt=prompt,
            system_prompt=quick_system,
            started_at_ms=started_at_ms,
            duration_ms=elapsed_ms,
            summary={
                "passed": not violated,
                "violated": bool(violated),
                "principle_id": str(data.get("principle_id", "")) if violated else "",
            },
        )

        if violated:
            principle_id = str(data.get("principle_id", "UNKNOWN"))
            evidence = str(data.get("evidence", ""))

            # Find the principle
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
        Critiques using the principles relevant to the request.

        Convenience method that uses the store for semantic retrieval.

        Args:
            request: Original request
            response: Response to evaluate
            domain: Optional domain for overlay

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

        # Get relevant principles
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
        Computes the aggregated severity score.

        Formula: weighted average with a boost for hard constraints.
        """
        if not violations:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for v in violations:
            # Hard constraints weigh more
            weight = 2.0 if v.constraint_type == "hard" else 1.0
            weighted_sum += v.severity * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        # Normalize and clamp
        score = weighted_sum / total_weight
        return max(0.0, min(1.0, score))


# =============================================================================
# Factory Functions
# =============================================================================


def create_critic(
    policy: PolicyLLMProtocol,
    store: ConstitutionStore | None = None,
    config: CriticConfig | None = None,
    prompt_set: PromptSet | None = None,
) -> LLMConstitutionalCritic:
    """
    Factory function for creating a Constitutional Critic.

    Args:
        policy: Policy LLM to use.
        store: Constitution Store (optional).
        config: CriticConfig (optional). When None, config is loaded from
                environment variables via load_critic_config_from_env().
        prompt_set: PromptSet to read prompts from (optional; default: packaged set).

    Returns:
        Configured LLMConstitutionalCritic.
    """
    return LLMConstitutionalCritic(
        policy=policy,
        store=store,
        config=config,
        prompt_set=prompt_set,
    )
