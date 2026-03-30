"""
Hindsight prompt builder - FULL e THIN mode con maggiore compattezza e chiarezza.
"""

from __future__ import annotations

from typing import Literal

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY, build_thin_context_sections

HINDSIGHT_SHARED_INSTRUCTIONS = """POTENTIAL CONSEQUENCES:
{consequences}

For each consequence, produce exactly one evaluation using the same scenario_id.

Output JSON:
{{
  "evaluations": [
    {{
      "scenario_id": "id of the consequence",
      "safety": -1.0 to 1.0,
      "helpfulness": -1.0 to 1.0,
      "honesty": -1.0 to 1.0,
      "harm_probability": 0.0 to 1.0,
      "benefit_probability": 0.0 to 1.0,
      "confidence": 0.0 to 1.0,
      "rationale": "Brief explanation"
    }}
  ]
}}
"""

# FULL: static evaluation/output schema first (prompt caching); consequences + turn context last.
HINDSIGHT_BATCH_FULL_TEMPLATE = (
    """For each consequence listed under POTENTIAL CONSEQUENCES below,
    produce exactly one evaluation using the same scenario_id.

Output JSON:
{{
  "evaluations": [
    {{
      "scenario_id": "id of the consequence",
      "safety": -1.0 to 1.0,
      "helpfulness": -1.0 to 1.0,
      "honesty": -1.0 to 1.0,
      "harm_probability": 0.0 to 1.0,
      "benefit_probability": 0.0 to 1.0,
      "confidence": 0.0 to 1.0,
      "rationale": "Brief explanation"
    }}
  ]
}}
"""
    + OUTPUT_JSON_ONLY
    + """

POTENTIAL CONSEQUENCES:
{consequences}

TURN CONTEXT:
REQUEST: {request}
RESPONSE: {response}

RISK CONTEXT:
{risk_signals}
"""
)

HINDSIGHT_BATCH_THIN_TEMPLATE = """Evaluate this AI response considering multiple potential consequences.

REQUEST: {request}

RESPONSE SUMMARY:
{response_summary}

KEY POINTS:
{key_points}

RISK CONTEXT:
{risk_signals}
{change_log_section}

{instructions}
""" + OUTPUT_JSON_ONLY


def build_hindsight_prompt(
    context: DelibContext,
    consequences_text: str,
    mode: Literal["full", "thin"] = "full",
) -> str:
    """
    Costruisce il prompt per l'hindsight evaluator (batch mode).

    Args:
        context: DelibContext con request, draft e risk signals
        consequences_text: Testo formattato delle conseguenze simulate
        mode: "full" = draft completo, "thin" = summary + key_points + delta

    Returns:
        Prompt string pronto per l'LLM
    """
    request = context.user_prompt or ""
    instructions = HINDSIGHT_SHARED_INSTRUCTIONS.format(consequences=consequences_text)

    if mode == "full":
        risk_signals = context.get_risk_signals_str() or "none"
        return HINDSIGHT_BATCH_FULL_TEMPLATE.format(
            consequences=consequences_text,
            request=request,
            response=context.draft_text_full or "",
            risk_signals=risk_signals,
        )

    sections = build_thin_context_sections(context)
    return HINDSIGHT_BATCH_THIN_TEMPLATE.format(
        request=request,
        response_summary=sections["response_summary"],
        key_points=sections["key_points"],
        risk_signals=sections["risk_signals"],
        change_log_section=sections["change_log_section"],
        instructions=instructions,
    )
