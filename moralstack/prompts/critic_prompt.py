"""
Critic prompt builder.

The prompt texts live in ``moralstack/prompts/data/critic.yaml``. The module-level constants below are
default re-exports (byte-identical to the former literals); builders and modules read their own ``PromptSet``.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY  # noqa: F401 - historical module-level name
from moralstack.prompts.catalog import NO_CONTEXT, PromptSet, default_prompt_set

_DEFAULT = default_prompt_set()

# The three RULES fragments joined as the ``critic.full.system`` composition joins them.
CRITIC_SHARED_RULES = (
    _DEFAULT.text("critic.rules.head")
    + "\n\n"
    + _DEFAULT.text("critic.rules.devcontract")
    + "\n\n"
    + _DEFAULT.text("critic.rules.tail")
)

# Verbatim base framing of the critic. The quick-check system prompt
# (critic_module.CRITIC_SYSTEM_PROMPT) is the very same fragment (``critic.base``).
_CRITIC_FULL_BASE_PROMPT = _DEFAULT.text("critic.base")

# Prompt-caching reorder (Part A): the static rules + JSON closure live
# entirely in the system prefix, so the whole thing is byte-identical across
# requests for the FULL critique path. Used ONLY at the two FULL-critique
# call sites in critic_module.py.
CRITIC_FULL_SYSTEM_PROMPT = _DEFAULT.render("critic.full.system", NO_CONTEXT)

# Dynamic-only: per-request fields last (nothing static remains here).
CRITIC_FULL_TEMPLATE = _DEFAULT.template("critic.full.user")


def build_critic_prompt(
    context: DelibContext,
    principles: str,
    previous_violations: str = "",
    previous_guidance: str = "",
    prompt_set: PromptSet | None = None,
) -> str:
    """
    Costruisce il prompt per il critic.

    Args:
        context: DelibContext con request, draft e risk signals
        principles: Testo formattato dei principi
        previous_violations: Violazioni del ciclo precedente
        previous_guidance: Revision guidance del ciclo precedente
        prompt_set: PromptSet da cui leggere i template (default: set impacchettato)

    Returns:
        Prompt string pronto per l'LLM (dynamic content only; the static
        rules/schema live in the ``critic.full.system`` composition).
    """
    ps = prompt_set or default_prompt_set()
    request = context.user_prompt or ""

    risk_signals = context.get_risk_signals_str() or "none"

    prior_blocks = []
    if previous_violations and previous_violations.strip():
        prior_blocks.append(
            ps.format("critic.full.previous_violations_block", previous_violations=previous_violations.strip())
        )
    if previous_guidance and previous_guidance.strip():
        prior_blocks.append(ps.format("critic.full.previous_guidance_block", previous_guidance=previous_guidance.strip()))

    previous_guidance_section = ""
    if prior_blocks:
        previous_guidance_section = "\n" + "\n\n".join(prior_blocks)

    return ps.format(
        "critic.full.user",
        principles=principles,
        risk_signals=risk_signals,
        request=request,
        response=context.draft_text_full or "",
        previous_guidance_section=previous_guidance_section,
    )
