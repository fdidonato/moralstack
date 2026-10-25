"""
LLMHindsightEvaluator - Hindsight evaluator for MoralStack.

Evaluates responses and simulated consequences retrospectively, producing
safety, helpfulness and honesty scores and a total reward.
Uses the Policy LLM with a specialized prompt and strict JSON parsing with retry.

FIX PERFORMANCE: Added caching to avoid recomputation on identical inputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import warnings
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

# =============================================================================
# Protocols
# =============================================================================
from moralstack.core.types import PolicyLLMProtocol, Turn
from moralstack.models.base import GenerationConfig
from moralstack.models.delib_context import DelibContext
from moralstack.observability.token_usage import TokenUsage, TokenUsageSource
from moralstack.observability.wire import (
    generate_messages_for,
    inline_wire_summary,
    safe_encode_wire,
    safe_encode_wire_calls,
)
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts._common import HINDSIGHT_BASE_FRAMING
from moralstack.prompts.catalog import PromptContext, PromptSet, default_prompt_set
from moralstack.prompts.hindsight_prompt import HINDSIGHT_BATCH_SYSTEM_PROMPT  # noqa: F401 - default re-export
from moralstack.prompts.retry import RETRY_HINDSIGHT_BATCH, RETRY_HINDSIGHT_SINGLE
from moralstack.runtime.modules.message_context import build_module_messages
from moralstack.runtime.modules.simulator_module import Consequence
from moralstack.utils.cache import build_context_fingerprint, get_global_cache
from moralstack.utils.json_utils import JSONParseError
from moralstack.utils.structured_output import (
    HindsightBatchOutput,
    hindsight_batch_response_format,
    hindsight_single_response_format,
    parse_and_validate_hindsight_batch_output,
    parse_and_validate_hindsight_single_output,
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


class HindsightRecommendation(Enum):
    """Recommendation based on the hindsight evaluation."""

    PROCEED = "proceed"  # Acceptable response
    REVISE = "revise"  # Needs revision
    REFUSE = "refuse"  # To be refused


@dataclass
class HindsightScores:
    """
    Detailed scores of the hindsight evaluation.

    All values are in the range [-1, 1]:
    - -1: Extremely negative
    -  0: Neutral
    - +1: Extremely positive

    Attributes:
        safety: How safe and non-harmful the response is
        helpfulness: How useful and informative the response is
        honesty: How honest and transparent the response is
        total: Weighted composite score
    """

    safety: float = 0.0
    helpfulness: float = 0.0
    honesty: float = 0.0
    total: float = 0.0

    def __post_init__(self) -> None:
        """Clamp all values to [-1, 1]."""
        self.safety = max(-1.0, min(1.0, self.safety))
        self.helpfulness = max(-1.0, min(1.0, self.helpfulness))
        self.honesty = max(-1.0, min(1.0, self.honesty))
        self.total = max(-1.0, min(1.0, self.total))

    @classmethod
    def compute_total(
        cls,
        safety: float,
        helpfulness: float,
        honesty: float,
        weights: tuple[float, float, float] = (0.5, 0.3, 0.2),
    ) -> "HindsightScores":
        """
        Computes the scores with a weighted total.

        Args:
            safety: Safety score [-1, 1]
            helpfulness: Helpfulness score [-1, 1]
            honesty: Honesty score [-1, 1]
            weights: Weights (safety, helpfulness, honesty)

        Returns:
            HindsightScores with the computed total
        """
        w_s, w_h, w_o = weights
        total = w_s * safety + w_h * helpfulness + w_o * honesty

        return cls(
            safety=safety,
            helpfulness=helpfulness,
            honesty=honesty,
            total=total,
        )


@dataclass
class HindsightEvaluation:
    """
    Hindsight evaluation of a single scenario.

    Attributes:
        scenario_id: ID of the evaluated scenario
        scores: Detailed scores (safety/helpfulness/honesty/total)
        harm_probability: Probability of harm [0, 1]
        benefit_probability: Probability of benefit [0, 1]
        confidence: Confidence in the evaluation [0, 1]
        rationale: Explanation of the evaluation
    """

    scenario_id: str
    scores: HindsightScores
    harm_probability: float = 0.0
    benefit_probability: float = 0.5
    confidence: float = 0.8
    rationale: str = ""

    def __post_init__(self) -> None:
        """Normalize probabilities to [0, 1]."""
        self.harm_probability = max(0.0, min(1.0, self.harm_probability))
        self.benefit_probability = max(0.0, min(1.0, self.benefit_probability))
        self.confidence = max(0.0, min(1.0, self.confidence))

    @property
    def reward_score(self) -> float:
        """Main reward score (alias for total)."""
        return self.scores.total


@dataclass
class AggregatedHindsight:
    """
    Aggregation of multiple hindsight evaluations.

    Attributes:
        expected_value: Expected reward value across all scenarios
        worst_case: Reward of the worst scenario
        best_case: Reward of the best scenario
        variance: Reward variance
        avg_scores: Average scores (safety/helpfulness/honesty/total)
        recommendation: Final recommendation
        evaluations: List of the individual evaluations
    """

    expected_value: float = 0.0
    worst_case: float = 0.0
    best_case: float = 0.0
    variance: float = 0.0
    avg_scores: HindsightScores = field(default_factory=HindsightScores)
    recommendation: HindsightRecommendation = HindsightRecommendation.PROCEED
    evaluations: list[HindsightEvaluation] = field(default_factory=list)

    @classmethod
    def from_evaluations(
        cls,
        evaluations: list[HindsightEvaluation],
        refuse_threshold: float = -0.5,
    ) -> "AggregatedHindsight":
        """
        Aggregates lists of evaluations into a final result.

        Args:
            evaluations: List of HindsightEvaluation
            refuse_threshold: expected_value threshold for refuse (default -0.5 here;
                the evaluator passes HindsightConfig.refuse_threshold, default -0.7)

        Returns:
            AggregatedHindsight with aggregated metrics

        Decision Logic:
            - expected_value >= 0.0: PROCEED (overall beneficial response)
            - expected_value >= refuse_threshold: REVISE (needs improvement)
            - expected_value < refuse_threshold: REFUSE (clearly negative consequences)
        """
        if not evaluations:
            return cls()

        # Extract rewards
        rewards = [e.scores.total for e in evaluations]

        # Compute statistics
        expected_value = sum(rewards) / len(rewards)
        worst_case = min(rewards)
        best_case = max(rewards)

        # Variance
        mean = expected_value
        variance = sum((r - mean) ** 2 for r in rewards) / len(rewards)

        # Average scores
        avg_safety = sum(e.scores.safety for e in evaluations) / len(evaluations)
        avg_helpfulness = sum(e.scores.helpfulness for e in evaluations) / len(evaluations)
        avg_honesty = sum(e.scores.honesty for e in evaluations) / len(evaluations)
        avg_scores = HindsightScores.compute_total(
            safety=avg_safety,
            helpfulness=avg_helpfulness,
            honesty=avg_honesty,
        )

        # Determine recommendation
        # CORRECT LOGIC: based on expected_value with NEGATIVE thresholds
        # - PROCEED: expected_value >= 0 (overall beneficial response)
        # - REVISE: expected_value between -0.5 and 0 (improvable but not harmful)
        # - REFUSE: expected_value < -0.5 (clearly negative)
        #
        # IMPORTANT NOTE for interpersonal ethical dilemmas:
        # - Slightly negative values (-0.1 to -0.3) are NORMAL
        # - Hurting feelings ≠ concrete harm
        # - Only very negative values (< -0.5) justify refuse

        if expected_value >= 0.0:
            # Positive expected value = overall beneficial response
            recommendation = HindsightRecommendation.PROCEED
        elif expected_value >= refuse_threshold:
            # Slightly negative expected value but recoverable with revision
            # This includes the range from -0.5 to 0
            recommendation = HindsightRecommendation.REVISE
        else:
            # Clearly negative expected value (< -0.5)
            recommendation = HindsightRecommendation.REFUSE

        return cls(
            expected_value=expected_value,
            worst_case=worst_case,
            best_case=best_case,
            variance=variance,
            avg_scores=avg_scores,
            recommendation=recommendation,
            evaluations=evaluations,
        )

    @classmethod
    def from_error(cls) -> "AggregatedHindsight":
        """Creates a fallback result on critical error."""
        return cls(
            expected_value=-1.0,
            worst_case=-1.0,
            best_case=-1.0,
            variance=0.0,
            recommendation=HindsightRecommendation.REFUSE,
        )


@dataclass
class HindsightResult:
    """
    Complete result of the hindsight evaluation.

    Attributes:
        aggregated: Aggregated metrics
        raw_response: Raw LLM response (for debug)
        parse_attempts: Number of JSON parsing attempts
    """

    aggregated: AggregatedHindsight = field(default_factory=AggregatedHindsight)
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
    """``{"wire_calls": [...], "wire_strings": [...]}`` when the individual path made several calls."""

    @classmethod
    def empty(cls) -> "HindsightResult":
        """Creates an empty result."""
        return cls(
            aggregated=AggregatedHindsight(),
        )

    @classmethod
    def from_error(cls, error_msg: str) -> "HindsightResult":
        """Creates a fallback result on error."""
        return cls(
            aggregated=AggregatedHindsight.from_error(),
            raw_response=error_msg,
        )


# =============================================================================
# Prompt Templates
# =============================================================================

# Base hindsight role framing — single source of truth in prompts/_common.py,
# shared with HINDSIGHT_BATCH_SYSTEM_PROMPT (moralstack/prompts/hindsight_prompt.py)
# so the batch and single/individual paths carry byte-identical base framing.
HINDSIGHT_SYSTEM_PROMPT = HINDSIGHT_BASE_FRAMING

# Prompt-caching reorder (A4): the single-scenario static rubric + the
# root-object JSON skeleton, moved out of HINDSIGHT_PROMPT_TEMPLATE below.
# Used ONLY by the single-scenario path (evaluate_scenario) AND the
# non-batch aggregate path (_evaluate_individual, which calls
# evaluate_scenario per consequence) — NEVER by the batch path, whose
# output is rooted at "evaluations" (see HINDSIGHT_BATCH_SYSTEM_PROMPT in
# moralstack/prompts/hindsight_prompt.py).
HINDSIGHT_SINGLE_SYSTEM_PROMPT = default_prompt_set().text("hindsight.single.system")

# Dynamic-only: per-request fields last (static rubric now lives entirely in
# HINDSIGHT_SINGLE_SYSTEM_PROMPT above).
HINDSIGHT_PROMPT_TEMPLATE = default_prompt_set().template("hindsight.single.user")

RETRY_PROMPT = RETRY_HINDSIGHT_SINGLE
RETRY_BATCH_PROMPT = RETRY_HINDSIGHT_BATCH


# =============================================================================
# JSON Parsing
# =============================================================================


def parse_hindsight_response(
    text: str,
    scenario_id: str = "default",
) -> HindsightEvaluation:
    """
    Parses the hindsight evaluator response for a single scenario
    (mandatory structured parser).

    Raises:
        JSONParseError: Unable to extract JSON
        ValidationError: Schema validation failed
    """
    out = parse_and_validate_hindsight_single_output(text)
    scores = HindsightScores.compute_total(
        safety=out.safety,
        helpfulness=out.helpfulness,
        honesty=out.honesty,
    )
    return HindsightEvaluation(
        scenario_id=out.scenario_id or scenario_id,
        scores=scores,
        harm_probability=max(0.0, min(1.0, out.harm_probability)),
        benefit_probability=max(0.0, min(1.0, out.benefit_probability)),
        confidence=max(0.0, min(1.0, out.confidence)),
        rationale=out.rationale or "",
    )


def _hindsight_batch_output_to_evaluations(
    out: HindsightBatchOutput,
    consequence_ids: list[str],
) -> list[HindsightEvaluation]:
    """Converts HindsightBatchOutput (structured parser) into list[HindsightEvaluation]."""
    evaluations: list[HindsightEvaluation] = []
    for i, e in enumerate(out.evaluations):
        sid = consequence_ids[i] if i < len(consequence_ids) else (e.scenario_id or f"scenario_{i}")
        scores = HindsightScores.compute_total(
            safety=e.safety,
            helpfulness=e.helpfulness,
            honesty=e.honesty,
        )
        evaluations.append(
            HindsightEvaluation(
                scenario_id=sid,
                scores=scores,
                harm_probability=max(0.0, min(1.0, e.harm_probability)),
                benefit_probability=max(0.0, min(1.0, e.benefit_probability)),
                confidence=max(0.0, min(1.0, e.confidence)),
                rationale=e.rationale or "",
            )
        )
    return evaluations


def parse_batch_hindsight_response(
    text: str,
    consequence_ids: list[str],
) -> list[HindsightEvaluation]:
    """
    Parses the hindsight evaluator batch response with the mandatory structured parser.

    Single source: parse_and_validate_hindsight_batch_output. No fallback.
    Raises:
        JSONParseError: Unable to extract JSON
        ValidationError: Schema validation failed
    """
    out = parse_and_validate_hindsight_batch_output(text)
    return _hindsight_batch_output_to_evaluations(out, consequence_ids)


# =============================================================================
# LLM Hindsight Evaluator
# =============================================================================


@dataclass
class HindsightConfig:
    """Configuration for the Hindsight Evaluator."""

    max_retries: int = 3
    max_tokens: int = 768  # Reduced for speed (batch evaluations should be more concise)
    temperature: float = 0.3  # Low for consistent evaluations
    top_p: float = 0.9  # Nucleus sampling; configurable via MORALSTACK_HINDSIGHT_TOP_P

    # Weights for computing the total score
    weight_safety: float = 0.5
    weight_helpfulness: float = 0.3
    weight_honesty: float = 0.2

    # Thresholds for recommendation (calibrated to avoid false positives)
    # NOTE: thresholds lowered to be less conservative on legitimate discussions
    refuse_threshold: float = -0.7  # expected_value < this -> refuse (was -0.5)
    revise_threshold: float = 0.0  # DEPRECATED (no effect, removal in 2.0): the REVISE band is fixed at expected_value < 0.0

    # If True, evaluates all scenarios in a single call
    use_batch_evaluation: bool = True

    # FIX PERFORMANCE: Caching to avoid recomputation on identical inputs
    enable_caching: bool = True  # Enable result caching

    def __post_init__(self) -> None:
        # Not frozen: warns at construction only (not on later assignment); dataclasses.replace re-warns.
        if self.revise_threshold != 0.0:
            warnings.warn(
                "HindsightConfig.revise_threshold is deprecated and has no effect; it will be removed in MoralStack 2.0.",
                DeprecationWarning,
                stacklevel=3,
            )


class LLMHindsightEvaluator:
    """
    LLM-based hindsight evaluator.

    Evaluates responses and simulated consequences retrospectively, producing
    safety, helpfulness and honesty scores and a total reward in [-1, 1].
    Uses the Policy LLM with a specialized prompt and strict JSON parsing
    with automatic retry.

    Attributes:
        policy: The Policy LLM for evaluation
        config: Evaluator configuration

    Usage:
        evaluator = LLMHindsightEvaluator(policy)

        # Evaluate a single scenario
        evaluation = evaluator.evaluate_scenario(request, response, consequence)
        print(f"Safety: {evaluation.scores.safety}")
        print(f"Total: {evaluation.scores.total}")

        # Evaluate multiple scenarios and aggregate
        result = evaluator.evaluate(request, response, consequences)
        print(f"Recommendation: {result.aggregated.recommendation}")
    """

    def __init__(
        self,
        policy: PolicyLLMProtocol,
        config: HindsightConfig | None = None,
        prompt_set: PromptSet | None = None,
    ) -> None:
        """
        Initializes the Hindsight Evaluator.

        Args:
            policy: Policy LLM for evaluation
            config: Configuration (optional)
            prompt_set: PromptSet to read prompts and retries from (default: packaged set)
        """
        self.policy = policy
        self._prompt_set = prompt_set or default_prompt_set()
        if config is not None:
            self.config = config
        else:
            from moralstack.runtime.modules.hindsight_config_loader import (
                load_hindsight_config_from_env,
            )

            self.config = load_hindsight_config_from_env()

        # Two prompts, two parsers, two schemas: the single-evaluation path and
        # the batch path must not share one response_format, or the provider
        # would be made to return the wrong shape for one of them.
        self._generation_config = GenerationConfig(
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            stop_sequences=[],
            response_format=hindsight_single_response_format(),
        )
        self._generation_config_batch = GenerationConfig(
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            stop_sequences=[],
            response_format=hindsight_batch_response_format(),
        )

        # Weights for HindsightScores
        self._score_weights = (
            self.config.weight_safety,
            self.config.weight_helpfulness,
            self.config.weight_honesty,
        )

    def evaluate_scenario(
        self,
        request: str,
        response: str,
        consequence: Consequence,
        *,
        sent_calls: list[tuple[list[dict[str, str]], str]] | None = None,
    ) -> HindsightEvaluation:
        """
        Evaluates a single scenario (response + consequence).

        Args:
            request: Original user request
            response: Response to evaluate
            consequence: Simulated consequence to evaluate
            sent_calls: if given, receives the (messages, branch) of the call that produced the evaluation
                (audit only; the individual path uses it to build the row's wire record)

        Returns:
            HindsightEvaluation with scores and metrics
        """
        prompt = self._prompt_set.format(
            "hindsight.single.user",
            request=request,
            response=response,
            consequence=consequence.text,
        )
        single_system_prompt = self._prompt_set.text("hindsight.single.system")

        ""
        parse_attempts = 0
        last_error = ""

        for attempt in range(self.config.max_retries):
            parse_attempts = attempt + 1
            attempt_token_usage: TokenUsage | None = None
            raw_response = ""

            sent: tuple[list[dict[str, str]], str] | None = None

            try:
                if attempt == 0:
                    user_text = prompt
                else:
                    # Retry with specific prompt
                    user_text = f"{prompt}\n\n{self._prompt_set.text('hindsight.retry.single')}"
                sent = (generate_messages_for(user_text, single_system_prompt), "generate")
                result = self.policy.generate(
                    prompt=user_text,
                    system=single_system_prompt,
                    config=self._generation_config,
                )

                raw_response = result.text
                attempt_token_usage = TokenUsage.from_generation_result(result)

                # Parse JSON
                evaluation = parse_hindsight_response(
                    raw_response,
                    scenario_id=consequence.scenario_id,
                )

                # Recompute total with the configured weights
                evaluation.scores = HindsightScores.compute_total(
                    safety=evaluation.scores.safety,
                    helpfulness=evaluation.scores.helpfulness,
                    honesty=evaluation.scores.honesty,
                    weights=self._score_weights,
                )

                if sent_calls is not None and sent is not None:
                    sent_calls.append(sent)
                return evaluation

            except Exception as e:  # JSONParseError included: every failed attempt is handled the same way
                last_error = str(e)
                self._persist_single_retry_failed(
                    consequence,
                    parse_attempts,
                    e,
                    raw_response=raw_response,
                    token_usage=attempt_token_usage,
                    sent=sent,
                )
                continue

        # All retries failed - return a pessimistic evaluation
        return HindsightEvaluation(
            scenario_id=consequence.scenario_id,
            scores=HindsightScores(
                safety=-1.0,
                helpfulness=0.0,
                honesty=0.0,
                total=-0.5,
            ),
            harm_probability=1.0,
            benefit_probability=0.0,
            confidence=0.5,
            rationale=f"Evaluation failed after {parse_attempts} attempts: {last_error}",
        )

    def _persist_single_retry_failed(
        self,
        consequence: Consequence,
        attempt_no: int,
        error: Exception,
        *,
        raw_response: str,
        token_usage: TokenUsage | None,
        sent: tuple[list[dict[str, str]], str] | None = None,
    ) -> None:
        """Record a paid-for single-path attempt whose output could not be parsed.

        Same shape as the batch path's retry-failed row (``call_outcome="retry_failed"``). A failure before the
        provider answered (no token usage) bills nothing and records nothing. Best-effort: never raises.
        """
        if token_usage is None:
            return
        try:
            from moralstack.observability.emit_helpers import async_persist_llm_call

            retry_prompt = f"Scenario {consequence.scenario_id} retry reason: {str(error)[:200]}"
            summary: dict[str, Any] = {"scenario_id": consequence.scenario_id}
            summary.update(inline_wire_summary(sent, retry_prompt))
            async_persist_llm_call(
                phase="hindsight_retry",
                module="hindsight",
                action=f"retry_failed_attempt_{attempt_no}",
                model=getattr(self.policy, "model", None) or "",
                prompt=retry_prompt,
                raw_response=raw_response or "",
                duration_ms=0.0,
                attempts=attempt_no,
                call_outcome="retry_failed",
                billable_provider_call=True,
                token_usage_json=token_usage.to_json(),
                # Additive: WHICH consequence failed (the UI names it on the node).
                parsed_summary_json=json.dumps(summary, ensure_ascii=False),
            )
        except Exception:
            logger.debug("persist hindsight single retry-failed llm call failed", exc_info=True)

    def evaluate(
        self,
        request: str,
        response: str,
        consequences: list[Consequence],
        delib_context: Any = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> HindsightResult:
        """
        Evaluates a response against multiple simulated consequences.

        FIX PERFORMANCE: Uses caching to avoid recomputation on identical inputs.
        If the triple (request, response, consequences) has already been evaluated,
        returns the cached result.

        Args:
            request: Original user request
            response: Response to evaluate
            consequences: List of simulated consequences

        Returns:
            HindsightResult with aggregated evaluations
        """
        if not consequences:
            return HindsightResult.empty()

        context_fingerprint = build_context_fingerprint(
            developer_contract=developer_contract,
            conversation_history=conversation_history,
            prompt_set_tag=self._prompt_set.cache_tag_for(
                PromptContext.from_contract(developer_contract, conversation_history)
            ),
        )

        # FIX PERFORMANCE: Check cache before evaluating
        # Use the hash of the consequences as part of the key
        if self.config.enable_caching:
            cache = get_global_cache()
            consequences_hash = hashlib.md5("|".join(c.scenario_id for c in consequences).encode()).hexdigest()
            cached_result = cache.get_hindsight_result(
                request,
                response,
                consequences_hash,
                context_fingerprint=context_fingerprint,
            )
            if isinstance(cached_result, HindsightResult):
                return replace(cached_result, from_cache=True, wire=None, wire_calls=None)

        if self.config.use_batch_evaluation and len(consequences) > 1:
            result = self._evaluate_batch(
                request,
                response,
                consequences,
                delib_context,
                developer_contract=developer_contract,
                conversation_history=conversation_history,
            )
        else:
            result = self._evaluate_individual(request, response, consequences)

        # FIX PERFORMANCE: Save to cache for future use
        if self.config.enable_caching:
            cache = get_global_cache()
            consequences_hash = hashlib.md5("|".join(c.scenario_id for c in consequences).encode()).hexdigest()
            cache.set_hindsight_result(
                request,
                response,
                result,
                consequences_hash,
                context_fingerprint=context_fingerprint,
            )

        return result

    def _evaluate_batch(
        self,
        request: str,
        response: str,
        consequences: list[Consequence],
        delib_context: Any = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> HindsightResult:
        """
        Evaluates all scenarios in a single LLM call.

        More efficient for multiple consequences.
        """
        # Format consequences for the prompt
        consequences_text = self._format_consequences(consequences)
        consequence_ids = [c.scenario_id for c in consequences]

        ctx = delib_context or DelibContext(user_prompt=request, draft_text_full=response)
        from moralstack.prompts.hindsight_prompt import build_hindsight_prompt

        prompt = build_hindsight_prompt(ctx, consequences_text, prompt_set=self._prompt_set)
        legacy_prompt = prompt + _build_context_block(developer_contract, conversation_history)
        # Per-call system prompt (``when`` evaluated against this call); also what gets persisted.
        batch_system_prompt = self._prompt_set.render(
            "hindsight.batch.system", PromptContext.from_contract(developer_contract, conversation_history)
        )
        retry_batch_text = self._prompt_set.text("hindsight.retry.batch")

        parse_attempts = 0
        last_error: Exception | None = None
        raw_response = ""

        for attempt in range(self.config.max_retries):
            parse_attempts = attempt + 1
            attempt_token_usage: TokenUsage | None = None

            sent: tuple[list[dict[str, str]], str] | None = None

            try:
                if hasattr(self.policy, "generate_messages"):
                    messages = build_module_messages(
                        system_prompt=batch_system_prompt,
                        user_prompt=prompt,
                        developer_contract=developer_contract,
                        conversation_history=conversation_history,
                        retry_prompt="" if attempt == 0 else retry_batch_text,
                        prompt_set=self._prompt_set,
                    )
                    sent = (messages, "messages")
                    result = self.policy.generate_messages(
                        messages=messages,
                        config=self._generation_config_batch,
                    )
                else:
                    legacy_user = legacy_prompt if attempt == 0 else f"{legacy_prompt}\n\n{retry_batch_text}"
                    sent = (generate_messages_for(legacy_user, batch_system_prompt), "generate")
                    result = self.policy.generate(
                        prompt=legacy_user,
                        system=batch_system_prompt,
                        config=self._generation_config_batch,
                    )

                raw_response = result.text
                attempt_token_usage = TokenUsage.from_generation_result(result)

                # Parse batch response
                evaluations = parse_batch_hindsight_response(
                    raw_response,
                    consequence_ids,
                )

                if not evaluations:
                    last_error = RuntimeError("No valid evaluations parsed")
                    continue

                # Recompute total for each evaluation with the configured weights
                for evaluation in evaluations:
                    evaluation.scores = HindsightScores.compute_total(
                        safety=evaluation.scores.safety,
                        helpfulness=evaluation.scores.helpfulness,
                        honesty=evaluation.scores.honesty,
                        weights=self._score_weights,
                    )

                # Aggregate results
                aggregated = AggregatedHindsight.from_evaluations(
                    evaluations=evaluations,
                    refuse_threshold=self.config.refuse_threshold,
                )

                effective_prompt = prompt if attempt == 0 else f"{prompt}\n\n{retry_batch_text}"
                return HindsightResult(
                    aggregated=aggregated,
                    raw_response=raw_response,
                    parse_attempts=parse_attempts,
                    prompt=effective_prompt,
                    system_prompt=batch_system_prompt,
                    tokens_used=int(getattr(result, "tokens_used", 0) or 0),
                    prompt_tokens=getattr(result, "prompt_tokens", None),
                    completion_tokens=getattr(result, "completion_tokens", None),
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

                        retry_reason = f"Retry reason: {str(e)[:200]}"
                        inline_wire = inline_wire_summary(sent, retry_reason)
                        async_persist_llm_call(
                            phase="hindsight_retry",
                            module="hindsight",
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
                        pass
                continue
        # All retries failed: explicit error, no decision fallback
        from moralstack.utils.structured_output import ValidationError as StructuredValidationError

        if last_error is not None and isinstance(last_error, (JSONParseError, StructuredValidationError)):
            raise last_error
        raise RuntimeError(f"Hindsight structured parsing failed after {parse_attempts} attempts: {last_error}") from (
            last_error if last_error is not None else None
        )

    def _evaluate_individual(
        self,
        request: str,
        response: str,
        consequences: list[Consequence],
    ) -> HindsightResult:
        """
        Evaluates each scenario individually.

        More robust but less efficient.
        """
        evaluations: list[HindsightEvaluation] = []
        prompts_used: list[str] = []
        sent_calls: list[tuple[list[dict[str, str]], str]] = []
        total_attempts = 0

        for consequence in consequences:
            prompt_fmt = self._prompt_set.format(
                "hindsight.single.user",
                request=request,
                response=response,
                consequence=consequence.text,
            )
            evaluation = self.evaluate_scenario(request, response, consequence, sent_calls=sent_calls)
            evaluations.append(evaluation)
            prompts_used.append(prompt_fmt)
            total_attempts += 1

        if not evaluations:
            return HindsightResult.from_error(f"No evaluations completed after {total_attempts} attempts")

        aggregated = AggregatedHindsight.from_evaluations(
            evaluations=evaluations,
            refuse_threshold=self.config.refuse_threshold,
        )

        row_prompt = "\n---\n".join(prompts_used) if prompts_used else ""
        row_system = self._prompt_set.text("hindsight.single.system")
        wire: dict[str, Any] | None = None
        wire_calls: dict[str, Any] | None = None
        if len(sent_calls) == 1:
            wire = safe_encode_wire(
                sent_calls[0][0], prompt_col=row_prompt, system_prompt_col=row_system, branch=sent_calls[0][1]
            )
        elif sent_calls:
            # Several consequences share one row (prompts joined): one record per successful call.
            wire_calls = safe_encode_wire_calls(sent_calls, prompt_col="", system_prompt_col=row_system)
        return HindsightResult(
            aggregated=aggregated,
            raw_response="",
            parse_attempts=total_attempts,
            prompt=row_prompt,
            system_prompt=row_system,
            wire=wire,
            wire_calls=wire_calls,
        )

    def aggregate(
        self,
        evaluations: list[HindsightEvaluation],
    ) -> AggregatedHindsight:
        """
        Aggregates a list of evaluations into a final result.

        Convenience method for manual aggregation.

        Args:
            evaluations: List of HindsightEvaluation

        Returns:
            AggregatedHindsight with recommendation
        """
        return AggregatedHindsight.from_evaluations(
            evaluations=evaluations,
            refuse_threshold=self.config.refuse_threshold,
        )

    def _format_consequences(self, consequences: list[Consequence]) -> str:
        """
        Formats the list of consequences for inclusion in the prompt.
        """
        lines = []

        for i, c in enumerate(consequences, 1):
            lines.append(f"\n{i}. [ID: {c.scenario_id}] (likelihood: {c.likelihood:.2f})")
            lines.append(f"   Type: {c.scenario_type.value}")
            lines.append(f"   {c.text}")
            if c.affected_stakeholders:
                lines.append(f"   Affected: {', '.join(c.affected_stakeholders)}")

        return "\n".join(lines)


# =============================================================================
# Factory Functions
# =============================================================================


def create_hindsight_evaluator(
    policy: PolicyLLMProtocol,
    config: HindsightConfig | None = None,
    prompt_set: PromptSet | None = None,
) -> LLMHindsightEvaluator:
    """
    Factory function for creating a Hindsight Evaluator.

    When config is None the constructor loads from env (MORALSTACK_HINDSIGHT_*).

    Args:
        policy: Policy LLM to use
        config: Optional explicit config; when None, loaded from env
        prompt_set: PromptSet to read prompts from (optional; default: packaged set)

    Returns:
        Configured LLMHindsightEvaluator
    """
    return LLMHindsightEvaluator(
        policy=policy,
        config=config,
        prompt_set=prompt_set,
    )
