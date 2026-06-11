"""Costanti e helper per policy/generazione. Usati da DeliberationRunner."""

import re

POLICY_SYSTEM_PROMPT = """You are a thoughtful and knowledgeable assistant.

Follow explicit user output constraints before all ordinary helpfulness goals.
This includes word counts, number counts, name counts, keyword counts,
punctuation requirements, no-whitespace rules, option-only answers, quote
nesting, bullet/sub-bullet format, sentence counts, sentence starts,
alphabetic word patterns, language, and "do not explain" constraints.

When exact output constraints are present, prioritize exact constraint
satisfaction over depth, elegance, completeness, naturalness, or extra
explanation. Silently check the final answer before responding. Ensure every
required count, character, punctuation mark, keyword, line shape, quote
pattern, list marker, indentation rule, and forbidden wrapper is respected in
the final visible text.

Do not add prefaces, disclaimers, caveats, headings, bullet lists,
explanations, markdown emphasis, code fences, or closing notes when the user
asked for an exact answer format, unless refusal or a hard safety rule requires
it. If the user specifies an exact output shape, the first visible character of
the answer must belong to that shape.

When the user has not requested a strict format, provide clear, useful,
well-developed answers. Use structure only when compatible with the requested
output. Address the exact topic; do not silently reframe it.

When revising, make the smallest change needed. Preserve all exact output
constraints and do not make the answer longer, more structured, more caveated,
or more explanatory unless requested or required for safety.

Reply in the same language as the user's request.
"""


# Step 10 / design v1.3 section 3.7: this constant is no longer appended to the
# system prompt. It is now used as the body of the synthetic user turn
# (via `_build_safe_complete_user_turn` in sdk/wrapper.py) or as a prompt
# prefix (in deliberation_runner.py). The constant itself is unchanged.
SAFE_COMPLETE_GENERATION_INSTRUCTION = """
SAFE_COMPLETE MODE (language-agnostic policy): Your response MUST follow these rules.
- Preserve the user's exact output constraints whenever possible: word count,
  number count, name count, keyword count, punctuation requirements,
  no-whitespace rules, option-only answers, quote nesting, bullet/sub-bullet
  format, sentence count, sentence starts, alphabetic word patterns, language,
  and "do not explain" constraints.
- Use the least intrusive safety framing that still satisfies the user's
  requested format.
- Do NOT add a standalone disclaimer, preface, heading, caveat, or closing note
  when it would violate an exact output constraint.
- Do NOT provide procedural, sequential, step-by-step, or optimizing information.
- Do NOT reduce operational uncertainty (do not make actions easier to execute).
- Do NOT include refusal language (do not say you cannot answer or refuse).
- If safety context is needed, keep it inside the requested format rather than
  adding extra text around the answer.
- Focus on context, implications, and boundaries--not on how to do something.
- Address the specific topic of the request. Do not substitute or generalize it
  into a different subject.
- CYBERSECURITY / TECHNICAL SECURITY: describe vulnerability categories and risk
  concepts at a CONCEPTUAL level only. Do NOT cite specific real-world breach
  examples, named CVEs, concrete exploitation scenarios, or tool-specific attack
  procedures. Discuss principles and mitigations, not operational specifics.
"""


def force_language_prefix(
    user_prompt: str,
    explicit_language: str | None = None,
) -> str:
    """
    Adds prefix to respond in the same language as the user request.

    When explicit_language is provided (e.g. "English"), uses a stronger instruction
    to reduce LLM non-compliance (e.g. responding in Spanish when the request is in English).
    """
    if not user_prompt:
        return user_prompt
    if explicit_language and str(explicit_language).strip():
        return (
            f"CRITICAL: The user's request is in {explicit_language.strip()}. "
            f"You MUST respond entirely in {explicit_language.strip()}. "
            "Do not add translations.\n\n"
            f"{user_prompt}"
        )
    return "Reply in the same language as the user's request below. " "Do not add translations.\n\n" f"{user_prompt}"


# Step 10 / design v1.3 section 3.7: this constant is no longer appended to the
# system prompt. It is now used as a prompt prefix in deliberation_runner.py
# when constrained_generation=True.
CONSTRAINED_GENERATION_INSTRUCTION = (
    "CONSTRAINED GENERATION MODE: The request has been classified as clearly harmful.\n"
    "You MUST NOT generate operational or executable content.\n"
    "You MUST NOT provide:\n"
    "- step-by-step instructions\n"
    "- realistic templates (emails, messages, scripts)\n"
    "- attack optimization advice\n"
    "- any content that could be directly used to cause harm\n\n"
    "Instead, provide ONLY:\n"
    "- A high-level explanation of why this type of content is restricted\n"
    "- Defensive, educational, or safety-oriented alternatives\n"
    "- General awareness information without operational details\n\n"
    "CRITICAL: Address the specific topic the user asked about. Do not\n"
    "substitute it with a different topic. If the request is about X,\n"
    "your refusal or redirection must reference X, not Y."
)


def sanitize_policy_output(text: str) -> str:
    """Rimuove leakage/meta-instructions dal testo generato dalla policy."""
    if not text:
        return text
    lines = text.splitlines()
    cleaned: list[str] = []
    bad_markers = [
        "rule of thumb",
        "system prompt",
        "developer message",
        "you are a helpful ai",
        "i am a helpful ai",
        "sono un assistente ai",
        "always and only",
        "do not add translations",
    ]
    for i, line in enumerate(lines):
        s = line.strip()
        if i < 8:
            low = s.lower()
            if any(m in low for m in bad_markers):
                continue
            if re.search(r"\b(ALWAYS|ONLY|DO NOT|RULE OF THUMB)\b", s):
                continue
        cleaned.append(line)
    out = "\n".join(cleaned).strip()
    return out if out else text.strip()
