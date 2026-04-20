"""
Base types for MoralStack LLM models.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional


@dataclass(frozen=True)
class GenerationConfig:
    """
    Configurazione per la generazione di testo.
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    stop_sequences: List[str] = field(default_factory=list)
    response_format: Any = None


@dataclass(frozen=True)
class GenerationResult:
    """
    Generation result.
    Immutable value object: any variation requires a new instance.
    """

    text: str
    tokens_used: int
    finish_reason: Literal["stop", "length", "content_filter"]
    logprobs: Optional[List[float]] = None
    prompt_used: Optional[str] = None
    system_used: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None

    def token_usage_json(self) -> str | None:
        """Serialize token usage breakdown as JSON string for observability.
        Returns None when no token data is available."""
        if self.tokens_used == 0 and not self.prompt_tokens:
            return None
        return json.dumps(
            {
                "prompt_tokens": self.prompt_tokens or 0,
                "completion_tokens": self.completion_tokens or 0,
                "total_tokens": self.tokens_used,
            }
        )
