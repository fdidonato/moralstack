"""
Generates the dynamic sections of HARM_SIGNAL_PROMPT_TEMPLATE from the SignalRegistry.

The static scaffolding (system prompt invariants, normalization instructions,
output footer) lives in the prompt set (``risk.yaml``). This module fills only the five
signal-specific placeholder sections.

Exported:
    render_signal_definitions(registry)     → str  (SIGNAL DEFINITIONS block)
    render_domain_sensitivity(registry)     → str  (domain_sensitivity block)
    render_coherence_rules(registry)        → str  (COHERENCE CHECK bullets)
    render_evaluation_order(registry)       → str  (EVALUATION ORDER block)
    render_output_schema(registry)          → str  (OUTPUT JSON schema block)
    get_harm_signal_prompts(registry)       → tuple[system_prompt, user_template]

The fixed wording around the registry-derived sections comes from a ``PromptSet``
(``prompt_set=None`` means the packaged defaults); the registry-derived labels stay in code.
"""

from __future__ import annotations

from typing import Any

from moralstack.prompts.catalog import NO_CONTEXT, PromptContext, PromptSet, default_prompt_set

from .registry import SignalRegistry


def render_evaluation_order(registry: SignalRegistry, prompt_set: PromptSet | None = None) -> str:
    """Render the EVALUATION ORDER section.

    Signals with effects.priority_resolution_in_prompt=True are listed first.
    Currently only q17_minor_exploitation carries this flag.
    """
    priority_signals = [sig for sig in registry.signals.values() if sig.effects.priority_resolution_in_prompt]
    if not priority_signals:
        return ""

    ps = prompt_set or default_prompt_set()
    lines = []
    for sig in priority_signals:
        lines.append(ps.format("risk.signals.evaluation_order_item", topic_hints=sig.short_description, key=sig.key))
    return "\n\n".join(lines)


def render_signal_definitions(registry: SignalRegistry) -> str:
    """Render the SIGNAL DEFINITIONS section for the LLM prompt.

    Format is byte-identical to the pre-externalization section (pinned by the A0 golden).
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


def render_output_schema(registry: SignalRegistry, prompt_set: PromptSet | None = None) -> str:
    """Render the OUTPUT JSON schema listing all signal keys + domain_sensitivity."""
    ps = prompt_set or default_prompt_set()
    field_count = len(registry.signals) + 1  # +1 for domain_sensitivity
    lines = [ps.format("risk.signals.output_schema_header", field_count=field_count), "", "{{"]
    for sig in registry.signals.values():
        if sig.key == "q8_self_harm_suicide":
            # Special case: key in JSON uses snake_case as-is
            pass
        lines.append(f'  "{sig.key}": "yes" | "no",')
    lines.append('  "domain_sensitivity": "LOW" | "MEDIUM" | "HIGH"')
    lines.append("}}")
    lines.append("")
    lines.append(ps.text("risk.signals.output_schema_footer"))
    return "\n".join(lines)


def render_output_json_schema(registry: SignalRegistry) -> dict[str, Any]:
    """Strict Structured Outputs schema for the Harm Signal Scanner.

    Built from the same registry as `render_output_schema`, so the schema the
    provider enforces and the schema the prompt describes cannot diverge.

    This is what makes two observed failure modes impossible at the source
    rather than silently degraded: a key the model misspells
    (`q10_weapons_explosives_txins` for `..._toxins`) and a payload wrapped in
    an unexpected envelope (`{"data": [...]}`). Both parse as valid JSON, so
    the tolerant `data.get(key, "no")` in `calibration.py` reads the signal as
    absent — a hard signal silently degraded to "no" with no error and no retry.
    """
    properties: dict[str, Any] = {sig.key: {"type": "string", "enum": ["yes", "no"]} for sig in registry.signals.values()}
    properties["domain_sensitivity"] = {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def harm_signal_response_format(registry: SignalRegistry) -> dict[str, Any]:
    """`response_format` that makes the provider enforce the harm-signal schema."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "harm_signals",
            "strict": True,
            "schema": render_output_json_schema(registry),
        },
    }


def get_harm_signal_prompts(
    registry: SignalRegistry,
    *,
    prompt_set: PromptSet | None = None,
    ctx: PromptContext = NO_CONTEXT,
) -> tuple[str, str]:
    """
    Return (system_prompt, user_template) for the Harm Signal Scanner mini-estimator.

    All five registry-derived sections (evaluation order, signal definitions,
    domain sensitivity, coherence rules, output schema) are static — they
    depend only on the fixed SignalRegistry, never on per-request data — so
    they are rendered once here and appended to the static
    ``risk.signals.system_head`` composition to form the cacheable system
    prefix. The user_template carries ONLY the per-request `{request}` field.

    ``ctx`` selects the ``when`` variant of the system head (the developer-contract
    scope principle can be conditional in a deployer override); the default set
    renders identically for every context.

    Callers should use user_template.format(request=...) to produce the
    final per-request user message.
    """
    ps = prompt_set or default_prompt_set()

    evaluation_order = render_evaluation_order(registry, ps)
    signal_definitions = render_signal_definitions(registry)
    domain_sensitivity = render_domain_sensitivity(registry)
    coherence_rules = render_coherence_rules(registry)
    output_schema = render_output_schema(registry, ps)

    # Fill the 5 static placeholder sections, then collapse any
    # format()-escaped literal braces (e.g. the JSON schema skeleton
    # produced by render_output_schema uses "{{"/"}}") since this text is
    # no longer passed through a second .format() call once it is static.
    static_suffix = (
        ps.format(
            "risk.signals.static_suffix",
            evaluation_order_section=evaluation_order,
            signal_definitions_section=signal_definitions,
            domain_sensitivity_section=domain_sensitivity,
            coherence_rules_section=coherence_rules,
            output_schema_section=output_schema,
        )
        .replace("{{", "{")
        .replace("}}", "}")
    )

    system_prompt = ps.render("risk.signals.system_head", ctx) + "\n\n" + static_suffix

    return system_prompt, ps.template("risk.signals.user")


# ── Internal helpers ────────────────────────────────────────────────────────


def _indent_continuation(text: str, indent: str) -> str:
    """Indent all lines except the first with the given prefix."""
    lines = text.splitlines()
    if len(lines) <= 1:
        return text
    first = lines[0]
    rest = "\n".join(indent + ln for ln in lines[1:])
    return first + "\n" + rest
