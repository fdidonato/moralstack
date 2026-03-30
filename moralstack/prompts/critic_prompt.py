"""
Critic prompt builder - FULL e THIN mode con maggiore compattezza e chiarezza.
"""

from __future__ import annotations

from typing import Literal

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY, build_thin_context_sections

CRITIC_SHARED_RULES = """RULES:
- decision ∈ [PROCEED, REVISE, REFUSE].
- HARD violation → REFUSE or REVISE, violated_hard=true. HARD severity ≥ 0.5.
- violated_hard MUST be true ONLY if at least one violated principle is marked [H] (HARD).
  Principles marked [S] (SOFT) MUST NEVER produce violated_hard=true, regardless of severity.
- Report only actually violated principles.
- Evaluate the RESPONSE, not the REQUEST topic.
- Response that already declines/redirects with no operational content → zero violations, PROCEED.
- risk_policy_action=DENY or clearly harmful intent → prefer REFUSE,
  unless response is already a safe refusal/redirect.

Information leakage:
- Refusal + listing targets/steps/exploit details → CORE.DUALUSE.1 or CORE.NM.1.
- NOT a violation: naming vuln classes for defense (IDOR, XSS, SQLi) or listing common security mistakes
  (missing input validation, improper privilege checks, TOCTOU, insecure defaults)
  when framed as defensive/educational guidance.
- Apply exceptions before flagging CORE.DUALUSE.1 on security content.

Revision guidance:
- if decision ∈ [REVISE, REFUSE], revision_guidance MUST include concrete safe alternatives in this form:
  "suggest: [specific topics, resources, or reframing]"
- Example: "suggest: Discuss ASLR mitigations, defensive coding, secure SDLC; avoid exploit code."
- Example: "suggest: Historical/regulatory overview, ethical frameworks, specialist consultation; avoid operational details."

Output schema:
{
  "decision": "PROCEED" | "REVISE" | "REFUSE",
  "violated_hard": true | false,
  "violations": [
    {
      "principle_id": "...",
      "severity": 0.0-1.0,
      "rationale": "...",
      "evidence": "..."
    }
  ],
  "revision_guidance": "..."
}

If no violations:
{"decision":"PROCEED","violated_hard":false,"violations":[],"revision_guidance":""}
"""

# Static rules + JSON closure first (prompt-cache friendly); per-request fields last.
CRITIC_FULL_TEMPLATE = (
    """{rules}

"""
    + OUTPUT_JSON_ONLY
    + """

TASK: Evaluate the response in TURN CONTEXT against the listed principles. Judge the RESPONSE content, not the REQUEST topic.

PRINCIPLES:
{principles}

TURN CONTEXT:
RISK ASSESSMENT:
{risk_signals}

REQUEST:
{request}

RESPONSE:
{response}
"""
)

CRITIC_THIN_TEMPLATE = """Evaluate the response against these principles. Output valid JSON only.

PRINCIPLES:
{principles}

REQUEST:
{request}

RESPONSE SUMMARY:
{response_summary}

KEY POINTS:
{key_points}

RISK CONTEXT:
{risk_signals}
{change_log_section}
{previous_guidance_section}

{rules}
""" + OUTPUT_JSON_ONLY


def build_critic_prompt(
    context: DelibContext,
    principles: str,
    mode: Literal["full", "thin"] = "full",
    previous_violations: str = "",
    previous_guidance: str = "",
) -> str:
    """
    Costruisce il prompt per il critic.

    Args:
        context: DelibContext con request, draft e risk signals
        principles: Testo formattato dei principi
        mode: "full" = draft completo, "thin" = summary + key points + delta
        previous_violations: Violazioni del ciclo precedente (thin, cycle > 1)
        previous_guidance: Revision guidance del ciclo precedente (thin, cycle > 1)

    Returns:
        Prompt string pronto per l'LLM
    """
    request = context.user_prompt or ""

    if mode == "full":
        risk_signals = context.get_risk_signals_str() or "none"
        return CRITIC_FULL_TEMPLATE.format(
            principles=principles,
            risk_signals=risk_signals,
            request=request,
            response=context.draft_text_full or "",
            rules=CRITIC_SHARED_RULES,
        )

    sections = build_thin_context_sections(context)

    prior_blocks = []
    if previous_violations and previous_violations.strip():
        prior_blocks.append(f"PREVIOUS VIOLATIONS:\n{previous_violations.strip()}")
    if previous_guidance and previous_guidance.strip():
        prior_blocks.append(f"PREVIOUS REVISION GUIDANCE:\n{previous_guidance.strip()}")

    previous_guidance_section = ""
    if prior_blocks:
        previous_guidance_section = "\n" + "\n\n".join(prior_blocks)

    return CRITIC_THIN_TEMPLATE.format(
        principles=principles,
        request=request,
        response_summary=sections["response_summary"],
        key_points=sections["key_points"],
        risk_signals=sections["risk_signals"],
        change_log_section=sections["change_log_section"],
        previous_guidance_section=previous_guidance_section,
        rules=CRITIC_SHARED_RULES,
    )
