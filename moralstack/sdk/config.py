"""
Public configuration for the MoralStack SDK.

GovernanceConfig is the only configuration surface users need to know about.
Runtime tuning (orchestrator cycles, risk thresholds, module temperatures, etc.)
is controlled exclusively via MORALSTACK_* environment variables loaded from .env.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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

    generation: Literal["internal", "upstream_then_verify"] = "internal"
    """
    Speculative-draft generation mode:
    - 'internal' (default): the governance model (``self.policy``) produces the
      speculative draft, exactly as today.
    - 'upstream_then_verify': when a client ``model`` is supplied, the wrapped/
      upstream client produces the speculative draft instead; the draft still
      receives the same route-specific governance treatment an internal draft
      gets today (parity, not a new gate) and is discarded on any hard/refuse
      path. Requires ``enable_speculative_generation``; otherwise a documented
      no-op. Override with ``MORALSTACK_GENERATION_MODE``.
    """

    # --- Constitution ---
    constitution_dir: str | None = None
    """Override path for constitution YAML overlays. If None, uses the default path."""

    domain_overlay: str | None = None
    """Force a specific domain overlay (e.g. 'healthcare', 'legal', 'finance')."""

    # --- Prompt overrides ---
    prompts_dir: str | None = None
    """
    Directory of prompt override files (``*.yaml``) that replace the text or the ``when`` condition of
    packaged prompt fragments. Precedence: this field, then ``MORALSTACK_PROMPTS_DIR``, then the packaged
    defaults; ``None`` or a blank string means "not set". A missing, empty or invalid directory fails the
    pipeline build (``GovernancePipelineError``). Overrides can weaken governance: they are a deployer-side
    vector, audited (the audit record is persisted per run from step A4). See docs/prompt_overrides.md.
    """

    # --- Observability ---
    # The three fields below are PROCESS-WIDE (one observability target per process), not per client.
    observability_mode: str = "off"
    """
    Observability mode: 'file_only' | 'db_only' | 'dual'. Precedence: this field, then
    ``MORALSTACK_OBSERVABILITY_MODE``, then the default (db_only when a DB path is set, else file_only).
    'off' (the default), ``None`` or blank means "not set here": env/defaults apply — it does NOT disable
    writing. An explicit value only takes effect if applied before observability is live (the first
    ``govern()`` / ``create_app`` in the process, before anything is enqueued); a later different value
    is ignored with a WARNING and never redirects a live target. A ``govern()`` that fails later (e.g. a
    pipeline build error) still fixes the observability target for the process. In-process report code
    (``reports/markdown_export.py``, ``reports/model.py``) sees the explicit value; a separate
    ``moralstack-ui`` process reads env only.
    """

    jsonl_dir: str | None = None
    """
    Directory for the JSONL audit trail (used in 'file_only' / 'dual'). Precedence: this field, then
    ``MORALSTACK_OBSERVABILITY_JSONL_DIR``, then ``logs/observability``. Process-wide; same
    first-applied-wins rule as ``observability_mode``.
    """

    db_path: str | None = None
    """
    SQLite path for the audit trail (used in 'db_only' / 'dual'). Precedence: this field, then
    ``MORALSTACK_OBSERVABILITY_DB_PATH`` (legacy alias ``MORALSTACK_DB_PATH``). With no mode set here or in
    env, a DB path implies 'db_only'. Process-wide; same
    first-applied-wins rule as ``observability_mode`` (the write-queue worker keeps one connection, so a
    live target is never redirected).
    """

    # --- Failure policy ---
    failure_policy: str = "refuse"
    """
    Behavior on pipeline error:
    - 'refuse': return a fail-closed governed refusal without calling the wrapped client.

    NOTE (Plan 1): 'passthrough' is deprecated. Passthrough delivery (returning
    wrapped/upstream client output without governance) was removed because no
    delivered answer may be generated by the wrapped client. Constructing the
    config with failure_policy='passthrough' now emits a DeprecationWarning and is
    mapped to 'refuse' (fail closed).
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
    Default: 0.92 (calibrated on OpenAI ``text-embedding-3-small``, 1536 dims).
    When using ``embedder_provider='local'`` with a different model or dimension,
    this threshold may need recalibration.
    Override with ``MORALSTACK_LEDGER_SIMILARITY_THRESHOLD``.
    """

    ledger_max_entries: int = 1000
    """
    Maximum entries in the in-process in-memory ledger storage (LRU eviction).
    Default: 1000. Override with ``MORALSTACK_LEDGER_MAX_ENTRIES``.
    """

    ledger_embedding_model: str | None = None
    """
    Override the embedding model name passed to the ledger embedder. Applies only when
    ``embedder_provider='openai'`` (selects the OpenAI model, default
    ``text-embedding-3-small``); ignored for ``embedder_provider='local'`` (model
    selection for local embedders uses ``MORALSTACK_LOCAL_EMBEDDING_MODEL``).
    Override with ``MORALSTACK_LEDGER_EMBEDDING_MODEL``.
    """

    embedder_provider: Literal["local", "openai"] = "local"
    """
    Embedding provider for the SemanticDecisionLedger.
      'local'  — LocalEmbedder: fastembed when installed, else HashingEmbedder (default).
                 No OPENAI_API_KEY required. Threshold calibrated at 0.92 for OpenAI
                 text-embedding-3-small may need adjustment for local models.
      'openai' — OpenAIEmbedder: requires OPENAI_API_KEY.
    Override with MORALSTACK_EMBEDDER_PROVIDER.
    """

    # Internal fields — not exposed to users
    _extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Plan 1: passthrough delivery was removed. Map the deprecated value to a
        # fail-closed refusal so no code path can deliver wrapped-client output.
        if self.failure_policy == "passthrough":
            import warnings

            warnings.warn(
                "GovernanceConfig(failure_policy='passthrough') is deprecated and no "
                "longer supported: passthrough delivery was removed (Plan 1). "
                "Mapping to 'refuse' (fail closed).",
                DeprecationWarning,
                stacklevel=2,
            )
            self.failure_policy = "refuse"
