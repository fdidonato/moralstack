# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
1. Proposed `HashingEmbedder` fallback will fail in the default no-fastembed path. Current package dependencies include `openai`, `pydantic`, `python-dotenv`, `ruamel.yaml`, and `langdetect`, but no `fastembed` hard dependency, and existing optional groups are `dev`, `ui`, and `server` only (`pyproject.toml:24`, `pyproject.toml:32`). The plan’s fallback log references `HashingEmbedder.DEFAULT_HASHING_DIM`, but the proposed constant is module-level, not a class attribute. In the expected fallback path, `LocalEmbedder()` raises `AttributeError`, breaking `_build_ledger()` and the default ledger path. Change the plan to reference `DEFAULT_HASHING_DIM` and add a test that instantiates `LocalEmbedder` with `fastembed` absent.

2. Invalid `embedder_provider` is not actually rejected by the proposed resolver. Current `GovernanceConfig` is a mutable dataclass (`moralstack/sdk/config.py:15`) and its `__post_init__` only remaps deprecated `failure_policy="passthrough"` (`moralstack/sdk/config.py:132`). The plan’s `_resolve_embedder_provider()` returns `(config.embedder_provider or "local").lower()` for any non-env value, and `_build_embedder()` treats anything other than `"openai"` as local. That silently accepts typos like `"opneai"` and contradicts the proposed invalid-provider test. Add explicit runtime validation in config and/or the resolver; unknown providers must raise/log clearly before falling back.

3. `store(prompt_embedding=...)` changes a safety-relevant storage invariant without validation. Today `SemanticDecisionLedger.store()` always computes the stored vector from `prompt` (`moralstack/orchestration/ledger.py:342`) and persists that vector into `LedgerEntry.embedding` (`moralstack/orchestration/ledger.py:344`). Lookup later compares the fresh query embedding against stored candidate embeddings (`moralstack/orchestration/ledger.py:259`, `moralstack/orchestration/ledger.py:263`). The plan allows any caller of public `store()` to persist an arbitrary vector unrelated to `prompt`, including empty or wrong-dimensional vectors. That can poison cache matching or trigger fail-closed lookup errors. Make the reuse path keyword-only and internally validated at minimum; preferably keep public `store()` embedding-from-prompt and add a constrained internal helper for a lookup-produced embedding.

## Non-blocking issues
The plan’s comment for `LedgerResult.query_embedding` conflicts with its later steps: it says `None` on hit, but the concrete code and edge case E7 populate it on hit. Current controller stores at the end of result attachment via `_maybe_store_in_ledger()` (`moralstack/orchestration/controller.py:582`), and that method calls `self._ledger.store(...)` unconditionally when eligible (`moralstack/orchestration/controller.py:684`). Decide whether hits should be re-stored; if not, skip hit stores explicitly and avoid carrying hit embeddings.

The fastembed test guidance is wrong for the proposed implementation. `_FastEmbedWrapper.__init__()` imports `TextEmbedding` with `from fastembed import TextEmbedding`, so `patch("moralstack.orchestration.embedder.fastembed", ...)` will not patch anything. Use `sys.modules["fastembed"]` with a fake module exposing `TextEmbedding`.

Existing bootstrap tests currently call `_bootstrap_pipeline(GovernanceConfig())` with only `OPENAI_API_KEY` set and no `LocalEmbedder` patch (`tests/test_sdk_bootstrap.py:98`). If `fastembed` is installed in a developer or CI environment, default ledger construction may try model initialization/download during this test. Force the hashing fallback or patch `LocalEmbedder` in bootstrap tests.

## Missing tests
Add a test that invalid `GovernanceConfig(embedder_provider="...")` raises before `_build_embedder()` silently picks local.

Add a test for invalid `MORALSTACK_EMBEDDER_PROVIDER`, including whether env invalid values are ignored, warned, or rejected.

Add a test that `LedgerResult(..., query_embedding=[...])` does not include the vector in `repr` if the field remains on the dataclass.

Add tests for `store(prompt_embedding=...)` validation: wrong dimension, empty vector, and mismatch with the lookup-produced prompt should not silently poison storage.

Add an end-to-end controller test that an applied ledger hit either does not store again or stores using a precomputed embedding, depending on the intended behavior.

Add a test for fastembed installed but `TextEmbedding(...)` raising a non-`ImportError`; the plan currently relies on `_build_ledger()` disabling the whole ledger, but the target behavior implies local fallback.

## Risky assumptions
The plan assumes no persistent embedding store. Current SDK bootstrap uses `InMemoryLedgerStorage` (`moralstack/sdk/bootstrap.py:94`), but the storage layer is a protocol and explicitly allows other backends (`moralstack/orchestration/ledger_storage.py:31`). Provider/dimension changes should document custom-storage migration risk.

The plan assumes `LedgerResult` is not hashed or keyed anywhere. I verified controller storage in `ProcessCallContext.ledger_lookup` (`moralstack/orchestration/process_context.py:33`) and field-level controller use, but the plan should require a full repo grep before relying on this.

The plan assumes default local hashing is semantically safe. The active ledger can apply cached decisions after structured decision/routing and a safety gate (`moralstack/orchestration/controller.py:2260`, `moralstack/orchestration/conversational_fast_path.py:112`), but hash bucket collisions are still possible and should be treated as an exact/near-exact duplicate cache, not semantic equivalence.

## Architecture concerns
The plan preserves key invariants if implemented carefully: decision/generation separation is still anchored in structured `final_action` rules (`.claude/rules/decision-policy.md:10`), hard-signal supremacy is still protected by hard-signal detection and ESCALATED skip paths (`.claude/rules/hard-signal-safety.md:9`, `moralstack/orchestration/ledger.py:245`), and governed delivery is not touched (`.claude/rules/governed-delivery.md:9`).

However, the plan widens the `LedgerResult` object with a large prompt-derived vector. Current observability emits selected ledger fields only (`moralstack/orchestration/ledger.py:374`), but future accidental `%r` logging would expose the vector unless `repr=False` is used.

Provider construction is doing too much at bootstrap time. Current `_build_ledger()` catches construction failure and disables the ledger (`moralstack/sdk/bootstrap.py:108`), but a local default that may download or initialize a model at startup should have explicit offline behavior.

## Security/performance concerns
The new `prompt_embedding` parameter is the main security concern: it lets a caller bypass the embedder and write arbitrary matching material into the ledger, whereas current code derives embeddings internally (`moralstack/orchestration/ledger.py:342`).

Embedding vectors should not be included in dataclass reprs or audit/debug payloads. Prompt transparency rules focus on prompt composition (`.claude/rules/prompt-transparency.md:9`), but leaking prompt-derived embeddings still expands sensitive data exposure.

Fastembed model initialization at ledger construction can add cold-start latency or fail in restricted environments. `_build_ledger()` catches construction errors (`moralstack/sdk/bootstrap.py:108`), so requests continue, but the ledger may unexpectedly be disabled.

## Suggested plan changes
Validate `embedder_provider` explicitly and make invalid values fail loudly.

Fix the `DEFAULT_HASHING_DIM` reference in the fallback log.

Make `query_embedding` use `field(default=None, hash=False, compare=False, repr=False)`.

Make `prompt_embedding` keyword-only and validate it, or replace it with a private/internal store path that can only reuse an embedding returned by the immediately preceding lookup.

Decide whether cache hits should be re-stored. If not, skip storing hits and reduce the need to carry embeddings on hit results.

Patch tests to avoid real fastembed initialization/download in bootstrap paths.

Add docs beyond the two planned files if module contracts change; PROJECT_SPEC requires module docs/traces when behavior or governance flow changes (`PROJECT_SPEC.md:20`).

## Questions for Claude/User
Should the hashing fallback be allowed to produce semantic cache hits at all, or should it be limited to exact duplicate matching to avoid collision-driven false positives?

Is re-storing ledger hits intentional for LRU/audit behavior, or can applied hits skip `store()` entirely?

Should `embedder_provider="local"` prefer deterministic no-network hashing unless the user explicitly opts into `fastembed`, instead of auto-using fastembed whenever installed?