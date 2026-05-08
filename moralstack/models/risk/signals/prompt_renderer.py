"""
Generates the dynamic sections of HARM_SIGNAL_PROMPT_TEMPLATE from the SignalRegistry.

The static scaffolding (system prompt invariants, normalization instructions,
output footer) remains in prompts.py. This module fills only the five
signal-specific placeholder sections.

Exported:
    render_signal_definitions(registry)     → str  (SIGNAL DEFINITIONS block)
    render_domain_sensitivity(registry)     → str  (domain_sensitivity block)
    render_coherence_rules(registry)        → str  (COHERENCE CHECK bullets)
    render_evaluation_order(registry)       → str  (EVALUATION ORDER block)
    render_output_schema(registry)          → str  (OUTPUT JSON schema block)
    get_harm_signal_prompts(registry)       → tuple[system_prompt, user_template]
"""

from __future__ import annotations

from .registry import SignalRegistry


def render_evaluation_order(registry: SignalRegistry) -> str:
    """Render the EVALUATION ORDER section.

    Signals with effects.priority_resolution_in_prompt=True are listed first.
    Currently only q17_minor_exploitation carries this flag.
    """
    priority_signals = [
        sig for sig in registry.signals.values() if sig.effects.priority_resolution_in_prompt
    ]
    if not priority_signals:
        return ""

    lines = []
    for sig in priority_signals:
        topic_hints = sig.short_description
        lines.append(
            f"If the payload involves {topic_hints}:\n"
            f"  → resolve {sig.key} FIRST, then evaluate the rest of the\n"
            f"    questions against the SAME payload. Wrapper cues about reputation,\n"
            f"    Twitter moderation, Wikipedia layout, or generic deceptive campaigns\n"
            f"    do NOT prevent {sig.key}=yes when the substantive payload is minor-contact\n"
            f"    or grooming."
        )
    return "\n\n".join(lines)


def render_signal_definitions(registry: SignalRegistry) -> str:
    """Render the SIGNAL DEFINITIONS section for the LLM prompt.

    Format mirrors the current hardcoded prompts.py section exactly.
    """
    parts: list[str] = []
    for sig in registry.signals.values():
        key = sig.key
        desc = sig.short_description

        if sig.prompt.yes_when is None and sig.prompt.no_when is None:
            # Simple one-liner: "q1_confidential — description"
            parts.append(f"{key} — {desc}")
        else:
            # Multi-line with yes/no criteria
            block = f"{key} — {desc}"
            if sig.prompt.yes_when:
                yes_text = sig.prompt.yes_when.strip()
                # Indent continuation lines
                indented = _indent_continuation(yes_text, "  ")
                block += f"\n  YES only when {indented}"
            if sig.prompt.no_when:
                no_text = sig.prompt.no_when.strip()
                indented = _indent_continuation(no_text, "  ")
                block += f"\n  NO when {indented}"
            parts.append(block)

    return "\n\n".join(parts)


def render_domain_sensitivity(registry: SignalRegistry) -> str:
    """Render the domain_sensitivity definition block."""
    ds = registry.domain_sensitivity
    lines = [f"domain_sensitivity — {ds.description}:"]
    # Ordered HIGH → MEDIUM → LOW
    for level in ("HIGH", "MEDIUM", "LOW"):
        desc = ds.values.get(level, "")
        if desc:
            lines.append(f"  {level}:   {desc}")
    return "\n".join(lines)


def render_coherence_rules(registry: SignalRegistry) -> str:
    """Render COHERENCE CHECK bullet list."""
    rules = registry.prompt_config.coherence_rules
    return "\n".join(f"• {rule}" for rule in rules)


def render_output_schema(registry: SignalRegistry) -> str:
    """Render the OUTPUT JSON schema listing all signal keys + domain_sensitivity."""
    field_count = len(registry.signals) + 1  # +1 for domain_sensitivity
    lines = [f"OUTPUT — STRICT JSON ONLY ({field_count} fields)", "", "{{"]
    for sig in registry.signals.values():
        if sig.key == "q8_self_harm_suicide":
            # Special case: key in JSON uses snake_case as-is
            pass
        lines.append(f'  "{sig.key}": "yes" | "no",')
    lines.append('  "domain_sensitivity": "LOW" | "MEDIUM" | "HIGH"')
    lines.append("}}")
    lines.append("")
    lines.append("Return ONLY valid JSON. No preamble, no markdown fences, no commentary.")
    return "\n".join(lines)


def get_harm_signal_prompts(registry: SignalRegistry) -> tuple[str, str]:
    """
    Return (system_prompt, user_template) for the Harm Signal Scanner mini-estimator.

    The system_prompt is the static HARM_SIGNAL_SYSTEM_PROMPT from prompts.py.
    The user_template has all five dynamic sections filled from the registry.

    Callers should use user_template.format(request=...) to produce the final prompt.
    """
    from moralstack.models.risk.prompts import HARM_SIGNAL_SYSTEM_PROMPT, HARM_SIGNAL_PROMPT_TEMPLATE  # noqa: PLC0415

    evaluation_order = render_evaluation_order(registry)
    signal_definitions = render_signal_definitions(registry)
    domain_sensitivity = render_domain_sensitivity(registry)
    coherence_rules = render_coherence_rules(registry)
    output_schema = render_output_schema(registry)

    # Fill the 5 dynamic placeholder sections; leave {request} intact for
    # the caller to fill per-request via .format(request=...).
    # We use a partial format with only the section keys, not {request}.
    user_template = HARM_SIGNAL_PROMPT_TEMPLATE.format(
        request="{request}",  # preserve the per-request placeholder
        evaluation_order_section=evaluation_order,
        signal_definitions_section=signal_definitions,
        domain_sensitivity_section=domain_sensitivity,
        coherence_rules_section=coherence_rules,
        output_schema_section=output_schema,
    )

    return HARM_SIGNAL_SYSTEM_PROMPT, user_template


# ── Internal helpers ────────────────────────────────────────────────────────

def _indent_continuation(text: str, indent: str) -> str:
    """Indent all lines except the first with the given prefix."""
    lines = text.splitlines()
    if len(lines) <= 1:
        return text
    first = lines[0]
    rest = "\n".join(indent + ln for ln in lines[1:])
    return first + "\n" + rest
