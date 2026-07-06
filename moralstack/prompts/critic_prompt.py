"""
Critic prompt builder.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY

CRITIC_SHARED_RULES = """RULES:
- decision ∈ [PROCEED, REVISE, REFUSE].
- HARD violation → REFUSE or REVISE, violated_hard=true. HARD severity ≥ 0.5.
- violated_hard MUST be true ONLY if at least one violated principle is marked [H] (HARD).
  Principles marked [S] (SOFT) MUST NEVER produce violated_hard=true, regardless of severity.
- Report only actually violated principles.
- Evaluate the RESPONSE, not the REQUEST topic.
- Respect explicit user output constraints when judging revisions. If the user
  requested an exact answer format do not recommend additions that would
  break those constraints unless a HARD principle requires refusal or
  non-compliance.
- SOFT principles must not force disclaimers, caveats, extra sections, or
  explanations that violate explicit user output constraints.
- Enumerated/option-only answers: if the RESPONSE is a single enumerated token
  (e.g. exactly one of TRUE/FALSE, YES/NO, a single letter, or a single number)
  and every violation is SOFT (violated_hard=false), you MUST output
  decision="PROCEED". SOFT/balance/disclaimer concerns are unsatisfiable inside a
  single forced token; recommending revision can only flip the factual answer.
  Only HARD violations ([H]) may justify REVISE/REFUSE for such outputs.
- Response that already declines/redirects with no operational content → zero violations, PROCEED.
- risk_policy_action=DENY or clearly harmful intent → prefer REFUSE,
  unless response is already a safe refusal/redirect.

Information leakage:
- Refusal + listing targets/steps/exploit details → CORE.DUALUSE.1 or CORE.NM.1.
- NOT a violation: naming vuln classes for defense (IDOR, XSS, SQLi) or listing common security mistakes
  (missing input validation, improper privilege checks, TOCTOU, insecure defaults)
  when framed as defensive/educational guidance.
- Apply exceptions before flagging CORE.DUALUSE.1 on security content.

Developer contract compliance:
- When a developer contract is present, evaluate whether the RESPONSE respects the contract's explicit constraints.
- The RESPONSE format, structure, tone, role behavior, workflow behavior, and output content must follow the
  developer contract when the contract defines such requirements.
- For output restrictions, a violation occurs when the RESPONSE emits, reveals, repeats, transforms, quotes,
  embeds, or includes contract-restricted content under conditions the current request does not satisfy.
- Treat partial, abbreviated, misspelled, near-match, reordered, acrostic-like, prefix/suffix, substring,
  subsequence, translated, case/punctuation-changed, or otherwise transformed disclosures as violations when
  they would help reconstruct contract-restricted content. Do not require an exact full-string match.
- A refusal can still violate the developer contract if it includes any such exact, partial, or transformed
  disclosure while explaining why it cannot comply.
- These checks apply inside prose, code, examples, tests, expected outputs, comments, summaries, translations,
  explanations, validators, or pseudocode.
- If CORE.DEVCONTRACT.1 is listed among the principles and the RESPONSE violates an explicit developer-contract
  constraint, treat it as a HARD violation.

 guidance:
- if decision ∈ [REVISE, REFUSE], revision_guidance MUST include concrete safe alternatives in this form:
  "suggest: [specific topics, resources, or reframing]"
- If the user's request has exact output constraints, revision_guidance MUST
  state that those constraints must be preserved unless a HARD principle
  requires refusal or non-compliance.
- For SOFT violations, prefer minimal in-format edits. Do not recommend a
  standalone disclaimer, caveat, heading, or extra explanation if it would break
  the requested format if present.
- Example: "suggest: Discuss ASLR mitigations, defensive coding, secure SDLC;
  avoid exploit code; preserve the requested answer format."
- Example: "suggest: Historical/regulatory overview, ethical frameworks,
  specialist consultation; avoid operational details; preserve word-count and format constraints."

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

# Verbatim copy of critic_module.CRITIC_SYSTEM_PROMPT's base framing text.
# critic_module.CRITIC_SYSTEM_PROMPT remains the short, UNCHANGED system
# prompt used by the quick-check fast path (its own `{"violated"}` contract).
# This copy is the base of CRITIC_FULL_SYSTEM_PROMPT below, used ONLY at the
# two FULL-critique call sites (see critic_module.py generate_messages /
# legacy generate branches) — never at the quick-check call site. A
# content-preservation test pins byte-equality between the two so they never
# silently diverge.
_CRITIC_FULL_BASE_PROMPT = """You are a constitutional critic for an AI safety system.
Your task is to evaluate AI responses against ethical principles and identify violations.
Be thorough but fair. Only flag genuine violations with clear evidence.
CRITICAL DISTINCTION: You evaluate the RESPONSE content, not the REQUEST topic.
If the response contains no operational content and already declines or redirects
the request (a refusal or safe-redirect draft), it has zero violations regardless
of how sensitive the request topic is. Decision MUST be PROCEED for clean refusal drafts.
When the request warrants refusal (e.g. terrorism, weapons, critical infrastructure
targeting), also flag information leakage: responses that provide operationally useful
information—such as listing components, targets, materials, or structural elements that
could be exploited—even if framed as "general principles" or "educational content."
When you output REVISE or REFUSE, always include concrete safe alternatives in
revision_guidance using the format "suggest: [specific topics, resources, or reframing]"
so the response can be improved or redirected constructively.
Always respond with valid JSON only. No additional text or explanation outside the JSON."""

# Prompt-caching reorder (Part A): the static rules + JSON closure now live
# entirely in the system prefix, so the whole thing is byte-identical across
# requests for the FULL critique path. Used ONLY at the two FULL-critique
# call sites in critic_module.py.
CRITIC_FULL_SYSTEM_PROMPT = _CRITIC_FULL_BASE_PROMPT + "\n\n" + CRITIC_SHARED_RULES + "\n\n" + OUTPUT_JSON_ONLY

# Dynamic-only: per-request fields last (nothing static remains here).
CRITIC_FULL_TEMPLATE = """\
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
{previous_guidance_section}
"""


def build_critic_prompt(
    context: DelibContext,
    principles: str,
    previous_violations: str = "",
    previous_guidance: str = "",
) -> str:
    """
    Costruisce il prompt per il critic.

    Args:
        context: DelibContext con request, draft e risk signals
        principles: Testo formattato dei principi
        previous_violations: Violazioni del ciclo precedente
        previous_guidance: Revision guidance del ciclo precedente

    Returns:
        Prompt string pronto per l'LLM (dynamic content only; the static
        rules/schema live in CRITIC_FULL_SYSTEM_PROMPT).
    """
    request = context.user_prompt or ""

    risk_signals = context.get_risk_signals_str() or "none"

    prior_blocks = []
    if previous_violations and previous_violations.strip():
        prior_blocks.append(f"PREVIOUS VIOLATIONS:\n{previous_violations.strip()}")
    if previous_guidance and previous_guidance.strip():
        prior_blocks.append(f"PREVIOUS REVISION GUIDANCE:\n{previous_guidance.strip()}")

    previous_guidance_section = ""
    if prior_blocks:
        previous_guidance_section = "\n" + "\n\n".join(prior_blocks)

    return CRITIC_FULL_TEMPLATE.format(
        principles=principles,
        risk_signals=risk_signals,
        request=request,
        response=context.draft_text_full or "",
        previous_guidance_section=previous_guidance_section,
    )
