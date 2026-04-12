"""
Public response types for the MoralStack SDK.

GovernanceMetadata: immutable snapshot of deliberation.
GovernedResponse: governed output combining OpenAI response + governance metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
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

    @classmethod
    def from_result(cls, result: OrchestratorResult) -> GovernanceMetadata:
        """
        Build from an OrchestratorResult. Single mapping point between
        internal types and the public SDK interface.
        """
        meta = result.response.metadata
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
    """True if the response bypassed governance (pipeline failure + failure_policy='passthrough')."""

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
        """Model used for generation. None for refusal responses."""
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
    def from_safe(cls, openai_resp: Any, result: OrchestratorResult) -> GovernedResponse:
        """Build a SAFE_COMPLETE response (OpenAI call with injected guidance)."""
        return cls(
            openai_response=openai_resp,
            governance_metadata=GovernanceMetadata.from_result(result),
        )

    @classmethod
    def from_passthrough(cls, openai_resp: Any, error: Exception) -> GovernedResponse:
        """
        Build a passthrough response (pipeline failed, failure_policy='passthrough').
        governance_metadata is empty/sentinel.
        """
        sentinel_meta = GovernanceMetadata(
            final_action="PASSTHROUGH",
            risk_score=0.0,
            risk_category="UNKNOWN",
            path="NONE",
            domain_overlay=None,
            reason_codes=["PIPELINE_ERROR"],
            winning_rule="",
            decision_reason=f"Pipeline error (passthrough): {error}",
            processing_time_ms=0,
            deliberation_cycles=0,
            triggered_principles=[],
            why_not_refuse="",
            why_not_safe_complete="",
            conversation_id=None,
            turn_index=None,
        )
        return cls(
            openai_response=openai_resp,
            governance_metadata=sentinel_meta,
            is_passthrough=True,
        )

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
