"""Prompt builders per moduli deliberativi (critic, perspectives, simulator, hindsight)."""

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
