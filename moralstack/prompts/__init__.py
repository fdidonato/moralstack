"""Prompt builders for the deliberative modules (critic, perspectives, simulator, hindsight).

The builder names are resolved lazily (PEP 562) so that importing ``moralstack.prompts.catalog`` stays cheap:
the builder modules load the packaged default prompt set at import (the fail-fast point), the catalog must not.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from moralstack.prompts.critic_prompt import build_critic_prompt
    from moralstack.prompts.hindsight_prompt import build_hindsight_prompt
    from moralstack.prompts.perspectives_prompt import (
        build_perspectives_prompt,
        build_perspectives_system_prompt,
        build_perspectives_user_prompt,
    )
    from moralstack.prompts.simulator_prompt import build_simulator_prompt

__all__ = [
    "build_critic_prompt",
    "build_perspectives_prompt",
    "build_perspectives_system_prompt",
    "build_perspectives_user_prompt",
    "build_simulator_prompt",
    "build_hindsight_prompt",
]

_LAZY = {
    "build_critic_prompt": "moralstack.prompts.critic_prompt",
    "build_hindsight_prompt": "moralstack.prompts.hindsight_prompt",
    "build_perspectives_prompt": "moralstack.prompts.perspectives_prompt",
    "build_perspectives_system_prompt": "moralstack.prompts.perspectives_prompt",
    "build_perspectives_user_prompt": "moralstack.prompts.perspectives_prompt",
    "build_simulator_prompt": "moralstack.prompts.simulator_prompt",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
