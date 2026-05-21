"""
Data types for the Developer Contract Compliance Layer.

All types are frozen dataclasses or string enums for safe propagation through
the request context and for JSON-serialization in observability logs.

Reference: dccl_specification_v0.3.md sections 2, 4.1, 7.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# =============================================================================
# Enums
# =============================================================================


class ComplianceDecision(str, Enum):
    """The four possible verdicts of a DCCL evaluation."""

    MATCH = "MATCH"
    """Request invokes an authorized rule; pipeline defers to DCCL."""

    NO_MATCH = "NO_MATCH"
    """Request does not invoke any rule; pipeline continues normally."""

    SAFETY_OVERRIDE = "SAFETY_OVERRIDE"
    """A rule matches but its output is safety-restricted; pipeline takes over."""

    NO_CONTRACT = "NO_CONTRACT"
    """No developer contract in the request; DCCL has no effect."""


class EvaluationPath(str, Enum):
    """The path used by DCCL to reach its verdict."""

    STRUCTURED = "structured"
    """Used structured_rules (fast, deterministic)."""

    LLM = "llm"
    """Used raw_text via LLM call (slow, flexible)."""

    HYBRID = "hybrid"
    """Tried structured first, fell back to LLM."""

    SKIPPED = "skipped"
    """DCCL did not evaluate (e.g. NO_CONTRACT)."""


class TriggerType(str, Enum):
    """How a rule's trigger pattern is matched against the user prompt."""

    LITERAL = "literal"
    """Exact string equality match."""

    REGEX = "regex"
    """Regex full-match pattern."""

    SEMANTIC = "semantic"
    """LLM-based semantic match (delegates to LLM path)."""


class ActionType(str, Enum):
    """What the rule produces when matched."""

    EMIT = "emit"
    """Produce the action_payload as response."""

    REFUSE = "refuse"
    """Decline with a deployer-provided message."""

    REDIRECT = "redirect"
    """Defer to another rule or to standard pipeline."""


# =============================================================================
# Structured rule (deployer-declarable)
# =============================================================================


@dataclass(frozen=True)
class StructuredRule:
    """
    A deployer-declared rule, used by the structured path of DCCL.

    A contract can carry zero or more StructuredRule instances. When present,
    the structured path tries to match the user prompt against each rule's
    trigger; on match, the rule's action_payload is produced as the response.

    Fields:
        rule_id: stable identifier (deployer-defined).
        trigger_pattern: the pattern to match against the user prompt.
        trigger_type: how to interpret trigger_pattern.
        action_type: what to do on match.
        action_payload: the content/text to emit/refuse/redirect with.
        description: human-readable description (for audit and UI).
        priority: integer used to disambiguate when multiple rules match.
            Higher priority wins. Default 50.

    Validation:
        - rule_id, trigger_pattern, action_payload must be non-empty strings.
        - priority must be in [0, 100].
        - action_payload is safety-checked at contract loading time (not here).
    """

    rule_id: str
    trigger_pattern: str
    trigger_type: TriggerType = TriggerType.LITERAL
    action_type: ActionType = ActionType.EMIT
    action_payload: str = ""
    description: str = ""
    priority: int = 50

    def __post_init__(self) -> None:
        if not self.rule_id or not self.rule_id.strip():
            raise ValueError("StructuredRule.rule_id must be non-empty")
        if not self.trigger_pattern:
            raise ValueError("StructuredRule.trigger_pattern must be non-empty")
        if self.action_type != ActionType.REDIRECT and not self.action_payload:
            raise ValueError(f"StructuredRule.action_payload must be non-empty for action_type={self.action_type.value}")
        if not (0 <= self.priority <= 100):
            raise ValueError(f"StructuredRule.priority must be in [0, 100], got {self.priority}")


# =============================================================================
# Matched rule (verdict-side, populated when DCCL returns MATCH)
# =============================================================================


@dataclass(frozen=True)
class MatchedRule:
    """
    Information about the rule that matched, for audit and downstream modules.

    Populated only when ComplianceVerdict.decision == MATCH.

    Fields:
        rule_id: the rule_id from StructuredRule (or synthesized for LLM path).
        rule_summary: short human-readable description.
        rule_excerpt: relevant excerpt from raw_text (LLM path) or trigger_pattern
            (structured path). Used in audit logs and UI.
        action_payload_summary: what the rule produces, truncated for audit.
    """

    rule_id: str
    rule_summary: str
    rule_excerpt: str
    action_payload_summary: str = ""


# =============================================================================
# Compliance verdict (output of DCCL.evaluate)
# =============================================================================


@dataclass(frozen=True)
class ComplianceVerdict:
    """
    Output of DeveloperContractComplianceLayer.evaluate().

    Fields:
        decision: the verdict (one of the 4 ComplianceDecision values).
        matched_rule: populated when decision == MATCH.
        safety_override_reason: populated when decision == SAFETY_OVERRIDE.
            One of the SAFETY_OVERRIDE_CATEGORIES strings.
        confidence: float in [0.0, 1.0]. For STRUCTURED path always 1.0.
            For LLM path comes from the LLM verdict.
        rationale: human-readable explanation of the decision.
        evaluation_path: which path produced this verdict.
        duration_ms: time spent on the evaluation (for observability).
        contract_hash: the contract_hash of the evaluated contract (for cache key).
        speculative_draft_validated: True if the speculative_draft matches the rule's
            expected output (only meaningful for MATCH).
        draft_match_method: how draft validation succeeded — "substring", "semantic",
            or "none" when not validated.
        draft_match_confidence: semantic draft-match confidence from the LLM verdict
            (0.0 when substring matched or not validated).
        degraded: True when the verdict is usable but quality gates were not fully met
            (e.g. soft timeout exceeded or confidence below threshold). Decision is preserved.
        degraded_reason: non-empty when degraded is True — ``llm_timeout`` or ``low_confidence``.
    """

    decision: ComplianceDecision
    matched_rule: MatchedRule | None = None
    safety_override_reason: str | None = None
    confidence: float = 0.0
    rationale: str = ""
    evaluation_path: EvaluationPath = EvaluationPath.SKIPPED
    duration_ms: float = 0.0
    contract_hash: str = ""
    speculative_draft_validated: bool = False
    draft_match_method: str = ""
    draft_match_confidence: float = 0.0
    degraded: bool = False
    degraded_reason: str = ""

    def is_match(self) -> bool:
        """True if the verdict authorizes the pipeline to defer to DCCL."""
        return self.decision == ComplianceDecision.MATCH

    def is_safety_override(self) -> bool:
        """True if a rule matched but was rejected due to safety."""
        return self.decision == ComplianceDecision.SAFETY_OVERRIDE


# =============================================================================
# Compliance signal (propagated to downstream modules via request context)
# =============================================================================


@dataclass(frozen=True)
class ComplianceSignal:
    """
    Signal attached to the request context when DCCL returns a non-NO_CONTRACT verdict.

    Downstream modules (risk_estimator, critic, simulator, perspectives) check
    for this signal at their entry point. When decision == MATCH, they return
    early with a synthetic "deferred" result.

    For SAFETY_OVERRIDE and NO_MATCH, the signal is informational (for audit),
    and modules continue normally.

    Fields mirror a subset of ComplianceVerdict, plus a timestamp.
    """

    decision: ComplianceDecision
    matched_rule_id: str | None = None
    matched_rule_summary: str | None = None
    safety_override_reason: str | None = None
    speculative_draft_validated: bool = False
    confidence: float = 0.0
    evaluation_path: EvaluationPath = EvaluationPath.SKIPPED
    timestamp_ms: int = 0

    @classmethod
    def from_verdict(cls, verdict: ComplianceVerdict, timestamp_ms: int) -> ComplianceSignal:
        """Build a ComplianceSignal from a ComplianceVerdict."""
        return cls(
            decision=verdict.decision,
            matched_rule_id=verdict.matched_rule.rule_id if verdict.matched_rule else None,
            matched_rule_summary=verdict.matched_rule.rule_summary if verdict.matched_rule else None,
            safety_override_reason=verdict.safety_override_reason,
            speculative_draft_validated=verdict.speculative_draft_validated,
            confidence=verdict.confidence,
            evaluation_path=verdict.evaluation_path,
            timestamp_ms=timestamp_ms,
        )
