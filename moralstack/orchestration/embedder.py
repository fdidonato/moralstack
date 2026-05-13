"""
Embedder — text-to-vector embedding for semantic equivalence checks.

The SemanticDecisionLedger (Step 4) uses an Embedder to detect when two queries
are semantically equivalent — so that a previous deliberation can be reused
instead of recomputed.

Defines:
- EmbedderProtocol: structural Protocol for any embedder.
- OpenAIEmbedder: production implementation using OpenAI text-embedding-3-small.
- cosine_similarity: numpy-free pure function for similarity scoring.

Normative reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §5.6.

Architectural decision: the design suggests np.ndarray as the embedding type,
but we use list[float] to avoid adding numpy as a hard dependency. Embeddings
are 1536 floats; the overhead of pure-Python cosine similarity is negligible
(microseconds per call) and the dependency footprint matters for the SDK.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# =============================================================================
# Protocol
# =============================================================================


class EmbedderProtocol(Protocol):
    """Structural protocol for any text embedder."""

    def embed(self, text: str) -> list[float]:
        """
        Compute the embedding vector for the given text.

        Args:
            text: the input text to embed. Empty or whitespace-only input
                should still produce a valid (zero-or-near-zero) vector
                — the caller decides whether to treat empty embeddings
                specially.

        Returns:
            A list of floats representing the embedding vector. The length
            depends on the underlying model (1536 for text-embedding-3-small).
        """
        ...


# =============================================================================
# Pure helpers (numpy-free)
# =============================================================================


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Compute cosine similarity between two equally-sized vectors.

    Cosine similarity = dot(a, b) / (||a|| * ||b||), in the range [-1.0, 1.0].
    Returns 0.0 when either vector has zero magnitude (degenerate case).

    Args:
        a: first vector.
        b: second vector. MUST have the same length as a.

    Returns:
        Cosine similarity score. 1.0 means identical direction;
        0.0 means orthogonal; -1.0 means opposite direction.

    Raises:
        ValueError: when the two vectors have different lengths.
    """
    if len(a) != len(b):
        raise ValueError(f"cosine_similarity requires equally-sized vectors, got len(a)={len(a)} and len(b)={len(b)}")
    if not a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for ai, bi in zip(a, b):
        dot += ai * bi
        norm_a += ai * ai
        norm_b += bi * bi
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


# =============================================================================
# Production implementation: OpenAIEmbedder
# =============================================================================


# Default OpenAI embedding model. Override with OPENAI_EMBEDDING_MODEL env var
# or via the model= argument.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


class OpenAIEmbedder:
    """
    OpenAI-backed embedder. Uses `text-embedding-3-small` by default (1536 dims).

    Configuration resolution priority:
        1. Constructor arguments (api_key, model, base_url).
        2. Environment variables (OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, OPENAI_BASE_URL).

    The OpenAI client is lazily initialized at construction time (eager init,
    same pattern as OpenAIPolicy in moralstack/models/policy.py).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """
        Args:
            api_key: override OPENAI_API_KEY (default: from env).
            model: override OPENAI_EMBEDDING_MODEL (default: text-embedding-3-small).
            base_url: override OPENAI_BASE_URL (default: from env).

        Raises:
            ValueError: when no api_key is provided and OPENAI_API_KEY is not set.
            ImportError: when the `openai` package is not installed.
        """
        resolved_api_key = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
        if not resolved_api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Pass api_key= to OpenAIEmbedder or set "
                "the OPENAI_API_KEY environment variable."
            )
        self._api_key = resolved_api_key
        self.model = model or os.getenv("OPENAI_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
        resolved_base_url = base_url or os.getenv("OPENAI_BASE_URL") or None

        try:
            import openai
        except ImportError as e:
            raise ImportError("The OpenAI client is required for OpenAIEmbedder. Install with: pip install openai") from e

        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if resolved_base_url:
            kwargs["base_url"] = resolved_base_url
        self._client = openai.OpenAI(**kwargs)

    def embed(self, text: str) -> list[float]:
        """
        Compute the embedding vector for the given text via OpenAI.

        Args:
            text: input text. Empty input is passed through to OpenAI;
                the resulting embedding is whatever OpenAI returns (typically
                a low-magnitude vector).

        Returns:
            The embedding as a list of floats (1536 floats for the default model).

        Raises:
            RuntimeError: when the OpenAI call succeeds but returns an unexpected
                response shape. Network/API errors propagate from the openai client.
        """
        response = self._client.embeddings.create(model=self.model, input=text)
        # OpenAI v2 returns CreateEmbeddingResponse with .data: list[Embedding].
        # Each Embedding has .embedding: list[float].
        data = getattr(response, "data", None)
        if not data:
            raise RuntimeError(f"OpenAI embeddings.create returned no data for model={self.model!r}")
        first = data[0]
        embedding = getattr(first, "embedding", None)
        if embedding is None:
            raise RuntimeError(
                f"OpenAI embeddings.create returned an entry without 'embedding' attribute for model={self.model!r}"
            )
        return list(embedding)
