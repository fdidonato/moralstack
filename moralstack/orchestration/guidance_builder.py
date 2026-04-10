"""
Aggregated guidance builder for deliberative cycles.
Builds a single guidance string from critic, perspectives, hindsight, and simulator state.
"""

from __future__ import annotations

import logging
from typing import Any

from moralstack.orchestration.convergence_evaluator import calculate_weighted_approval
from moralstack.orchestration.types import (
    DeliberationState,
    PerspectiveResultProtocol,
)

_LOG = logging.getLogger(__name__)


def _append_simulator_guidance(guidance_parts: list[str], state: DeliberationState) -> None:
    """Append simulator consequence lines using the same rules as the legacy aggregator."""
    if not state.simulations:
        return
    negative_consequences: list[str] = []
    has_negative_valence = False
    valence = 0.0
    for sim in state.simulations:
        if sim.expected_valence < 0:
            has_negative_valence = True
            valence = sim.expected_valence
        if sim.consequences:
            for cons in sim.consequences:
                harm_sev = getattr(cons, "harm_severity", getattr(cons, "severity", 0.0))
                outcome_val = getattr(cons, "outcome_valence", 0.0)
                desc = getattr(cons, "text", getattr(cons, "description", str(cons)))
                if cons.likelihood > 0.5 and harm_sev > 0.5:
                    negative_consequences.append(desc)
                elif outcome_val < 0 or getattr(cons, "is_negative", False):
                    negative_consequences.append(desc)
    if negative_consequences:
        avoid_str = "; ".join(negative_consequences[:3])
        guidance_parts.append(f"[SIMULATOR - Avoid] {avoid_str}")
    elif has_negative_valence:
        guidance_parts.append(
            f"[SIMULATOR] Response may have negative consequences "
            f"(valence: {valence:.2f}). Mitigate potential risks and "
            "make the response more constructive."
        )


def _append_hindsight_guidance(guidance_parts: list[str], state: DeliberationState) -> None:
    """Append hindsight lines (legacy behavior when signal filter is off or bypassed)."""
    if state.hindsight:
        hindsight_feedback = None
        h = state.hindsight
        fb = getattr(h, "feedback", None)
        if fb:
            hindsight_feedback = fb
        else:
            suggestions = getattr(h, "suggestions", None)
            if suggestions:
                hindsight_feedback = "; ".join(suggestions[:3])
            else:
                reasoning = getattr(h, "reasoning", None)
                if reasoning and float(getattr(h, "score", 1.0)) < 0.7:
                    hindsight_feedback = reasoning
        if hindsight_feedback:
            guidance_parts.append(f"[HINDSIGHT] {hindsight_feedback}")
    if state.hindsight_score < 0.7:
        if not any("[HINDSIGHT]" in p for p in guidance_parts):
            guidance_parts.append(
                f"[HINDSIGHT] Low score ({state.hindsight_score:.2f}). "
                "Improve the overall ethical value of the response, "
                "making it more balanced and responsible."
            )


def _append_perspectives_guidance(
    guidance_parts: list[str],
    state: DeliberationState,
    *,
    per_perspective_approval_threshold: float | None,
) -> None:
    """Collect perspective suggestions/concerns. If threshold is set, skip satisfied perspectives."""
    if not state.perspectives:
        return
    persp_suggestions: list[str] = []
    persp_concerns: list[str] = []
    priority_order = ["observer", "user", "vulnerable", "adversary", "compliance"]

    def _persp_sort_key(p: PerspectiveResultProtocol) -> int:
        pid = getattr(p, "perspective_id", "") or ""
        return priority_order.index(pid) if pid in priority_order else 99

    sorted_perspectives = sorted(state.perspectives, key=_persp_sort_key)
    for p in sorted_perspectives:
        approval = float(getattr(p, "approval_score", 1.0) or 1.0)
        if per_perspective_approval_threshold is not None and approval >= per_perspective_approval_threshold:
            continue
        perspective_name = getattr(p, "perspective_name", None) or getattr(p, "perspective_id", "") or "Unknown"
        if getattr(p, "suggestions", None):
            for s in p.suggestions[:2]:
                persp_suggestions.append(f"{perspective_name}: {s}")
        if getattr(p, "concerns", None):
            for c in p.concerns[:2]:
                persp_concerns.append(f"{perspective_name}: {c}")
    if persp_suggestions:
        guidance_parts.append(f"[PERSPECTIVES - Suggestions] {'; '.join(persp_suggestions[:5])}")
    if persp_concerns:
        guidance_parts.append(f"[PERSPECTIVES - Concerns] {'; '.join(persp_concerns[:5])}")


def _build_unfiltered_guidance(state: DeliberationState, has_critical: bool) -> str:
    """Original aggregation: balance first (if no hard violations), then all modules."""
    guidance_parts: list[str] = []
    if not has_critical:
        guidance_parts.append(
            "[BALANCE] Maintain or improve the balance between pros and cons; " "do not add only caveats or disclaimers."
        )
        guidance_parts.append(
            "[BALANCE] Present both sides of the argument; prefer improving "
            "balance and structure over adding more caveats."
        )
    if state.last_critique and state.last_critique.revision_guidance:
        critic_guidance = state.last_critique.revision_guidance
        if critic_guidance and critic_guidance.strip():
            guidance_parts.append(f"[CRITIC] {critic_guidance}")
    _append_perspectives_guidance(guidance_parts, state, per_perspective_approval_threshold=None)
    _append_hindsight_guidance(guidance_parts, state)
    _append_simulator_guidance(guidance_parts, state)
    if not guidance_parts:
        return "Improve the response to make it clearer and more complete."
    return "\n".join(guidance_parts)


def _hindsight_effectively_present(state: DeliberationState) -> bool:
    """True when hindsight (or simulator-carried hindsight score) was actually produced."""
    if state.hindsight is not None:
        return True
    if state.simulations:
        last_sim = state.simulations[-1]
        if hasattr(last_sim, "hindsight_score"):
            return True
    return False


def _fill_telemetry_defaults(telemetry: dict[str, Any], state: DeliberationState) -> None:
    telemetry.setdefault("hindsight_score", state.hindsight_score)
    if state.simulations:
        last_sim = state.simulations[-1]
        telemetry.setdefault(
            "semantic_expected_harm",
            float(getattr(last_sim, "semantic_expected_harm", 0.0) or 0.0),
        )


def build_aggregated_guidance(
    state: DeliberationState,
    *,
    filter_marginal: bool = True,
    telemetry: dict[str, Any] | None = None,
) -> str:
    """
    Build a single guidance string from the current deliberation state,
    aggregating critic revision_guidance, perspectives suggestions/concerns,
    hindsight feedback, and simulator consequences.

    When ``filter_marginal`` is True (default), marginal/cosmetic signals are dropped unless
    the last critique reports critical violations (hard), in which case all signals are kept.

    When guidance is empty after filtering, returns ``""`` so callers can skip rewrite.

    If ``telemetry`` is provided, it is updated with gate decisions and scalar signals for logging
    and persistence.
    """
    has_critical = bool(state.last_critique and getattr(state.last_critique, "has_critical_violations", False))
    apply_filter = bool(filter_marginal and not has_critical)

    if telemetry is not None:
        telemetry["has_critical_violations"] = has_critical
        telemetry["filter_marginal"] = filter_marginal
        telemetry["apply_signal_filter"] = apply_filter
        _fill_telemetry_defaults(telemetry, state)

    if not apply_filter:
        result = _build_unfiltered_guidance(state, has_critical)
        if telemetry is not None:
            telemetry["guidance_empty"] = not result.strip()
            telemetry["guidance_char_len"] = len(result)
        return result

    guidance_parts: list[str] = []

    # --- CRITIC ---
    critic_appended = False
    if state.last_critique and state.last_critique.revision_guidance:
        has_violations = bool(getattr(state.last_critique, "violations", None))
        critic_dec = (getattr(state.last_critique, "decision", "") or "").strip().upper()
        include_critic = has_violations or critic_dec in ("REVISE", "REFUSE")
        cg = state.last_critique.revision_guidance.strip()
        if include_critic and cg:
            guidance_parts.append(f"[CRITIC] {cg}")
            critic_appended = True
    if telemetry is not None:
        telemetry["critic_included"] = critic_appended

    # --- PERSPECTIVES ---
    include_perspectives = True
    weighted_approval = 0.0
    if state.perspectives:
        weighted_approval = calculate_weighted_approval(state.perspectives)
        include_perspectives = weighted_approval < 0.85
    if telemetry is not None:
        telemetry["weighted_approval"] = weighted_approval
        telemetry["perspectives_section_skipped_high_approval"] = not include_perspectives

    if include_perspectives:
        _append_perspectives_guidance(guidance_parts, state, per_perspective_approval_threshold=0.75)

    # --- HINDSIGHT (only when a real hindsight signal exists; avoid default 0.0 with no module output) ---
    include_hindsight = state.hindsight_score < 0.7 and _hindsight_effectively_present(state)
    if telemetry is not None:
        telemetry["hindsight_section_included"] = include_hindsight
        telemetry["hindsight_effectively_present"] = _hindsight_effectively_present(state)
    if include_hindsight:
        _append_hindsight_guidance(guidance_parts, state)

    # --- SIMULATOR ---
    include_simulator = False
    if state.simulations:
        last_sim = state.simulations[-1]
        sem_harm = float(getattr(last_sim, "semantic_expected_harm", 0.0) or 0.0)
        include_simulator = sem_harm >= 0.35
        if telemetry is not None:
            telemetry["semantic_expected_harm"] = sem_harm
            telemetry["simulator_section_included"] = include_simulator
    if include_simulator:
        _append_simulator_guidance(guidance_parts, state)

    if filter_marginal:
        if not guidance_parts:
            _LOG.info(
                "guidance_filter: all signals below threshold, guidance empty (rewrite will be skipped)",
            )
        else:
            tags = [p.split("]")[0] + "]" for p in guidance_parts if p.startswith("[")]
            _LOG.info(
                "guidance_filter: %d substantive signals included, %s",
                len(guidance_parts),
                tags,
            )

    # --- BALANCE (only with substantive content, and never for hard-violation path) ---
    if guidance_parts and not has_critical:
        guidance_parts.insert(
            0,
            "[BALANCE] Maintain or improve the balance between pros and cons; " "do not add only caveats or disclaimers.",
        )
        guidance_parts.insert(
            1,
            "[BALANCE] Present both sides of the argument; prefer improving "
            "balance and structure over adding more caveats.",
        )

    result = "\n".join(guidance_parts) if guidance_parts else ""
    if telemetry is not None:
        telemetry["guidance_empty"] = not result.strip()
        telemetry["guidance_char_len"] = len(result)
    return result
