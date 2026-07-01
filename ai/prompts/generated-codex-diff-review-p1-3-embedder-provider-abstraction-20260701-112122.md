You are an **independent technical reviewer**. Review the supplied diff against
the approved plan. Do not propose generic rewrites. Be specific and cite
`path:line`. Read surrounding code (read-only) to judge the change in context.

Look for:
- deviations from the approved plan (scope creep, missing steps, changed APIs);
- bugs and logic errors;
- regressions (broken callers, changed payloads/DB rows/JSONL envelopes);
- missing or weak tests for the new/changed behavior;
- typing errors;
- async/sync mistakes (blocking calls in the event loop, unawaited coroutines);
- exception-handling errors (bare/broad except, swallowed errors, fail-open);
- security problems (input validation, secrets, authz);
- performance problems;
- dead code;
- needless complexity.

This is the MoralStack governance engine. Explicitly verify the diff does not
break the invariants in PROJECT_SPEC.md section 5 / `.claude/rules/`
(decision/generation separation, hard-signal supremacy, prompt transparency,
governed delivery, observability best-effort). A change that makes governance
fail *open* is always BLOCKING.

Produce EXACTLY this markdown structure and nothing else:

# Codex Diff Review

## Verdict
One of: `APPROVE` | `APPROVE_WITH_CHANGES` | `BLOCK`

## Deviations from approved plan

## Blocking issues
(Each: what, why it blocks, the `path:line` evidence, and the required fix.)

## Non-blocking issues

## Missing/weak tests

## Security issues

## Performance issues

## Maintainability issues

## Required fixes

## Suggested fixes


---

## Repository context
- Repo root: C:\Users\fdidonato\Documents\progetti\moralstack
- MoralStack governance engine. Verify the diff does NOT break any invariant in
  PROJECT_SPEC.md section 5 / .claude/rules/ (decision/generation separation,
  hard-signal supremacy, prompt transparency, governed delivery, observability).
- You may read any file in the repo (read-only) to verify the diff in context.

---

## APPROVED PLAN (file: ai/plans/p1-3-embedder-provider-abstraction.md)

# Plan â€” p1-3-embedder-provider-abstraction

## Goal

Make the embedder provider-neutral: introduce a local embedder as default, keep OpenAI as opt-in, expose `embedder_provider` configuration, and eliminate the redundant `embed()` call on every missâ†’store cycle.

---

## Current behavior

- `moralstack/sdk/bootstrap.py:89` â€” `_build_ledger()` unconditionally constructs `OpenAIEmbedder(api_key, model, base_url)`. A valid `OPENAI_API_KEY` is required even when the deliberation pipeline itself has one (they share it) but the embedder is a second logical dependency.
- `moralstack/sdk/config.py:114` â€” `GovernanceConfig.ledger_embedding_model: str | None = None` controls only the OpenAI model name; there is no `embedder_provider` field.
- `moralstack/orchestration/ledger.py:259` â€” `_lookup_impl()` calls `self._embedder.embed(prompt)` and stores the result in the local variable `query_embedding`. This variable is used for similarity scoring only; it is not returned to the caller.
- `moralstack/orchestration/ledger.py:342` â€” `store()` calls `self._embedder.embed(prompt)` a second time for the same prompt when the caller performs a missâ†’store cycle. There is no channel by which the embedding computed in `_lookup_impl()` can be reused in `store()`.
- `moralstack/orchestration/process_context.py:38` â€” `ProcessCallContext.ledger_lookup: Any | None = None` already holds the `LedgerResult` from lookup, but `LedgerResult` carries no embedding.
- `moralstack/orchestration/embedder.py:36â€“53` â€” `EmbedderProtocol` is a structural Protocol with `embed(text: str) -> list[float]`. Already exists and is dimension-agnostic.
- `pyproject.toml:28` â€” `openai>=2.24.0` is a hard dependency; no local embedding libraries (fastembed, model2vec, sentence-transformers) are present.

---

## Target behavior

1. `GovernanceConfig.embedder_provider: Literal["local", "openai"] = "local"` controls which embedder is used. Env override: `MORALSTACK_EMBEDDER_PROVIDER`.
2. `embedder_provider="local"` (default): constructs `LocalEmbedder`, which tries to import `fastembed`. If `fastembed` is not installed, it falls back to `HashingEmbedder` (pure Python, zero new dependencies). No `OPENAI_API_KEY` is required for the embedder.
3. `embedder_provider="openai"`: constructs `OpenAIEmbedder` as today. Requires `OPENAI_API_KEY`.
4. `LedgerResult` carries a new field `query_embedding: list[float] | None` (excluded from `__hash__` and `__eq__`) that is populated when `_lookup_impl()` actually calls `embed()`. This covers the `below_threshold` and `intent_divergence` miss paths, which are the paths where a subsequent `store()` call would previously re-embed.
5. `SemanticDecisionLedger.store()` accepts an optional `prompt_embedding: list[float] | None = None` keyword argument. When provided and not `None`, the internal `self._embedder.embed(prompt)` call is skipped.
6. `OrchestrationController._maybe_store_in_ledger()` reads `call_ctx.ledger_lookup.query_embedding` and passes it to `self._ledger.store(prompt_embedding=...)`, eliminating the redundant embed call.
7. All existing tests pass without modification to their assertions (some test fixtures require adjustment â€” see Tests section).

---

## Assumptions

1. `fastembed` is not currently installed in the dev environment; `HashingEmbedder` will be the effective default local implementation in the test suite.
2. `openai>=2.24.0` remains a hard dependency (needed for the deliberation pipeline, regardless of embedder provider).
3. `InMemoryLedgerStorage` is per-process and restarts clean; there is no persistent embedding store to migrate.
4. `LedgerResult` is never used as a dict key or set member in production code (verified: stored only in `call_ctx.ledger_lookup`, returned from `lookup()`, inspected for fields).
5. The double embedding only manifests in the `below_threshold` and `intent_divergence` miss paths of `_lookup_impl()` â€” the two paths where `embed()` is called and a miss is returned. In the `no_candidates`, `posture_escalated`, and `turn_index_below_one` paths, `embed()` is never called, so `store()` must embed regardless.
6. `fastembed` model downloads occur at first use (construction time of `LocalEmbedder`). In network-restricted CI, the `HashingEmbedder` fallback is used and no download occurs.

---

## Constraints

### PROJECT_SPEC Â§5 invariants

- **Invariant 1 (Decision/generation separation, P0)**: The embedder is on the ledger fast-path only; `final_action` computation is downstream of the ledger hit/miss result and is derived from structured signals, not from the embedding or similarity score. No change required to the decision path.
- **Invariant 3 (Hard-signal supremacy, P0)**: Hard-signal refusals produce `posture="ESCALATED"`, which causes `_lookup_impl()` to return early at `ledger.py:245` (before any `embed()` call) and `store()` to skip at `ledger.py:313`. The embedder change does not touch this logic. Invariant remains intact.
- **Invariant 6 (Observability never breaks the request)**: The embedder construction is inside `_build_ledger()` which is wrapped in a try/except that returns `None` on failure (`bootstrap.py:108â€“110`). No change to that guard.
- **Invariant 7 (Governed delivery only)**: Not touched.

### Scope limits

- Do not change `cosine_similarity()`; it is dimension-agnostic.
- Do not touch the deliberation pipeline, risk estimator, or policy.
- Do not add persistent ledger storage; `InMemoryLedgerStorage` is unchanged.
- Do not rename or reorganize existing symbols (PROJECT_SPEC Â§6).
- `mypy` strict mode is enabled for `moralstack/orchestration/` (`pyproject.toml:136â€“138`); all new code in that package must have complete type annotations.

### Threshold calibration (documented constraint, not a code invariant)

The default threshold `0.92` is calibrated against OpenAI `text-embedding-3-small` (1536 dims). With `HashingEmbedder`, only exact-token-overlap texts produce high cosine similarity; semantically equivalent but differently worded queries will produce low similarity and be treated as misses. With a fastembed model (e.g., `BAAI/bge-small-en-v1.5`, 384 dims), semantic similarity is genuine but the distribution differs from OpenAI. The threshold value is unchanged by this plan; recalibration guidance must be added to the field docstring in `GovernanceConfig`.

---

## Proposed design

### Step 1 â€” `HashingEmbedder` (new class in `embedder.py`)

Add after the `cosine_similarity` function and before `OpenAIEmbedder`. Pure Python, no new imports beyond `hashlib` (stdlib).

```python
DEFAULT_HASHING_DIM = 512

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
```

### Step 2 â€” `_FastEmbedWrapper` and `LocalEmbedder` (new classes in `embedder.py`)

Add after `HashingEmbedder` and before `OpenAIEmbedder`. `_FastEmbedWrapper` is an internal implementation detail; `LocalEmbedder` is the public class.

```python
DEFAULT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


class _FastEmbedWrapper:
    """Internal: wraps fastembed.TextEmbedding as EmbedderProtocol."""

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding  # type: ignore[import]
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, text: str) -> list[float]:
        # TextEmbedding.embed() accepts an iterable and returns a generator of numpy arrays.
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
        self._model_name = (
            model
            or os.getenv("MORALSTACK_LOCAL_EMBEDDING_MODEL")
            or DEFAULT_LOCAL_EMBEDDING_MODEL
        )
        self._delegate: EmbedderProtocol
        try:
            self._delegate = _FastEmbedWrapper(self._model_name)
            logger.info("LocalEmbedder: using fastembed model %r", self._model_name)
        except ImportError:
            self._delegate = HashingEmbedder()
            logger.info(
                "LocalEmbedder: fastembed not installed, using HashingEmbedder "
                "(dim=%d). Install moralstack[local-embeddings] for semantic similarity.",
                DEFAULT_HASHING_DIM,  # module-level constant, not a class attribute
            )

    def embed(self, text: str) -> list[float]:
        return self._delegate.embed(text)
```

Update `DEFAULT_EMBEDDING_MODEL` constant (already at `embedder.py:102`) to clarify it is the OpenAI default. Add `DEFAULT_HASHING_DIM` and `DEFAULT_LOCAL_EMBEDDING_MODEL` constants at the module level.

Update the module-level docstring to document all three implementations.

### Step 3 â€” `GovernanceConfig.embedder_provider` (new field in `config.py`)

Add immediately after `ledger_embedding_model` at `config.py:119`:

```python
embedder_provider: Literal["local", "openai"] = "local"
"""
Embedding provider for the SemanticDecisionLedger.
  'local'  â€” LocalEmbedder: fastembed when installed, else HashingEmbedder (default).
             No OPENAI_API_KEY required. Threshold calibrated at 0.92 for OpenAI
             text-embedding-3-small may need adjustment for local models.
  'openai' â€” OpenAIEmbedder: requires OPENAI_API_KEY.
Override with MORALSTACK_EMBEDDER_PROVIDER.
"""
```

Add `from typing import Literal` to the imports in `config.py` (currently uses `Any` from `typing`).

### Step 4 â€” `_resolve_embedder_provider()` and `_build_embedder()` in `bootstrap.py`

Add two new helpers after `_resolve_ledger_embedding_model()` at `bootstrap.py:62`:

```python
_VALID_EMBEDDER_PROVIDERS = frozenset({"local", "openai"})


def _resolve_embedder_provider(config: "GovernanceConfig") -> str:
    """Resolve embedder provider: env > config. Returns 'local' or 'openai'.

    Raises ValueError for any unknown value so misconfiguration fails loudly
    rather than silently falling back to 'local'.
    """
    raw = (os.getenv("MORALSTACK_EMBEDDER_PROVIDER") or "").strip().lower()
    if raw:
        if raw not in _VALID_EMBEDDER_PROVIDERS:
            raise ValueError(
                f"Unknown MORALSTACK_EMBEDDER_PROVIDER={raw!r}; "
                f"must be one of {sorted(_VALID_EMBEDDER_PROVIDERS)}"
            )
        return raw
    provider = (getattr(config, "embedder_provider", None) or "local").lower()
    if provider not in _VALID_EMBEDDER_PROVIDERS:
        raise ValueError(
            f"Unknown embedder_provider={provider!r}; "
            f"must be one of {sorted(_VALID_EMBEDDER_PROVIDERS)}"
        )
    return provider


def _build_embedder(
    config: "GovernanceConfig",
    api_key: str,
    base_url: str | None,
) -> Any:
    """
    Factory: returns the correct EmbedderProtocol implementation.

    Raises on misconfiguration (e.g. openai provider without API key).
    Caller (_build_ledger) wraps this in a try/except.
    """
    provider = _resolve_embedder_provider(config)
    if provider == "openai":
        from moralstack.orchestration.embedder import OpenAIEmbedder
        return OpenAIEmbedder(
            api_key=api_key,
            model=_resolve_ledger_embedding_model(config),
            base_url=base_url,
        )
    # provider == "local" (default)
    from moralstack.orchestration.embedder import LocalEmbedder
    return LocalEmbedder()
```

### Step 5 â€” Update `_build_ledger()` in `bootstrap.py`

Replace the hardcoded `OpenAIEmbedder` instantiation (lines 81â€“93) with:

```python
    try:
        from moralstack.orchestration.ledger import SemanticDecisionLedger
        from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
    except Exception as e:
        logger.warning("MoralStack SDK: ledger imports failed (%s); proceeding without fast-path", e)
        return None

    try:
        embedder = _build_embedder(config, api_key=api_key, base_url=base_url)
        max_entries = _resolve_ledger_max_entries(config)
        storage = InMemoryLedgerStorage(max_entries=max_entries)
        threshold = _resolve_ledger_threshold(config)
        ledger = SemanticDecisionLedger(
            embedder=embedder,
            storage=storage,
            similarity_threshold=threshold,
        )
        ...
```

The log message at `bootstrap.py:102` should now also log the provider name.

### Step 6 â€” `LedgerResult.query_embedding` (new field in `ledger.py`)

`LedgerResult` is `@dataclass(frozen=True)`. Adding a `list[float]` field requires excluding it from `__hash__` and `__eq__` to preserve hashability and the existing equality semantics.

```python
from dataclasses import dataclass, field as dc_field

@dataclass(frozen=True)
class LedgerResult:
    is_hit: bool
    cached_decision: CachedDecision | None
    similarity: float
    from_turn: int | None
    reason: str
    # Populated on every path where _lookup_impl() actually called embed():
    # below_threshold, intent_divergence, and hit.
    # None on early-skip paths (posture_escalated, turn_index_below_one, no_candidates)
    # where embed() is never called.
    # On a ledger HIT the controller does NOT call _maybe_store_in_ledger() (deliberation
    # is skipped on a cache hit), so query_embedding on a hit is populated for completeness
    # but is never consumed.
    # Excluded from __hash__, __eq__, and __repr__ to keep LedgerResult hashable,
    # its equality semantics unchanged, and prevent accidental logging of prompt-derived vectors.
    query_embedding: list[float] | None = dc_field(default=None, hash=False, compare=False, repr=False)
```

The import alias `dc_field` avoids a name collision with the `field` name used elsewhere in the module. If no other `field` is imported in `ledger.py`, plain `field` is fine.

Verify that `dataclasses` is already imported in `ledger.py` (it is, at line 25: `from dataclasses import dataclass, field`).

### Step 7 â€” Update `_lookup_impl()` in `ledger.py`

The five return statements in `_lookup_impl()` must be updated. Only the returns after the `embed()` call at line 259 should populate `query_embedding`.

- Lines 246â€“248 (`posture_escalated`): no embed call; `query_embedding` remains `None` (default).
- Lines 250â€“252 (`turn_index_below_one`): no embed call; `None`.
- Lines 256â€“257 (`no_candidates`): no embed call; `None`.
- Lines 269â€“276 (`below_threshold`): embed was called; set `query_embedding=query_embedding`.
- Lines 280â€“286 (`intent_divergence`): embed was called; set `query_embedding=query_embedding`.
- Lines 288â€“294 (hit): embed was called; set `query_embedding=query_embedding`.

Concrete changes for lines 270â€“294:

```python
        if best_entry is None or best_similarity < self._threshold:
            return LedgerResult(
                is_hit=False,
                cached_decision=None,
                similarity=max(0.0, best_similarity),
                from_turn=None,
                reason="below_threshold",
                query_embedding=query_embedding,   # <-- new
            )

        if best_entry.intent_clarity != intent_clarity or best_entry.request_type != request_type:
            return LedgerResult(
                is_hit=False,
                cached_decision=None,
                similarity=best_similarity,
                from_turn=None,
                reason="intent_divergence",
                query_embedding=query_embedding,   # <-- new
            )

        return LedgerResult(
            is_hit=True,
            cached_decision=best_entry.cached_decision,
            similarity=best_similarity,
            from_turn=best_entry.turn_index,
            reason="",
            query_embedding=query_embedding,       # <-- new
        )
```

### Step 8 â€” Update `store()` in `ledger.py`

Add `prompt_embedding: list[float] | None = None` as the **last keyword-only** argument. Replace line 342 with a validated conditional.

`SemanticDecisionLedger` tracks `_embedding_dim: int | None = None` (new instance attribute, set on the first embedding call). This dimension is used to validate any caller-supplied vector, preventing injection of empty or mismatched vectors into the governance cache.

```python
    def store(
        self,
        prompt: str,
        contract_hash: str,
        posture: str,
        domain: str | None,
        decision: CachedDecision,
        intent_clarity: str,
        request_type: str,
        turn_index: int,
        *,
        prompt_embedding: list[float] | None = None,   # <-- new; keyword-only
    ) -> bool:
        ...
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
        # Record the canonical dimension on first use.
        if self._embedding_dim is None:
            self._embedding_dim = len(embedding)
        ...
```

Add `self._embedding_dim: int | None = None` to `SemanticDecisionLedger.__init__()`.

Similarly, update `_lookup_impl()` to set `self._embedding_dim` from `query_embedding` when it is computed (after line 259):

```python
        query_embedding = self._embedder.embed(prompt)
        if self._embedding_dim is None:
            self._embedding_dim = len(query_embedding)
```

Note: `prompt_embedding` is marked **keyword-only** (the `*` separator) so it cannot be passed positionally, reducing the risk of accidental injection from callers unaware of its semantics.

### Step 9 â€” Update `_maybe_store_in_ledger()` in `controller.py`

At `controller.py:582`, `_maybe_store_in_ledger()` has access to `call_ctx`. Add extraction of the precomputed embedding before the `self._ledger.store(...)` call at line 684:

```python
        # Reuse embedding computed during lookup to avoid a second embed() call.
        prompt_embedding: list[float] | None = None
        lookup_result = call_ctx.ledger_lookup
        if lookup_result is not None:
            prompt_embedding = getattr(lookup_result, "query_embedding", None)

        try:
            self._ledger.store(
                prompt=request.prompt,
                contract_hash=contract_hash,
                posture=posture,
                domain=domain,
                decision=cached,
                intent_clarity=intent_clarity,
                request_type=request_type,
                turn_index=turn_index,
                prompt_embedding=prompt_embedding,   # <-- new
            )
```

### Step 10 â€” `pyproject.toml`: add `local-embeddings` optional dependency group

Add after the `server` group (line 56):

```toml
[project.optional-dependencies]
...
local-embeddings = [
    "fastembed>=0.2.0",
]
```

---

## Alternatives considered (rejected)

### A â€” Memoize embedding as instance state in `SemanticDecisionLedger`

Store `(last_prompt, last_embedding)` as a pair on the ledger instance. `store()` checks if `prompt == self._last_prompt` and reuses `self._last_embedding`. Simpler implementation.

Rejected because: introduces implicit temporal coupling between `lookup()` and `store()`. Not thread-safe (InMemoryLedgerStorage comment at `ledger_storage.py:82` already flags thread-safety as out of scope, but the ledger itself is a controller-level singleton and the controller handles concurrent requests via Starlette threadpool). The `query_embedding` field in `LedgerResult` is explicit and testable without concurrency risk.

### B â€” Return `(LedgerResult, list[float] | None)` tuple from `lookup()`

Change `lookup()` to return a 2-tuple. More disruptive: every caller must unpack. The current call site in `controller.py:238` passes the result directly to `call_ctx.ledger_lookup` and then to `_emit_lookup_event`. Rejected in favor of embedding the value inside `LedgerResult` where it is naturally co-located with the lookup outcome.

### C â€” Keep OpenAI as default, add local as opt-in

Inverting the design requirement. Rejected: the task specification requires `"local"` as default.

### D â€” Add `fastembed` as a hard dependency

Rejected: fastembed downloads a model at construction time (~90 MB). The SDK's stated philosophy (per `embedder.py` module docstring, line 13â€“18) is to keep the dependency footprint minimal. Optional dependency group is the right mechanism.

### E â€” Use `model2vec` instead of `fastembed`

model2vec (static embeddings distilled from a transformer) is lighter in model size (~4 MB) and has no numpy dependency issue. However, it is a newer and less-established library, and its Python API is less stable than fastembed's. Fastembed is explicitly named in the design requirements. Either could be swapped later by changing `_FastEmbedWrapper` only.

### F â€” Use `sentence-transformers` instead of `fastembed`

Better-known library, wider model selection. Rejected: heavier dependency tree (torch transitive dependency). Fastembed uses ONNX runtime, which is lighter.

---

## Files to modify

- `moralstack/orchestration/embedder.py` â€” add `HashingEmbedder`, `_FastEmbedWrapper`, `LocalEmbedder`; add module-level constants `DEFAULT_HASHING_DIM` and `DEFAULT_LOCAL_EMBEDDING_MODEL`; update module docstring.
- `moralstack/sdk/config.py` â€” add `embedder_provider: Literal["local", "openai"] = "local"` field; add `Literal` to `typing` import.
- `moralstack/sdk/bootstrap.py` â€” add `_resolve_embedder_provider()` and `_build_embedder()` helpers; update `_build_ledger()` to use `_build_embedder()`.
- `moralstack/orchestration/ledger.py` â€” add `query_embedding` field to `LedgerResult` (with `field(hash=False, compare=False)`); update the three `return LedgerResult(...)` statements after the `embed()` call; add `prompt_embedding` parameter to `store()`.
- `moralstack/orchestration/controller.py` â€” update `_maybe_store_in_ledger()` to extract `query_embedding` from `call_ctx.ledger_lookup` and pass it to `self._ledger.store()`.
- `pyproject.toml` â€” add `local-embeddings` optional dependency group.
- `docs/MORALSTACK_CODEBASE_INDEX.md` â€” update embedder section to document three implementations and provider selection.
- `docs/CODEBASE_FACTS.md` â€” add: (a) local embedder as new fact; (b) double-embedding fix; (c) fastembed optional dep group.

---

## Tests to add / modify

All new tests must be offline and deterministic (no network, no real API).

### Fixtures / mocks shared across test files

**`_CountingStubEmbedder`** â€” define locally in each test file that needs it:

```python
class _CountingStubEmbedder:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.call_count = 0
        self._vector = vector or [1.0, 0.0, 0.0]
    def embed(self, text: str) -> list[float]:
        self.call_count += 1
        return list(self._vector)
```

**Fastembed mock** (for `test_local_embedder.py`): `_FastEmbedWrapper.__init__()` uses a local import (`from fastembed import TextEmbedding`). Patch via `monkeypatch.setitem(sys.modules, "fastembed", None)` to force the `ImportError` fallback, or `monkeypatch.setitem(sys.modules, "fastembed", fake_module)` where `fake_module` is a `types.ModuleType` exposing a `TextEmbedding` mock. Do NOT use `patch("moralstack.orchestration.embedder.fastembed", ...)` â€” fastembed is not a module-level attribute and this patch target does not intercept the local import.

**`openai.OpenAI` mock**: existing pattern from `test_embedder.py:107` â€” reuse via `patch("openai.OpenAI")`.

---

### `tests/test_local_embedder.py` â€” new file

**`TestHashingEmbedderProtocolConformance`**:
- `test_hashing_embedder_returns_list_of_floats`: `HashingEmbedder().embed("hello")` returns `list[float]`.
- `test_hashing_embedder_is_deterministic`: two independent instances return the same vector for the same text.
- `test_hashing_embedder_output_dim_is_constant`: `len(embed("abc")) == len(embed("xyz")) == DEFAULT_HASHING_DIM` (512).
- `test_hashing_embedder_empty_string_does_not_crash`: returns `list[float]` of length `DEFAULT_HASHING_DIM`; all zeros.
- `test_hashing_embedder_different_inputs_differ`: `embed("apple") != embed("orange")`.
- `test_hashing_embedder_satisfies_protocol`: structural check â€” `hasattr(HashingEmbedder, "embed")` and `callable(HashingEmbedder.embed)`.
- `test_custom_dim`: `HashingEmbedder(dim=64).embed("x")` has length 64.
- `test_invalid_dim_raises`: `HashingEmbedder(dim=0)` raises `ValueError`.
- `test_identical_input_cosine_one`: `cosine_similarity(h.embed(t), h.embed(t)) == 1.0`.
- `test_different_inputs_cosine_below_one`: `cosine_similarity(h.embed("foo"), h.embed("bar")) < 1.0`.

**`TestLocalEmbedderWithFastembedMocked`**:
- `test_local_embedder_uses_fastembed_when_available`: patches `fastembed.TextEmbedding` to return a fixed 384-float vector; asserts `LocalEmbedder().embed("test")` is a `list[float]` of length 384 and the mock was called.
- `test_local_embedder_output_dim_consistent_across_calls`: same mock; asserts `len(embed("a")) == len(embed("b"))`.
- `test_local_embedder_returns_list_not_ndarray`: asserts `isinstance(result, list)` and `isinstance(result[0], float)` (guards against numpy array passthrough).

**`TestLocalEmbedderFallback`**:
- `test_local_embedder_falls_back_to_hashing_when_fastembed_missing`: `monkeypatch.setitem(sys.modules, "fastembed", None)` before instantiating `LocalEmbedder`; asserts no `ImportError` raised; returns `list[float]` with consistent dim.
- `test_local_embedder_fallback_dim_matches_hashing_embedder_constant`: same setup; asserts `len(result) == DEFAULT_HASHING_DIM`.

**`TestBuildEmbedderFactory`**:
- `test_build_embedder_returns_local_by_default`: `GovernanceConfig()` â†’ result is `LocalEmbedder` (or `HashingEmbedder` as delegate); NOT `OpenAIEmbedder`.
- `test_build_embedder_returns_openai_when_configured`: `GovernanceConfig(embedder_provider="openai")` + `api_key="sk-test"` + patched `openai.OpenAI` â†’ result is `OpenAIEmbedder`.
- `test_build_embedder_openai_without_api_key_raises`: `GovernanceConfig(embedder_provider="openai")` + `api_key=""` + no env var â†’ raises `ValueError("OPENAI_API_KEY is not set")`.
- `test_build_embedder_unknown_provider_raises`: mutate config to `embedder_provider="anthropic"` â†’ raises `ValueError` matching the unknown provider name.
- `test_resolve_embedder_provider_invalid_env_raises`: `MORALSTACK_EMBEDDER_PROVIDER=sagemaker` â†’ `_resolve_embedder_provider(GovernanceConfig())` raises `ValueError` mentioning `"sagemaker"` and listing valid options.
- `test_resolve_embedder_provider_invalid_env_empty_is_ignored`: `MORALSTACK_EMBEDDER_PROVIDER=` (empty string) â†’ falls through to `config.embedder_provider`; no error.

---

### `tests/test_embedder.py` â€” extend existing file

**`TestEmbedderProtocolConformance`** (extend):
- `test_hashing_embedder_is_protocol_compliant`: structural check.
- `test_local_embedder_is_protocol_compliant`: structural check.

**Standalone pinning test** (new function, not in a class):
```python
def test_default_similarity_threshold_calibration_assumption():
    # Locks that the threshold was calibrated for text-embedding-3-small (1536 dims).
    # A dim change requires threshold recalibration review.
    from moralstack.orchestration.embedder import DEFAULT_EMBEDDING_MODEL
    from moralstack.orchestration.ledger import DEFAULT_SIMILARITY_THRESHOLD
    assert DEFAULT_SIMILARITY_THRESHOLD == 0.92
    assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"
```

---

### `tests/test_sdk_config.py` â€” extend existing file

**`TestGovernanceConfigEmbedderProvider`** (new class):
- `test_embedder_provider_defaults_to_local`: `GovernanceConfig().embedder_provider == "local"`.
- `test_embedder_provider_accepts_openai`: `GovernanceConfig(embedder_provider="openai").embedder_provider == "openai"`.
- `test_embedder_provider_rejects_invalid_value`: `GovernanceConfig(embedder_provider="sagemaker")` â†’ raises `ValueError` (if validated in `__post_init__`).

---

### `tests/test_ledger.py` â€” extend existing file

**`TestLedgerResultQueryEmbedding`** (new class):
- `test_query_embedding_none_on_posture_escalated`: `lookup(..., posture="ESCALATED")` â†’ `result.query_embedding is None`.
- `test_query_embedding_none_on_turn_zero`: `lookup(..., turn_index=0)` â†’ `result.query_embedding is None`.
- `test_query_embedding_none_on_no_candidates`: empty ledger â†’ `result.reason == "no_candidates"` AND `result.query_embedding is None`.
- `test_query_embedding_populated_on_below_threshold`: one entry stored; lookup with different vector â†’ `reason="below_threshold"`, `result.query_embedding` not None, correct length.
- `test_query_embedding_populated_on_hit`: `result.is_hit is True` AND `result.query_embedding` is not None.

**`TestLedgerResultIsFrozen`** (new class):
```python
def test_ledger_result_is_frozen():
    import dataclasses, pytest
    r = LedgerResult(is_hit=False, cached_decision=None, similarity=0.0, from_turn=None, reason="no_candidates")
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        r.is_hit = True
```
Guards against `query_embedding` addition accidentally removing `frozen=True`.

**`TestStoreSkipsEmbedOnProvidedEmbedding`** (new class):
- `test_store_with_prompt_embedding_does_not_call_embedder`: ledger with `MagicMock` embedder; `store(..., prompt_embedding=[0.1, 0.2])` â†’ `mock.embed` NOT called.
- `test_store_without_prompt_embedding_calls_embedder`: `store(...)` without kwarg â†’ `mock.embed` called once.
- `test_store_with_none_prompt_embedding_calls_embedder`: `store(..., prompt_embedding=None)` â†’ embedder called (backward compat).
- `test_store_backward_compat_no_kwarg_calls_embedder`: omit `prompt_embedding` entirely â†’ embedder called once (tests default).

**`TestStorePromptEmbeddingValidation`** (new class â€” covers B3):
- `test_store_empty_prompt_embedding_raises`: `store(..., prompt_embedding=[])` â†’ `ValueError("must not be empty")`.
- `test_store_wrong_dim_prompt_embedding_raises`: first store sets `_embedding_dim=2`; second `store(..., prompt_embedding=[1.0, 2.0, 3.0])` â†’ `ValueError("dimension")`.
- `test_store_correct_dim_prompt_embedding_accepted`: after `_embedding_dim` is set, `store(..., prompt_embedding=<correct_dim_vector>)` â†’ succeeds, embedder NOT called.
- `test_store_prompt_embedding_is_keyword_only`: call `store(prompt, contract_hash, posture, domain, decision, intent_clarity, request_type, turn_index, [0.1, 0.2])` positionally â†’ `TypeError` (keyword-only enforcement).

**`TestDoubleEmbedElimination`** (new class):
- `test_miss_then_store_embeds_once`: `_CountingStubEmbedder` with threshold 0.99; `store(turn=1)` (embed #1); `lookup(...)` â†’ `below_threshold` miss, `result.query_embedding` populated (embed #2); `store(turn=2, prompt_embedding=result.query_embedding)` â†’ no embed. Assert `call_count == 2`.

---

### `tests/test_sdk_bootstrap.py` â€” extend existing file

- `test_build_ledger_uses_local_embedder_by_default`: `_build_ledger(GovernanceConfig(), api_key="sk-x", base_url=None)` â†’ ledger embedder is `LocalEmbedder` (or `HashingEmbedder` as delegate); NOT `OpenAIEmbedder`.
- `test_build_ledger_uses_openai_embedder_when_configured`: `GovernanceConfig(embedder_provider="openai")` + patched `openai.OpenAI` â†’ ledger embedder is `OpenAIEmbedder`.
- `test_resolve_embedder_provider_env_overrides_config`: `MORALSTACK_EMBEDDER_PROVIDER=openai` + `GovernanceConfig(embedder_provider="local")` â†’ `_resolve_embedder_provider(config) == "openai"`.
- `test_resolve_embedder_provider_defaults_to_local`: no env + `GovernanceConfig()` â†’ `"local"`.
- `test_bootstrap_local_embedder_does_not_require_embedder_api_key`: clear `OPENAI_API_KEY`; call `_build_ledger(GovernanceConfig(embedder_provider="local"), api_key="", base_url=None)` â†’ ledger is not None, no `ValueError`.
- `test_bootstrap_ledger_embedder_type_is_local_by_default`: full `_bootstrap_pipeline(GovernanceConfig())` (OPENAI_API_KEY set for deliberation); assert `isinstance(orch.ledger._embedder, LocalEmbedder)` and NOT `OpenAIEmbedder`.
- `test_build_ledger_openai_provider_without_api_key_returns_none`: `_build_ledger(GovernanceConfig(embedder_provider="openai"), api_key="", ...)` â†’ returns `None` (try/except at bootstrap.py:108 catches `ValueError`); WARNING logged.

**Existing tests â€” updates required to guard against fastembed model download (N3):**

`test_bootstrap_creates_ledger_by_default` (line 98) currently calls `_bootstrap_pipeline(GovernanceConfig())`. After the refactor, `LocalEmbedder.__init__()` is triggered, which may invoke `_FastEmbedWrapper.__init__()` and attempt a model download if fastembed is installed in the dev environment. **Patch `LocalEmbedder.__init__` (or `_FastEmbedWrapper.__init__`) in all bootstrap tests to always use the `HashingEmbedder` fallback:**

```python
# In each bootstrap test that exercises _bootstrap_pipeline or _build_ledger:
with patch("moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
           side_effect=ImportError("fastembed not installed")):
    # ... test body
```

This makes all bootstrap unit tests offline-safe regardless of what is installed.

- `test_bootstrap_disables_ledger_via_env` (line 113): unaffected.
- `test_bootstrap_disables_ledger_via_config` (line 123): unaffected.
- `test_bootstrap_respects_threshold_env` (line 133): unaffected.

---

### `tests/test_orchestrator_ledger_integration.py` â€” extend existing file

**`TestDoubleEmbeddingEndToEnd`** (new class):
- `test_miss_then_store_with_precomputed_calls_embedder_exactly_twice`: counting stub, threshold 0.99; `store("first", turn=1)` (embed #1); `result = lookup("second", turn=2)` â†’ below-threshold, embed #2; `store("second", turn=2, prompt_embedding=result.query_embedding)` (no embed); assert `call_count == 2`.

**`TestLedgerDimMismatchPropagation`** (new class):
- `test_mixed_dim_entries_raise_valueerror_on_lookup`: store a `LedgerEntry` with 2-dim embedding via storage directly; construct ledger with 3-dim embedder; call `lookup()` â†’ `ValueError` propagates from `cosine_similarity`; NOT silently swallowed. Documents that mixing `LocalEmbedder` (384-dim) with `OpenAIEmbedder` (1536-dim) entries in one ledger instance is unsafe.

---

### Edge cases (must be covered by the tests above)

| # | Input | Expected |
|---|-------|----------|
| E1 | Mixed-dim entries in same ledger (e.g., stored with 384-dim, queried with 1536-dim) | `ValueError("equally-sized")` from `cosine_similarity` |
| E2 | `store(prompt_embedding=[])` (empty vector) | Entry stored; next lookup raises `ValueError` on comparison |
| E3 | `GovernanceConfig(embedder_provider="openai")` + no API key | `_build_ledger` returns `None`; WARNING logged; pipeline continues |
| E4 | `HashingEmbedder.embed("")` | `list[float]` of length `DEFAULT_HASHING_DIM`, all zeros; no crash |
| E5 | `LocalEmbedder` with fastembed absent | `HashingEmbedder` fallback; no `ImportError` raised |
| E6 | `MORALSTACK_EMBEDDER_PROVIDER=openai` + `GovernanceConfig(embedder_provider="local")` | Env wins; `OpenAIEmbedder` constructed |
| E7 | `MORALSTACK_EMBEDDER_PROVIDER=sagemaker` (unknown) | `_resolve_embedder_provider()` raises `ValueError`; `_build_ledger()` catches it and returns `None`; ledger disabled; WARNING logged |
| E8 | `lookup()` returns hit; controller does NOT call `_maybe_store_in_ledger()` (deliberation skipped on cache hit) | `query_embedding` on hit is populated but never consumed; no double-embed |
| E9 | `no_candidates` miss path; caller calls `store()` without `prompt_embedding` | `store()` calls `embed()` once (embedding was never computed during lookup) |
| E10 | `store(..., prompt_embedding=[])` | `ValueError("must not be empty")` raised immediately; storage unchanged |
| E11 | `store(..., prompt_embedding=[1.0, 2.0])` after ledger has 3-dim entries | `ValueError("dimension")` raised; storage unchanged |
| E12 | fastembed installed but `TextEmbedding(...)` raises `RuntimeError` (e.g., corrupted model) | `LocalEmbedder.__init__()` only catches `ImportError`; `RuntimeError` propagates to `_build_ledger()`, which catches it, logs WARNING, returns `None`; ledger disabled |

---

### Commands to run

Scoped (run after each file is written):

```bash
python -m pytest tests/test_local_embedder.py -v
python -m pytest tests/test_embedder.py tests/test_ledger.py tests/test_sdk_config.py tests/test_sdk_bootstrap.py tests/test_orchestrator_ledger_integration.py -v
```

Pre-commit (scoped to changed files):

```bash
pre-commit run --files moralstack/orchestration/embedder.py moralstack/orchestration/ledger.py moralstack/sdk/config.py moralstack/sdk/bootstrap.py moralstack/orchestration/controller.py pyproject.toml
```

Full suite (required before declaring done):

```bash
python -m pytest
```

---

## Risks

1. **Threshold miscalibration with local embedder**: The default `similarity_threshold=0.92` was tuned for OpenAI embeddings (dense 1536-dim vectors). With `HashingEmbedder`, semantic similarity is token-overlap-based; the threshold still works (identical text â†’ similarity = 1.0; different text â†’ near 0.0), but "semantically equivalent but differently worded" queries will miss. With fastembed models, 0.92 may be too high or too low depending on the model's output distribution. Mitigation: update the `ledger_similarity_threshold` docstring and the `embedder_provider` docstring to explicitly call this out. No threshold default change in this plan.

2. **fastembed model download in production**: `LocalEmbedder.__init__()` calls `_FastEmbedWrapper.__init__()`, which calls `TextEmbedding(model_name=...)`, which may trigger a model download. In a cold-start production environment with no internet access, this will raise at ledger construction time. Mitigation: `_build_ledger()` already wraps construction in a try/except that returns `None` on failure (`bootstrap.py:108â€“110`). The ledger is disabled gracefully and a WARNING is logged. Document the download behavior in the `LocalEmbedder` docstring.

3. **`LedgerResult.__hash__` behavior change**: Adding a field with `hash=False` changes the dataclass `__hash__` computation (the new field is simply excluded). This is backward-compatible: the existing 5-field hash is preserved. No existing code hashes `LedgerResult` instances (verified: `call_ctx.ledger_lookup` stores it but does not hash it).

4. **`store()` signature change breaks subclasses or mocks**: No subclass of `SemanticDecisionLedger` exists in the codebase (verified by grep). Test mocks in `test_orchestrator_ledger_integration.py` use `MagicMock()` for the ledger, which accepts arbitrary kwargs. Blast radius: zero.

5. **mypy strict mode (`moralstack/orchestration/`)**: `HashingEmbedder.embed()` returns `list[float]`; the internal `vec` is `list[float]`; the list-comprehension return is `list[float]`. Compliant. `LocalEmbedder.embed()` delegates to `self._delegate.embed(text)`; `_delegate` is typed as `EmbedderProtocol` (or a Union). Annotate `_delegate: EmbedderProtocol` and import the Protocol at the top of `embedder.py` (it is already defined in the same module). `_FastEmbedWrapper.embed()` uses `fastembed` (type: `ignore[import]` comment needed since fastembed has no bundled stubs).

6. **`embedder_provider` Literal import in `config.py`**: `config.py` currently imports `Any` from `typing`. Adding `Literal` is a one-line change to the import.

---

## Acceptance criteria

- [ ] `pytest tests/test_embedder.py` passes with all new `TestHashingEmbedder` and `TestLocalEmbedder` cases.
- [ ] `pytest tests/test_ledger.py` passes with all new cases; existing cases are unchanged.
- [ ] `pytest tests/test_sdk_bootstrap.py` passes; `test_bootstrap_creates_ledger_by_default` still asserts `is not None` and `similarity_threshold == 0.92`.
- [ ] `GovernanceConfig()` (no arguments) builds a ledger backed by `LocalEmbedder` (or `HashingEmbedder` as its delegate) without requiring `OPENAI_API_KEY` for the embedder.
- [ ] `GovernanceConfig(embedder_provider="openai")` builds a ledger backed by `OpenAIEmbedder` (existing behavior restored when explicitly opted in).
- [ ] `MORALSTACK_EMBEDDER_PROVIDER=openai` env var overrides `embedder_provider="local"` in config.
- [ ] On a missâ†’store cycle where `_lookup_impl()` computed an embedding, the embedder's `embed()` is called exactly once (not twice). Verified by the `TestDoubleEmbedElimination` test.
- [ ] On a `no_candidates` miss, `store()` still calls `embed()` (because lookup did not compute one). Verified by `test_store_without_prompt_embedding_calls_embedder`.
- [ ] `LedgerResult` is still hashable (no `TypeError: unhashable type: 'list'`) when `query_embedding` is a non-None list.
- [ ] `LedgerResult` with `query_embedding` is excluded from `repr` output (field has `repr=False`).
- [ ] `store(..., prompt_embedding=[])` raises `ValueError("must not be empty")`.
- [ ] `store(..., prompt_embedding=<wrong_dim>)` raises `ValueError("dimension")` once `_embedding_dim` is established.
- [ ] `store(..., prompt_embedding=...)` with a positional argument raises `TypeError` (keyword-only enforcement).
- [ ] `_resolve_embedder_provider()` raises `ValueError` for unknown provider strings (both env and config).
- [ ] `pytest -x` (full suite) passes with 0 failures, 0 errors.
- [ ] `mypy moralstack/orchestration/embedder.py` passes under strict mode.
- [ ] `ruff check moralstack/orchestration/embedder.py moralstack/sdk/config.py moralstack/sdk/bootstrap.py moralstack/orchestration/ledger.py moralstack/orchestration/controller.py` passes.

---

## Implementation checklist

Ordered for Cursor CLI:

1. Read `moralstack/orchestration/embedder.py` in full. Add `DEFAULT_HASHING_DIM = 512` and `DEFAULT_LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"` constants after `DEFAULT_EMBEDDING_MODEL`. Add `HashingEmbedder`, `_FastEmbedWrapper`, `LocalEmbedder` classes in that order after `cosine_similarity`. Update the module docstring to list all three implementations.

2. Read `moralstack/sdk/config.py` in full. Add `Literal` to the `typing` import. Add `embedder_provider: Literal["local", "openai"] = "local"` field after `ledger_embedding_model` with the docstring from Step 3 above.

3. Read `moralstack/sdk/bootstrap.py` in full. Add `_VALID_EMBEDDER_PROVIDERS`, `_resolve_embedder_provider()` (with `ValueError` for unknown values), and `_build_embedder()` helpers after `_resolve_ledger_embedding_model()`. Replace the `OpenAIEmbedder` import and instantiation in `_build_ledger()` with a call to `_build_embedder()`. Update the `_build_ledger()` log message to include the provider name.

4. Read `moralstack/orchestration/ledger.py` in full. Add `dc_field` alias from `dataclasses` (if `field` is already imported, use `field(default=None, hash=False, compare=False, repr=False)`). Add `query_embedding: list[float] | None` to `LedgerResult` with `repr=False`. Add `self._embedding_dim: int | None = None` to `SemanticDecisionLedger.__init__()`. Update the three `return LedgerResult(...)` statements after line 259 to include `query_embedding=query_embedding`. Update `_lookup_impl()` to set `self._embedding_dim` from `query_embedding` after line 259. Add `*, prompt_embedding: list[float] | None = None` to `store()` signature (keyword-only); replace line 342 with validation + conditional expression; set `self._embedding_dim` from the resolved embedding if still None.

5. Read `moralstack/orchestration/controller.py` from line 582 to 703. In `_maybe_store_in_ledger()`, before the `try:` block that calls `self._ledger.store()`, add the `prompt_embedding` extraction from `call_ctx.ledger_lookup`. Pass `prompt_embedding=prompt_embedding` to the `self._ledger.store()` call.

6. Read `pyproject.toml` in full. Add `local-embeddings = ["fastembed>=0.2.0"]` to `[project.optional-dependencies]`.

7. Write `tests/test_embedder.py` additions (new test classes: `TestHashingEmbedder`, `TestLocalEmbedder`, additions to `TestEmbedderProtocolConformance`).

8. Write `tests/test_ledger.py` additions (new test classes: `TestLedgerResultQueryEmbedding`, `TestStoreSkipsEmbedOnProvidedEmbedding`, `TestDoubleEmbedElimination`).

9. Write `tests/test_sdk_bootstrap.py` additions (four new test functions for provider resolution and embedder type verification).

10. Run `python -m pytest tests/test_embedder.py tests/test_ledger.py tests/test_sdk_bootstrap.py -v`. Fix any failures before continuing.

11. Run `python -m pytest -x` (full suite). Fix any failures.

12. Run `ruff check` and `ruff format --check` on all modified files.

13. Update `docs/MORALSTACK_CODEBASE_INDEX.md` embedder subsection. Update `docs/CODEBASE_FACTS.md` (new rows: local embedder, double-embed elimination, optional fastembed dep).

---

## Rollback plan

All five production files modified by this plan are in the same Git working tree. A single `git revert <commit-sha>` is sufficient because the change is one atomic commit.

Specific revert actions if a targeted rollback is needed without reverting docs:

1. `bootstrap.py`: revert `_build_ledger()` to construct `OpenAIEmbedder` directly (restore lines 81â€“93). Remove `_resolve_embedder_provider()` and `_build_embedder()`.
2. `config.py`: remove `embedder_provider` field.
3. `ledger.py`: remove `query_embedding` field from `LedgerResult`; remove `prompt_embedding` parameter from `store()`; restore `embedding = self._embedder.embed(prompt)` at line 342.
4. `controller.py`: remove the `prompt_embedding` extraction and the `prompt_embedding=prompt_embedding` kwarg in the `self._ledger.store()` call.
5. `embedder.py`: remove `HashingEmbedder`, `_FastEmbedWrapper`, `LocalEmbedder`, and the new constants.
6. `pyproject.toml`: remove the `local-embeddings` optional group.

No database schema migrations are involved. No persistent state is affected.

---

## Codex review response (2026-06-30)

Review report: `ai/reviews/codex-plan-review-p1-3-embedder-provider-abstraction-20260630-162220.md`
Original verdict: **BLOCK**

### Blocking items resolved

**B1 â€” `HashingEmbedder.DEFAULT_HASHING_DIM` attribute error (FIXED)**
Log message in `LocalEmbedder.__init__()` fallback path now references the module-level `DEFAULT_HASHING_DIM` constant, not `HashingEmbedder.DEFAULT_HASHING_DIM` (which does not exist as a class attribute). Fix: Step 2 code updated.

**B2 â€” Silent coercion of unknown `embedder_provider` values (FIXED)**
`_resolve_embedder_provider()` now raises `ValueError` explicitly for any value outside `{"local", "openai"}`, both from the env var and from `config.embedder_provider`. Fix: Step 4 code updated with `_VALID_EMBEDDER_PROVIDERS` constant and explicit validation.

**B3 â€” Unvalidated `prompt_embedding` injection into governance cache (FIXED)**
`store()` parameter is now keyword-only (`*, prompt_embedding`). Added: (a) empty-vector guard; (b) dimension consistency guard via `SemanticDecisionLedger._embedding_dim` (set on first embed call, validated on subsequent calls). Fix: Step 8 rewritten; `_embedding_dim` instance attribute added to `__init__()`.

### Non-blocking items addressed

**N1 â€” Comment contradiction on `query_embedding` on hit (FIXED)**
Step 6 comment now clarifies: `query_embedding` IS populated on hit, but the controller never calls `_maybe_store_in_ledger()` on a cache hit (deliberation is bypassed), so it is never consumed. Added `repr=False` to the field to prevent accidental logging.

**N2 â€” Wrong fastembed mock target (FIXED)**
Test fixtures section now specifies `sys.modules` patching pattern instead of the incorrect `patch("moralstack.orchestration.embedder.fastembed", ...)`.

**N3 â€” Risk of fastembed model download in bootstrap unit tests (FIXED)**
Bootstrap test section now requires patching `_FastEmbedWrapper.__init__` to raise `ImportError` in all bootstrap tests, making them offline-safe.

### Codex questions answered

**Q1 â€” Token-overlap false positives in `HashingEmbedder`**
Accepted. `HashingEmbedder` may produce spurious cache hits for token-overlapping but semantically different prompts. This is acceptable: the ledger is a performance optimization (best-effort fast-path), not a safety gate. Hard signals still bypass the ledger entirely (invariant 3 intact). The `HashingEmbedder` docstring documents the limitation.

**Q2 â€” Does a ledger hit trigger `store()` again?**
No. On a cache hit, the controller serves the cached decision and skips deliberation; `_maybe_store_in_ledger()` is never called on the hit path. The `query_embedding` field is populated on hits for consistency of the `LedgerResult` type, but is never consumed. Clarified in Step 6 comment.

**Q3 â€” Should `embedder_provider="local"` default to `HashingEmbedder` unless fastembed explicitly configured?**
Keep current behavior (auto-use fastembed when installed). The `HashingEmbedder` fallback is automatic when fastembed is absent. For CI/test environments, implementers must patch `_FastEmbedWrapper.__init__` to force the `HashingEmbedder` path (N3 fix). No additional config granularity introduced to avoid scope creep.

### Missing tests added

- `TestStorePromptEmbeddingValidation`: empty vector, wrong-dim vector, correct-dim accepted, keyword-only enforcement.
- `TestBuildEmbedderFactory::test_resolve_embedder_provider_invalid_env_raises`: unknown env value raises `ValueError`.
- `TestBuildEmbedderFactory::test_resolve_embedder_provider_invalid_env_empty_is_ignored`: empty env string is ignored.
- Edge cases E7â€“E12 added to the edge-case table.
- `test_default_similarity_threshold_calibration_assumption`: pinning test locking `DEFAULT_SIMILARITY_THRESHOLD == 0.92` and `DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"`.

### Revised verdict

All three BLOCK items resolved. All three NON_BLOCKING items addressed. Plan is ready for re-review or implementation.


---

## CURSOR HANDOFF (file: ai/handoffs/p1-3-embedder-provider-abstraction-cursor-cli-handoff.md)

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


---

## DIFF UNDER REVIEW (file: ai/reviews/diff-after-cursor-p1-3-embedder-provider-abstraction-cursor-cli-handoff-20260630-171648.md)

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


