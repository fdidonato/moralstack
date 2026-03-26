"""
Tipi base per modelli LLM in MoralStack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional


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


@dataclass(frozen=True)
class GenerationResult:
    """
    Risultato della generazione.
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    text: str
    tokens_used: int
    finish_reason: Literal["stop", "length", "content_filter"]
    logprobs: Optional[List[float]] = None
    prompt_used: Optional[str] = None
    system_used: Optional[str] = None
