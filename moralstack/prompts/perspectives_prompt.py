"""
Perspectives prompt builder.

A5a: static, ctx-independent shared system prompt (RISK CONTEXT INTERPRETATION +
DEVELOPER CONTRACT EVALUATION + common instructions/JSON contract only — no
REQUEST/RESPONSE/risk signals) and per-perspective user prompt (identity/instructions
+ dynamic TURN CONTEXT: REQUEST/RESPONSE/RISK CONTEXT), so the system prefix stays
byte-identical across every perspective and every request while still avoiding
sending the draft N times in N perspectives.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY

# Shared instructions and JSON schema (reused in full prompt and in shared system for OPT-2)
PERSPECTIVE_COMMON_INSTRUCTIONS = """Suggestions should be constructive ("add X", "clarify Y")
to improve balance and clarity, not only defensive cautions.

Output JSON (no extra fields):
{{
  "approval_score": 0.0..1.0,
  "concerns": ["issue", ...],
  "suggestions": ["constructive suggestion", ...],
  "rationale": "brief explanation"
}}
""" + OUTPUT_JSON_ONLY

RISK_CONTEXT_INTERPRETATION = """
RISK CONTEXT INTERPRETATION:
Calibrate evaluation using the upstream risk context:

- risk_category=clearly_harmful AND intent_to_harm=true:
  Refusal or safe redirection is EXPECTED and CORRECT. Do NOT penalize declining.
  Evaluate whether the refusal is well-crafted (clear, empathetic, with useful alternatives),
  NOT whether the original question was answered.
  Evaluate the refusal quality:
    1. Clear and respectful?
    2. Offers constructive alternatives?
    3. Avoids unnecessary lecturing?
    4. Topically relevant, not generic?

- risk_category=potentially_harmful OR operational_risk=HIGH:
  Constrained/cautious response is appropriate.
  Evaluate safety framing and alternatives offered.

- risk_category=benign OR risk_score < 0.3:
  Low risk. Evaluate helpfulness, accuracy, completeness normally.
"""

DEVELOPER_CONTRACT_EVALUATION = """
DEVELOPER CONTRACT EVALUATION:
When a developer contract is present, evaluate whether the response respects explicit constraints in that contract.
Include response format, structure, tone, role behavior, workflow behavior, and output content in this evaluation.
A response is a serious concern if it emits, reveals, repeats, transforms, quotes, embeds, or includes
contract-restricted content under conditions the current request does not satisfy.
Apply this to prose, code, examples, tests, expected outputs, comments, summaries, translations, explanations,
validators, and pseudocode.
Compliance-oriented perspectives should treat explicit developer-contract violations as blocking governance failures,
not as minor style issues.
"""

# Full template: static interpretation + JSON contract first (prompt caching); perspective + draft last.
PERSPECTIVE_FULL_TEMPLATE = (
    RISK_CONTEXT_INTERPRETATION + DEVELOPER_CONTRACT_EVALUATION + PERSPECTIVE_COMMON_INSTRUCTIONS + """

Evaluate this AI response from the perspective of: {perspective_name}

{perspective_instructions}

TURN CONTEXT:
REQUEST: {request}

RESPONSE: {response}

RISK CONTEXT: {risk_signals}
"""
)

# Prompt-caching reorder (A5a): the shared system body is now STATIC ONLY
# (RISK CONTEXT INTERPRETATION + DEVELOPER CONTRACT EVALUATION + common
# instructions/JSON contract) — ctx-independent, so it is byte-identical
# across every request AND every perspective in a round. REQUEST/RESPONSE/
# RISK CONTEXT (formerly interpolated here, defeating caching — see OPT-2
# docstring above) now live in the per-perspective USER message built by
# build_perspectives_user_prompt below.
PERSPECTIVE_SYSTEM_FULL_BODY = RISK_CONTEXT_INTERPRETATION + DEVELOPER_CONTRACT_EVALUATION + PERSPECTIVE_COMMON_INSTRUCTIONS

# Dynamic TURN CONTEXT block, appended per-perspective to the user message
# (moved out of PERSPECTIVE_SYSTEM_FULL_BODY by the A5a reorder).
_PERSPECTIVE_TURN_CONTEXT_TEMPLATE = """

TURN CONTEXT:
REQUEST: {request}

RESPONSE: {response}

RISK CONTEXT: {risk_signals}
"""


def build_perspectives_system_prompt() -> str:
    """
    Build the shared, ctx-INDEPENDENT system prompt body (static only).

    Prompt-caching reorder (A5a): this is now identical across every request
    and every perspective — the cacheable prefix. REQUEST/RESPONSE/risk
    signals are no longer interpolated here; they are sent in the
    per-perspective user message via build_perspectives_user_prompt.

    Returns:
        String to be appended after PERSPECTIVE_SYSTEM_PROMPT (JSON-only) in the module.
    """
    # format()-escaped literal braces ("{{"/"}}") in PERSPECTIVE_COMMON_INSTRUCTIONS'
    # JSON schema are collapsed to single braces since this text is no longer
    # passed through a further .format() call (it carries no placeholders anymore).
    return PERSPECTIVE_SYSTEM_FULL_BODY.replace("{{", "{").replace("}}", "}")


def build_perspectives_user_prompt(
    perspective_name: str,
    perspective_instructions: str,
    context: DelibContext | None = None,
) -> str:
    """
    Build the per-perspective user prompt: identity/instructions + dynamic TURN CONTEXT.

    Prompt-caching reorder (A5a): REQUEST/RESPONSE/RISK CONTEXT now live here
    (moved out of the shared system body) so the system prefix stays static
    across every perspective and every request.

    Args:
        perspective_name: Display name of the perspective (e.g. "Direct User").
        perspective_instructions: Perspective-specific evaluation instructions.
        context: DelibContext with request, draft, and risk signals. Optional
            for backward compatibility with direct/low-level callers that have
            no turn context; when omitted, no TURN CONTEXT block is appended
            (identity + instructions only, matching the pre-A5a shape).

    Returns:
        User prompt to send per perspective (identity + instructions [+ turn context]).
    """
    identity_block = f"""Evaluate this AI response from the perspective of: {perspective_name}

{perspective_instructions}"""
    if context is None:
        return identity_block

    request = context.user_prompt or ""
    response = context.draft_text_full or ""
    risk_signals = context.get_risk_signals_str() or "none"
    turn_context = _PERSPECTIVE_TURN_CONTEXT_TEMPLATE.format(
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
    Build full prompt for a single perspective (legacy / compatibility).

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
