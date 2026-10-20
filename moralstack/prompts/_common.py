"""Shared prompt constants.

Default re-exports of the packaged fragments (``moralstack/prompts/data/*.yaml``): the values are
byte-identical to the pre-externalization literals. Runtime code reads its own ``PromptSet``, never these.
"""

from __future__ import annotations

from moralstack.prompts.catalog import default_prompt_set

_DEFAULT = default_prompt_set()

OUTPUT_JSON_ONLY = _DEFAULT.text("common.output_json_only")

# Base hindsight role framing — single source of truth shared by the hindsight
# system prompts. `hindsight_module.py` (HINDSIGHT_SYSTEM_PROMPT /
# HINDSIGHT_SINGLE_SYSTEM_PROMPT) and `hindsight_prompt.py`
# (HINDSIGHT_BATCH_SYSTEM_PROMPT) both import it, so the batch and single/
# individual paths carry byte-identical base framing without a duplicated literal.
HINDSIGHT_BASE_FRAMING = _DEFAULT.text("hindsight.base")


def double_braces(text: str) -> str:
    """``{``/``}`` -> ``{{``/``}}``: the source form of the dead-path constants that are never formatted."""
    return text.replace("{", "{{").replace("}", "}}")
