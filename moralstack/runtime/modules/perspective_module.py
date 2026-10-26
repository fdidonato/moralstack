"""
LLMPerspectiveEnsemble - Perspective ensemble for MoralStack.

Evaluates responses from multiple cognitive perspectives to ensure
a complete and balanced evaluation of the AI output.
Uses the Policy LLM with specialized prompts and weighted aggregation.

FIX PERFORMANCE: Added caching to avoid recomputation on identical inputs.
"""

from __future__ import annotations

import contextvars
import json
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from typing import Any, Literal, Union

# =============================================================================
# Protocols
# =============================================================================
from moralstack.core.types import PolicyLLMProtocol, Turn
from moralstack.models.base import GenerationConfig
from moralstack.models.delib_context import DelibContext
from moralstack.observability.token_usage import TokenUsage, TokenUsageSource
from moralstack.observability.wire import generate_messages_for, inline_wire_summary, safe_encode_wire_calls
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts.catalog import NO_CONTEXT, PromptContext, PromptSet, default_prompt_set
from moralstack.prompts.perspectives_prompt import build_perspectives_user_prompt
from moralstack.prompts.retry import RETRY_PERSPECTIVES
from moralstack.runtime.modules.message_context import build_module_messages
from moralstack.utils.cache import build_context_fingerprint, get_global_cache
from moralstack.utils.json_utils import JSONParseError, extract_json
from moralstack.utils.structured_output import perspective_response_format

# =============================================================================
# Data Models
# =============================================================================


@dataclass
class Perspective:
    """
    Represents an evaluation perspective.

    Attributes:
        id: Unique identifier (e.g. "user", "vulnerable")
        name: Descriptive name of the perspective
        prompt_template: Template for the evaluation prompt
        weight: Weight in the aggregation [0, 2]
    """

    id: str
    name: str
    prompt_template: str
    weight: float = 1.0

    def __post_init__(self) -> None:
        """Normalizes the weight to a valid range."""
        self.weight = max(0.0, min(2.0, self.weight))


@dataclass
class PerspectiveResult:
    """
    Result of the evaluation from a single perspective.

    Attributes:
        perspective_id: ID of the perspective used
        perspective_name: Name of the perspective
        approval_score: Approval score [0, 1]
        concerns: List of identified concerns
        suggestions: List of suggestions for improvement
        rationale: Detailed reasoning
        raw_response: Raw LLM response (for debug)
    """

    perspective_id: str
    perspective_name: str = ""
    approval_score: float = 0.5
    concerns: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    rationale: str = ""
    raw_response: str = ""
    prompt: str = ""
    system_prompt: str = ""
    tokens_used: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    token_usage_source: TokenUsageSource = "unknown"

    def __post_init__(self) -> None:
        """Normalizes the approval score to a valid range."""
        self.approval_score = max(0.0, min(1.0, self.approval_score))


@dataclass
class PerspectiveAggregation:
    """
    Aggregation of the results from all perspectives.

    Attributes:
        weighted_approval: Weighted average of the approval scores
        min_approval: Minimum approval score
        max_approval: Maximum approval score
        all_concerns: Unified, deduplicated list of concerns
        all_suggestions: Unified, deduplicated list of suggestions
        perspective_count: Number of perspectives evaluated
        dissent_level: Level of disagreement among perspectives [0, 1]
    """

    weighted_approval: float = 0.0
    min_approval: float = 0.0
    max_approval: float = 0.0
    all_concerns: list[str] = field(default_factory=list)
    all_suggestions: list[str] = field(default_factory=list)
    perspective_count: int = 0
    dissent_level: float = 0.0

    @classmethod
    def empty(cls) -> PerspectiveAggregation:
        """Creates an empty aggregation."""
        return cls()

    @property
    def has_concerns(self) -> bool:
        """True if there are concerns."""
        return len(self.all_concerns) > 0

    @property
    def recommendation(self) -> Literal["proceed", "revise", "refuse"]:
        """
        Recommendation based on the aggregation.

        Returns:
            "proceed" if approval > 0.7
            "revise" if 0.3 <= approval <= 0.7
            "refuse" if approval < 0.3
        """
        # Conservative rule: if any perspective is strongly opposed,
        # prefer REVISION/REFUSAL even if the weighted average is high.
        # This prevents "Potential Misuser" (0.0) from being masked by others (1.0).
        if self.min_approval <= 0.2:
            return "refuse"
        if self.min_approval < 0.3:
            return "revise"
        if self.weighted_approval >= 0.7:
            return "proceed"
        elif self.weighted_approval >= 0.3:
            return "revise"
        else:
            return "refuse"


@dataclass
class EnsembleResult:
    """
    Complete result of the perspective ensemble.

    Attributes:
        results: List of results for each perspective
        aggregation: Aggregation of the results
        raw_responses: Raw responses (for debug)
        evaluation_count: Number of successful evaluations
        failed_perspectives: Perspectives that failed
    """

    results: list[PerspectiveResult] = field(default_factory=list)
    aggregation: PerspectiveAggregation = field(default_factory=PerspectiveAggregation)
    raw_responses: list[str] = field(default_factory=list)
    evaluation_count: int = 0
    failed_perspectives: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    system_prompts: list[str] = field(default_factory=list)
    tokens_used: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    token_usage_source: TokenUsageSource = "unknown"
    from_cache: bool = False
    wire_calls: dict[str, Any] | None = None
    """``{"wire_calls": [...], "wire_strings": [...]}``: the exact messages of every call whose prompt is joined in the
    row (one record per result, same order as ``prompts``). Additive audit data; a cache hit carries none."""

    @classmethod
    def empty(cls) -> EnsembleResult:
        """Creates an empty result."""
        return cls(
            results=[],
            aggregation=PerspectiveAggregation.empty(),
            evaluation_count=0,
        )

    @classmethod
    def from_error(cls, error_msg: str) -> EnsembleResult:
        """Creates a fallback result on critical error."""
        return cls(
            results=[],
            aggregation=PerspectiveAggregation(
                weighted_approval=0.5,  # Neutral on error
                min_approval=0.0,
                max_approval=0.0,
            ),
            raw_responses=[error_msg],
            evaluation_count=0,
        )


# =============================================================================
# Default Perspectives
# =============================================================================

# (id, display name, weight). Names and weights are code-owned; the prompt_template of each default
# perspective is the packaged ``perspectives.identity.<id>`` fragment of the PromptSet in use.
_DEFAULT_PERSPECTIVE_SPECS: tuple[tuple[str, str, float], ...] = (
    ("user", "Direct User", 1.0),
    ("vulnerable", "Vulnerable User", 1.2),  # Higher weight for protection
    ("observer", "Neutral Observer", 1.0),
    ("adversary", "Potential Misuser", 0.8),
    ("compliance", "Compliance Officer", 1.0),
)


def _build_default_perspectives(prompt_set: PromptSet | None = None) -> list[Perspective]:
    """The five default perspectives, with their instruction text taken from ``prompt_set``."""
    ps = prompt_set or default_prompt_set()
    return [
        Perspective(id=pid, name=name, prompt_template=ps.text(f"perspectives.identity.{pid}"), weight=weight)
        for pid, name, weight in _DEFAULT_PERSPECTIVE_SPECS
    ]


# Default re-exports (packaged set). Instances read their own set via ``_build_default_perspectives``.
DEFAULT_PERSPECTIVES = _build_default_perspectives()

# Map for fast lookup
PERSPECTIVES_BY_ID = {p.id: p for p in DEFAULT_PERSPECTIVES}

# =============================================================================
# Prompt Templates
# =============================================================================

PERSPECTIVE_SYSTEM_PROMPT = default_prompt_set().text("perspectives.base")

RETRY_PROMPT = RETRY_PERSPECTIVES


# =============================================================================
# JSON Parsing
# =============================================================================


def validate_perspective_data(data: dict[str, Any]) -> bool:
    """Validates that the data of a perspective evaluation is complete."""
    return "approval_score" in data


def parse_perspective_response(
    text: str,
    perspective: Perspective,
) -> PerspectiveResult:
    """
    Parses the perspective evaluation response.

    Args:
        text: JSON response from the LLM
        perspective: Perspective used for the evaluation

    Returns:
        PerspectiveResult

    Raises:
        JSONParseError: If parsing fails
    """
    data = extract_json(text)

    if not validate_perspective_data(data):
        raise JSONParseError("Missing required field: approval_score")

    # Parse approval_score with fallback
    try:
        approval_score = float(data.get("approval_score", 0.5))
    except (ValueError, TypeError):
        approval_score = 0.5

    # Parse concerns
    concerns = data.get("concerns", [])
    if not isinstance(concerns, list):
        concerns = []
    concerns = [str(c) for c in concerns if c]  # Filter empty strings

    # Parse suggestions
    suggestions = data.get("suggestions", [])
    if not isinstance(suggestions, list):
        suggestions = []
    suggestions = [str(s) for s in suggestions if s]  # Filter empty strings

    # Parse rationale
    rationale = str(data.get("rationale", ""))

    return PerspectiveResult(
        perspective_id=perspective.id,
        perspective_name=perspective.name,
        approval_score=approval_score,
        concerns=concerns,
        suggestions=suggestions,
        rationale=rationale,
        raw_response=text,
    )


def _ensemble_wire_calls(results: list[PerspectiveResult]) -> dict[str, Any] | None:
    """Wire records of the calls behind ``results``, in result order (the order of the row's joined prompts).

    None unless every result carries its captured messages. No prompt/system reference: the row's columns are the
    joined texts, and the shared perspectives system prompt is stored once in the string table.
    """
    sent = [getattr(r, "_sent_wire", None) for r in results]
    for r in results:
        # Consumed here: the results outlive the call (state.perspectives, the LRU cache) and must not keep the messages.
        r.__dict__.pop("_sent_wire", None)
    calls = [item for item in sent if item is not None]
    if not results or len(calls) != len(results):
        return None
    return safe_encode_wire_calls(calls, prompt_col="", system_prompt_col="")


def _sum_optional_token_field(results: list[PerspectiveResult], field_name: str) -> int | None:
    values: list[int] = []
    for result in results:
        value = getattr(result, field_name, None)
        if value is not None:
            values.append(int(value))
    if not values:
        return None
    return sum(values)


def _build_history_snippet(conversation_history: list[Turn] | None) -> str:
    """Build a compact snippet for the last three turns."""
    if not conversation_history:
        return ""
    recent = list(conversation_history)[-3:]
    lines: list[str] = []
    for turn in recent:
        role = getattr(turn, "role", "") or "unknown"
        content = (getattr(turn, "content", "") or "")[:200]
        lines.append(f"[{role}]: {content}")
    return "context_mode=role_serialized_truncated; last 3 turns\n" + "\n".join(lines)


# =============================================================================
# LLM Perspective Ensemble
# =============================================================================


@dataclass
class EnsembleConfig:
    """Configuration for the Perspective Ensemble."""

    max_retries: int = 3  # Increased from 2 to give more chances of getting valid JSON
    max_tokens: int = 512  # INCREASED from 256: 256 is too low for complete JSON with concerns/suggestions
    temperature: float = 0.1  # Lowered from 0.2 for more deterministic, structured output
    top_p: float = 0.9  # Nucleus sampling; configurable via MORALSTACK_PERSPECTIVES_TOP_P
    parallel_evaluation: bool = True
    max_workers: int = 3
    timeout_seconds: float = 60.0
    max_perspectives: int = 2  # Default: user + compliance (reduces cost and latency)
    # The critical perspectives (adversary, compliance) are essential to
    # detect potentially dangerous content that the user-centric
    # perspectives might not identify
    conservative_on_failure: bool = True  # DEPRECATED (no effect, removal in 3.0)

    # FIX PERFORMANCE: Caching to avoid recomputation on identical inputs
    enable_caching: bool = False  # Disabled by default: avoids global state in tests

    def __post_init__(self) -> None:
        # Not frozen: warns at construction only (not on later assignment); dataclasses.replace re-warns.
        default = True
        if self.conservative_on_failure != default:
            warnings.warn(
                "EnsembleConfig.conservative_on_failure is deprecated and has no effect; "
                "it will be removed in MoralStack 3.0.",
                DeprecationWarning,
                stacklevel=3,
            )


class LLMPerspectiveEnsemble:
    """
    LLM-based perspective ensemble.

    Evaluates responses from multiple cognitive perspectives to ensure
    a complete and balanced evaluation. Supports
    parallel evaluation for better performance.

    Attributes:
        policy: The Policy LLM used for generation
        config: Ensemble configuration
        perspectives: List of perspectives to use

    Usage:
        ensemble = LLMPerspectiveEnsemble(policy)
        result = ensemble.evaluate(request, response)

        print(f"Approval: {result.aggregation.weighted_approval}")
        print(f"Concerns: {result.aggregation.all_concerns}")

        if result.aggregation.recommendation == "revise":
            # Handle revision needed
            pass
    """

    def __init__(
        self,
        policy: PolicyLLMProtocol,
        config: EnsembleConfig | None = None,
        perspectives: list[Perspective] | None = None,
        prompt_set: PromptSet | None = None,
    ) -> None:
        """
        Initializes the Perspective Ensemble.

        Args:
            policy: Policy LLM for evaluation generation
            config: Configuration (optional)
            perspectives: List of perspectives (optional, uses defaults if None)
            prompt_set: PromptSet to read prompts, identities and retries from (default: packaged set)
        """
        self.policy = policy
        self._prompt_set = prompt_set or default_prompt_set()
        # Default perspectives built from THIS set (lookup by id for evaluate_single / set_perspectives).
        self._perspectives_by_id = {p.id: p for p in _build_default_perspectives(self._prompt_set)}
        if config is None:
            from moralstack.runtime.modules.perspective_config_loader import (
                load_perspective_config_from_env,
            )

            config = load_perspective_config_from_env()
        self.config = config
        # Use defaults only if perspectives is None, not if it is an empty list
        self.perspectives = list(self._perspectives_by_id.values()) if perspectives is None else list(perspectives)

        self._generation_config = GenerationConfig(
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            stop_sequences=[],
            response_format=perspective_response_format(),
        )

    def evaluate(
        self,
        request: str,
        response: str,
        perspectives: list[Perspective] | None = None,
        delib_context: Any = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> EnsembleResult:
        """
        Evaluates the response from all perspectives.

        FIX PERFORMANCE: Uses caching to avoid recomputation on identical inputs.
        If the (request, response) pair has already been evaluated, returns the cached result.

        Args:
            request: Original user request
            response: Response to evaluate
            perspectives: Specific perspectives (override)

        Returns:
            EnsembleResult with results and aggregation
        """
        # FIX PERFORMANCE: Check cache before evaluating
        prompt_ctx = PromptContext.from_contract(developer_contract, conversation_history)
        context_fingerprint = build_context_fingerprint(
            developer_contract=developer_contract,
            conversation_history=conversation_history,
            prompt_set_tag=self._prompt_set.cache_tag_for(prompt_ctx),
        )
        if self.config.enable_caching:
            cache = get_global_cache()
            cached_result = cache.get_perspective_result(
                request,
                response,
                context_fingerprint=context_fingerprint,
            )
            if isinstance(cached_result, EnsembleResult):
                return dataclass_replace(cached_result, from_cache=True, wire_calls=None)

        active_perspectives = perspectives or self.perspectives

        if not active_perspectives:
            return EnsembleResult.empty()

        # Limit the number of perspectives for speed
        if self.config.max_perspectives > 0 and len(active_perspectives) > self.config.max_perspectives:
            # Take the first N perspectives (priority to the most important)
            active_perspectives = active_perspectives[: self.config.max_perspectives]

        # OPT-2: build shared system once (REQUEST+RESPONSE+CONTRACT+HISTORY+common instructions)
        history_snippet = _build_history_snippet(conversation_history)
        contract_text = ""
        if developer_contract is not None:
            contract_text = (getattr(developer_contract, "raw_text", "") or "").strip()

        if delib_context is None:
            ctx = DelibContext(
                user_prompt=request,
                draft_text_full=response,
                conversation_history_snippet=history_snippet,
                developer_contract_text=contract_text,
            )
        else:
            updates: dict[str, Any] = {}
            current_snippet = getattr(delib_context, "conversation_history_snippet", "") or ""
            if history_snippet and not current_snippet and isinstance(delib_context, DelibContext):
                updates["conversation_history_snippet"] = history_snippet
            current_contract = getattr(delib_context, "developer_contract_text", "") or ""
            if contract_text and not current_contract and isinstance(delib_context, DelibContext):
                updates["developer_contract_text"] = contract_text
            if updates and isinstance(delib_context, DelibContext):
                ctx = dataclass_replace(delib_context, **updates)
            else:
                ctx = delib_context
        shared_system = (
            self._prompt_set.text("perspectives.base")
            + "\n\n"
            + self._prompt_set.render("perspectives.system_body", prompt_ctx)
        )

        if self.config.parallel_evaluation:
            result = self._evaluate_parallel(
                active_perspectives,
                shared_system,
                ctx,
                developer_contract=developer_contract,
                conversation_history=conversation_history,
            )
        else:
            result = self._evaluate_sequential(
                active_perspectives,
                shared_system,
                ctx,
                developer_contract=developer_contract,
                conversation_history=conversation_history,
            )

        # FIX PERFORMANCE: Save to cache for future use
        if self.config.enable_caching:
            cache = get_global_cache()
            cache.set_perspective_result(
                request,
                response,
                result,
                context_fingerprint=context_fingerprint,
            )

        return result

    def _evaluate_parallel(
        self,
        perspectives: list[Perspective],
        shared_system: str,
        context: DelibContext | None = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> EnsembleResult:
        """
        Evaluate perspectives in parallel.

        Prompt-caching reorder (A5a): shared_system is now ctx-independent
        (static prefix only); REQUEST/RESPONSE/risk signals are threaded via
        `context` into each per-perspective user message.
        """
        results: list[PerspectiveResult] = []
        raw_responses: list[str] = []
        failed_perspectives: list[str] = []

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_perspective = {
                # One context copy per worker: the observability ContextVars (run/request/cycle) must reach the
                # retry-failed rows written from inside the worker (NEW-1).
                executor.submit(
                    contextvars.copy_context().run,
                    self._evaluate_single_perspective,
                    shared_system,
                    perspective,
                    context=context,
                    developer_contract=developer_contract,
                    conversation_history=conversation_history,
                ): perspective
                for perspective in perspectives
            }

            # Collect the results with timeout handling
            try:
                for future in as_completed(future_to_perspective, timeout=self.config.timeout_seconds):
                    perspective = future_to_perspective[future]
                    try:
                        result = future.result(timeout=1.0)  # Timeout per single result
                        if result is not None:
                            results.append(result)
                            raw_responses.append(result.raw_response)
                        else:
                            failed_perspectives.append(perspective.id)
                    except Exception:
                        failed_perspectives.append(perspective.id)
                        raise
            except TimeoutError:
                for future, perspective in future_to_perspective.items():
                    if not future.done():
                        failed_perspectives.append(perspective.id)
                    else:
                        try:
                            result = future.result(timeout=0.1)
                            if result is not None:
                                results.append(result)
                                raw_responses.append(result.raw_response)
                        except Exception:
                            failed_perspectives.append(perspective.id)
                            raise

        # Aggregate results
        aggregation = self.aggregate(results, perspectives)

        prompts_list = [getattr(r, "prompt", "") for r in results]
        system_prompts_list = [getattr(r, "system_prompt", "") for r in results]
        prompt_tokens = _sum_optional_token_field(results, "prompt_tokens")
        completion_tokens = _sum_optional_token_field(results, "completion_tokens")
        cached_prompt_tokens = _sum_optional_token_field(results, "cached_prompt_tokens")
        combined_source = TokenUsage.combine(
            [
                TokenUsage(
                    r.prompt_tokens or 0,
                    r.completion_tokens or 0,
                    r.tokens_used,
                    r.token_usage_source,
                )
                for r in results
            ]
        ).source

        return EnsembleResult(
            results=results,
            aggregation=aggregation,
            raw_responses=raw_responses,
            evaluation_count=len(results),
            failed_perspectives=failed_perspectives,
            prompts=prompts_list,
            system_prompts=system_prompts_list,
            tokens_used=sum(int(getattr(r, "tokens_used", 0) or 0) for r in results),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            token_usage_source=combined_source,
            wire_calls=_ensemble_wire_calls(results),
        )

    def _evaluate_sequential(
        self,
        perspectives: list[Perspective],
        shared_system: str,
        context: DelibContext | None = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> EnsembleResult:
        """
        Evaluate perspectives sequentially.

        Prompt-caching reorder (A5a): shared_system is now ctx-independent
        (static prefix only); REQUEST/RESPONSE/risk signals are threaded via
        `context` into each per-perspective user message.
        """
        results: list[PerspectiveResult] = []
        raw_responses: list[str] = []
        failed_perspectives: list[str] = []

        for perspective in perspectives:
            result = self._evaluate_single_perspective(
                shared_system,
                perspective,
                context=context,
                developer_contract=developer_contract,
                conversation_history=conversation_history,
            )

            if result is not None:
                results.append(result)
                raw_responses.append(result.raw_response)
            else:
                failed_perspectives.append(perspective.id)

        # Aggregate results
        aggregation = self.aggregate(results, perspectives)

        prompts_list = [getattr(r, "prompt", "") for r in results]
        system_prompts_list = [getattr(r, "system_prompt", "") for r in results]
        prompt_tokens = _sum_optional_token_field(results, "prompt_tokens")
        completion_tokens = _sum_optional_token_field(results, "completion_tokens")
        cached_prompt_tokens = _sum_optional_token_field(results, "cached_prompt_tokens")
        combined_source = TokenUsage.combine(
            [
                TokenUsage(
                    r.prompt_tokens or 0,
                    r.completion_tokens or 0,
                    r.tokens_used,
                    r.token_usage_source,
                )
                for r in results
            ]
        ).source

        return EnsembleResult(
            results=results,
            aggregation=aggregation,
            raw_responses=raw_responses,
            evaluation_count=len(results),
            failed_perspectives=failed_perspectives,
            prompts=prompts_list,
            system_prompts=system_prompts_list,
            tokens_used=sum(int(getattr(r, "tokens_used", 0) or 0) for r in results),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            token_usage_source=combined_source,
            wire_calls=_ensemble_wire_calls(results),
        )

    def _evaluate_single_perspective(
        self,
        shared_system_prompt: str,
        perspective: Perspective,
        context: DelibContext | None = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> PerspectiveResult | None:
        """
        Evaluate the response from a single perspective.

        Prompt-caching reorder (A5a): shared_system_prompt is the static,
        ctx-independent prefix (JSON contract + common instructions); the
        per-request REQUEST/RESPONSE/risk signals are carried in the
        per-perspective user message built from `context` (optional, for
        backward compatibility with direct/low-level callers with no turn
        context — see build_perspectives_user_prompt).

        Args:
            shared_system_prompt: Static system prompt (JSON + common instructions).
            perspective: Perspective to use.
            context: DelibContext with request, draft, and risk signals for the
                dynamic TURN CONTEXT block of the user message.

        Returns:
            PerspectiveResult or None on failure after retries.
        """
        user_prompt = build_perspectives_user_prompt(
            perspective.name, perspective.prompt_template, context, prompt_set=self._prompt_set
        )
        retry_text = self._prompt_set.text("perspectives.retry")

        for attempt in range(self.config.max_retries):
            attempt_token_usage: TokenUsage | None = None
            sent: tuple[list[dict[str, str]], str] | None = None
            raw_response = ""
            try:
                effective_prompt = user_prompt if attempt == 0 else f"{user_prompt}\n\n{retry_text}"
                if (
                    (developer_contract is not None or conversation_history)
                    and "generate_messages" in dir(self.policy)
                    and callable(getattr(self.policy, "generate_messages", None))
                ):
                    messages = build_module_messages(
                        system_prompt=shared_system_prompt,
                        user_prompt=user_prompt,
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
                    sent = (generate_messages_for(effective_prompt, shared_system_prompt), "generate")
                    result = self.policy.generate(
                        prompt=effective_prompt,
                        system=shared_system_prompt,
                        config=self._generation_config,
                    )
                raw_response = result.text
                attempt_token_usage = TokenUsage.from_generation_result(result)
                pr = parse_perspective_response(raw_response, perspective)
                pr.prompt = effective_prompt
                pr.system_prompt = shared_system_prompt
                pr.tokens_used = attempt_token_usage.total_tokens
                pr.prompt_tokens = getattr(result, "prompt_tokens", None)
                pr.completion_tokens = getattr(result, "completion_tokens", None)
                pr.cached_prompt_tokens = attempt_token_usage.cached_input_tokens
                pr.token_usage_source = attempt_token_usage.source
                # Transient audit data, not a dataclass field: the ensemble turns it into the row's wire_calls.
                pr._sent_wire = sent  # type: ignore[attr-defined]
                return pr
            except (JSONParseError, Exception) as e:
                if attempt_token_usage is not None:
                    try:
                        from moralstack.observability.emit_helpers import async_persist_llm_call

                        retry_reason = f"Perspective {perspective.id}/{perspective.name} retry reason: {str(e)[:200]}"
                        summary: dict[str, Any] = {"perspective_id": perspective.id, "perspective_name": perspective.name}
                        summary.update(inline_wire_summary(sent, retry_reason))
                        async_persist_llm_call(
                            phase="perspectives_retry",
                            module="perspectives",
                            action=f"retry_failed_attempt_{attempt + 1}",
                            model=getattr(self.policy, "model", None) or "",
                            prompt=retry_reason,
                            raw_response=raw_response or "",
                            duration_ms=0.0,
                            attempts=attempt + 1,
                            call_outcome="retry_failed",
                            billable_provider_call=True,
                            token_usage_json=attempt_token_usage.to_json(),
                            # Additive: WHO failed (the UI names the perspective on the node).
                            parsed_summary_json=json.dumps(summary, ensure_ascii=False),
                        )
                    except Exception:
                        pass
                continue
        return None

    def evaluate_single(
        self,
        request: str,
        response: str,
        perspective_id: str,
    ) -> PerspectiveResult | None:
        """
        Evaluate the response from a single perspective by ID.

        Args:
            request: Original user request.
            response: Response to evaluate.
            perspective_id: Perspective ID (e.g. "user", "vulnerable").

        Returns:
            PerspectiveResult or None if perspective not found or evaluation fails.
        """
        # Instance perspectives first, then the defaults built from THIS instance's set
        # (never the module-level PERSPECTIVES_BY_ID, which belongs to the packaged set).
        perspective = next((p for p in self.perspectives if p.id == perspective_id), None)
        if perspective is None:
            perspective = self._perspectives_by_id.get(perspective_id)
        if perspective is None:
            return None

        ctx = DelibContext(user_prompt=request, draft_text_full=response)
        shared_system = (
            self._prompt_set.text("perspectives.base")
            + "\n\n"
            + self._prompt_set.render("perspectives.system_body", NO_CONTEXT)
        )
        return self._evaluate_single_perspective(shared_system, perspective, context=ctx)

    def aggregate(
        self,
        results: list[PerspectiveResult],
        perspectives: list[Perspective] | None = None,
    ) -> PerspectiveAggregation:
        """
        Aggregates the evaluation results.

        Computes the weighted average of the approval scores, merges concerns
        and suggestions removing duplicates.

        Args:
            results: List of results to aggregate
            perspectives: Perspectives for weight lookup (optional)

        Returns:
            PerspectiveAggregation
        """
        if not results:
            return PerspectiveAggregation.empty()

        # Build weights map
        weight_map: dict[str, float] = {}
        if perspectives:
            weight_map = {p.id: p.weight for p in perspectives}
        else:
            # Use weights from default perspectives
            weight_map = {p.id: p.weight for p in self.perspectives}
            # Add weights from the defaults (of this set) as fallback
            for pid, p in self._perspectives_by_id.items():
                if pid not in weight_map:
                    weight_map[pid] = p.weight

        # Compute weighted average
        total_weight = 0.0
        weighted_sum = 0.0

        for result in results:
            weight = weight_map.get(result.perspective_id, 1.0)
            weighted_sum += result.approval_score * weight
            total_weight += weight

        weighted_approval = weighted_sum / total_weight if total_weight > 0 else 0.5

        # Find min/max
        scores = [r.approval_score for r in results]
        min_approval = min(scores)
        max_approval = max(scores)

        # Compute dissent level (normalized variance)
        dissent_level = max_approval - min_approval

        # Merge concerns (deduplicate preserving order)
        all_concerns = self._deduplicate_strings([concern for result in results for concern in result.concerns])

        # Merge suggestions (deduplicate preserving order)
        all_suggestions = self._deduplicate_strings([suggestion for result in results for suggestion in result.suggestions])

        return PerspectiveAggregation(
            weighted_approval=weighted_approval,
            min_approval=min_approval,
            max_approval=max_approval,
            all_concerns=all_concerns,
            all_suggestions=all_suggestions,
            perspective_count=len(results),
            dissent_level=dissent_level,
        )

    def _deduplicate_strings(self, items: list[str]) -> list[str]:
        """
        Deduplicates a list of strings preserving the order.

        Uses case-insensitive comparison for deduplication but
        keeps the original case.
        """
        seen: set[str] = set()
        result: list[str] = []

        for item in items:
            item_lower = item.lower().strip()
            if item_lower and item_lower not in seen:
                seen.add(item_lower)
                result.append(item.strip())

        return result

    def add_perspective(self, perspective: Perspective) -> None:
        """
        Adds a perspective to the ensemble.

        Args:
            perspective: Perspective to add
        """
        # Remove existing one with the same ID
        self.perspectives = [p for p in self.perspectives if p.id != perspective.id]
        self.perspectives.append(perspective)

    def remove_perspective(self, perspective_id: str) -> bool:
        """
        Removes a perspective from the ensemble.

        Args:
            perspective_id: ID of the perspective to remove

        Returns:
            True if removed, False if not found
        """
        original_len = len(self.perspectives)
        self.perspectives = [p for p in self.perspectives if p.id != perspective_id]
        return len(self.perspectives) < original_len

    def set_perspectives(self, perspective_ids: list[str]) -> None:
        """
        Sets the active perspectives through a list of IDs.

        Uses the default perspectives built from the instance's PromptSet.

        Args:
            perspective_ids: List of IDs to activate
        """
        self.perspectives = [self._perspectives_by_id[pid] for pid in perspective_ids if pid in self._perspectives_by_id]

    def get_active_perspectives(self) -> list[str]:
        """Returns the list of IDs of the active perspectives."""
        return [p.id for p in self.perspectives]


# =============================================================================
# Factory Functions
# =============================================================================


def create_perspective_ensemble(
    policy: PolicyLLMProtocol,
    max_retries: int = 3,
    temperature: float = 0.3,
    parallel: bool = True,
    perspective_ids: list[str] | None = None,
    config: EnsembleConfig | None = None,
    prompt_set: PromptSet | None = None,
) -> LLMPerspectiveEnsemble:
    """
    Factory function for creating a Perspective Ensemble.

    Args:
        policy: Policy LLM to use
        max_retries: Maximum attempts for JSON parsing (ignored if config is provided)
        temperature: Temperature for generation (ignored if config is provided)
        parallel: If True, parallel evaluation (ignored if config is provided)
        perspective_ids: List of perspective IDs to use (default: all)
        config: Optional EnsembleConfig; if provided, overrides kwargs. If None, built from kwargs.
        prompt_set: PromptSet to read prompts and perspective identities from (default: packaged set)

    Returns:
        Configured LLMPerspectiveEnsemble
    """
    if config is None:
        config = EnsembleConfig(
            max_retries=max_retries,
            temperature=temperature,
            parallel_evaluation=parallel,
        )

    # Select perspectives
    perspectives = None
    if perspective_ids:
        by_id = {p.id: p for p in _build_default_perspectives(prompt_set)}
        perspectives = [by_id[pid] for pid in perspective_ids if pid in by_id]

    return LLMPerspectiveEnsemble(
        policy=policy,
        config=config,
        perspectives=perspectives,
        prompt_set=prompt_set,
    )


def create_minimal_ensemble(
    policy: PolicyLLMProtocol,
    config: EnsembleConfig | None = None,
    prompt_set: PromptSet | None = None,
) -> LLMPerspectiveEnsemble:
    """
    Creates a minimal ensemble with 2 key perspectives.

    Useful for quick evaluations with less overhead (reduces cost and latency).
    When config is None, loads EnsembleConfig from environment (MORALSTACK_PERSPECTIVES_*).

    Args:
        policy: Policy LLM to use
        config: Optional EnsembleConfig; if None, loaded from env via perspective_config_loader.
        prompt_set: PromptSet to read prompts and perspective identities from (default: packaged set)

    Returns:
        LLMPerspectiveEnsemble with perspectives: user, compliance
    """
    if config is None:
        from moralstack.runtime.modules.perspective_config_loader import (
            load_perspective_config_from_env,
        )

        config = load_perspective_config_from_env()
    return create_perspective_ensemble(
        policy=policy,
        config=config,
        parallel=True,
        perspective_ids=["user", "compliance"],
        prompt_set=prompt_set,
    )


def apply_constitutional_override(
    aggregation: Union[PerspectiveAggregation, EnsembleResult],
    critic_result: Any,
) -> Union[PerspectiveAggregation, EnsembleResult]:
    """
    Override weighted approval when the Critic reports HARD constitutional violations.

    Perspectives cannot approve content that the Constitution forbids; the Critic
    has structural priority. Accepts either PerspectiveAggregation or EnsembleResult
    (in which case the inner aggregation is modified in place).

    Args:
        aggregation: Perspective aggregation, or full EnsembleResult (unwrap applied).
        critic_result: Critic result (CriticReport or compatible).

    Returns:
        The same object passed in (EnsembleResult or PerspectiveAggregation), with
        inner aggregation potentially modified (weighted_approval capped to 0.2).
    """
    if critic_result is None:
        return aggregation

    # Unwrap EnsembleResult so we always operate on PerspectiveAggregation
    inner = aggregation.aggregation if hasattr(aggregation, "aggregation") else aggregation

    # Check HARD violations: explicit flag or iteration over violations
    has_hard_violation = getattr(critic_result, "violated_hard", False)
    if not has_hard_violation:
        violations = getattr(critic_result, "violations", [])
        for v in violations:
            if getattr(v, "constraint_type", "") == "hard":
                has_hard_violation = True
                break

    if not has_hard_violation:
        return aggregation

    # Override: cap weighted_approval to 0.2
    if inner.weighted_approval > 0.2:
        logging.getLogger(__name__).info(
            "Perspective override applied due to HARD constitutional violation: weighted_approval capped from %.2f to 0.20",
            inner.weighted_approval,
        )
        inner.weighted_approval = min(inner.weighted_approval, 0.2)
        inner.all_concerns.append("Constitutional HARD violation overrides user approval.")

    return aggregation


def create_safety_focused_ensemble(
    policy: PolicyLLMProtocol,
    prompt_set: PromptSet | None = None,
) -> LLMPerspectiveEnsemble:
    """
    Creates a safety-focused ensemble.

    Includes perspectives with greater attention to risks.

    Args:
        policy: Policy LLM to use
        prompt_set: PromptSet to read prompts from (default: packaged set)

    Returns:
        LLMPerspectiveEnsemble with perspectives: vulnerable, adversary, compliance
    """
    return create_perspective_ensemble(
        policy=policy,
        parallel=True,
        perspective_ids=["vulnerable", "adversary", "compliance"],
        prompt_set=prompt_set,
    )
