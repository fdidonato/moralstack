"""
Embedder — text-to-vector embedding for semantic equivalence checks.

The SemanticDecisionLedger (Step 4) uses an Embedder to detect when two queries
are semantically equivalent — so that a previous deliberation can be reused
instead of recomputed.

Defines:
- EmbedderProtocol: structural Protocol for any embedder.
- HashingEmbedder: pure-Python deterministic feature-hashing embedder (zero deps).
- LocalEmbedder: local embedder using fastembed when available, else HashingEmbedder.
- OpenAIEmbedder: opt-in implementation using OpenAI text-embedding-3-small.
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
import time
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
DEFAULT_HASHING_DIM = 512
DEFAULT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


class HashingEmbedder:
    """
    Pure-Python deterministic feature-hashing embedder. Zero external dependencies.

    Tokenizes text on whitespace (lowercased), hashes each token to a bucket index
    (MD5 mod dim), accumulates term-frequency counts, and L2-normalizes. Produces
    cosine_similarity = 1.0 for identical inputs; no cross-sentence semantic similarity.

    Suitable for exact-duplicate / near-exact-duplicate detection. When semantic
    equivalence across differently-worded queries is required, use LocalEmbedder
    with fastembed or OpenAIEmbedder instead.
    """

    def __init__(self, dim: int = DEFAULT_HASHING_DIM) -> None:
        if dim < 1:
            raise ValueError(f"HashingEmbedder dim must be >= 1, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        import hashlib

        tokens = text.lower().split()
        vec = [0.0] * self._dim
        for token in tokens:
            h = int(hashlib.md5(token.encode(), usedforsecurity=False).hexdigest(), 16)
            vec[h % self._dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            return [x / norm for x in vec]
        return vec


class _FastEmbedWrapper:
    """Internal: wraps fastembed.TextEmbedding as EmbedderProtocol."""

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)

    def embed(self, text: str) -> list[float]:
        result = list(self._model.embed([text]))
        return [float(x) for x in result[0]]


class LocalEmbedder:
    """
    Local embedder. Uses fastembed when available; falls back to HashingEmbedder.

    Configuration resolution priority:
        1. Constructor argument (model).
        2. MORALSTACK_LOCAL_EMBEDDING_MODEL environment variable.
        3. DEFAULT_LOCAL_EMBEDDING_MODEL ("BAAI/bge-small-en-v1.5").

    When fastembed is not installed the fallback HashingEmbedder captures
    exact-duplicate and near-exact-duplicate cache hits. For genuine semantic
    equivalence detection across differently-worded queries, install fastembed:
        pip install moralstack[local-embeddings]
    """

    def __init__(self, model: str | None = None) -> None:
        self._model_name = model or os.getenv("MORALSTACK_LOCAL_EMBEDDING_MODEL") or DEFAULT_LOCAL_EMBEDDING_MODEL
        self._delegate: EmbedderProtocol
        try:
            self._delegate = _FastEmbedWrapper(self._model_name)
            logger.info("LocalEmbedder: using fastembed model %r", self._model_name)
        except ImportError:
            self._delegate = HashingEmbedder()
            logger.info(
                "LocalEmbedder: fastembed not installed, using HashingEmbedder "
                "(dim=%d). Install moralstack[local-embeddings] for semantic similarity.",
                DEFAULT_HASHING_DIM,
            )

    def embed(self, text: str) -> list[float]:
        return self._delegate.embed(text)


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
                "OPENAI_API_KEY is not set. Pass api_key= to OpenAIEmbedder or set the OPENAI_API_KEY environment variable."
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
        started_at = int(time.time() * 1000)
        t0 = time.time()
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
        result = list(embedding)
        try:
            from moralstack.observability.token_usage import TokenUsage
            from moralstack.orchestration.persistence_helpers import record_llm_call

            usage = TokenUsage.from_openai_usage(getattr(response, "usage", None), is_embedding=True)
            elapsed_ms = (time.time() - t0) * 1000
            record_llm_call(
                None,
                None,
                {
                    "phase": "ledger",
                    "module": "embedder",
                    "action": "embed",
                    "model": self.model,
                    "started_at": started_at,
                    "duration_ms": elapsed_ms,
                    "token_usage_json": usage.to_json(),
                    "call_kind": "embedding",
                },
            )
        except Exception:
            logger.debug("embedder token accounting failed", exc_info=True)
        return result
