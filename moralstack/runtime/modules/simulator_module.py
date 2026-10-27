"""
LLMConsequenceSimulator - Consequence simulator for MoralStack.

Generates plausible future scenarios to evaluate the impact of the responses.
Uses the Policy LLM with a specialized prompt and strict JSON parsing with retry.

FIX PERFORMANCE: Added caching to avoid recomputation on identical inputs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, cast

from moralstack.core.types import PolicyLLMProtocol, Turn
from moralstack.models.base import GenerationConfig
from moralstack.models.delib_context import DelibContext
from moralstack.observability.token_usage import TokenUsage, TokenUsageSource
from moralstack.observability.wire import (
    generate_messages_for,
    inline_wire_summary,
    safe_encode_wire,
)
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts.catalog import PromptContext, PromptSet, default_prompt_set
from moralstack.prompts.retry import RETRY_SIMULATOR
from moralstack.prompts.simulator_prompt import SIMULATOR_BATCH_SYSTEM_PROMPT  # noqa: F401 - default re-export
from moralstack.runtime.modules.message_context import build_module_messages
from moralstack.utils.cache import build_context_fingerprint, get_global_cache
from moralstack.utils.json_utils import JSONParseError
from moralstack.utils.structured_output import (
    SimulatorOutput,
    parse_and_validate_simulator_output,
    simulator_response_format,
)


def _build_context_block(
    developer_contract: DeveloperContract | None,
    conversation_history: list[Turn] | None,
) -> str:
    """Legacy compatibility hook; context is sent as native messages instead."""
    return ""


# =============================================================================
# Data Models
# =============================================================================


class ScenarioType(Enum):
    """Types of consequence scenarios."""

    IMMEDIATE_HARM = "immediate_harm"
    DOWNSTREAM_MISUSE = "downstream_misuse"
    SOCIAL_IMPACT = "social_impact"
    LEGAL_CONSEQUENCE = "legal_consequence"
    POSITIVE_OUTCOME = "positive_outcome"


@dataclass
class Consequence:
    """
    Represents a single simulated consequence.

    Attributes:
        text: Narrative description of the consequence (max 160 chars)
        likelihood: Probability/plausibility [0, 1]
        scenario_id: Unique identifier
        scenario_type: Category of the consequence
        outcome_valence: Valence [-1, 1] (negative/positive)
        affected_stakeholders: Parties involved
        harm_type: Semantic harm type
        harm_severity: Harm severity [0, 1]
        harm_scope: Scope (individual|group|societal|systemic)
        reversibility: Reversibility [0, 1]
    """

    text: str
    likelihood: float
    scenario_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    scenario_type: ScenarioType = ScenarioType.SOCIAL_IMPACT
    outcome_valence: float = 0.0
    affected_stakeholders: list[str] = field(default_factory=list)
    harm_type: str = "none"
    harm_severity: float = 0.0
    harm_scope: str = "individual"
    reversibility: float = 0.0

    def __post_init__(self) -> None:
        """Normalizes the values to valid ranges."""
        self.likelihood = max(0.0, min(1.0, self.likelihood))
        self.outcome_valence = max(-1.0, min(1.0, self.outcome_valence))


@dataclass
class SimulationResult:
    """
    Complete result of the consequence simulation.

    Attributes:
        consequences: List of simulated consequences
        worst_case_valence: Valence of the worst scenario
        best_case_valence: Valence of the best scenario
        expected_valence: Expected valence (likelihood-weighted average)
        semantic_expected_harm: max(likelihood * harm_severity) for harm_type != none
        dominant_harm_types: top 2 harm_type by risk_i
        worst_harm: record argmax(risk_i): {harm_type, harm_scope, risk}
        raw_response: Raw LLM response (for debug)
        parse_attempts: Number of JSON parsing attempts
    """

    consequences: list[Consequence] = field(default_factory=list)
    worst_case_valence: float = 0.0
    best_case_valence: float = 0.0
    expected_valence: float = 0.0
    semantic_expected_harm: float = 0.0
    dominant_harm_types: list[str] = field(default_factory=list)
    worst_harm: dict[str, Any] | None = None
    raw_response: str = ""
    parse_attempts: int = 1
    prompt: str = ""
    system_prompt: str = ""
    tokens_used: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    token_usage_source: TokenUsageSource = "unknown"
    from_cache: bool = False
    wire: dict[str, Any] | None = None
    """Exact messages of the single call that produced this result (wire record v1). Additive audit data."""
    wire_calls: dict[str, Any] | None = None
    """``{"wire_calls": [...], "wire_strings": [...]}`` for the seeded path (one record per model call, failed
    attempts included, like the joined ``prompt``). A cache hit carries neither."""

    @classmethod
    def empty(cls) -> SimulationResult:
        """Creates an empty result (no simulated consequences)."""
        return cls(
            consequences=[],
            worst_case_valence=0.0,
            best_case_valence=0.0,
            expected_valence=0.0,
        )

    @classmethod
    def from_error(cls, error_msg: str) -> SimulationResult:
        """Creates a fallback result on critical error."""
        return cls(
            consequences=[],
            worst_case_valence=-1.0,  # Assume worst case on error
            best_case_valence=-1.0,
            expected_valence=-1.0,
            raw_response=error_msg,
        )


# =============================================================================
# Protocols
# =============================================================================

# Default re-export of the packaged fragment (byte-identical to the former literal). Runtime code reads
# the module's own ``PromptSet`` (``self._prompt_set``), never this constant.
RETRY_PROMPT = RETRY_SIMULATOR


# =============================================================================
# Conversion from structured output to Consequence (single source: structured parser)
# =============================================================================

_SCENARIO_TYPE_MAP = {
    "immediate_harm": ScenarioType.IMMEDIATE_HARM,
    "downstream_misuse": ScenarioType.DOWNSTREAM_MISUSE,
    "social_impact": ScenarioType.SOCIAL_IMPACT,
    "legal_consequence": ScenarioType.LEGAL_CONSEQUENCE,
    "positive_outcome": ScenarioType.POSITIVE_OUTCOME,
}


def _simulator_output_to_consequences(out: SimulatorOutput) -> list[Consequence]:
    """Converts SimulatorOutput (structured parser) into list[Consequence]."""
    consequences: list[Consequence] = []
    for c in out.consequences:
        text = c.text
        if len(text) > 160:
            text = text[:160]
        st = _SCENARIO_TYPE_MAP.get(
            c.scenario_type if isinstance(c.scenario_type, str) else "social_impact",
            ScenarioType.SOCIAL_IMPACT,
        )
        harm_type = str(c.harm_type).strip().lower() if c.harm_type else "none"
        harm_scope = str(c.harm_scope).strip().lower() if c.harm_scope else "individual"
        consequences.append(
            Consequence(
                text=text,
                likelihood=max(0.0, min(1.0, c.likelihood)),
                scenario_type=st,
                outcome_valence=max(-1.0, min(1.0, c.outcome_valence)),
                affected_stakeholders=(list(c.affected_stakeholders)[:3] if c.affected_stakeholders else []),
                harm_type=harm_type,
                harm_severity=max(0.0, min(1.0, c.harm_severity)),
                harm_scope=harm_scope,
                reversibility=max(0.0, min(1.0, c.reversibility)),
            )
        )
    return consequences


def parse_simulator_response(text: str) -> list[Consequence]:
    """
    Parses the simulator response with the mandatory structured parser.

    Single source: parse_and_validate_simulator_output. If parsing fails,
    JSONParseError or ValidationError is raised. No fallback.
    """
    out = parse_and_validate_simulator_output(text)
    return _simulator_output_to_consequences(out)


# =============================================================================
# LLM Consequence Simulator
# =============================================================================


@dataclass
class SimulatorConfig:
    """Configuration for the Consequence Simulator."""

    max_retries: int = 3
    max_tokens: int = 384  # Ultra-lean: compact narrative, minimal schema
    temperature: float = 0.8  # Higher for scenario diversity
    top_p: float = 0.95  # Nucleus sampling; configurable via MORALSTACK_SIMULATOR_TOP_P
    default_num_scenarios: int = 3
    use_seeded_generation: bool = False  # If True, uses separate seeds

    # FIX PERFORMANCE: Caching to avoid recomputation on identical inputs
    enable_caching: bool = True  # Enable result caching


class LLMConsequenceSimulator:
    """
    LLM-based consequence simulator.

    Generates plausible future scenarios to evaluate the impact of the responses.
    Uses the Policy LLM with a specialized prompt and strict JSON parsing
    with automatic retry.

    Attributes:
        policy: The Policy LLM used for generation
        config: Simulator configuration

    Usage:
        simulator = LLMConsequenceSimulator(policy)
        result = simulator.simulate(request, response, num_scenarios=3)

        for consequence in result.consequences:
            print(f"{consequence.text} (likelihood: {consequence.likelihood})")
    """

    def __init__(
        self,
        policy: PolicyLLMProtocol,
        config: SimulatorConfig | None = None,
        prompt_set: PromptSet | None = None,
    ) -> None:
        """
        Initializes the Consequence Simulator.

        Args:
            policy: Policy LLM for scenario generation
            config: Configuration (optional)
            prompt_set: PromptSet to read prompts and retries from (default: packaged set)
        """
        self.policy = policy
        self._prompt_set = prompt_set or default_prompt_set()
        if config is not None:
            self.config = config
        else:
            from moralstack.runtime.modules.simulator_config_loader import (
                load_simulator_config_from_env,
            )

            self.config = load_simulator_config_from_env()

        self._generation_config = GenerationConfig(
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            stop_sequences=[],
            # Strict Structured Outputs: the provider enforces the enums, so an
            # out-of-enum value can no longer cost a discarded call plus retries.
            # Degraded to plain JSON mode by PolicyLLM on models that lack it.
            response_format=simulator_response_format(),
        )

    def simulate(
        self,
        request: str,
        response: str,
        num_scenarios: int | None = None,
        delib_context: Any = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> SimulationResult:
        """
        Generates k plausible future consequences.

        FIX PERFORMANCE: Uses caching to avoid recomputation on identical inputs.
        If the (request, response) pair has already been simulated, returns the cached result.

        Requests exactly k consequences in one call, with a balanced mix (at least one positive
        outcome and at least one risk/harm scenario; ``simulator.batch.system``).

        Args:
            request: Original user request
            response: Response to evaluate
            num_scenarios: Number of scenarios to generate (default: 3)

        Returns:
            SimulationResult with consequences and aggregated metrics
        """
        # FIX PERFORMANCE: Check cache before simulating
        context_fingerprint = build_context_fingerprint(
            developer_contract=developer_contract,
            conversation_history=conversation_history,
            prompt_set_tag=self._prompt_set.cache_tag_for(
                PromptContext.from_contract(developer_contract, conversation_history)
            ),
        )
        if self.config.enable_caching:
            cache = get_global_cache()
            cached_result = cache.get_simulation_result(
                request,
                response,
                context_fingerprint=context_fingerprint,
            )
            if cached_result is not None:
                return replace(cast(SimulationResult, cached_result), from_cache=True, wire=None, wire_calls=None)

        k = num_scenarios or self.config.default_num_scenarios
        k = max(1, min(k, 10))  # Limit range

        result = self._simulate_batch(
            request,
            response,
            k,
            delib_context,
            developer_contract=developer_contract,
            conversation_history=conversation_history,
        )

        # FIX PERFORMANCE: Save to cache for future use
        if self.config.enable_caching:
            cache = get_global_cache()
            cache.set_simulation_result(
                request,
                response,
                result,
                context_fingerprint=context_fingerprint,
            )

        return result

    def _simulate_batch(
        self,
        request: str,
        response: str,
        num_scenarios: int,
        delib_context: Any = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> SimulationResult:
        """
        Generates all scenarios in a single LLM call.

        More efficient but less control over diversity.
        """
        ctx = delib_context or DelibContext(user_prompt=request, draft_text_full=response)
        from moralstack.prompts.simulator_prompt import build_simulator_prompt

        prompt = build_simulator_prompt(ctx, num_scenarios=num_scenarios, prompt_set=self._prompt_set)
        legacy_prompt = prompt + _build_context_block(developer_contract, conversation_history)
        # Per-call system prompt (``when`` evaluated against this call); also what gets persisted.
        batch_system_prompt = self._prompt_set.render(
            "simulator.batch.system", PromptContext.from_contract(developer_contract, conversation_history)
        )
        retry_text = self._prompt_set.text("simulator.retry")

        raw_response = ""
        parse_attempts = 0
        last_error: Exception | None = None
        tokens_used = 0
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        attempt_token_usage: TokenUsage | None = None

        for attempt in range(self.config.max_retries):
            parse_attempts = attempt + 1
            attempt_token_usage = None

            sent: tuple[list[dict[str, str]], str] | None = None

            try:
                if hasattr(self.policy, "generate_messages"):
                    messages = build_module_messages(
                        system_prompt=batch_system_prompt,
                        user_prompt=prompt,
                        developer_contract=developer_contract,
                        conversation_history=conversation_history,
                        retry_prompt="" if attempt == 0 else retry_text,
                        prompt_set=self._prompt_set,
                    )
                    sent = (messages, "messages")
                    result = self.policy.generate_messages(
                        messages=messages,
                        config=self._generation_config,
                    )
                else:
                    legacy_user = legacy_prompt if attempt == 0 else f"{legacy_prompt}\n\n{retry_text}"
                    sent = (generate_messages_for(legacy_user, batch_system_prompt), "generate")
                    result = self.policy.generate(
                        prompt=legacy_user,
                        system=batch_system_prompt,
                        config=self._generation_config,
                    )

                raw_response = result.text
                attempt_token_usage = TokenUsage.from_generation_result(result)
                tokens_used = attempt_token_usage.total_tokens
                prompt_tokens = getattr(result, "prompt_tokens", None)
                completion_tokens = getattr(result, "completion_tokens", None)

                # Parse JSON (mandatory structured parser; no fallback)
                consequences = parse_simulator_response(raw_response)
                effective_prompt = prompt if attempt == 0 else f"{prompt}\n\n{retry_text}"
                return self._build_result(
                    consequences=consequences,
                    raw_response=raw_response,
                    parse_attempts=parse_attempts,
                    prompt=effective_prompt,
                    system_prompt=batch_system_prompt,
                    tokens_used=tokens_used,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached_prompt_tokens=attempt_token_usage.cached_input_tokens,
                    token_usage_source=attempt_token_usage.source,
                    wire=safe_encode_wire(
                        sent[0], prompt_col=effective_prompt, system_prompt_col=batch_system_prompt, branch=sent[1]
                    ),
                )
            except (JSONParseError, Exception) as e:
                last_error = e
                if attempt_token_usage is not None:
                    try:
                        from moralstack.observability.emit_helpers import async_persist_llm_call

                        async_persist_llm_call(
                            phase="simulator_retry",
                            module="simulator",
                            action=f"retry_failed_attempt_{parse_attempts}",
                            model=getattr(self.policy, "model", None) or "",
                            prompt=f"Retry reason: {str(e)[:200]}",
                            raw_response=raw_response or "",
                            duration_ms=0.0,
                            attempts=parse_attempts,
                            call_outcome="retry_failed",
                            billable_provider_call=True,
                            token_usage_json=attempt_token_usage.to_json(),
                            # Additive: WHICH simulator call failed (the UI names it on the node).
                            parsed_summary_json=json.dumps(
                                {"simulator_path": "batch", **inline_wire_summary(sent, f"Retry reason: {str(e)[:200]}")}
                            ),
                        )
                    except Exception:
                        pass
                continue
        # All retries failed: explicit error, no decision fallback
        from moralstack.utils.structured_output import ValidationError as StructuredValidationError

        if last_error is not None and isinstance(last_error, (JSONParseError, StructuredValidationError)):
            raise last_error
        raise RuntimeError(
            f"Simulator structured parsing failed after {parse_attempts} attempts: {last_error}"
        ) from last_error

    def _build_result(
        self,
        consequences: list[Consequence],
        raw_response: str,
        parse_attempts: int,
        prompt: str = "",
        system_prompt: str = "",
        tokens_used: int = 0,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cached_prompt_tokens: int | None = None,
        token_usage_source: TokenUsageSource = "unknown",
        wire: dict[str, Any] | None = None,
        wire_calls: dict[str, Any] | None = None,
    ) -> SimulationResult:
        """
        Builds SimulationResult with aggregated metrics.
        expected_valence unchanged; semantic_expected_harm from risk_i = likelihood * harm_severity.
        """
        if not consequences:
            return SimulationResult.empty()

        # Find worst and best case
        valences = [c.outcome_valence for c in consequences]
        worst_case = min(valences)
        best_case = max(valences)

        # Compute expected value (weighted by likelihood) - unchanged
        total_weight = sum(c.likelihood for c in consequences)
        if total_weight > 0:
            expected = sum(c.outcome_valence * c.likelihood for c in consequences) / total_weight
        else:
            expected = 0.0

        # Semantic aggregation: risk_i = likelihood * harm_severity, ignores harm_type == "none"
        risk_records: list[tuple[float, str, str]] = []
        for c in consequences:
            if (getattr(c, "harm_type", "none") or "none").strip().lower() == "none":
                continue
            risk_i = c.likelihood * getattr(c, "harm_severity", 0.0)
            risk_records.append((risk_i, getattr(c, "harm_type", "none"), getattr(c, "harm_scope", "individual")))

        semantic_expected_harm = max((r[0] for r in risk_records), default=0.0)
        dominant_harm_types: list[str] = []
        worst_harm: dict[str, Any] | None = None

        if risk_records:
            sorted_by_risk = sorted(risk_records, key=lambda x: -x[0])
            dominant_harm_types = list(dict.fromkeys(r[1] for r in sorted_by_risk[:4]))[:2]
            best = sorted_by_risk[0]
            worst_harm = {"harm_type": best[1], "harm_scope": best[2], "risk": best[0]}

        return SimulationResult(
            consequences=consequences,
            worst_case_valence=worst_case,
            best_case_valence=best_case,
            expected_valence=expected,
            semantic_expected_harm=semantic_expected_harm,
            dominant_harm_types=dominant_harm_types,
            worst_harm=worst_harm,
            raw_response=raw_response,
            parse_attempts=parse_attempts,
            prompt=prompt,
            system_prompt=system_prompt,
            tokens_used=tokens_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            token_usage_source=token_usage_source,
            wire=wire,
            wire_calls=wire_calls,
        )


# =============================================================================
# Factory Functions
# =============================================================================


def create_simulator(
    policy: PolicyLLMProtocol,
    config: SimulatorConfig | None = None,
    prompt_set: PromptSet | None = None,
) -> LLMConsequenceSimulator:
    """
    Factory function for creating a Consequence Simulator.

    When config is None the constructor loads from env (MORALSTACK_SIMULATOR_*).

    Args:
        policy: Policy LLM to use
        config: Optional explicit config; when None, loaded from env
        prompt_set: PromptSet to read prompts from (optional; default: packaged set)

    Returns:
        Configured LLMConsequenceSimulator
    """
    return LLMConsequenceSimulator(
        policy=policy,
        config=config,
        prompt_set=prompt_set,
    )
