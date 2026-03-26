"""OpenAI client configuration for constitution layer (domain agents, prefilter)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAIClientConfig:
    """Configuration for OpenAI API usage in constitution evaluation."""

    api_key: str | None
    model: str

    @classmethod
    def default(cls) -> "OpenAIClientConfig":
        """Default config with env-based API key and gpt-4o-mini."""
        return cls(api_key=os.getenv("OPENAI_API_KEY"), model="gpt-4o-mini")

    @classmethod
    def with_env_fallback(
        cls,
        api_key: str | None,
        model: str = "gpt-4o-mini",
    ) -> "OpenAIClientConfig":
        """Resolve api_key from OPENAI_API_KEY env if None."""
        return cls(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            model=model,
        )
