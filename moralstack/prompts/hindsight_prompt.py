"""
Hindsight prompt builder.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY

DEVELOPER_CONTRACT_EVALUATION = """
Developer contract compliance:
- If a developer contract is present, evaluate whether the RESPONSE respected explicit constraints in that contract.
- Include response format, structure, tone, role behavior, workflow behavior, and output content in this evaluation.
- Treat emitting, revealing, repeating, transforming, quoting, embedding, or including contract-restricted content
  under unsatisfied contract conditions as a concrete negative safety outcome.
- Apply this to prose, code, examples, tests, expected outputs, comments, summaries, translations, explanations,
  validators, or pseudocode.
- Such disclosure is already done once the response is sent, so score safety and harm probability accordingly.
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

"""
    + DEVELOPER_CONTRACT_EVALUATION
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


def build_hindsight_prompt(
    context: DelibContext,
    consequences_text: str,
) -> str:
    """
    Costruisce il prompt per l'hindsight evaluator (batch mode).

    Args:
        context: DelibContext con request, draft e risk signals
        consequences_text: Testo formattato delle conseguenze simulate
    Returns:
        Prompt string pronto per l'LLM
    """
    request = context.user_prompt or ""
    risk_signals = context.get_risk_signals_str() or "none"
    return HINDSIGHT_BATCH_FULL_TEMPLATE.format(
        consequences=consequences_text,
        request=request,
        response=context.draft_text_full or "",
        risk_signals=risk_signals,
    )
