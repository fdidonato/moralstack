"""
Prompt di retry centralizzati per parsing JSON fallito.
Usati da critic, perspectives, simulator, hindsight.

Default re-exports of the packaged compositions (byte-identical to the former literals). The modules read
the same texts from their own ``PromptSet`` (``retry.prefix``, ``critic.retry``, ``simulator.retry``, ...).
"""

from __future__ import annotations

from moralstack.prompts.catalog import default_prompt_set
from moralstack.prompts.simulator_prompt import SIMULATOR_ENUMS, SIMULATOR_SCHEMA_SKELETON  # noqa: F401 - historical names

_DEFAULT = default_prompt_set()

RETRY_PREFIX = _DEFAULT.text("retry.prefix")

# Critic: schema decision/violations/revision_guidance
RETRY_CRITIC = _DEFAULT.text("critic.retry")

# Perspectives: schema compatto
RETRY_PERSPECTIVES = _DEFAULT.text("perspectives.retry")

# Simulator: usa schema condiviso
RETRY_SIMULATOR = _DEFAULT.text("simulator.retry")

# Hindsight single evaluation
RETRY_HINDSIGHT_SINGLE = _DEFAULT.text("hindsight.retry.single")

# Hindsight batch
RETRY_HINDSIGHT_BATCH = _DEFAULT.text("hindsight.retry.batch")
