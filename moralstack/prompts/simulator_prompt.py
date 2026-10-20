"""
Simulator prompt builder.

Generates potential consequences for an AI response.

The prompt texts live in ``moralstack/prompts/data/simulator.yaml``. The module-level constants below are
default re-exports (byte-identical to the former literals); builders and modules read their own ``PromptSet``.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import OUTPUT_JSON_ONLY, double_braces  # noqa: F401 - OUTPUT_JSON_ONLY: historical name
from moralstack.prompts.catalog import NO_CONTEXT, PromptSet, default_prompt_set

_DEFAULT = default_prompt_set()

# JSON skeleton. The packaged fragment is in wire form (single braces); this constant keeps the historical
# format()-escaped form ("{{"/"}}") because it is shared with the (never formatted) legacy retry text.
SIMULATOR_SCHEMA_SKELETON = double_braces(_DEFAULT.text("simulator.schema_skeleton"))


# Allowed enums
SIMULATOR_ENUMS = _DEFAULT.text("simulator.enums")


# Mapping rule for harm_type
HARM_TYPE_MAPPING_RULE = _DEFAULT.text("simulator.harm_type_mapping_rule")

DOMAIN_GUIDANCE: dict[str, str] = {
    "cybersecurity": _DEFAULT.text("simulator.domain_guidance.cybersecurity"),
}

DEFAULT_DOMAIN_GUIDANCE = _DEFAULT.text("simulator.domain_guidance.default")

DEVELOPER_CONTRACT_EVALUATION = _DEFAULT.text("simulator.devcontract_evaluation")


# Prompt-caching reorder (Part A / A3): the base short framing shared by
# both the batch and seeded LLM paths (single source of truth: ``simulator.base``).
_SIMULATOR_BASE_SYSTEM_PROMPT = _DEFAULT.text("simulator.base")

# BATCH path system prompt (simulator_module.py _simulate_batch). Must NEVER be reused
# for the seeded path (different static contract — see A3 plan).
SIMULATOR_BATCH_SYSTEM_PROMPT = _DEFAULT.render("simulator.batch.system", NO_CONTEXT)

# FULL/BATCH static contract (incl. "exactly N" / num_scenarios): the batch system prompt after the base
# framing and its single "\n" separator.
SIMULATOR_FULL_STATIC_PREFIX = SIMULATOR_BATCH_SYSTEM_PROMPT[len(_SIMULATOR_BASE_SYSTEM_PROMPT) + 1 :]

# Dynamic-only: per-request fields last (static contract lives entirely
# in SIMULATOR_BATCH_SYSTEM_PROMPT above).
SIMULATOR_FULL_TEMPLATE = _DEFAULT.template("simulator.batch.user")

SIMULATOR_SEEDED_SYSTEM_PROMPT = _DEFAULT.render("simulator.seeded.system", NO_CONTEXT)

# SEEDED path static rubric — generates ONE consequence from a seed; distinct
# contract from the batch path (no "exactly N"/num_scenarios text): the seeded system prompt after the base
# framing and its "\n\n" separator.
SIMULATOR_SEEDED_STATIC_RUBRIC = SIMULATOR_SEEDED_SYSTEM_PROMPT[len(_SIMULATOR_BASE_SYSTEM_PROMPT) + 2 :]


def build_simulator_prompt(
    context: DelibContext,
    num_scenarios: int = 3,
    prompt_set: PromptSet | None = None,
) -> str:
    """
    Build prompt for the consequence simulator.

    Args:
        context: DelibContext with request, draft, and risk signals
        num_scenarios: number of consequences to generate
        prompt_set: PromptSet to read the template and the domain guidance from (default: packaged set)
    Returns:
        Prompt string for the LLM
    """
    ps = prompt_set or default_prompt_set()

    request = context.user_prompt or ""

    response = context.draft_text_full or ""
    domain = (
        getattr(context, "domain_overlay", None)
        or getattr(context, "domain", None)
        or getattr(context, "detected_domain", "general")
    )
    domain_str = domain if isinstance(domain, str) else "general"
    guidance_id = f"simulator.domain_guidance.{domain_str.lower()}"
    domain_guidance = getattr(context, "simulator_domain_guidance", "") or (
        ps.text(guidance_id) if ps.has(guidance_id) else ps.text("simulator.domain_guidance.default")
    )
    risk_signals = context.get_risk_signals_str() or "none"

    return ps.format(
        "simulator.batch.user",
        num_scenarios=num_scenarios,
        request=request,
        response=response,
        risk_signals=risk_signals,
        domain=domain_str,
        domain_guidance=domain_guidance,
    )
