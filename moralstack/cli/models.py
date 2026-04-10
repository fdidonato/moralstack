"""
CLI data models and trace parsers for MoralStack.

Phase types, decision reasons, trace structures, and call parsing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PhaseType(Enum):
    """Phase types in the deliberative process."""

    RISK_ESTIMATION = "risk_estimation"
    PATH_DECISION = "path_decision"
    GENERATION = "generation"
    REVISION = "revision"
    CRITIQUE = "critique"
    SIMULATION = "simulation"
    HINDSIGHT = "hindsight"
    PERSPECTIVES = "perspectives"
    CONVERGENCE_CHECK = "convergence_check"
    ASSEMBLY = "assembly"


class DecisionReason(Enum):
    """Decision reasons in the flow."""

    LOW_RISK = "Low risk (< 0.3): no in-depth analysis needed"
    MEDIUM_RISK = "Medium risk (0.3-0.7): requires constitutional validation"
    HIGH_RISK = "High risk (> 0.7): full deliberation required"
    REFUSAL_POLICY = "Policy refusal: response refused (path_reason never LOW_RISK)"
    CRITICAL_VIOLATION = "Critical violation detected: requires revision or refusal"
    SOFT_VIOLATION = "Soft violation detected: requires caveat or revision"
    NO_VIOLATIONS = "No violations: response compliant with constitution"
    HINDSIGHT_POSITIVE = "Positive hindsight evaluation: acceptable consequences"
    HINDSIGHT_NEGATIVE = "Negative hindsight evaluation: potentially harmful consequences"
    MAX_CYCLES_REACHED = "Maximum deliberative cycles reached"
    CONVERGED = "Convergence reached: response satisfies all criteria"
    TIMEOUT = "Timeout reached: processing interrupted"
    ERROR = "Error during processing"


def path_reason_from_risk_and_action(risk_score: float, final_action: str = "") -> str:
    """
    Determines path_reason ONLY from risk_score (path_taken is never used).
    - score < 0.3  -> LOW_RISK
    - 0.3 <= score < 0.7 -> MEDIUM_RISK
    - score >= 0.7 -> HIGH_RISK
    If final_action == REFUSE, path_reason cannot be LOW_RISK: it is forced to
    HIGH_RISK (if risk_score >= 0.7) or REFUSAL_POLICY (otherwise).
    """
    fa = (final_action or "").strip().upper()
    if fa == "REFUSE":
        if risk_score >= 0.7:
            return DecisionReason.HIGH_RISK.value
        return DecisionReason.REFUSAL_POLICY.value
    if risk_score < 0.3:
        return DecisionReason.LOW_RISK.value
    if risk_score < 0.7:
        return DecisionReason.MEDIUM_RISK.value
    return DecisionReason.HIGH_RISK.value


@dataclass
class PhaseResult:
    """Result of a single phase."""

    phase: PhaseType
    cycle: int
    duration_ms: float
    success: bool
    input_summary: str
    output_summary: str
    decision: Optional[str] = None
    decision_reason: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DraftRevision:
    """A single draft revision."""

    cycle: int
    draft_text: str
    guidance_used: str
    is_initial: bool = False  # True if this is the first generation


@dataclass
class TraceParseResult:
    """Result of parsing a single call into trace phase data."""

    phase_type: PhaseType
    decision: Optional[str] = None
    decision_reason: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_score: Optional[float] = None
    risk_category: Optional[str] = None
    draft_revisions: list[DraftRevision] = field(default_factory=list)


def _parse_risk_trace(call: dict[str, Any]) -> TraceParseResult:
    """Parse risk_estimator call into trace phase data."""
    response = call.get("full_response", call.get("response", ""))
    details: dict[str, Any] = {}
    risk_score: Optional[float] = None
    risk_category: Optional[str] = None

    if "Risk:" in response:
        try:
            risk_part = response.split("Risk:")[1].split(",")[0].strip()
            risk_score = float(risk_part)
            details["risk_score"] = risk_part
        except Exception:
            pass
    if "Category:" in response:
        try:
            cat_part = response.split("Category:")[1].strip()
            risk_category = cat_part
            details["category"] = cat_part
        except Exception:
            pass

    score = risk_score if risk_score is not None else 0.0
    cat = risk_category if risk_category is not None else ""
    is_moral_dilemma = cat == "morally_nuanced"
    if score < 0.3 and not is_moral_dilemma:
        decision = "FAST PATH"
        decision_reason = DecisionReason.LOW_RISK.value
    elif is_moral_dilemma:
        decision = "DELIBERATIVE (ethical)"
        decision_reason = "Interpersonal ethical dilemma: requires moral deliberation"
    elif score < 0.7:
        decision = "DELIBERATIVE (1 cycle)"
        decision_reason = DecisionReason.MEDIUM_RISK.value
    else:
        decision = "DELIBERATIVE (full)"
        decision_reason = DecisionReason.HIGH_RISK.value

    return TraceParseResult(
        phase_type=PhaseType.RISK_ESTIMATION,
        decision=decision,
        decision_reason=decision_reason,
        details=details,
        risk_score=risk_score,
        risk_category=risk_category,
    )


def _parse_policy_trace(call: dict[str, Any], current_cycle: int) -> Optional[TraceParseResult]:
    """Parse policy call into trace phase data."""
    action = call.get("action", "")
    prompt = call.get("full_prompt", call.get("prompt", ""))
    response = call.get("full_response", call.get("response", ""))

    guidance_used = ""
    if "Guidance:" in prompt:
        try:
            guidance_used = prompt.split("Guidance:")[1].strip()
        except Exception:
            pass

    if "generate" in action.lower():
        if "fast_path" in action.lower():
            phase_type = PhaseType.GENERATION
            decision = "DIRECT GENERATION"
            decision_reason = "Fast path: direct generation without deliberation"
        else:
            phase_type = PhaseType.GENERATION
            decision = "DRAFT GENERATED"
            decision_reason = "Draft generated, awaiting validation"

        draft_revisions = [
            DraftRevision(
                cycle=current_cycle,
                draft_text=response,
                guidance_used="",
                is_initial=True,
            )
        ]
        return TraceParseResult(
            phase_type=phase_type,
            decision=decision,
            decision_reason=decision_reason,
            details={"response_length": f"{len(response)} chars"},
            draft_revisions=draft_revisions,
        )

    if "rewrite" in action.lower():
        return TraceParseResult(
            phase_type=PhaseType.REVISION,
            decision="REVISED",
            decision_reason="Response revised based on guidance",
            details={"response_length": f"{len(response)} chars"},
            draft_revisions=[
                DraftRevision(
                    cycle=current_cycle,
                    draft_text=response,
                    guidance_used=guidance_used,
                    is_initial=False,
                )
            ],
        )

    return None


def _parse_critic_trace(call: dict[str, Any]) -> TraceParseResult:
    """Parse critic call into trace phase data."""
    action = call.get("action", "")
    response = call.get("full_response", call.get("response", ""))
    details: dict[str, Any] = {}
    errors: list[str] = []
    decision = None
    decision_reason = None

    if "Violations:" in response:
        try:
            v_count = int(response.split("Violations:")[1].split("\n")[0].strip().split(",")[0])
            details["violations_count"] = v_count

            if v_count == 0:
                decision = "APPROVED"
                decision_reason = DecisionReason.NO_VIOLATIONS.value
            elif "Critical violations: True" in response:
                decision = "CRITICAL VIOLATION"
                decision_reason = DecisionReason.CRITICAL_VIOLATION.value
            else:
                decision = "SOFT VIOLATIONS"
                decision_reason = DecisionReason.SOFT_VIOLATION.value
        except Exception:
            pass

    if "Severity score:" in response:
        try:
            sev = response.split("Severity score:")[1].split("\n")[0].strip()
            details["severity_score"] = sev
        except Exception:
            pass

    if "Guidance:" in response:
        try:
            guidance = response.split("Guidance:")[1].split("\n")[0].strip()[:100]
            details["guidance"] = guidance
        except Exception:
            pass

    try:
        g_full = None
        if "Guidance:" in response:
            g_full = response.split("Guidance:")[1].split("\n")[0].strip()
        if g_full and g_full.upper().startswith("REFUSE"):
            decision = "CRITICAL VIOLATION"
            decision_reason = "Guidance requires REFUSE: treated as critical violation"
    except Exception:
        pass

    if "ERROR" in action or "ERROR" in response:
        errors.append("Error during constitutional critique")

    return TraceParseResult(
        phase_type=PhaseType.CRITIQUE,
        decision=decision,
        decision_reason=decision_reason,
        details=details,
        errors=errors,
    )


def _parse_simulator_trace(call: dict[str, Any]) -> TraceParseResult:
    """Parse simulator call into trace phase data."""
    response = call.get("full_response", call.get("response", ""))
    details: dict[str, Any] = {}
    decision = None
    decision_reason = None

    if "Expected valence:" in response:
        try:
            ev_part = response.split("Expected valence:")[1].split(",")[0].split("\n")[0].strip()
            details["expected_valence"] = ev_part
            ev_f = float(ev_part)
            if ev_f > 0.5:
                decision = "POSITIVE OUTLOOK"
                decision_reason = "Simulated consequences predominantly positive"
            elif ev_f > 0:
                decision = "MIXED OUTLOOK"
                decision_reason = "Mixed simulated consequences, requires attention"
            else:
                decision = "NEGATIVE OUTLOOK"
                decision_reason = "Potentially negative simulated consequences"
        except (ValueError, IndexError):
            pass

    if "Semantic harm:" in response:
        try:
            sh_part = response.split("Semantic harm:")[1].split(",")[0].split("\n")[0].strip()
            details["semantic_harm"] = sh_part
        except (ValueError, IndexError):
            pass

    if "Dominant harms:" in response:
        try:
            dh_part = response.split("Dominant harms:")[1].split(", Worst harm:")[0].split("\n")[0].strip()
            details["dominant_harms"] = dh_part
        except (ValueError, IndexError):
            pass

    if "Worst harm:" in response:
        try:
            wh_part = response.split("Worst harm:")[1].split("\n")[0].strip()
            details["worst_harm"] = wh_part
        except (ValueError, IndexError):
            pass

    if "Consequences:" in response:
        try:
            c_count = response.split("Consequences:")[1].split(",")[0].strip()
            details["scenarios_count"] = c_count
        except (ValueError, IndexError):
            pass

    if "Worst case:" in response:
        try:
            wc = response.split("Worst case:")[1].split("\n")[0].strip()
            details["worst_case"] = wc
        except (ValueError, IndexError):
            pass

    return TraceParseResult(
        phase_type=PhaseType.SIMULATION,
        decision=decision,
        decision_reason=decision_reason,
        details=details,
    )


def _parse_hindsight_trace(call: dict[str, Any]) -> TraceParseResult:
    """Parse hindsight call into trace phase data."""
    response = call.get("full_response", call.get("response", ""))
    details: dict[str, Any] = {}
    decision = None
    decision_reason = None

    if "Expected value:" in response:
        try:
            ev = response.split("Expected value:")[1].split("\n")[0].strip()
            details["expected_value"] = ev
        except Exception:
            pass

    if "Recommendation:" in response:
        try:
            rec = response.split("Recommendation:")[1].split("\n")[0].strip()
            details["recommendation"] = rec

            if rec == "proceed":
                decision = "PROCEED"
                decision_reason = DecisionReason.HINDSIGHT_POSITIVE.value
            elif rec == "revise":
                decision = "REVISE"
                decision_reason = "Hindsight suggests response revision"
            else:
                decision = "REFUSE"
                decision_reason = DecisionReason.HINDSIGHT_NEGATIVE.value
        except Exception:
            pass

    return TraceParseResult(
        phase_type=PhaseType.HINDSIGHT,
        decision=decision,
        decision_reason=decision_reason,
        details=details,
    )


def _parse_perspectives_trace(call: dict[str, Any]) -> TraceParseResult:
    """Parse perspectives call into trace phase data."""
    response = call.get("full_response", call.get("response", ""))
    details: dict[str, Any] = {}
    decision = None
    decision_reason = None

    if "Weighted approval:" in response:
        try:
            wa = response.split("Weighted approval:")[1].split("\n")[0].strip()
            details["weighted_approval"] = wa

            if float(wa) >= 0.8:
                decision = "HIGH APPROVAL"
                decision_reason = "All perspectives approve the response"
            elif float(wa) >= 0.6:
                decision = "MODERATE APPROVAL"
                decision_reason = "Moderate approval with some reservations"
            else:
                decision = "LOW APPROVAL"
                decision_reason = "Critical perspectives, may require revision"
        except Exception:
            pass

    if "Min approval:" in response:
        try:
            ma = response.split("Min approval:")[1].split("\n")[0].strip()
            details["min_approval"] = ma
        except Exception:
            pass

    return TraceParseResult(
        phase_type=PhaseType.PERSPECTIVES,
        decision=decision,
        decision_reason=decision_reason,
        details=details,
    )


@dataclass
class DeliberationTrace:
    """Full trace of the deliberative process."""

    request_id: str = ""
    prompt: str = ""
    start_time: float = 0.0
    end_time: float = 0.0

    # Path taken
    path: str = "unknown"  # "fast" or "deliberative"
    path_reason: str = ""

    # Risk estimation
    risk_score: float = 0.0
    risk_category: str = ""
    risk_signals: list[str] = field(default_factory=list)

    # Phases
    phases: list[PhaseResult] = field(default_factory=list)

    # Draft revision history
    draft_history: list[DraftRevision] = field(default_factory=list)

    # Final outcome
    response_type: str = ""
    final_decision: str = ""
    total_cycles: int = 0
    converged: bool = False

    # Errors and warnings
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Constitution
    relevant_principles: list[str] = field(default_factory=list)
    triggered_principles: list[str] = field(default_factory=list)

    def add_phase(self, phase: PhaseResult) -> None:
        """Adds a phase to the trace."""
        self.phases.append(phase)

    def total_duration_ms(self) -> float:
        """Total duration in milliseconds."""
        if self.end_time > 0 and self.start_time > 0:
            return (self.end_time - self.start_time) * 1000
        return float(sum(p.duration_ms for p in self.phases))

    def get_phases_by_cycle(self) -> dict[int, list[PhaseResult]]:
        """Groups phases by cycle."""
        by_cycle: dict[int, list[PhaseResult]] = {}
        for phase in self.phases:
            if phase.cycle not in by_cycle:
                by_cycle[phase.cycle] = []
            by_cycle[phase.cycle].append(phase)
        return by_cycle


@dataclass
class CLIConfig:
    """CLI configuration (OpenAI-only)."""

    use_mock: bool = False
    minimal: bool = False
    clean_start: bool = False
    clean_db: bool = False
    verbose: bool = False
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    max_parallel_agents: int = 2
    max_cycles: int = 2
    enable_perspectives: bool = True
    enable_simulation: bool = True
    enable_hindsight: bool = True
