"""
Hindsight prompt builder.

The prompt texts live in ``moralstack/prompts/data/hindsight.yaml``. The module-level constants below are
default re-exports (byte-identical to the former literals); builders and modules read their own ``PromptSet``.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import HINDSIGHT_BASE_FRAMING, OUTPUT_JSON_ONLY  # noqa: F401 - historical names
from moralstack.prompts.catalog import NO_CONTEXT, PromptSet, default_prompt_set

_DEFAULT = default_prompt_set()

# The fragment carries no terminal newline (the composition's ``end`` owns it): re-add it for the historical value.
DEVELOPER_CONTRACT_EVALUATION = _DEFAULT.text("hindsight.devcontract_evaluation") + "\n"

# Prompt-caching reorder (A4): the base hindsight role framing + the batch static
# evaluation instructions + the "evaluations"-rooted output schema + the (static)
# developer contract evaluation block, sent as the system prefix. Used ONLY by the
# batch path (hindsight_module.py _evaluate_batch); NEVER by the single-scenario or
# non-batch aggregate paths, whose output is a root object (see
# the ``hindsight.single.system`` composition).
HINDSIGHT_BATCH_SYSTEM_PROMPT = _DEFAULT.render("hindsight.batch.system", NO_CONTEXT)

# Dynamic-only: per-request fields last (static contract lives entirely
# in the batch system prompt above).
HINDSIGHT_BATCH_FULL_TEMPLATE = _DEFAULT.template("hindsight.batch.user")


def build_hindsight_prompt(
    context: DelibContext,
    consequences_text: str,
    prompt_set: PromptSet | None = None,
) -> str:
    """
    Costruisce il prompt per l'hindsight evaluator (batch mode).

    Args:
        context: DelibContext con request, draft e risk signals
        consequences_text: Testo formattato delle conseguenze simulate
        prompt_set: PromptSet da cui leggere il template (default: set impacchettato)
    Returns:
        Prompt string pronto per l'LLM (dynamic content only; the static
        evaluation instructions/schema live in the ``hindsight.batch.system`` composition).
    """
    request = context.user_prompt or ""
    risk_signals = context.get_risk_signals_str() or "none"
    return (prompt_set or default_prompt_set()).format(
        "hindsight.batch.user",
        consequences=consequences_text,
        request=request,
        response=context.draft_text_full or "",
        risk_signals=risk_signals,
    )
