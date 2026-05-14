"""
Public configuration for the MoralStack SDK.

GovernanceConfig is the only configuration surface users need to know about.
Runtime tuning (orchestrator cycles, risk thresholds, module temperatures, etc.)
is controlled exclusively via MORALSTACK_* environment variables loaded from .env.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GovernanceConfig:
    """
    Configuration for a ``govern()`` instance.

    All fields have sensible defaults. The minimum requirement is to set
    OPENAI_API_KEY in the environment (or pass ``api_key`` here).

    Minimal example::

        client = govern(OpenAI())  # everything from env

    Example with overrides::

        client = govern(
            OpenAI(),
            config=GovernanceConfig(
                domain_overlay="healthcare",
                observability_mode="file_only",
                jsonl_dir="logs/audit",
            ),
        )
    """

    # --- Provider for the internal deliberative pipeline ---
    # The pipeline uses its own LLM client, separate from the user's client.
    api_key: str | None = None
    """API key for the deliberative pipeline. If None, uses OPENAI_API_KEY from the environment."""

    model: str | None = None
    """Model for the deliberative pipeline. If None, uses OPENAI_MODEL from the environment (default gpt-4o)."""

    base_url: str | None = None
    """OpenAI-compatible base URL. If None, uses OPENAI_BASE_URL from the environment."""

    # --- Constitution ---
    constitution_dir: str | None = None
    """Override path for constitution YAML overlays. If None, uses the default path."""

    domain_overlay: str | None = None
    """Force a specific domain overlay (e.g. 'healthcare', 'legal', 'finance')."""

    # --- Observability ---
    observability_mode: str = "off"
    """Observability mode: 'off' | 'file_only' | 'db_only' | 'dual'."""

    jsonl_dir: str | None = None
    """Directory for JSONL audit trail. Requires observability_mode 'file_only' or 'dual'."""

    db_path: str | None = None
    """SQLite path for audit trail. Requires observability_mode 'db_only' or 'dual'."""

    # --- Failure policy ---
    failure_policy: str = "refuse"
    """
    Behavior on pipeline error:
    - 'refuse': return a refusal without calling the original client
    - 'passthrough': call the original client without governance (unsafe fallback)
    """

    # --- Session tracking (prepares Level 2 multi-turn) ---
    enable_session_tracking: bool = True
    """
    Keeps conversation_id and turn_index across calls on the same GovernedClient.
    Disable only for stateless use cases.
    """

    max_history_tokens: int | None = None
    """
    Token limit for conversation history passed to the pipeline.
    If None, no compression is applied (prepares Level 2.2).
    """

    # --- Semantic Decision Ledger (fast-path, Step 14.2) ---
    enable_ledger: bool = True
    """
    Enable ``SemanticDecisionLedger`` fast-path. When False, every turn runs the full
    deliberative pipeline (no semantic cache short-circuit). Default: True. Override
    with ``MORALSTACK_LEDGER_ENABLED=false``.
    """

    ledger_similarity_threshold: float = 0.92
    """
    Cosine similarity threshold for a ledger cache hit. Two queries count as
    semantically equivalent when embedding cosine similarity is at least this value.
    Default: 0.92. Override with ``MORALSTACK_LEDGER_SIMILARITY_THRESHOLD``.
    """

    ledger_max_entries: int = 1000
    """
    Maximum entries in the in-process in-memory ledger storage (LRU eviction).
    Default: 1000. Override with ``MORALSTACK_LEDGER_MAX_ENTRIES``.
    """

    ledger_embedding_model: str | None = None
    """
    Override the embedding model for the ledger. None means use ``OPENAI_EMBEDDING_MODEL``
    or the embedder default (``text-embedding-3-small``). Override with
    ``MORALSTACK_LEDGER_EMBEDDING_MODEL``.
    """

    # Internal fields — not exposed to users
    _extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
