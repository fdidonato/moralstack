"""
Simulator prompt builder.

Generates potential consequences for an AI response.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY

# JSON skeleton
SIMULATOR_SCHEMA_SKELETON = """{{
  "consequences": [
    {{
      "text": "",
      "likelihood": 0.0,
      "scenario_type": "",
      "outcome_valence": 0.0,
      "affected_stakeholders": [],
      "harm_type": "",
      "harm_severity": 0.0,
      "harm_scope": "",
      "reversibility": 0.0
    }}
  ]
}}"""


# Allowed enums
SIMULATOR_ENUMS = """
Allowed values (use ONLY these):

scenario_type:
immediate_harm | downstream_misuse | social_impact | legal_consequence | positive_outcome

harm_type:
financial_loss | physical_harm | psychological_harm | privacy_breach |
legal_risk | reputational_harm | security_breach | discrimination |
misinformation | exploitation | self_harm | emotional_manipulation |
financial_deception | none

harm_scope:
individual | group | societal | systemic
"""


# Mapping rule for harm_type
HARM_TYPE_MAPPING_RULE = (
    "violence/weapons/explosives → physical_harm | security_breach; "
    "finance/investment → financial_loss; "
    "privacy/data → privacy_breach; "
    "legal/illegal → legal_risk; "
    "misinformation → misinformation; "
    "exploitation → exploitation; "
    "self-harm → self_harm; "
    "emotional/mental → psychological_harm; "
    "else → none"
)

DOMAIN_GUIDANCE: dict[str, str] = {
    "cybersecurity": """For CYBERSECURITY domain, distinguish between:

DEFENSIVE intent:
- "common security mistakes"
- "how to prevent"
- "security best practices"

Focus on positive outcomes such as:
- improved security awareness
- better defensive practices

OFFENSIVE intent:
- "how to hack"
- "bypass security"
- "exploit vulnerabilities"

Focus on negative outcomes such as:
- unauthorized access attempts
- system compromise""",
}

DEFAULT_DOMAIN_GUIDANCE = "Consider whether the intent is educational/protective vs exploitative."

DEVELOPER_CONTRACT_EVALUATION = """
Developer contract compliance:
- If a developer contract is present, consider whether the RESPONSE violates explicit constraints in that contract.
- Include response format, structure, tone, role behavior, workflow behavior, and output content in this evaluation.
- Treat emission, revelation, repetition, transformation, quotation, embedding, or inclusion of contract-restricted
  content under unsatisfied contract conditions as a concrete safety consequence.
- Apply this to prose, code, examples, tests, expected outputs, comments, summaries, translations, explanations,
  validators, or pseudocode.
- Prefer harm_type security_breach or privacy_breach when the primary consequence is unauthorized disclosure of
  contract-restricted content.
"""


# FULL template: longest static prefix first (prompt caching); TURN PARAMETERS and draft last.
SIMULATOR_FULL_STATIC_PREFIX = (
    """
CONTRACT (fixed instructions; identical across requests when this prefix is reused):

Requirements:

- Generate exactly N potential consequences for one AI response, where N is the integer num_scenarios in
  TURN PARAMETERS below.
- Return exactly N objects inside "consequences".

- Include a balanced mix:
  - at least one positive outcome (scenario_type: positive_outcome)
  - at least one risk or harm scenario.

- Do not focus only on harms; consider both risks and benefits.

"""
    + DEVELOPER_CONTRACT_EVALUATION
    + """

Text constraints:

- consequence.text:
  - max 15 words (do not exceed)
  - use short noun phrases
  - no explanations

Stakeholders:

- affected_stakeholders:
  - max 3 short strings

Fields required for each consequence:

- likelihood
- scenario_type
- outcome_valence
- affected_stakeholders
- harm_type
- harm_severity
- harm_scope
- reversibility

Choose harm_type based on REQUEST topic using this mapping:

"""
    + HARM_TYPE_MAPPING_RULE
    + """

Minimal schema skeleton:

"""
    + SIMULATOR_SCHEMA_SKELETON
    + """

"""
    + SIMULATOR_ENUMS
    + """

"""
    + OUTPUT_JSON_ONLY
    + """
"""
)

SIMULATOR_FULL_TEMPLATE = SIMULATOR_FULL_STATIC_PREFIX + """

---

TURN PARAMETERS:
num_scenarios: {num_scenarios}

REQUEST:
{request}

RESPONSE:
{response}

RISK CONTEXT:
{risk_signals}

DOMAIN CONTEXT:
{domain}

DOMAIN-SPECIFIC GUIDANCE:

{domain_guidance}
"""


def build_simulator_prompt(
    context: DelibContext,
    num_scenarios: int = 3,
) -> str:
    """
    Build prompt for the consequence simulator.

    Args:
        context: DelibContext with request, draft, and risk signals
        num_scenarios: number of consequences to generate
    Returns:
        Prompt string for the LLM
    """

    request = context.user_prompt or ""

    response = context.draft_text_full or ""
    domain = (
        getattr(context, "domain_overlay", None)
        or getattr(context, "domain", None)
        or getattr(context, "detected_domain", "general")
    )
    domain_str = domain if isinstance(domain, str) else "general"
    domain_guidance = getattr(context, "simulator_domain_guidance", "") or DOMAIN_GUIDANCE.get(
        domain_str.lower(), DEFAULT_DOMAIN_GUIDANCE
    )
    risk_signals = context.get_risk_signals_str() or "none"

    return SIMULATOR_FULL_TEMPLATE.format(
        num_scenarios=num_scenarios,
        request=request,
        response=response,
        risk_signals=risk_signals,
        domain=domain_str,
        domain_guidance=domain_guidance,
    )
