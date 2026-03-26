"""
Prompt di retry centralizzati per parsing JSON fallito.
Usati da critic, perspectives, simulator, hindsight.
"""

from __future__ import annotations

from moralstack.prompts.simulator_prompt import SIMULATOR_ENUMS, SIMULATOR_SCHEMA_SKELETON

RETRY_PREFIX = "Your previous response was not valid JSON. " "Please return ONLY valid JSON with no additional text."

# Critic: schema decision/violations/revision_guidance
RETRY_CRITIC = RETRY_PREFIX + """

Expected format:
{{
  "decision": "PROCEED" | "REVISE" | "REFUSE",
  "violated_hard": true | false,
  "violations": [
    {{
      "principle_id": "string",
      "severity": number between 0.0 and 1.0,
      "rationale": "string",
      "evidence": "string"
    }}
  ],
  "revision_guidance": "string"
}}

Return only valid JSON:"""

# Perspectives: schema compatto
RETRY_PERSPECTIVES = """ERROR: Your response was not valid JSON.
Output ONLY a JSON object like this example:
{{"approval_score": 0.7, "concerns": [], "suggestions": [], "rationale": "Good response"}}
JSON:"""

# Simulator: usa schema condiviso
RETRY_SIMULATOR = (
    RETRY_PREFIX
    + """

Use ONLY these values:
"""
    + SIMULATOR_ENUMS
    + """

"""
    + SIMULATOR_SCHEMA_SKELETON
    + """

Return only valid JSON:"""
)

# Hindsight single evaluation
RETRY_HINDSIGHT_SINGLE = RETRY_PREFIX + """

Expected format:
{{
  "safety": number between -1.0 and 1.0,
  "helpfulness": number between -1.0 and 1.0,
  "honesty": number between -1.0 and 1.0,
  "harm_probability": number between 0.0 and 1.0,
  "benefit_probability": number between 0.0 and 1.0,
  "confidence": number between 0.0 and 1.0,
  "rationale": "string"
}}

Return only valid JSON:"""

# Hindsight batch
RETRY_HINDSIGHT_BATCH = RETRY_PREFIX + """

Expected format:
{{
  "evaluations": [
    {{
      "scenario_id": "string",
      "safety": number between -1.0 and 1.0,
      "helpfulness": number between -1.0 and 1.0,
      "honesty": number between -1.0 and 1.0,
      "harm_probability": number between 0.0 and 1.0,
      "benefit_probability": number between 0.0 and 1.0,
      "confidence": number between 0.0 and 1.0,
      "rationale": "string"
    }}
  ]
}}

Return only valid JSON:"""
