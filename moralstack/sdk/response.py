"""
Public response types for the MoralStack SDK.

GovernanceMetadata: immutable snapshot of deliberation.
GovernedResponse: governed output combining OpenAI response + governance metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from moralstack.orchestration.delivery import GovernedDelivery
    from moralstack.orchestration.types import OrchestratorResult


# =============================================================================
# GovernanceMetadata
# =============================================================================


@dataclass(frozen=True)
class GovernanceMetadata:
    """
    User-readable governance metadata.

    Curated subset of ResponseMetadata, plus session fields.
    Immutable: captures the deliberation outcome.

    Example::

        response = client.chat.completions.create(...)
        meta = response.governance_metadata
        print(meta.final_action)      # NORMAL_COMPLETE | SAFE_COMPLETE | REFUSE
        print(meta.risk_score)        # 0.0 - 1.0
        print(meta.reason_codes)      # ["SENSITIVE_DOMAIN", "DUAL_USE", ...]
    """

    final_action: str
    """Decision action: NORMAL_COMPLETE | SAFE_COMPLETE | REFUSE."""

    risk_score: float
    """Normalized risk score 0.0 (benign) - 1.0 (harmful)."""

    risk_category: str
    """Category: CLEARLY_BENIGN | SENSITIVE | CLEARLY_HARMFUL."""

    path: str
    """Processing path: FAST_PATH | DELIBERATIVE_PATH."""

    domain_overlay: str | None
    """Active domain overlay (e.g. 'healthcare'), or None."""

    reason_codes: list[str]
    """Machine-readable decision codes (e.g. ['DUAL_USE', 'SENSITIVE_DOMAIN'])."""

    winning_rule: str
    """Rule that determined the final decision."""

    decision_reason: str
    """Human-readable explanation of the decision."""

    processing_time_ms: int
    """Deliberation latency in milliseconds."""

    deliberation_cycles: int
    """Number of deliberation cycles (0 for FAST_PATH)."""

    triggered_principles: list[str]
    """Constitutional principles triggered during deliberation."""

    why_not_refuse: str
    """Counterfactual reasoning: why REFUSE was not chosen."""

    why_not_safe_complete: str
    """Counterfactual reasoning: why SAFE_COMPLETE was not chosen."""

    # Session (prepares Level 2)
    conversation_id: str | None
    """Conversation identifier. None if session tracking is disabled."""

    turn_index: int | None
    """Turn index in the conversation. None if session tracking is disabled."""

    # DCCL fields (populated when contract is present)
    compliance_decision: str | None = None
    """DCCL verdict: MATCH | NO_MATCH | SAFETY_OVERRIDE | NO_CONTRACT."""

    matched_rule_id: str | None = None
    """Rule id when DCCL decision is MATCH."""

    matched_rule_summary: str | None = None
    """Human-readable matched rule summary."""

    compliance_evaluation_path: str | None = None
    """DCCL evaluation path: STRUCTURED | LLM | HYBRID | SKIPPED."""

    compliance_confidence: float | None = None
    """DCCL confidence score."""

    safety_override_reason: str | None = None
    """Safety override category when DCCL decision is SAFETY_OVERRIDE."""

    @classmethod
    def from_result(cls, result: OrchestratorResult) -> GovernanceMetadata:
        """
        Build from an OrchestratorResult. Single mapping point between
        internal types and the public SDK interface.
        """
        meta = result.response.metadata
        cv = getattr(result, "compliance_verdict", None)
        compliance_decision = cv.decision.value if cv else None
        matched_rule_id = cv.matched_rule.rule_id if (cv and cv.matched_rule) else None
        matched_rule_summary = cv.matched_rule.rule_summary if (cv and cv.matched_rule) else None
        compliance_evaluation_path = cv.evaluation_path.value if cv else None
        compliance_confidence = cv.confidence if cv else None
        safety_override_reason = cv.safety_override_reason if cv else None
        return cls(
            final_action=meta.final_action,
            risk_score=meta.risk_score,
            risk_category=meta.risk_category,
            path=meta.path,
            domain_overlay=meta.domain_overlay,
            reason_codes=list(meta.reason_codes),
            winning_rule=meta.winning_rule,
            decision_reason=meta.decision_reason,
            processing_time_ms=meta.processing_time_ms,
            deliberation_cycles=meta.deliberation_cycles,
            triggered_principles=list(meta.triggered_principles),
            why_not_refuse=meta.why_not_refuse,
            why_not_safe_complete=meta.why_not_safe_complete,
            conversation_id=result.conversation_id,
            turn_index=result.turn_index,
            compliance_decision=compliance_decision,
            matched_rule_id=matched_rule_id,
            matched_rule_summary=matched_rule_summary,
            compliance_evaluation_path=compliance_evaluation_path,
            compliance_confidence=compliance_confidence,
            safety_override_reason=safety_override_reason,
        )


# =============================================================================
# Synthetic types for OpenAI interface compatibility
# =============================================================================


@dataclass
class _SyntheticMessage:
    """Mimics ChatCompletionMessage for REFUSE cases (no OpenAI response)."""

    content: str
    role: str = "assistant"
    function_call: Any = None
    tool_calls: Any = None


@dataclass
class _SyntheticChoice:
    """Mimics ChatCompletionChoice for REFUSE cases (no OpenAI response)."""

    message: _SyntheticMessage
    finish_reason: str = "stop"
    index: int = 0
    logprobs: Any = None

    def __init__(self, content: str) -> None:
        self.message = _SyntheticMessage(content=content)
        self.finish_reason = "stop"
        self.index = 0
        self.logprobs = None


# =============================================================================
# GovernedResponse
# =============================================================================


@dataclass
class GovernedResponse:
    """
    Governed response: combines OpenAI output (if any) with governance metadata.

    Compatible with ChatCompletion for common cases::

        response.content                        # response text
        response.choices[0].message.content    # ChatCompletion compatibility
        response.governance_metadata           # deliberation metadata

    For REFUSE there is no call to the OpenAI client; choices contains
    a synthetic choice with the refusal text.
    """

    openai_response: Any | None
    """Original OpenAI client response. None for REFUSE or pipeline error."""

    governance_metadata: GovernanceMetadata
    """MoralStack deliberation metadata."""

    governance_content: str | None = None
    """Content produced by governance (refusal text for REFUSE). None otherwise."""

    is_passthrough: bool = False
    """
    Deprecated and always False. Passthrough delivery (returning wrapped-client
    output without governance) was removed in Plan 1: no delivered answer is ever
    generated by the wrapped/upstream client. Retained only so older readers that
    inspect the attribute do not break.
    """

    requested_model: str = ""
    """
    The ``model`` value supplied by the caller in ``chat.completions.create(model=...)``.

    OpenAI-compatibility / alias field only. After Plan 1 it does NOT select the
    model that generates the delivered MoralStack answer; use
    ``GovernanceConfig.model`` / ``OPENAI_MODEL`` for governed generation.
    """

    generation_model: str | None = None
    """
    The actual MoralStack policy model used for governed first-pass / speculative /
    compliance generation. This is the authoritative model for audit.
    """

    rewrite_model: str | None = None
    """
    The actual MoralStack policy model used for governed revisions
    (``MORALSTACK_POLICY_REWRITE_MODEL`` when set, otherwise the primary policy model).
    """

    @property
    def content(self) -> str:
        """Response text from whichever source applies."""
        if self.governance_content is not None:
            return self.governance_content
        if self.openai_response is not None:
            choices = getattr(self.openai_response, "choices", [])
            if choices:
                return getattr(choices[0].message, "content", "") or ""
        return ""

    @property
    def choices(self) -> list[Any]:
        """Compatibility with ChatCompletion.choices."""
        if self.openai_response is not None:
            return cast(list[Any], self.openai_response.choices)
        return [_SyntheticChoice(self.governance_content or "")]

    @property
    def model(self) -> str | None:
        """
        Model attribution for the delivered answer.

        Returns the actual MoralStack ``generation_model`` when known (governed
        delivery). Otherwise it echoes the upstream OpenAI-compatibility model
        field, or None for legacy refusal responses. The authoritative audit
        value is always ``generation_model``; this property exists for
        OpenAI-SDK shape compatibility.
        """
        if self.generation_model:
            return self.generation_model
        if self.openai_response is not None:
            return getattr(self.openai_response, "model", None)
        return None

    @property
    def usage(self) -> Any | None:
        """Token usage. None for refusal responses."""
        if self.openai_response is not None:
            return getattr(self.openai_response, "usage", None)
        return None

    # --- Factory methods ---

    @classmethod
    def from_refusal(cls, result: OrchestratorResult) -> GovernedResponse:
        """Build a refusal response (no call to the OpenAI client)."""
        return cls(
            openai_response=None,
            governance_metadata=GovernanceMetadata.from_result(result),
            governance_content=result.response.content,
        )

    @classmethod
    def from_normal(cls, openai_resp: Any, result: OrchestratorResult) -> GovernedResponse:
        """Build a NORMAL_COMPLETE response (direct OpenAI call)."""
        return cls(
            openai_response=openai_resp,
            governance_metadata=GovernanceMetadata.from_result(result),
        )

    @classmethod
    def from_governed_text(
        cls,
        result: OrchestratorResult,
        delivery: GovernedDelivery,
        *,
        requested_model: str = "",
        generation_model: str | None = None,
        rewrite_model: str | None = None,
    ) -> GovernedResponse:
        """
        Build a response that delivers already-governed pipeline text directly,
        with no call to the wrapped/upstream OpenAI client (Plan 1 invariant).

        ``delivery`` is the :class:`GovernedDelivery` computed by
        ``finalize_delivery`` for an already-governed ``OrchestratorResult``
        (NORMAL_COMPLETE, SAFE_COMPLETE, governed refusal, or a blank-content
        fail-closed refusal). The delivered text is ``delivery.text``.
        """
        metadata = GovernanceMetadata.from_result(result)
        if metadata.final_action != delivery.final_action:
            metadata = replace(metadata, final_action=delivery.final_action)
        return cls(
            openai_response=None,
            governance_metadata=metadata,
            governance_content=delivery.text,
            requested_model=requested_model,
            generation_model=generation_model,
            rewrite_model=rewrite_model,
        )

    @classmethod
    def from_governed_draft(cls, result: OrchestratorResult) -> GovernedResponse:
        """
        Deprecated. Compliance-fast-path drafts are delivered through
        :meth:`from_governed_text` now. Retained as a thin alias so any external
        caller keeps working; it delivers ``result.response.content`` directly
        with no wrapped-client call.
        """
        return cls(
            openai_response=None,
            governance_metadata=GovernanceMetadata.from_result(result),
            governance_content=result.response.content,
        )

    @classmethod
    def from_safe(cls, openai_resp: Any, result: OrchestratorResult) -> GovernedResponse:
        """Build a SAFE_COMPLETE response (OpenAI call with injected guidance)."""
        return cls(
            openai_response=openai_resp,
            governance_metadata=GovernanceMetadata.from_result(result),
        )

    @classmethod
    def from_passthrough(cls, openai_resp: Any, error: Exception) -> GovernedResponse:
        """
        Deprecated fail-closed alias. Passthrough delivery was removed in Plan 1:
        no delivered answer is ever generated by the wrapped/upstream client.

        This now returns a fail-closed governed refusal and never sets
        ``is_passthrough=True`` nor exposes the wrapped-client response.
        """
        import warnings

        warnings.warn(
            "GovernedResponse.from_passthrough is deprecated; passthrough delivery "
            "was removed (Plan 1). Returning a fail-closed governed refusal.",
            DeprecationWarning,
            stacklevel=2,
        )
        del openai_resp
        return cls.from_pipeline_error(error)

    @classmethod
    def from_pipeline_error(cls, error: Exception) -> GovernedResponse:
        """
        Build an error response (pipeline failed, failure_policy='refuse').
        """
        sentinel_meta = GovernanceMetadata(
            final_action="REFUSE",
            risk_score=1.0,
            risk_category="UNKNOWN",
            path="NONE",
            domain_overlay=None,
            reason_codes=["PIPELINE_ERROR"],
            winning_rule="pipeline_error",
            decision_reason=f"Pipeline error: {error}",
            processing_time_ms=0,
            deliberation_cycles=0,
            triggered_principles=[],
            why_not_refuse="",
            why_not_safe_complete="",
            conversation_id=None,
            turn_index=None,
        )
        return cls(
            openai_response=None,
            governance_metadata=sentinel_meta,
            governance_content="I'm unable to process this request at the moment.",
        )
