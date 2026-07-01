# Cursor CLI Handoff - p1-3-embedder-provider-abstraction

## Context

Plan: ai/plans/p1-3-embedder-provider-abstraction.md
Codex review: ai/reviews/codex-plan-review-p1-3-embedder-provider-abstraction-20260630-162220.md

Codex verdict: BLOCK -> resolved. All B1/B2/B3 blocking items fixed in the plan.
All non-blocking items addressed. APPROVED for implementation.

What: Make MoralStack embedder provider-neutral:
- LocalEmbedder (fastembed or HashingEmbedder fallback) as default
- OpenAIEmbedder kept as opt-in via embedder_provider=openai
- GovernanceConfig.embedder_provider field added
- Redundant embed() call on miss-to-store cycle eliminated via LedgerResult.query_embedding

---

## Objective

Provider-neutral embedder, LocalEmbedder as default, double-embedding eliminated,
comprehensive tests added. All existing tests stay green. All PROJECT_SPEC 5 invariants intact.

---

## Files allowed to modify

Production source:
- moralstack/orchestration/embedder.py
- moralstack/sdk/config.py
- moralstack/sdk/bootstrap.py
- moralstack/orchestration/ledger.py
- moralstack/orchestration/controller.py
- pyproject.toml

Tests (new or extended):
- tests/test_local_embedder.py (NEW)
- tests/test_embedder.py (extend)
- tests/test_ledger.py (extend)
- tests/test_sdk_config.py (NEW)
- tests/test_sdk_bootstrap.py (extend)
- tests/test_orchestrator_ledger_integration.py (extend)

Documentation:
- docs/MORALSTACK_CODEBASE_INDEX.md
- docs/CODEBASE_FACTS.md
---

## Files NOT to modify

- moralstack/orchestration/ledger_storage.py
- moralstack/orchestration/process_context.py
- moralstack/orchestration/controller.py (EXCEPT _maybe_store_in_ledger() ONLY)
- Any other moralstack/ file not in allowed list
- Any test file not in allowed list (tests/test_ledger_*.py except test_ledger.py)
- .claude/, .cursor/, ai/plans/, ai/reviews/, scripts/
- docs/TRACES/, docs/modules/

---

## Invariants (PROJECT_SPEC section 5)

### Invariant 1 - Decision/generation separation (P0)
final_action computed from structured signals only. Embedder on ledger fast-path;
similarity score triggers cache hit/miss only. Do NOT change cosine_similarity().
Do NOT change hit/miss logic beyond adding query_embedding to _lookup_impl() return.

### Invariant 3 - Hard-signal supremacy (P0)
posture=ESCALATED causes _lookup_impl() and store() to skip early before embed().
ESCALATED guards must remain the FIRST check in each method. Do NOT move them.

### Invariant 6 - Observability never breaks the request
_build_ledger() wrapped in try/except returning None on failure.
store() in _maybe_store_in_ledger() also wrapped in try/except.
Do NOT remove either. LocalEmbedder catches ImportError from fastembed only;
RuntimeError propagates to _build_ledger so ledger is disabled gracefully.

### Invariant 7 - Governed delivery only
Not touched. This plan does not change response generation or delivery.

---

## Implementation checklist (ordered)

Read each file fully before editing it. Follow this exact order.

### STEP 1 - moralstack/orchestration/embedder.py (currently 182 lines)

1. After DEFAULT_EMBEDDING_MODEL (line 102) add:
   DEFAULT_HASHING_DIM = 512
   DEFAULT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

2. Add HashingEmbedder class between cosine_similarity and OpenAIEmbedder.
   Pure Python, lazy import hashlib inside embed(). Contract:
   - __init__(self, dim: int = DEFAULT_HASHING_DIM) raises ValueError when dim < 1
   - @property dim(self) -> int
   - embed(self, text: str) -> list[float]: whitespace-tokenize (lowercased),
     MD5-hash each token to bucket index (mod dim), accumulate TF counts, L2-normalize.
     Empty string -> list of dim zeros, no crash.
   See plan Step 1 for exact code.

3. Add _FastEmbedWrapper class after HashingEmbedder (internal, not exported).
   - __init__(self, model_name: str):
     from fastembed import TextEmbedding  # type: ignore[import]
     self._model = TextEmbedding(model_name=model_name)
   - embed(self, text: str) -> list[float]:
     result = list(self._model.embed([text]))
     return [float(x) for x in result[0]]
   See plan Step 2 for exact code.

4. Add LocalEmbedder class after _FastEmbedWrapper.
   - Tries _FastEmbedWrapper(self._model_name), catches ImportError,
     falls back to HashingEmbedder().
   - CRITICAL (B1 fix): fallback log MUST use module-level DEFAULT_HASHING_DIM,
     NOT HashingEmbedder.DEFAULT_HASHING_DIM (class attr does NOT exist -> AttributeError).
   - Annotate self._delegate: EmbedderProtocol (mypy strict mode on orchestration/).
   See plan Step 2 for exact code.

5. Update module docstring to list all three implementations.

### STEP 2 - moralstack/sdk/config.py (currently 138 lines)

1. Change from typing import Any  ->  from typing import Any, Literal
2. Add after ledger_embedding_model field (~line 119):
   embedder_provider: Literal["local", "openai"] = "local"
   Docstring: local=no OPENAI_API_KEY needed, threshold 0.92 calibrated for OpenAI
   text-embedding-3-small and may need recalibration for local models;
   openai=requires OPENAI_API_KEY. Override with MORALSTACK_EMBEDDER_PROVIDER env var.

### STEP 3 - moralstack/sdk/bootstrap.py (currently 159 lines)

After _resolve_ledger_embedding_model() (~line 62), add:

  _VALID_EMBEDDER_PROVIDERS = frozenset({"local", "openai"})

  def _resolve_embedder_provider(config: GovernanceConfig) -> str:
      Env var MORALSTACK_EMBEDDER_PROVIDER overrides config.embedder_provider.
      MUST raise ValueError for unknown values from BOTH env and config.
      Do NOT silently coerce unknown values to local.
      raw = (os.getenv("MORALSTACK_EMBEDDER_PROVIDER") or "").strip().lower()
      if raw:
          if raw not in _VALID_EMBEDDER_PROVIDERS: raise ValueError(...)
          return raw
      provider = (getattr(config, "embedder_provider", None) or "local").lower()
      if provider not in _VALID_EMBEDDER_PROVIDERS: raise ValueError(...)
      return provider

  def _build_embedder(config, api_key, base_url) -> Any:
      provider = _resolve_embedder_provider(config)
      if provider == "openai":
          from moralstack.orchestration.embedder import OpenAIEmbedder
          return OpenAIEmbedder(api_key=api_key, model=..., base_url=base_url)
      from moralstack.orchestration.embedder import LocalEmbedder
      return LocalEmbedder()

Update _build_ledger(): replace hardcoded OpenAIEmbedder instantiation (lines 88-93) with
  embedder = _build_embedder(config, api_key=api_key, base_url=base_url)
Keep two-block try/except structure. Update log message to include provider name.
See plan Steps 4 and 5 for exact code.

### STEP 4 - moralstack/orchestration/ledger.py (read in full)

1. LedgerResult: after reason: str, add:
   query_embedding: list[float] | None = field(
       default=None, hash=False, compare=False, repr=False
   )
   Verify @dataclass(frozen=True) remains. field already imported at line 25.

2. SemanticDecisionLedger.__init__(): after self._threshold = similarity_threshold:
   self._embedding_dim: int | None = None

3. _lookup_impl(): after query_embedding = self._embedder.embed(prompt) (line 259):
   if self._embedding_dim is None:
       self._embedding_dim = len(query_embedding)
   Update THREE return LedgerResult(...) AFTER embed call (below_threshold,
   intent_divergence, hit) to include query_embedding=query_embedding.
   THREE early returns (posture_escalated, turn_index_below_one, no_candidates)
   must NOT include query_embedding (default is None).

4. store(): add *, prompt_embedding: list[float] | None = None as LAST keyword-only param.
   if prompt_embedding is not None:
       if len(prompt_embedding) == 0:
           raise ValueError("prompt_embedding must not be empty")
       if self._embedding_dim is not None and len(prompt_embedding) != self._embedding_dim:
           raise ValueError(
               f"prompt_embedding dimension {len(prompt_embedding)} does not match "
               f"ledger embedding dimension {self._embedding_dim}"
           )
       embedding = prompt_embedding
   else:
       embedding = self._embedder.embed(prompt)
   if self._embedding_dim is None:
       self._embedding_dim = len(embedding)
   Use embedding for LedgerEntry.embedding throughout. See plan Step 8.

### STEP 5 - moralstack/orchestration/controller.py (read lines 582-703 only)

In _maybe_store_in_ledger(), before the try: block around self._ledger.store() (~line 683):
   # Reuse embedding computed during lookup to avoid a second embed() call.
   prompt_embedding: list[float] | None = None
   lookup_result = call_ctx.ledger_lookup
   if lookup_result is not None:
       prompt_embedding = getattr(lookup_result, "query_embedding", None)
Add prompt_embedding=prompt_embedding to self._ledger.store(...).
NO other changes to controller.py.

### STEP 6 - pyproject.toml

After the server group in [project.optional-dependencies]:
   local-embeddings = [
       "fastembed>=0.2.0",
   ]

### STEP 7 - tests/test_local_embedder.py (NEW FILE)

Create with all test classes from the plan Tests section.

Helper class:
  class _CountingStubEmbedder:
      def __init__(self, vector=None):
          self.call_count = 0
          self._vector = vector or [1.0, 0.0, 0.0]
      def embed(self, text: str) -> list[float]:
          self.call_count += 1
          return list(self._vector)

TestHashingEmbedderProtocolConformance (10 tests):
- test_hashing_embedder_returns_list_of_floats
- test_hashing_embedder_is_deterministic
- test_hashing_embedder_output_dim_is_constant (len == DEFAULT_HASHING_DIM == 512)
- test_hashing_embedder_empty_string_does_not_crash (list[float] len 512, all zeros)
- test_hashing_embedder_different_inputs_differ
- test_hashing_embedder_satisfies_protocol
- test_custom_dim (HashingEmbedder(dim=64).embed("x") has length 64)
- test_invalid_dim_raises (HashingEmbedder(dim=0) raises ValueError)
- test_identical_input_cosine_one (cosine_similarity(h.embed(t), h.embed(t)) == 1.0)
- test_different_inputs_cosine_below_one

TestLocalEmbedderWithFastembedMocked (3 tests):
USE sys.modules PATCHING ONLY, NOT patch("moralstack.orchestration.embedder.fastembed", ...)
- test_local_embedder_uses_fastembed_when_available
- test_local_embedder_output_dim_consistent_across_calls
- test_local_embedder_returns_list_not_ndarray

TestLocalEmbedderFallback (2 tests - monkeypatch.setitem(sys.modules, "fastembed", None)):
- test_local_embedder_falls_back_to_hashing_when_fastembed_missing
- test_local_embedder_fallback_dim_matches_hashing_embedder_constant

TestBuildEmbedderFactory (6 tests):
- test_build_embedder_returns_local_by_default
- test_build_embedder_returns_openai_when_configured
- test_build_embedder_openai_without_api_key_raises
- test_build_embedder_unknown_provider_raises
- test_resolve_embedder_provider_invalid_env_raises
- test_resolve_embedder_provider_invalid_env_empty_is_ignored

### STEP 8 - tests/test_embedder.py (extend)

Add TestEmbedderProtocolConformance class:
- test_hashing_embedder_is_protocol_compliant
- test_local_embedder_is_protocol_compliant

Add standalone pinning function test_default_similarity_threshold_calibration_assumption():
  from moralstack.orchestration.embedder import DEFAULT_EMBEDDING_MODEL
  from moralstack.orchestration.ledger import DEFAULT_SIMILARITY_THRESHOLD
  assert DEFAULT_SIMILARITY_THRESHOLD == 0.92
  assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"

### STEP 9 - tests/test_sdk_config.py (NEW FILE)

Create TestGovernanceConfigEmbedderProvider:
- test_embedder_provider_defaults_to_local
- test_embedder_provider_accepts_openai
- test_embedder_provider_rejects_invalid_value

### STEP 10 - tests/test_sdk_bootstrap.py (extend)

Add 7 functions. ALL that call _bootstrap_pipeline() or _build_ledger() must
patch _FastEmbedWrapper.__init__ to prevent network model downloads (R3/N3 fix):
  with patch(
      "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
      side_effect=ImportError("fastembed not installed"),
  ):
      ...

Functions:
- test_build_ledger_uses_local_embedder_by_default
- test_build_ledger_uses_openai_embedder_when_configured
- test_resolve_embedder_provider_env_overrides_config
- test_resolve_embedder_provider_defaults_to_local
- test_bootstrap_local_embedder_does_not_require_embedder_api_key
- test_bootstrap_ledger_embedder_type_is_local_by_default
- test_build_ledger_openai_provider_without_api_key_returns_none

### STEP 11 - tests/test_orchestrator_ledger_integration.py (extend)

TestDoubleEmbeddingEndToEnd (1 test): counting stub, threshold 0.99;
store("first", turn=1) [embed #1]; lookup("second", turn=2) -> below_threshold,
embed #2; store("second", turn=2, prompt_embedding=result.query_embedding) [no embed];
assert call_count == 2.

TestLedgerDimMismatchPropagation (1 test): store LedgerEntry with 2-dim embedding
via storage directly; construct ledger with 3-dim embedder; lookup() -> ValueError
from cosine_similarity propagates (NOT silently swallowed).

Also add to tests/test_ledger.py:

TestLedgerResultQueryEmbedding (5 tests):
- test_query_embedding_none_on_posture_escalated
- test_query_embedding_none_on_turn_zero
- test_query_embedding_none_on_no_candidates
- test_query_embedding_populated_on_below_threshold
- test_query_embedding_populated_on_hit

TestLedgerResultIsFrozen (1 test):
- test_ledger_result_is_frozen (assignment to is_hit raises FrozenInstanceError)

TestStoreSkipsEmbedOnProvidedEmbedding (4 tests):
- test_store_with_prompt_embedding_does_not_call_embedder
- test_store_without_prompt_embedding_calls_embedder
- test_store_with_none_prompt_embedding_calls_embedder
- test_store_backward_compat_no_kwarg_calls_embedder

TestStorePromptEmbeddingValidation (4 tests):
- test_store_empty_prompt_embedding_raises
- test_store_wrong_dim_prompt_embedding_raises
- test_store_correct_dim_prompt_embedding_accepted
- test_store_prompt_embedding_is_keyword_only

TestDoubleEmbedElimination (1 test):
- test_miss_then_store_embeds_once

### STEP 12 - Run scoped tests

  python -m pytest tests/test_local_embedder.py tests/test_sdk_config.py -v
  python -m pytest tests/test_embedder.py tests/test_ledger.py tests/test_sdk_bootstrap.py tests/test_orchestrator_ledger_integration.py -v
Fix failures before continuing.

### STEP 13 - Full suite

  python -m pytest -x
Fix failures. Do NOT weaken or delete any existing test.

### STEP 14 - Lint

  ruff check moralstack/orchestration/embedder.py moralstack/sdk/config.py moralstack/sdk/bootstrap.py moralstack/orchestration/ledger.py moralstack/orchestration/controller.py
  ruff format --check moralstack/orchestration/embedder.py moralstack/sdk/config.py moralstack/sdk/bootstrap.py moralstack/orchestration/ledger.py moralstack/orchestration/controller.py

### STEP 15 - Documentation

- docs/MORALSTACK_CODEBASE_INDEX.md: update embedder subsection (3 implementations + provider selection).
- docs/CODEBASE_FACTS.md: add 3 new verified facts:
  (a) LocalEmbedder is the new default (no OPENAI_API_KEY required for embedder by default)
  (b) Double-embedding eliminated: LedgerResult.query_embedding carries precomputed vector
  (c) local-embeddings optional dep group added to pyproject.toml

---

## Acceptance criteria

1.  pytest tests/test_local_embedder.py tests/test_sdk_config.py -v passes.
2.  pytest tests/test_embedder.py tests/test_ledger.py tests/test_sdk_bootstrap.py tests/test_orchestrator_ledger_integration.py -v passes.
3.  pytest -x (full suite) - 0 failures, 0 errors.
4.  GovernanceConfig() builds ledger with LocalEmbedder (or HashingEmbedder delegate) without OPENAI_API_KEY for embedder.
5.  GovernanceConfig(embedder_provider="openai") builds ledger with OpenAIEmbedder.
6.  MORALSTACK_EMBEDDER_PROVIDER=openai env overrides embedder_provider="local" in config.
7.  Miss-to-store cycle: embed() called exactly once (not twice). Verified by TestDoubleEmbedElimination.
8.  no_candidates miss: store() still calls embed().
9.  LedgerResult with query_embedding=[1.0, 2.0] is hashable (no TypeError).
10. LedgerResult repr does NOT include query_embedding (repr=False).
11. store(prompt_embedding=[]) raises ValueError("prompt_embedding must not be empty").
12. store(prompt_embedding=<wrong_dim>) raises ValueError("dimension") after _embedding_dim set.
13. Positional store(..., [0.1]) raises TypeError (keyword-only enforcement).
14. _resolve_embedder_provider() raises ValueError for unknown providers.
15. ruff check passes on all modified source files.
16. mypy passes for moralstack/orchestration/embedder.py (strict mode).

---

## Risks

R1: Threshold miscalibration with local embedder. Document in docstrings. No change to 0.92 default.
R2: fastembed model download at cold-start. _build_ledger() try/except handles it gracefully.
R3: fastembed network calls in CI tests. Patch _FastEmbedWrapper.__init__ in ALL bootstrap tests.
R4: LedgerResult hashability broken if hash=False omitted. Verify field declaration.
R5: store() keyword-only enforcement. Verified by TestStorePromptEmbeddingValidation.
R6: mypy strict on orchestration. Annotate _delegate: EmbedderProtocol; type: ignore[import].
R7 (B1 fix): LocalEmbedder fallback log MUST use module-level DEFAULT_HASHING_DIM,
             NOT HashingEmbedder.DEFAULT_HASHING_DIM (class attr NOT exist -> AttributeError).

---

## Ready prompt for Cursor CLI

You are running headless as the MoralStack implementer for task p1-3-embedder-provider-abstraction.

Read the handoff file at:
  C:\Users\fdidonato\Documents\progetti\moralstack\ai\handoffs\p1-3-embedder-provider-abstraction-cursor-cli-handoff.md

Then implement EXACTLY and ONLY what that handoff approves. Hard rules:
- Modify ONLY files listed under Files allowed to modify.
- Do NOT touch any file listed under Files NOT to modify.
- Do NOT refactor opportunistically or rename symbols.
- Do NOT weaken, skip, xfail, or delete existing tests.
- Add all tests listed in the handoff.
- Honor every invariant under Invariants (PROJECT_SPEC section 5).
- Run all verification commands; report their REAL output.
- If the plan is ambiguous or you hit a blocking problem, STOP and report it.
- Do NOT git add, commit, push, or delete files outside your own edits.

Critical points:
1. R7/B1: LocalEmbedder fallback log must use module-level DEFAULT_HASHING_DIM,
   NOT HashingEmbedder.DEFAULT_HASHING_DIM (class attr does NOT exist -> AttributeError).
2. R3: ALL bootstrap tests touching _bootstrap_pipeline or _build_ledger must patch
   moralstack.orchestration.embedder._FastEmbedWrapper.__init__
   with side_effect=ImportError("fastembed not installed") to prevent network downloads.
3. R4: LedgerResult.query_embedding must have hash=False, compare=False, repr=False.
4. ESCALATED guard in _lookup_impl() and store() must remain the FIRST check in each.
5. prompt_embedding in store() must be keyword-only (the * separator must appear before it).

At the end, output:
- files modified (list)
- tests added (list of files and classes/functions)
- commands run with their REAL results (exact output, not expected)
- deviations from the plan (list or none)
- residual problems / blockers (list or none)

---

## Output required from Cursor CLI

1. Files modified (explicit list)
2. Tests added (list of new files and new test classes/functions)
3. Commands run + real results (exact output of all pytest and ruff runs)
4. Deviations from the plan (list or none)
5. Residual problems / blockers (list or none)
