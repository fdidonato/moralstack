"""
Aggregated guidance builder for deliberative cycles.
Builds a single guidance string from critic, perspectives, hindsight, and simulator state.
"""

from __future__ import annotations

from moralstack.orchestration.types import (
    DeliberationState,
    PerspectiveResultProtocol,
)


def build_aggregated_guidance(state: DeliberationState) -> str:
    """
    Build a single guidance string from the current deliberation state,
    aggregating critic revision_guidance, perspectives suggestions/concerns,
    hindsight feedback, and simulator consequences.
    """
    guidance_parts = []
    has_critical = state.last_critique.has_critical_violations if state.last_critique else False
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
    if state.perspectives:
        persp_suggestions = []
        persp_concerns = []
        priority_order = ["observer", "user", "vulnerable", "adversary", "compliance"]

        def _persp_sort_key(p: PerspectiveResultProtocol) -> int:
            pid = getattr(p, "perspective_id", "") or ""
            return priority_order.index(pid) if pid in priority_order else 99

        sorted_perspectives = sorted(state.perspectives, key=_persp_sort_key)
        for p in sorted_perspectives:
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
    if state.hindsight:
        hindsight_feedback = None
        if getattr(state.hindsight, "feedback", None):
            hindsight_feedback = state.hindsight.feedback
        elif getattr(state.hindsight, "suggestions", None):
            hindsight_feedback = "; ".join(state.hindsight.suggestions[:3])
        elif getattr(state.hindsight, "reasoning", None):
            if getattr(state.hindsight, "score", 1.0) < 0.7:
                hindsight_feedback = state.hindsight.reasoning
        if hindsight_feedback:
            guidance_parts.append(f"[HINDSIGHT] {hindsight_feedback}")
    if state.hindsight_score < 0.7:
        if not any("[HINDSIGHT]" in p for p in guidance_parts):
            guidance_parts.append(
                f"[HINDSIGHT] Low score ({state.hindsight_score:.2f}). "
                "Improve the overall ethical value of the response, "
                "making it more balanced and responsible."
            )
    if state.simulations:
        negative_consequences = []
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
    if not guidance_parts:
        return "Improve the response to make it clearer and more complete."
    return "\n".join(guidance_parts)
