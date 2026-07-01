# Working-tree diff snapshot

- Generated: 20260630-172648
- Repo: C:\Users\fdidonato\Documents\progetti\moralstack

## git status
```
## main...origin/main
 M .claude/agents/architect-planner.md
 M docs/CODEBASE_FACTS.md
 M docs/MORALSTACK_CODEBASE_INDEX.md
 M moralstack/orchestration/controller.py
 M moralstack/orchestration/embedder.py
 M moralstack/orchestration/ledger.py
 M moralstack/sdk/bootstrap.py
 M moralstack/sdk/config.py
 M pyproject.toml
 M tests/test_embedder.py
 M tests/test_ledger.py
 M tests/test_orchestrator_ledger_integration.py
 M tests/test_sdk_bootstrap.py
 M tests/test_sdk_config.py
?? ANALISI_TECNICA_MORALSTACK.md
?? ai/handoffs/cursor-run-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-170512.log
?? ai/handoffs/cursor-run-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-171648.log
?? ai/handoffs/p1-3-embedder-provider-abstraction-cursor-cli-handoff.md
?? ai/plans/p1-3-embedder-provider-abstraction.md
?? ai/plans/test-output-protection.md
?? ai/prompts/generated-codex-plan-review-p1-3-embedder-provider-abstraction-20260630-162211.md
?? ai/prompts/generated-codex-plan-review-p1-3-embedder-provider-abstraction-20260630-162220.md
?? ai/prompts/generated-cursor-bootstrap-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-171524.md
?? ai/reviews/codex-plan-review-p1-3-embedder-provider-abstraction-20260630-162220.md
?? ai/reviews/diff-after-cursor-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-170512.md
?? claude_upgrade_plan.md
?? codex_upgrade_plan.md
?? tests/test_local_embedder.py
```

## Untracked files (not yet added)
```
ANALISI_TECNICA_MORALSTACK.md
ai/handoffs/cursor-run-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-170512.log
ai/handoffs/cursor-run-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-171648.log
ai/handoffs/p1-3-embedder-provider-abstraction-cursor-cli-handoff.md
ai/plans/p1-3-embedder-provider-abstraction.md
ai/plans/test-output-protection.md
ai/prompts/generated-codex-plan-review-p1-3-embedder-provider-abstraction-20260630-162211.md
ai/prompts/generated-codex-plan-review-p1-3-embedder-provider-abstraction-20260630-162220.md
ai/prompts/generated-cursor-bootstrap-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-171524.md
ai/reviews/codex-plan-review-p1-3-embedder-provider-abstraction-20260630-162220.md
ai/reviews/diff-after-cursor-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-170512.md
claude_upgrade_plan.md
codex_upgrade_plan.md
tests/test_local_embedder.py
```

## git diff (unstaged working tree)
```diff
diff --git a/.claude/agents/architect-planner.md b/.claude/agents/architect-planner.md
index 9600d6a..62803c4 100644
--- a/.claude/agents/architect-planner.md
+++ b/.claude/agents/architect-planner.md
@@ -6,7 +6,7 @@ description: >-
   tests, risks, acceptance criteria, checklist, rollback. Consumes the
   codebase-cartographer map. Does NOT implement code; the plan is handed to Codex
   for review and then to Cursor CLI for implementation.
-tools: Read, Grep, Glob, Bash
+tools: Read, Write, Grep, Glob, Bash
 ---
 
 You are the **Architect Planner** for MoralStack. You turn a request plus a
diff --git a/docs/CODEBASE_FACTS.md b/docs/CODEBASE_FACTS.md
index 902c116..58dd3be 100644
--- a/docs/CODEBASE_FACTS.md
+++ b/docs/CODEBASE_FACTS.md
@@ -74,6 +74,9 @@ elsewhere appear only when the `[ui]`/`[server]` extras are absent.
 | Ledger fast-path reuse gate: cached REFUSE always applied; non-deliberative current route (benign/safe_complete/refuse/fast_path) applied; deliberative route + non-REFUSE cache rejected | `orchestration/conversational_fast_path.py:111-151` (`is_safe_to_apply`) | High | |
 | Fast-path reuses only governance metadata (final_action, reason_codes, triggered_principles); response **content is never cached** (regenerated fresh) | `orchestration/conversational_fast_path.py:1-15,96-101` | High | DAF-4 |
 | Ledger `lookup` and `store` both skip when posture==ESCALATED (`posture_escalated`) or turn_index<1 (`turn_index_below_one`); lookup can also miss on `below_threshold`/`intent_divergence` | `orchestration/ledger.py:15-17,245-251,313-340,141-142` | High | |
+| `LocalEmbedder` is the default ledger embedder (`GovernanceConfig.embedder_provider="local"`); no `OPENAI_API_KEY` required for embedding unless `embedder_provider="openai"` | `sdk/config.py` (`embedder_provider`); `sdk/bootstrap.py` (`_build_embedder`, `_resolve_embedder_provider`); `orchestration/embedder.py` (`LocalEmbedder`, `HashingEmbedder`) | High | fastembed used when installed; `HashingEmbedder` fallback otherwise; optional dep `moralstack[local-embeddings]` |
+| MissÔåÆstore ledger cycle reuses `LedgerResult.query_embedding` via `store(prompt_embedding=ÔÇª)` so `embed()` is not called twice on below-threshold / intent-divergence misses | `orchestration/ledger.py` (`LedgerResult.query_embedding`, `store`); `orchestration/controller.py` (`_maybe_store_in_ledger`) | High | `no_candidates` misses still embed at store time |
+| `local-embeddings` optional dependency group adds `fastembed>=0.2.0` for semantic local embeddings | `pyproject.toml` (`[project.optional-dependencies].local-embeddings`) | High | |
 | Pipeline failure fails closed to a governed refusal; the wrapped client is never called. `failure_policy="passthrough"` is deprecated and mapped to `refuse` (DeprecationWarning) | `sdk/wrapper.py` (`_handle_pipeline_failure`, `GovernedResponse.from_pipeline_error`); `sdk/config.py` (`__post_init__`); `server/proxy.py` (`_handle_chat_completion_sync`) | High | Plan 1: passthrough delivery removed. `GovernedResponse.is_passthrough` is always `False`; `from_passthrough` is a deprecated fail-closed alias |
 | Multi-turn governance state extended per turn (posture, contract hash, turn summary) | `orchestration/controller.py:478-543` (`_extend_state_out_v04`) | High | |
 | `enable_speculative_generation` defaults to `True` (risk + draft run in parallel) | `orchestration/types.py:540` (`OrchestratorConfig`) | High | |
diff --git a/docs/MORALSTACK_CODEBASE_INDEX.md b/docs/MORALSTACK_CODEBASE_INDEX.md
index 2068c6d..163b7d0 100644
--- a/docs/MORALSTACK_CODEBASE_INDEX.md
+++ b/docs/MORALSTACK_CODEBASE_INDEX.md
@@ -68,9 +68,12 @@ Python `>=3.11` (`pyproject.toml:11`). Runtime deps: `openai>=2.24`, `pydantic>=
   non-empty `system`/`developer` message wins, `mode="opaque"`),
   `_messages_to_turns`, `_build_safe_complete_user_turn`.
 - `bootstrap.py` ÔÇö `_bootstrap_pipeline(config)` builds the `Orchestrator`;
-  `_resolve_model(config)` resolves the generation model.
+  `_resolve_model(config)` resolves the generation model; `_build_ledger(config)`
+  wires `SemanticDecisionLedger` with a provider-selected embedder (`LocalEmbedder`
+  by default, `OpenAIEmbedder` when `embedder_provider="openai"` or
+  `MORALSTACK_EMBEDDER_PROVIDER=openai`).
 - `config.py` ÔÇö `GovernanceConfig` (domain_overlay, failure_policy,
-  observability_mode, jsonl_dir, enable_session_tracking, ÔÇª).
+  observability_mode, jsonl_dir, enable_session_tracking, `embedder_provider`, ÔÇª).
 - `session.py` ÔÇö `SessionState`: per-client conversation_id + turn counter,
   wraps a `SessionStore`.
 - `session_store.py` ÔÇö `SessionStoreProtocol`, `InMemorySessionStore`.
@@ -122,8 +125,14 @@ Python `>=3.11` (`pyproject.toml:11`). Runtime deps: `openai>=2.24`, `pydantic>=
 - `convergence.py`, `convergence_evaluator.py` ÔÇö convergence engine.
 - `conversation_state.py` ÔÇö `ConversationGovernanceState`, `TurnDecisionSummary`.
 - `conversational_fast_path.py` ÔÇö `ConversationalFastPathRunner` (cache-driven skip).
+- `embedder.py` ÔÇö `EmbedderProtocol`, `HashingEmbedder` (pure-Python fallback),
+  `LocalEmbedder` (fastembed when installed, else `HashingEmbedder`; default via
+  `GovernanceConfig.embedder_provider="local"`), `OpenAIEmbedder` (opt-in OpenAI),
+  `cosine_similarity`. Provider selection: config `embedder_provider` or env
+  `MORALSTACK_EMBEDDER_PROVIDER`; optional dep `moralstack[local-embeddings]`.
 - `ledger.py`, `ledger_storage.py` ÔÇö `SemanticDecisionLedger`, `CachedDecision`,
-  `LedgerResult`.
+  `LedgerResult` (`query_embedding` carries the lookup-time vector for reuse in
+  `store(prompt_embedding=ÔÇª)`, eliminating double-embedding on missÔåÆstore).
 - `refusal_handler.py`, `refusal_context.py`, `safe_refusal_generator.py` ÔÇö refusal text.
 - `response_assembler.py` ÔÇö `ResponseAssembler` builds the `FinalResponse`.
 - `speculative_overlap.py` ÔÇö `SpeculativeOverlapHandle` (parallel draft + risk).
diff --git a/moralstack/orchestration/controller.py b/moralstack/orchestration/controller.py
index ae516da..f3bb2e3 100644
--- a/moralstack/orchestration/controller.py
+++ b/moralstack/orchestration/controller.py
@@ -680,6 +680,12 @@ class OrchestrationController:
             if isinstance(meta_request_type, str):
                 request_type = meta_request_type
 
+        # Reuse embedding computed during lookup to avoid a second embed() call.
+        prompt_embedding: list[float] | None = None
+        lookup_result = call_ctx.ledger_lookup
+        if lookup_result is not None:
+            prompt_embedding = getattr(lookup_result, "query_embedding", None)
+
         try:
             self._ledger.store(
                 prompt=request.prompt,
@@ -690,6 +696,7 @@ class OrchestrationController:
                 intent_clarity=intent_clarity,
                 request_type=request_type,
                 turn_index=turn_index,
+                prompt_embedding=prompt_embedding,
             )
         except Exception as e:
             # The ledger is best-effort; a store failure must never break the response flow.
@@ -1460,7 +1467,7 @@ class OrchestrationController:
                     "contract, and the output is not safety-restricted."
                 ),
                 why_not_safe_complete=(
-                    "Contract execution does not require caveats; the deployer authorized " "the direct response."
+                    "Contract execution does not require caveats; the deployer authorized the direct response."
                 ),
             )
         except Exception:
diff --git a/moralstack/orchestration/embedder.py b/moralstack/orchestration/embedder.py
index 724f383..27cf6b5 100644
--- a/moralstack/orchestration/embedder.py
+++ b/moralstack/orchestration/embedder.py
@@ -7,7 +7,9 @@ instead of recomputed.
 
 Defines:
 - EmbedderProtocol: structural Protocol for any embedder.
-- OpenAIEmbedder: production implementation using OpenAI text-embedding-3-small.
+- HashingEmbedder: pure-Python deterministic feature-hashing embedder (zero deps).
+- LocalEmbedder: local embedder using fastembed when available, else HashingEmbedder.
+- OpenAIEmbedder: opt-in implementation using OpenAI text-embedding-3-small.
 - cosine_similarity: numpy-free pure function for similarity scoring.
 
 Normative reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 ┬º5.6.
@@ -100,6 +102,90 @@ def cosine_similarity(a: list[float], b: list[float]) -> float:
 # Default OpenAI embedding model. Override with OPENAI_EMBEDDING_MODEL env var
 # or via the model= argument.
 DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
+DEFAULT_HASHING_DIM = 512
+DEFAULT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
+
+
+class HashingEmbedder:
+    """
+    Pure-Python deterministic feature-hashing embedder. Zero external dependencies.
+
+    Tokenizes text on whitespace (lowercased), hashes each token to a bucket index
+    (MD5 mod dim), accumulates term-frequency counts, and L2-normalizes. Produces
+    cosine_similarity = 1.0 for identical inputs; no cross-sentence semantic similarity.
+
+    Suitable for exact-duplicate / near-exact-duplicate detection. When semantic
+    equivalence across differently-worded queries is required, use LocalEmbedder
+    with fastembed or OpenAIEmbedder instead.
+    """
+
+    def __init__(self, dim: int = DEFAULT_HASHING_DIM) -> None:
+        if dim < 1:
+            raise ValueError(f"HashingEmbedder dim must be >= 1, got {dim}")
+        self._dim = dim
+
+    @property
+    def dim(self) -> int:
+        return self._dim
+
+    def embed(self, text: str) -> list[float]:
+        import hashlib
+
+        tokens = text.lower().split()
+        vec = [0.0] * self._dim
+        for token in tokens:
+            h = int(hashlib.md5(token.encode(), usedforsecurity=False).hexdigest(), 16)
+            vec[h % self._dim] += 1.0
+        norm = math.sqrt(sum(x * x for x in vec))
+        if norm > 0.0:
+            return [x / norm for x in vec]
+        return vec
+
+
+class _FastEmbedWrapper:
+    """Internal: wraps fastembed.TextEmbedding as EmbedderProtocol."""
+
+    def __init__(self, model_name: str) -> None:
+        from fastembed import TextEmbedding  # type: ignore[import-not-found]
+
+        self._model = TextEmbedding(model_name=model_name)
+
+    def embed(self, text: str) -> list[float]:
+        result = list(self._model.embed([text]))
+        return [float(x) for x in result[0]]
+
+
+class LocalEmbedder:
+    """
+    Local embedder. Uses fastembed when available; falls back to HashingEmbedder.
+
+    Configuration resolution priority:
+        1. Constructor argument (model).
+        2. MORALSTACK_LOCAL_EMBEDDING_MODEL environment variable.
+        3. DEFAULT_LOCAL_EMBEDDING_MODEL ("BAAI/bge-small-en-v1.5").
+
+    When fastembed is not installed the fallback HashingEmbedder captures
+    exact-duplicate and near-exact-duplicate cache hits. For genuine semantic
+    equivalence detection across differently-worded queries, install fastembed:
+        pip install moralstack[local-embeddings]
+    """
+
+    def __init__(self, model: str | None = None) -> None:
+        self._model_name = model or os.getenv("MORALSTACK_LOCAL_EMBEDDING_MODEL") or DEFAULT_LOCAL_EMBEDDING_MODEL
+        self._delegate: EmbedderProtocol
+        try:
+            self._delegate = _FastEmbedWrapper(self._model_name)
+            logger.info("LocalEmbedder: using fastembed model %r", self._model_name)
+        except ImportError:
+            self._delegate = HashingEmbedder()
+            logger.info(
+                "LocalEmbedder: fastembed not installed, using HashingEmbedder "
+                "(dim=%d). Install moralstack[local-embeddings] for semantic similarity.",
+                DEFAULT_HASHING_DIM,
+            )
+
+    def embed(self, text: str) -> list[float]:
+        return self._delegate.embed(text)
 
 
 class OpenAIEmbedder:
@@ -133,8 +219,7 @@ class OpenAIEmbedder:
         resolved_api_key = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
         if not resolved_api_key:
             raise ValueError(
-                "OPENAI_API_KEY is not set. Pass api_key= to OpenAIEmbedder or set "
-                "the OPENAI_API_KEY environment variable."
+                "OPENAI_API_KEY is not set. Pass api_key= to OpenAIEmbedder or set the OPENAI_API_KEY environment variable."
             )
         self._api_key = resolved_api_key
         self.model = model or os.getenv("OPENAI_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
diff --git a/moralstack/orchestration/ledger.py b/moralstack/orchestration/ledger.py
index f71b2ae..b806b51 100644
--- a/moralstack/orchestration/ledger.py
+++ b/moralstack/orchestration/ledger.py
@@ -147,6 +147,7 @@ class LedgerResult:
     similarity: float
     from_turn: int | None
     reason: str
+    query_embedding: list[float] | None = field(default=None, hash=False, compare=False, repr=False)
 
 
 # =============================================================================
@@ -189,6 +190,7 @@ class SemanticDecisionLedger:
         self._embedder = embedder
         self._storage = storage
         self._threshold = similarity_threshold
+        self._embedding_dim: int | None = None
 
     @property
     def similarity_threshold(self) -> float:
@@ -257,6 +259,8 @@ class SemanticDecisionLedger:
             return LedgerResult(is_hit=False, cached_decision=None, similarity=0.0, from_turn=None, reason="no_candidates")
 
         query_embedding = self._embedder.embed(prompt)
+        if self._embedding_dim is None:
+            self._embedding_dim = len(query_embedding)
 
         best_entry: LedgerEntry | None = None
         best_similarity = -1.0
@@ -273,6 +277,7 @@ class SemanticDecisionLedger:
                 similarity=max(0.0, best_similarity),
                 from_turn=None,
                 reason="below_threshold",
+                query_embedding=query_embedding,
             )
 
         # Secondary intent check: same embedding magnitude is not enough.
@@ -283,6 +288,7 @@ class SemanticDecisionLedger:
                 similarity=best_similarity,
                 from_turn=None,
                 reason="intent_divergence",
+                query_embedding=query_embedding,
             )
 
         return LedgerResult(
@@ -291,6 +297,7 @@ class SemanticDecisionLedger:
             similarity=best_similarity,
             from_turn=best_entry.turn_index,
             reason="",
+            query_embedding=query_embedding,
         )
 
     def store(
@@ -303,6 +310,8 @@ class SemanticDecisionLedger:
         intent_clarity: str,
         request_type: str,
         turn_index: int,
+        *,
+        prompt_embedding: list[float] | None = None,
     ) -> bool:
         """
         Persist a decision under the appropriate LedgerKey.
@@ -339,7 +348,19 @@ class SemanticDecisionLedger:
             )
             return False
 
-        embedding = self._embedder.embed(prompt)
+        if prompt_embedding is not None:
+            if len(prompt_embedding) == 0:
+                raise ValueError("prompt_embedding must not be empty")
+            if self._embedding_dim is not None and len(prompt_embedding) != self._embedding_dim:
+                raise ValueError(
+                    f"prompt_embedding dimension {len(prompt_embedding)} does not match "
+                    f"ledger embedding dimension {self._embedding_dim}"
+                )
+            embedding = prompt_embedding
+        else:
+            embedding = self._embedder.embed(prompt)
+        if self._embedding_dim is None:
+            self._embedding_dim = len(embedding)
         key = LedgerKey(contract_hash=contract_hash, posture=posture, domain=domain)
         entry = LedgerEntry(
             cached_decision=decision,
diff --git a/moralstack/sdk/bootstrap.py b/moralstack/sdk/bootstrap.py
index fc719d6..0985fdb 100644
--- a/moralstack/sdk/bootstrap.py
+++ b/moralstack/sdk/bootstrap.py
@@ -62,9 +62,56 @@ def _resolve_ledger_embedding_model(config: GovernanceConfig) -> str | None:
     return raw.strip() if raw else None
 
 
+_VALID_EMBEDDER_PROVIDERS = frozenset({"local", "openai"})
+
+
+def _resolve_embedder_provider(config: GovernanceConfig) -> str:
+    """Resolve embedder provider: env > config. Returns 'local' or 'openai'.
+
+    Raises ValueError for any unknown value so misconfiguration fails loudly
+    rather than silently falling back to 'local'.
+    """
+    raw = (os.getenv("MORALSTACK_EMBEDDER_PROVIDER") or "").strip().lower()
+    if raw:
+        if raw not in _VALID_EMBEDDER_PROVIDERS:
+            raise ValueError(
+                f"Unknown MORALSTACK_EMBEDDER_PROVIDER={raw!r}; must be one of {sorted(_VALID_EMBEDDER_PROVIDERS)}"
+            )
+        return raw
+    provider = (getattr(config, "embedder_provider", None) or "local").lower()
+    if provider not in _VALID_EMBEDDER_PROVIDERS:
+        raise ValueError(f"Unknown embedder_provider={provider!r}; must be one of {sorted(_VALID_EMBEDDER_PROVIDERS)}")
+    return provider
+
+
+def _build_embedder(
+    config: GovernanceConfig,
+    api_key: str,
+    base_url: str | None,
+) -> Any:
+    """
+    Factory: returns the correct EmbedderProtocol implementation.
+
+    Raises on misconfiguration (e.g. openai provider without API key).
+    Caller (_build_ledger) wraps this in a try/except.
+    """
+    provider = _resolve_embedder_provider(config)
+    if provider == "openai":
+        from moralstack.orchestration.embedder import OpenAIEmbedder
+
+        return OpenAIEmbedder(
+            api_key=api_key,
+            model=_resolve_ledger_embedding_model(config),
+            base_url=base_url,
+        )
+    from moralstack.orchestration.embedder import LocalEmbedder
+
+    return LocalEmbedder()
+
+
 def _build_ledger(config: GovernanceConfig, api_key: str, base_url: str | None) -> Any:
     """
-    Build ``SemanticDecisionLedger`` with ``OpenAIEmbedder`` and ``InMemoryLedgerStorage``.
+    Build ``SemanticDecisionLedger`` with a provider-selected embedder and ``InMemoryLedgerStorage``.
 
     Returns None when disabled or when construction fails (logged at WARNING); the
     pipeline continues without a fast-path.
@@ -78,7 +125,6 @@ def _build_ledger(config: GovernanceConfig, api_key: str, base_url: str | None)
         return None
 
     try:
-        from moralstack.orchestration.embedder import OpenAIEmbedder
         from moralstack.orchestration.ledger import SemanticDecisionLedger
         from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
     except Exception as e:
@@ -86,11 +132,8 @@ def _build_ledger(config: GovernanceConfig, api_key: str, base_url: str | None)
         return None
 
     try:
-        embedder = OpenAIEmbedder(
-            api_key=api_key,
-            model=_resolve_ledger_embedding_model(config),
-            base_url=base_url,
-        )
+        provider = _resolve_embedder_provider(config)
+        embedder = _build_embedder(config, api_key=api_key, base_url=base_url)
         max_entries = _resolve_ledger_max_entries(config)
         storage = InMemoryLedgerStorage(max_entries=max_entries)
         threshold = _resolve_ledger_threshold(config)
@@ -100,7 +143,8 @@ def _build_ledger(config: GovernanceConfig, api_key: str, base_url: str | None)
             similarity_threshold=threshold,
         )
         logger.info(
-            "MoralStack SDK: SemanticDecisionLedger enabled (threshold=%.3f, max_entries=%d)",
+            "MoralStack SDK: SemanticDecisionLedger enabled (provider=%s, threshold=%.3f, max_entries=%d)",
+            provider,
             ledger.similarity_threshold,
             max_entries,
         )
diff --git a/moralstack/sdk/config.py b/moralstack/sdk/config.py
index c6fbdc9..9232922 100644
--- a/moralstack/sdk/config.py
+++ b/moralstack/sdk/config.py
@@ -9,7 +9,7 @@ is controlled exclusively via MORALSTACK_* environment variables loaded from .en
 from __future__ import annotations
 
 from dataclasses import dataclass, field
-from typing import Any
+from typing import Any, Literal
 
 
 @dataclass
@@ -118,6 +118,16 @@ class GovernanceConfig:
     ``MORALSTACK_LEDGER_EMBEDDING_MODEL``.
     """
 
+    embedder_provider: Literal["local", "openai"] = "local"
+    """
+    Embedding provider for the SemanticDecisionLedger.
+      'local'  ÔÇö LocalEmbedder: fastembed when installed, else HashingEmbedder (default).
+                 No OPENAI_API_KEY required. Threshold calibrated at 0.92 for OpenAI
+                 text-embedding-3-small may need adjustment for local models.
+      'openai' ÔÇö OpenAIEmbedder: requires OPENAI_API_KEY.
+    Override with MORALSTACK_EMBEDDER_PROVIDER.
+    """
+
     # Internal fields ÔÇö not exposed to users
     _extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
 
diff --git a/pyproject.toml b/pyproject.toml
index a2857c2..aa84ff7 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -54,6 +54,9 @@ server = [
     "uvicorn>=0.27",
     "httpx>=0.27",
 ]
+local-embeddings = [
+    "fastembed>=0.2.0",
+]
 
 [project.scripts]
 moralstack = "moralstack.cli.run:main"
diff --git a/tests/test_embedder.py b/tests/test_embedder.py
index ec83b1a..04f24ac 100644
--- a/tests/test_embedder.py
+++ b/tests/test_embedder.py
@@ -14,6 +14,8 @@ import pytest
 from moralstack.orchestration.embedder import (
     DEFAULT_EMBEDDING_MODEL,
     EmbedderProtocol,
+    HashingEmbedder,
+    LocalEmbedder,
     OpenAIEmbedder,
     cosine_similarity,
 )
@@ -94,6 +96,21 @@ class TestEmbedderProtocolConformance:
         stub: EmbedderProtocol = StubEmbedder()  # Static type-check accepts this.
         assert stub.embed("anything") == [0.0]
 
+    def test_hashing_embedder_is_protocol_compliant(self) -> None:
+        assert hasattr(HashingEmbedder, "embed")
+        assert callable(HashingEmbedder.embed)
+
+    def test_local_embedder_is_protocol_compliant(self) -> None:
+        assert hasattr(LocalEmbedder, "embed")
+        assert callable(LocalEmbedder.embed)
+
+
+def test_default_similarity_threshold_calibration_assumption() -> None:
+    from moralstack.orchestration.ledger import DEFAULT_SIMILARITY_THRESHOLD
+
+    assert DEFAULT_SIMILARITY_THRESHOLD == 0.92
+    assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"
+
 
 # =============================================================================
 # OpenAIEmbedder ÔÇö initialization
diff --git a/tests/test_ledger.py b/tests/test_ledger.py
index b727f69..c0b0340 100644
--- a/tests/test_ledger.py
+++ b/tests/test_ledger.py
@@ -4,12 +4,15 @@ Test suite for moralstack/orchestration/ledger.py ÔÇö SemanticDecisionLedger.
 
 from __future__ import annotations
 
+from unittest.mock import MagicMock
+
 import pytest
 
 from moralstack.orchestration.embedder import EmbedderProtocol
 from moralstack.orchestration.ledger import (
     DEFAULT_SIMILARITY_THRESHOLD,
     CachedDecision,
+    LedgerResult,
     SemanticDecisionLedger,
 )
 from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
@@ -442,3 +445,319 @@ class TestProtocolConformance:
     def test_stub_embedder_satisfies_protocol(self):
         stub: EmbedderProtocol = StubEmbedder()
         assert stub.embed("anything") == [0.0, 0.0]
+
+
+class _CountingStubEmbedder:
+    def __init__(self, vector: list[float] | None = None) -> None:
+        self.call_count = 0
+        self._default = vector or [1.0, 0.0, 0.0]
+        self._vectors: dict[str, list[float]] = {
+            "first": [1.0, 0.0, 0.0],
+            "second": [0.0, 1.0, 0.0],
+        }
+
+    def embed(self, text: str) -> list[float]:
+        self.call_count += 1
+        return list(self._vectors.get(text, self._default))
+
+
+class TestLedgerResultQueryEmbedding:
+    def test_query_embedding_none_on_posture_escalated(self) -> None:
+        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
+        result = ledger.lookup(
+            prompt="q",
+            contract_hash="abc",
+            posture="ESCALATED",
+            domain=None,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=5,
+        )
+        assert result.query_embedding is None
+
+    def test_query_embedding_none_on_turn_zero(self) -> None:
+        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
+        result = ledger.lookup(
+            prompt="q",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=0,
+        )
+        assert result.query_embedding is None
+
+    def test_query_embedding_none_on_no_candidates(self) -> None:
+        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
+        result = ledger.lookup(
+            prompt="q",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+        )
+        assert result.reason == "no_candidates"
+        assert result.query_embedding is None
+
+    def test_query_embedding_populated_on_below_threshold(self) -> None:
+        emb = StubEmbedder(mapping={"first": [1.0, 0.0], "second": [0.0, 1.0]})
+        ledger = SemanticDecisionLedger(emb, InMemoryLedgerStorage())
+        ledger.store(
+            prompt="first",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=1,
+        )
+        result = ledger.lookup(
+            prompt="second",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+        )
+        assert result.reason == "below_threshold"
+        assert result.query_embedding is not None
+        assert len(result.query_embedding) == 2
+
+    def test_query_embedding_populated_on_hit(self) -> None:
+        emb = StubEmbedder(mapping={"first": [1.0, 0.0], "second": [1.0, 0.0]})
+        ledger = SemanticDecisionLedger(emb, InMemoryLedgerStorage())
+        ledger.store(
+            prompt="first",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=1,
+        )
+        result = ledger.lookup(
+            prompt="second",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+        )
+        assert result.is_hit is True
+        assert result.query_embedding is not None
+
+
+class TestLedgerResultIsFrozen:
+    def test_ledger_result_is_frozen(self) -> None:
+        import dataclasses
+
+        r = LedgerResult(
+            is_hit=False,
+            cached_decision=None,
+            similarity=0.0,
+            from_turn=None,
+            reason="no_candidates",
+        )
+        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
+            r.is_hit = True
+
+
+class TestStoreSkipsEmbedOnProvidedEmbedding:
+    def test_store_with_prompt_embedding_does_not_call_embedder(self) -> None:
+        mock = MagicMock()
+        mock.embed.return_value = [0.1, 0.2]
+        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
+        ledger.store(
+            prompt="q",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+            prompt_embedding=[0.1, 0.2],
+        )
+        mock.embed.assert_not_called()
+
+    def test_store_without_prompt_embedding_calls_embedder(self) -> None:
+        mock = MagicMock()
+        mock.embed.return_value = [0.1, 0.2]
+        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
+        ledger.store(
+            prompt="q",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+        )
+        mock.embed.assert_called_once()
+
+    def test_store_with_none_prompt_embedding_calls_embedder(self) -> None:
+        mock = MagicMock()
+        mock.embed.return_value = [0.1, 0.2]
+        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
+        ledger.store(
+            prompt="q",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+            prompt_embedding=None,
+        )
+        mock.embed.assert_called_once()
+
+    def test_store_backward_compat_no_kwarg_calls_embedder(self) -> None:
+        mock = MagicMock()
+        mock.embed.return_value = [0.1, 0.2]
+        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
+        ledger.store(
+            prompt="q",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+        )
+        mock.embed.assert_called_once()
+
+
+class TestStorePromptEmbeddingValidation:
+    def test_store_empty_prompt_embedding_raises(self) -> None:
+        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
+        with pytest.raises(ValueError, match="must not be empty"):
+            ledger.store(
+                prompt="q",
+                contract_hash="abc",
+                posture="NORMAL",
+                domain=None,
+                decision=_decision(),
+                intent_clarity="HIGH",
+                request_type="factual",
+                turn_index=2,
+                prompt_embedding=[],
+            )
+
+    def test_store_wrong_dim_prompt_embedding_raises(self) -> None:
+        emb = StubEmbedder(default=[0.1, 0.2])
+        ledger = SemanticDecisionLedger(emb, InMemoryLedgerStorage())
+        ledger.store(
+            prompt="first",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=1,
+        )
+        with pytest.raises(ValueError, match="dimension"):
+            ledger.store(
+                prompt="second",
+                contract_hash="abc",
+                posture="NORMAL",
+                domain=None,
+                decision=_decision(),
+                intent_clarity="HIGH",
+                request_type="factual",
+                turn_index=2,
+                prompt_embedding=[1.0, 2.0, 3.0],
+            )
+
+    def test_store_correct_dim_prompt_embedding_accepted(self) -> None:
+        mock = MagicMock()
+        mock.embed.return_value = [0.1, 0.2]
+        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
+        ledger.store(
+            prompt="first",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=1,
+        )
+        mock.reset_mock()
+        stored = ledger.store(
+            prompt="second",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+            prompt_embedding=[0.3, 0.4],
+        )
+        assert stored is True
+        mock.embed.assert_not_called()
+
+    def test_store_prompt_embedding_is_keyword_only(self) -> None:
+        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
+        with pytest.raises(TypeError):
+            ledger.store(
+                "q",
+                "abc",
+                "NORMAL",
+                None,
+                _decision(),
+                "HIGH",
+                "factual",
+                2,
+                [0.1, 0.2],
+            )
+
+
+class TestDoubleEmbedElimination:
+    def test_miss_then_store_embeds_once(self) -> None:
+        emb = _CountingStubEmbedder(vector=[1.0, 0.0, 0.0])
+        ledger = SemanticDecisionLedger(emb, InMemoryLedgerStorage(), similarity_threshold=0.99)
+        ledger.store(
+            prompt="first",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=1,
+        )
+        result = ledger.lookup(
+            prompt="second",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+        )
+        assert result.reason == "below_threshold"
+        assert result.query_embedding is not None
+        ledger.store(
+            prompt="second",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=_decision(),
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+            prompt_embedding=result.query_embedding,
+        )
+        assert emb.call_count == 2
diff --git a/tests/test_orchestrator_ledger_integration.py b/tests/test_orchestrator_ledger_integration.py
index 19cb51b..833bc48 100644
--- a/tests/test_orchestrator_ledger_integration.py
+++ b/tests/test_orchestrator_ledger_integration.py
@@ -11,6 +11,8 @@ from __future__ import annotations
 
 from unittest.mock import MagicMock
 
+import pytest
+
 from moralstack.orchestration.controller import OrchestrationController
 from moralstack.orchestration.process_context import ProcessCallContext
 from moralstack.orchestration.types import (
@@ -223,3 +225,111 @@ class TestLedgerRoundTripHit:
         )
         assert result.is_hit is False
         assert result.reason == "intent_divergence"
+
+
+class TestDoubleEmbeddingEndToEnd:
+    def test_miss_then_store_with_precomputed_calls_embedder_exactly_twice(self) -> None:
+        from moralstack.orchestration.ledger import CachedDecision, SemanticDecisionLedger
+        from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
+
+        class _CountingStubEmbedder:
+            def __init__(self) -> None:
+                self.call_count = 0
+                self._vectors = {
+                    "first": [1.0, 0.0, 0.0],
+                    "second": [0.0, 1.0, 0.0],
+                }
+
+            def embed(self, text: str) -> list[float]:
+                self.call_count += 1
+                return list(self._vectors.get(text, [1.0, 0.0, 0.0]))
+
+        emb = _CountingStubEmbedder()
+        ledger = SemanticDecisionLedger(
+            embedder=emb,
+            storage=InMemoryLedgerStorage(),
+            similarity_threshold=0.99,
+        )
+        decision = CachedDecision(
+            final_action="NORMAL_COMPLETE",
+            risk_score=0.1,
+            governance_posture="NORMAL",
+        )
+        ledger.store(
+            prompt="first",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=decision,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=1,
+        )
+        result = ledger.lookup(
+            prompt="second",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+        )
+        assert result.reason == "below_threshold"
+        ledger.store(
+            prompt="second",
+            contract_hash="abc",
+            posture="NORMAL",
+            domain=None,
+            decision=decision,
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=2,
+            prompt_embedding=result.query_embedding,
+        )
+        assert emb.call_count == 2
+
+
+class TestLedgerDimMismatchPropagation:
+    def test_mixed_dim_entries_raise_valueerror_on_lookup(self) -> None:
+        from moralstack.orchestration.ledger import (
+            CachedDecision,
+            LedgerEntry,
+            LedgerKey,
+            SemanticDecisionLedger,
+        )
+        from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
+
+        class _ThreeDimEmbedder:
+            def embed(self, _text: str) -> list[float]:
+                return [1.0, 0.0, 0.0]
+
+        storage = InMemoryLedgerStorage()
+        key = LedgerKey(contract_hash="abc", posture="NORMAL", domain=None)
+        entry = LedgerEntry(
+            cached_decision=CachedDecision(
+                final_action="NORMAL_COMPLETE",
+                risk_score=0.1,
+                governance_posture="NORMAL",
+            ),
+            embedding=[1.0, 0.0],
+            original_prompt="stored",
+            intent_clarity="HIGH",
+            request_type="factual",
+            turn_index=1,
+        )
+        storage.put(key, entry)
+        ledger = SemanticDecisionLedger(
+            embedder=_ThreeDimEmbedder(),
+            storage=storage,
+            similarity_threshold=0.5,
+        )
+        with pytest.raises(ValueError, match="equally-sized"):
+            ledger.lookup(
+                prompt="query",
+                contract_hash="abc",
+                posture="NORMAL",
+                domain=None,
+                intent_clarity="HIGH",
+                request_type="factual",
+                turn_index=2,
+            )
diff --git a/tests/test_sdk_bootstrap.py b/tests/test_sdk_bootstrap.py
index 4bc81f2..f7832ea 100644
--- a/tests/test_sdk_bootstrap.py
+++ b/tests/test_sdk_bootstrap.py
@@ -8,7 +8,9 @@ import pytest
 from moralstack.pipeline.deliberation_stack import DeliberationBuildMeta, DeliberationModules
 from moralstack.sdk.bootstrap import (
     _bootstrap_pipeline,
+    _build_ledger,
     _resolve_api_key,
+    _resolve_embedder_provider,
     _resolve_model,
 )
 from moralstack.sdk.config import GovernanceConfig
@@ -104,7 +106,11 @@ def test_bootstrap_creates_ledger_by_default(monkeypatch: pytest.MonkeyPatch) ->
 
     from moralstack.orchestration.ledger import SemanticDecisionLedger
 
-    orch = _bootstrap_pipeline(GovernanceConfig())
+    with patch(
+        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
+        side_effect=ImportError("fastembed not installed"),
+    ):
+        orch = _bootstrap_pipeline(GovernanceConfig())
     assert orch.ledger is not None
     assert isinstance(orch.ledger, SemanticDecisionLedger)
     assert orch.ledger.similarity_threshold == 0.92
@@ -137,6 +143,81 @@ def test_bootstrap_respects_threshold_env(monkeypatch: pytest.MonkeyPatch) -> No
     monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
     monkeypatch.setenv("MORALSTACK_LEDGER_SIMILARITY_THRESHOLD", "0.85")
 
-    orch = _bootstrap_pipeline(GovernanceConfig())
+    with patch(
+        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
+        side_effect=ImportError("fastembed not installed"),
+    ):
+        orch = _bootstrap_pipeline(GovernanceConfig())
     assert orch.ledger is not None
     assert orch.ledger.similarity_threshold == 0.85
+
+
+def test_build_ledger_uses_local_embedder_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
+    from moralstack.orchestration.embedder import LocalEmbedder, OpenAIEmbedder
+
+    with patch(
+        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
+        side_effect=ImportError("fastembed not installed"),
+    ):
+        ledger = _build_ledger(GovernanceConfig(), api_key="sk-x", base_url=None)
+    assert ledger is not None
+    assert isinstance(ledger._embedder, LocalEmbedder)
+    assert not isinstance(ledger._embedder, OpenAIEmbedder)
+
+
+def test_build_ledger_uses_openai_embedder_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
+    from moralstack.orchestration.embedder import OpenAIEmbedder
+
+    with patch("openai.OpenAI"):
+        ledger = _build_ledger(
+            GovernanceConfig(embedder_provider="openai"),
+            api_key="sk-test",
+            base_url=None,
+        )
+    assert ledger is not None
+    assert isinstance(ledger._embedder, OpenAIEmbedder)
+
+
+def test_resolve_embedder_provider_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setenv("MORALSTACK_EMBEDDER_PROVIDER", "openai")
+    assert _resolve_embedder_provider(GovernanceConfig(embedder_provider="local")) == "openai"
+
+
+def test_resolve_embedder_provider_defaults_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.delenv("MORALSTACK_EMBEDDER_PROVIDER", raising=False)
+    assert _resolve_embedder_provider(GovernanceConfig()) == "local"
+
+
+def test_bootstrap_local_embedder_does_not_require_embedder_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
+    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
+    with patch(
+        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
+        side_effect=ImportError("fastembed not installed"),
+    ):
+        ledger = _build_ledger(GovernanceConfig(embedder_provider="local"), api_key="", base_url=None)
+    assert ledger is not None
+
+
+def test_bootstrap_ledger_embedder_type_is_local_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)
+    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
+    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
+    from moralstack.orchestration.embedder import LocalEmbedder, OpenAIEmbedder
+
+    with patch(
+        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
+        side_effect=ImportError("fastembed not installed"),
+    ):
+        orch = _bootstrap_pipeline(GovernanceConfig())
+    assert isinstance(orch.ledger._embedder, LocalEmbedder)
+    assert not isinstance(orch.ledger._embedder, OpenAIEmbedder)
+
+
+def test_build_ledger_openai_provider_without_api_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
+    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
+    ledger = _build_ledger(GovernanceConfig(embedder_provider="openai"), api_key="", base_url=None)
+    assert ledger is None
diff --git a/tests/test_sdk_config.py b/tests/test_sdk_config.py
index 57ce7a6..26fe5d5 100644
--- a/tests/test_sdk_config.py
+++ b/tests/test_sdk_config.py
@@ -2,6 +2,7 @@
 
 import pytest
 
+from moralstack.sdk.bootstrap import _resolve_embedder_provider
 from moralstack.sdk.config import GovernanceConfig
 
 
@@ -58,3 +59,18 @@ class TestGovernanceConfigDefaults:
         cfg = GovernanceConfig()
         cfg.api_key = "sk-new"
         assert cfg.api_key == "sk-new"
+
+
+class TestGovernanceConfigEmbedderProvider:
+    def test_embedder_provider_defaults_to_local(self) -> None:
+        assert GovernanceConfig().embedder_provider == "local"
+
+    def test_embedder_provider_accepts_openai(self) -> None:
+        assert GovernanceConfig(embedder_provider="openai").embedder_provider == "openai"
+
+    def test_embedder_provider_rejects_invalid_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
+        monkeypatch.delenv("MORALSTACK_EMBEDDER_PROVIDER", raising=False)
+        cfg = GovernanceConfig()
+        object.__setattr__(cfg, "embedder_provider", "sagemaker")
+        with pytest.raises(ValueError, match="sagemaker"):
+            _resolve_embedder_provider(cfg)
```

## git diff --cached (staged)
```diff

```

