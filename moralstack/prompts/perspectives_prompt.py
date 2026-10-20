"""
Perspectives prompt builder.

A5a: static, ctx-independent shared system prompt (RISK CONTEXT INTERPRETATION +
DEVELOPER CONTRACT EVALUATION + common instructions/JSON contract only — no
REQUEST/RESPONSE/risk signals) and per-perspective user prompt (identity/instructions
+ dynamic TURN CONTEXT: REQUEST/RESPONSE/RISK CONTEXT), so the system prefix stays
byte-identical across every perspective and every request while still avoiding
sending the draft N times in N perspectives.

The prompt texts live in ``moralstack/prompts/data/perspectives.yaml``. The module-level constants below are
default re-exports (byte-identical to the former literals); builders and modules read their own ``PromptSet``.
``build_perspectives_prompt`` / ``PERSPECTIVE_FULL_TEMPLATE`` are a dead path and stay on the defaults.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY, double_braces
from moralstack.prompts.catalog import NO_CONTEXT, PromptContext, PromptSet, default_prompt_set

_DEFAULT = default_prompt_set()

# Shared instructions and JSON schema. The packaged fragment is in wire form (single braces); this constant
# keeps the historical format()-escaped form ("{{"/"}}").
PERSPECTIVE_COMMON_INSTRUCTIONS = double_braces(_DEFAULT.text("perspectives.common_instructions")) + OUTPUT_JSON_ONLY

RISK_CONTEXT_INTERPRETATION = _DEFAULT.text("perspectives.risk_context_interpretation")

DEVELOPER_CONTRACT_EVALUATION = _DEFAULT.text("perspectives.devcontract_evaluation")

# Dynamic TURN CONTEXT block, appended per-perspective to the user message
# (moved out of PERSPECTIVE_SYSTEM_FULL_BODY by the A5a reorder).
_PERSPECTIVE_TURN_CONTEXT_TEMPLATE = _DEFAULT.template("perspectives.turn_context")

# Full template (dead path): static interpretation + JSON contract first (prompt caching); perspective + draft last.
PERSPECTIVE_FULL_TEMPLATE = (
    RISK_CONTEXT_INTERPRETATION
    + DEVELOPER_CONTRACT_EVALUATION
    + PERSPECTIVE_COMMON_INSTRUCTIONS
    + "\n\n"
    + _DEFAULT.template("perspectives.identity_block")
    + _PERSPECTIVE_TURN_CONTEXT_TEMPLATE
)

# Prompt-caching reorder (A5a): the shared system body is STATIC ONLY
# (RISK CONTEXT INTERPRETATION + DEVELOPER CONTRACT EVALUATION + common
# instructions/JSON contract) — byte-identical across every request AND every
# perspective in a round. REQUEST/RESPONSE/RISK CONTEXT live in the per-perspective
# USER message built by build_perspectives_user_prompt below.
PERSPECTIVE_SYSTEM_FULL_BODY = RISK_CONTEXT_INTERPRETATION + DEVELOPER_CONTRACT_EVALUATION + PERSPECTIVE_COMMON_INSTRUCTIONS


def build_perspectives_system_prompt(
    prompt_set: PromptSet | None = None,
    ctx: PromptContext = NO_CONTEXT,
) -> str:
    """
    Build the shared system prompt body (static; the ``perspectives.system_body`` composition).

    Prompt-caching reorder (A5a): this is identical across every request
    and every perspective — the cacheable prefix. REQUEST/RESPONSE/risk
    signals are not interpolated here; they are sent in the
    per-perspective user message via build_perspectives_user_prompt.

    Args:
        prompt_set: PromptSet to render from (default: packaged set).
        ctx: per-call context for ``when`` conditions (the default set renders identically for every context).

    Returns:
        String to be appended after the perspectives base system prompt (JSON-only) in the module.
    """
    return (prompt_set or default_prompt_set()).render("perspectives.system_body", ctx)


def build_perspectives_user_prompt(
    perspective_name: str,
    perspective_instructions: str,
    context: DelibContext | None = None,
    prompt_set: PromptSet | None = None,
) -> str:
    """
    Build the per-perspective user prompt: identity/instructions + dynamic TURN CONTEXT.

    Prompt-caching reorder (A5a): REQUEST/RESPONSE/RISK CONTEXT live here
    (moved out of the shared system body) so the system prefix stays static
    across every perspective and every request.

    Args:
        perspective_name: Display name of the perspective (e.g. "Direct User").
        perspective_instructions: Perspective-specific evaluation instructions.
        context: DelibContext with request, draft, and risk signals. Optional
            for backward compatibility with direct/low-level callers that have
            no turn context; when omitted, no TURN CONTEXT block is appended
            (identity + instructions only, matching the pre-A5a shape).
        prompt_set: PromptSet to read the templates from (default: packaged set).

    Returns:
        User prompt to send per perspective (identity + instructions [+ turn context]).
    """
    ps = prompt_set or default_prompt_set()
    identity_block = ps.format(
        "perspectives.identity_block",
        perspective_name=perspective_name,
        perspective_instructions=perspective_instructions,
    )
    if context is None:
        return identity_block

    request = context.user_prompt or ""
    response = context.draft_text_full or ""
    risk_signals = context.get_risk_signals_str() or "none"
    turn_context = ps.format(
        "perspectives.turn_context",
        request=request,
        response=response,
        risk_signals=risk_signals,
    )
    return identity_block + turn_context


def build_perspectives_prompt(
    context: DelibContext,
    perspective_name: str,
    perspective_instructions: str,
) -> str:
    """
    Build full prompt for a single perspective (legacy / compatibility; dead path, stays on the defaults).

    Combines perspective user prompt with shared system body for callers that need
    a single prompt string. Prefer build_perspectives_system_prompt + build_perspectives_user_prompt
    for token reduction when N > 1 perspectives.

    Args:
        context: DelibContext with request, draft, risk signals.
        perspective_name: Perspective name (e.g. "Direct User").
        perspective_instructions: Perspective-specific instructions.
    Returns:
        Single prompt string for the LLM.
    """
    request = context.user_prompt or ""
    response = context.draft_text_full or ""
    risk_signals = context.get_risk_signals_str() or "none"
    return PERSPECTIVE_FULL_TEMPLATE.format(
        perspective_name=perspective_name,
        perspective_instructions=perspective_instructions,
        request=request,
        response=response,
        risk_signals=risk_signals,
    )
