# Plan (v4.2) — Unify constitution retrieval into a single upstream wave + intent-prompt caching refactor

> **Status: APPROVED** (Codex `APPROVE_WITH_CHANGES`, 2026-07-07 —
> `ai/reviews/codex-plan-review-unify-constitution-retrieval-single-pass-20260707-115503.md`;
> no blocking issues). All non-blocking items folded in: stale observability lines rewritten
> (controller emits on risk-success, runner only on fallback — never both); `quick_check`
> HARD fallback preserved; `final_revalidation` scoped out; controller event reuses the
> runner payload shape; double-emit and zero-HARD-fallback tests added. Ready for
> `/ai-implement`.

> **Provenance / supersession.** This v4 **replaces** all prior content of this file
> (the v3 "conservative prefilter-only / decoupling" design and its "Feasibility under
> strict field-for-field" options A–D). Those were abandoned after the user made two
> explicit, superseding decisions in session:
> 1. **Full single-wave unification** — eliminate the second (deliberation) retrieval wave;
>    one retrieval per request, owned at risk-estimation time, reused by deliberation.
> 2. **Validation gate = statistical equivalence within the measured noise floor**, NOT
>    field-for-field equality on retrieval internals. Rationale is evidence-based: the
>    retriever runs at temperature 0.1 and is nondeterministic; the two waves already
>    diverge run-to-run (measured), so field-for-field on retrieval internals is not
>    achievable even by unchanged HEAD. The gate therefore targets decision **output**
>    (`final_action`, route, hard-signal) within HEAD's own noise band.
>
> The two prior Codex BLOCK reviews
> (`ai/reviews/codex-plan-review-unify-constitution-retrieval-single-pass-20260706-172446.md`,
> `...-180518.md`) remain valid input: their blockers (top_k reconciliation, retrieval-phase
> ordering, cache ownership across construction paths, contract/history exposure) are
> addressed explicitly in the Design and Risks below.
>
> **Revision v4.1 (post 3rd Codex BLOCK — 2026-07-07,**
> `ai/reviews/codex-plan-review-unify-constitution-retrieval-single-pass-20260707-103908.md`**).**
> Codex BLOCKed v4 with 3 blockers, all verified against code and fixed here:
> 1. **Empty-retrieval ambiguity** — v4 inferred "degraded" from an empty principle tuple,
>    but an empty retrieval is a *valid* result and critic reuse currently gates on
>    `len(relevant_principles) > 0` (`deliberation_runner.py:2808-2810`), which would force a
>    second retrieval. Fixed: an explicit `retrieval_succeeded`/`retrieval_error` flag on the
>    carrier; the controller-supplied `RequestAnalysisContext` is **authoritative even when
>    empty**; the critic reuse gate is changed to not require `len > 0`; fallback triggers
>    only on *unavailable/failed* retrieval.
> 2. **`retrieval_phase` labeling incomplete** — enhanced/legacy domain agents call
>    `_call_openai` without a phase and persist under the default (`retriever.py:807,830,906`).
>    Fixed: `moralstack/constitution/retriever.py` added to files-to-modify to thread
>    `retrieval_phase` through enhanced+legacy agent calls, with phase-label tests.
> 3. **Private `_top_k` access** — the public `RiskEstimatorProtocol` exposes only
>    `estimate(prompt)` (`core/types.py:100`); repo mocks have no `_top_k`. Fixed: a
>    guarded/public accessor with a safe default, plus a protocol-only-estimator controller
>    test.
> Resolved review questions: (Q1) a successful zero-principle retrieval → critic **proceeds
> with the empty principle set** (authoritative context), never re-retrieves; (Q2)
> contract/history exposure to retrieval LLMs is **accepted and bounded by truncation** —
> the contract text already reaches the same provider via the risk minis today (the
> provider call carrying contract/history is `estimator.py:802-809`, with message
> construction at `estimator.py:240-247`), so this is a marginal, documented expansion, not
> a new class of leak; (Q3) hard-signal codes must be **exact on the targeted safety suite**
> (the full hard-signal set, not only Q17); the ~1.4% intrinsic hard-signal noise applies
> only to the general suite and is orthogonal (the signals mini receives no principles).
>
> **Revision v4.2 (post 4th Codex BLOCK — 2026-07-07,**
> `ai/reviews/codex-plan-review-unify-constitution-retrieval-single-pass-20260707-111314.md`**).**
> Codex confirmed the 3 v4.1 blockers are resolved and raised ONE new blocker: the FAST_PATH
> route runs a **separate** `critic.quick_check` retrieval (`critic_module.py:638-643`,
> verified — `query=request, top_k=10, domain=None`, filtered to HARD), and on quick-check
> failure calls `run_deliberative_path` which retrieves again — so "exactly one retrieval"
> did not hold globally. **User decision: option (b) GLOBAL scope** — one retrieval per
> request across ALL routes. Fixes folded here: (i) thread the risk-owned
> `RequestAnalysisContext` through `_route_fast_path` → `run_fast_path` → `critic.quick_check`
> (quick_check gains an optional pre-retrieved-principles param, filtering the shared result
> to HARD) and into the quick-check-failed `run_deliberative_path` call; (ii) route the same
> context through the COMPLIANCE_FAST_PATH branch (`controller.py:2120,2163`); (iii) emit the
> single `RELEVANT_PRINCIPLES_RETRIEVED` at the **controller/risk-carrier level** (not only
> the runner) so non-deliberative requests that consume risk retrieval are observable too;
> (iv) expand the retriever phase-propagation site list; (v) tighten the acceptance checklist
> to exact route/REFUSE-set/hard-signal equality; (vi) correct the R6 citation (above).

## Goal

Retrieve constitution principles **exactly once per request** (owned at risk-estimation
time), have deliberation **reuse** that result via `RequestAnalysisContext`, and move the 5
fixed SEMANTIC ANALYSIS GUIDELINES out of the intent **user** message into the intent
**system** prompt so they become part of the cacheable static prefix (OpenAI prompt
caching).

## Current behavior (verified)

Two independent retrievals per request:

1. **Wave 1 — risk.** `LLMBasedRiskEstimator._get_principles_context`
   (`moralstack/models/risk/estimator.py:452-515`) calls
   `constitution_store.get_relevant_principles(query=prompt, top_k=self._top_k, domain=None)`
   (`estimator.py:464-466`; `self._top_k` default 10 at `estimator.py:325`). It derives
   `runtime_domain` from `get_debug_info()["prefiltered_domains"]`, excluding `core`
   (`estimator.py:467-478`, exclusion at `:474`), and formats the principles list **plus**
   the 5 guidelines into one `constitution_context` string (guidelines at
   `estimator.py:504-511`). That string is injected into `INTENT_CONTEXT_PROMPT_TEMPLATE`
   (`estimator.py:737-742`; template `REQUEST` + `{constitution_context}` at
   `moralstack/models/risk/prompts.py:347-353`). Only the **intent** mini receives it;
   the **signals** and **operational** minis get `REQUEST` only (`estimator.py:744-745`).
2. **Wave 2 — deliberation.** `DeliberationRunner._try_build_request_analysis_context`
   (`moralstack/orchestration/deliberation_runner.py:456-510`) calls
   `get_relevant_principles(query=_build_enriched_retrieval_query(request),
   top_k=self._retrieval_top_k_for_request(), domain=request.get_domain(),
   retrieval_phase="deliberation_retrieval")`. `_retrieval_top_k_for_request` returns the
   critic `top_k_principles` (default 20 — `deliberation_runner.py:446-454`,
   `critic_module.py:303`). It builds a `RequestAnalysisContext` (`types.py:883-902`) that
   the critic reuses (`deliberation_runner.py:2807-2837`, reuse event
   `RELEVANT_PRINCIPLES_REUSED` at `:2843`). The retrieval event
   `RELEVANT_PRINCIPLES_RETRIEVED` is emitted from `_record_retrieval_start_and_event`
   (`:546`), driven from `_deliberative_path` (`:1378-1388`).

The single-turn query differs (raw vs enriched), `top_k` differs (10 vs 20), and `domain`
differs (`None` vs `request.get_domain()`).

**Empirical basis (this session).** Across the local benchmark + 15 COMPL-AI task DBs
(1,622 comparable requests): the two waves diverge on the detected **domain** set 14% (no
contract) to 59% (with contract), and on **principle IDs** 64% (benchmark) — largely LLM
nondeterminism (retriever temperature 0.1, `retriever.py:42`). Noise floor on `final_action`
measured across 4 same-suite benchmark runs (6 pairs, n=83): mean 4.2% / max 8.4%, **all
confined to `NORMAL_COMPLETE↔SAFE_COMPLETE`, zero touching `REFUSE`**; route/path flips
0.0%; hard-signal flips 1.4% mean (intrinsic — the signals mini gets no principles).

## Target behavior

**Exactly one** `get_relevant_principles` call per request **across all routes**
(deliberative, FAST_PATH, COMPLIANCE_FAST_PATH), executed inside the risk thread at
`top_k = max(risk_top_k, critic_top_k)`. Risk slices its own `top_k` for intent formatting;
the full list + retrieval snapshot travels on the `RiskEstimation`; the controller lifts it
into a `RequestAnalysisContext`; every downstream consumer — the deliberation critic, the
fast-path `quick_check` (filtering the shared result to HARD), and the quick-check-failed
deliberative fallback — **reuses** it (retrieving again only as a fail-safe fallback). The
intent mini keeps receiving the (variable) principles list; the 5 guidelines live in the
intent system prompt. Query policy: **RAW** prompt when no `developer_contract` and no
`conversation_history`, **ENRICHED** otherwise.

**Scope note.** "Exactly one retrieval per request" covers the **active controller routes**
(deliberative, FAST_PATH, COMPLIANCE_FAST_PATH). Standalone `revalidate_final_output`
(`final_revalidation.py:272`) is **out of scope**: active governed delivery uses
`finalize_delivery` (`server/proxy.py:384`) and tests already assert no final-revalidation
event on governed-delivery paths (`tests/test_server_proxy.py:1211`), so it is not on the
per-request hot path this change targets.

## Assumptions (each verifiable)

- **A1.** `RiskEstimation` (`schema.py`) is a frozen dataclass whose fields all have
  defaults; adding further defaulted fields is backward-compatible and does not break the
  `benign`/`clearly_harmful`/`from_error` factories. Verify no serializer enumerates fields
  positionally.
- **A2.** Risk retrieval runs concurrently with speculative generation inside a worker
  thread (`controller.py:1066-1090`); no other component reads/writes the store between
  `risk_fut.result()` and deliberation. The debug-info snapshot must be captured at
  retrieval time (`store.get_debug_info()` returns last-execution state).
- **A3.** The critic reuse path keys entirely on `request_analysis.relevant_principles`
  (`deliberation_runner.py:2808-2828`); supplying a controller-built context needs no
  critic-internal change beyond a top_k slice.
- **A4.** `INTENT_CONTEXT_SYSTEM_PROMPT` is never passed through `.format()`
  (`test_static_prefix_stability.py:215-217` asserts no `{{`/`}}` and compares to the live
  constant), so appending literal guideline text is safe.
- **A5.** `_build_enriched_retrieval_query` (`deliberation_runner.py:255-296`) is a
  module-level pure function importable from the controller.

## Constraints / Invariants touched (PROJECT_SPEC §5) — how each stays intact

- **§5.1 decision/generation separation.** `final_action` still computed from structured
  signals; this change only affects which principles feed the intent mini and the critic.
- **§5.3 hard-signal supremacy (P0).** Structurally preserved: only the **intent** mini
  receives principles. The **signals** mini gets none today and still gets none. *Do not*
  add principles to the signals/operational minis. Hard-signal detection is structural, not
  principle-content-derived. Locked by a new prompt-level test (below).
- **§5.4 single-turn prompt transparency / byte-equality.** The **delivered** prompt
  (`POLICY_SYSTEM_PROMPT`) is untouched. Two internal risk-mini surfaces change and are
  re-anchored **with justification, not weakened**: (a) `INTENT_CONTEXT_SYSTEM_PROMPT` grows
  the guidelines block; (b) under a contract/history the intent principle list changes
  because the query becomes enriched. For the no-contract/no-history path the retrieval
  query stays RAW and the guidelines merely move system-ward, so the delivered composition
  stays byte-identical.
- **§5.5 `core` is retrieval-only (P0).** Preserved twice: risk still derives
  `runtime_domain` excluding `core` (`estimator.py:474`) and the controller still normalizes
  via `_normalize_runtime_domain` (`controller.py:123`). The single retrieval keeps
  `domain=None` (prefilter-driven) — exactly today's wave-1 behavior.
- **§5.6 observability best-effort.** All new/moved event emission stays inside swallowing
  try/except (mirroring `deliberation_runner.py:541-561`, `:2838-2854`).
- **§7 governed delivery.** No change to delivery; a retrieval failure fails **safe** (see
  Fallback), never fails open.

## Design

### Ownership & ordering — chosen: **Option 1 (risk owns execution, controller owns query policy)**

Retrieval stays physically inside the risk thread (no latency regression vs today's overlap
with speculative generation); the `get_debug_info` snapshot stays where it is captured
today. The controller supplies the *query* and *top_k*; risk executes, formats, and exposes
the result; the controller re-packages it for deliberation.

1. **Controller query policy** in `_estimate_risk` (`controller.py:857-900`):
   - `retrieval_query = request.prompt` when the contract text is empty/absent **and**
     history is empty/absent; else `retrieval_query = _build_enriched_retrieval_query(request)`
     (import the existing helper). Key off meaningful contract **text** and non-empty
     history, matching `controller.py:868-882`'s existing extraction (`[]`/`None`/empty
     string all count as "absent").
   - `retrieval_top_k = max(risk_top_k, runner_critic_top_k)`, where `runner_critic_top_k`
     comes from a promoted public accessor over `_retrieval_top_k_for_request()`, and
     `risk_top_k` is read via a **guarded/public accessor** — NOT the private field. The
     public `RiskEstimatorProtocol.estimate(prompt)` (`core/types.py:97-100`) exposes no
     `_top_k`, and repo mocks (`tests/test_orchestrator.py:186-194`, `cli/mocks.py:32-35`)
     implement the protocol without it. Use `getattr(risk_estimator, "_top_k",
     DEFAULT_RISK_TOP_K)` (default 10) or add a public `risk_estimator.retrieval_top_k`
     property; the controller must work with a protocol-only estimator that has neither.
     Retrieve once at the max; each consumer slices to its own `top_k` (problem-C
     reconciliation).
   Pass both into `estimate()` as optional kwargs, preserving the controller's existing
   defensive optional-kwarg handling (`controller.py:888-896`) so protocol-only estimators
   that ignore the kwargs still work.
2. **Estimator API.** Add optional keyword params `retrieval_query: str | None = None` and
   `retrieval_top_k: int | None = None` to `estimate` (`estimator.py:374`), threaded to
   `_semantic_analysis` → `_parallel_mini_analysis` → `_get_principles_context`. Defaults
   preserve standalone behavior (raw prompt, `self._top_k`) so direct callers/probes/tests
   are unaffected. **Do not force-pass new keyword-only args the store protocol makes
   optional** (protects existing store doubles — see Test Gap 1).
3. **`_get_principles_context`** (`estimator.py:452-515`): retrieve with
   `query = retrieval_query or prompt`, `top_k = retrieval_top_k or self._top_k`,
   `domain=None` (unchanged). Return the **full** retrieved list and the debug snapshot in
   addition to the formatted (sliced-to-`self._top_k`) intent string. **Remove** the
   guidelines block (`:504-511`) from `formatted_context`.
4. **Carry the payload on `RiskEstimation`.** Defaulted fields, **no import of
   `orchestration.types`** (avoids a layering inversion): `relevant_principles:
   tuple[Any, ...] = ()` (these are **in-memory `Principle` objects**, not primitives — they
   MUST NOT be serialized into any persisted payload; `LOCAL_LLM_CALL_PAYLOAD_KEYS` stays
   unchanged), `retrieval_metadata: dict[str, Any]` (default factory), `retrieval_count:
   int = 0`, `retrieval_duration_ms: float = 0.0`, `retrieval_started_at_ms: int = 0`,
   `retrieval_top_k: int = 0`, and **the retrieval-status flags** `retrieval_attempted:
   bool = False`, `retrieval_succeeded: bool = False`, `retrieval_error: str | None = None`.
   Populate where `detected_domain` is set (`estimator.py:1005-1017`). The status flags are
   the *only* signal the controller uses to decide reuse-vs-fallback — never the emptiness of
   `relevant_principles` (an empty list is a valid successful retrieval,
   `deliberation_runner.py:493,499`).
5. **Controller → deliberation.** In the `run_deliberative_path` invocation
   (`controller.py:1800-1808`), when `risk_estimation.retrieval_succeeded` is `True`, build a
   `RequestAnalysisContext` from the risk payload (even when `relevant_principles == ()` — an
   **authoritative empty context**) plus `get_constitution_safe(self.constitution_store,
   request.get_domain())`, and pass it as a new `request_analysis=` argument. Pass `None`
   ONLY when `retrieval_succeeded` is `False` (no risk estimator / no store / retrieval
   raised) so deliberation falls back to its own retrieval.
6. **Runner.** Add `request_analysis: RequestAnalysisContext | None = None` to
   `run_deliberative_path` and `_deliberative_path`. Inside (`:1378-1388`), when a context is
   supplied, use it **as authoritative even if empty**; only when it is `None` fall back to
   `_try_build_request_analysis_context(request)` (today's wave-2, unchanged). Emit
   `RELEVANT_PRINCIPLES_RETRIEVED` via `_record_retrieval_start_and_event` **only on the
   fallback path** (when the runner performed its own retrieval); on the reuse path the
   controller has already emitted it (see Observability). Never both. **Change the critic
   reuse gate** at
   `deliberation_runner.py:2808-2810`: `use_precomputed` must NOT require
   `len(request_analysis.relevant_principles) > 0` — a supplied authoritative context (empty
   or not) must be used and must NOT trigger the `critique_with_relevant_principles()`
   re-retrieval path (`:2856`). Distinguish "no context supplied" (fall back) from "context
   supplied, zero principles" (proceed with the empty set), so the "exactly one retrieval"
   criterion holds for legitimately-empty results. `RELEVANT_PRINCIPLES_REUSED` (`:2843`)
   unchanged, except the reuse slice (below).

### Global single-wave: fast-path & compliance-fast-path (v4.2)

Risk runs before routing (`controller.py:2092-2099`), so the risk-owned retrieval exists for
**every** request, including non-deliberative ones. Thread the same `RequestAnalysisContext`
to every consumer so no route re-retrieves:

- **FAST_PATH.** `controller` dispatches FAST_PATH (`controller.py:2605`) → `_route_fast_path`
  (`controller.py:1752`) → `run_fast_path` (`deliberation_runner.py:968`) → `critic.quick_check`
  (`critic_module.py:613`). Today `quick_check` self-retrieves `get_relevant_principles(
  query=request, top_k=10, domain=None)` and filters to HARD (`critic_module.py:638-643`).
  Add an **optional pre-retrieved principles** parameter to `quick_check`; when the
  controller/runner supplies the risk-owned context, `quick_check` **filters the shared list
  to `level == "hard"`** instead of retrieving. When not supplied (degraded / no risk
  retrieval), it self-retrieves as today (fail-safe). Thread the context through
  `_route_fast_path` and `run_fast_path` to reach it. **Preserve the existing constitution
  HARD fallback:** if the filtered shared list has zero HARD principles, `quick_check` must
  still fall back to the top constitution HARD constraints exactly as today
  (`critic_module.py:647-649`), never skip the check.
- **Quick-check-failed fallback.** On `not quick_result.passed`, `run_fast_path` calls
  `run_deliberative_path` (`deliberation_runner.py:970-972`); pass the same
  `request_analysis` into that call so the escalation reuses it (no 3rd retrieval).
- **COMPLIANCE_FAST_PATH.** The compliance branch can return before deliberation
  (`controller.py:2120,2163`). Ensure any principle consumption there reuses the risk-owned
  context; if it consumes none, no retrieval is added.
- **Semantic caveat.** `quick_check` today always uses the RAW query; under a contract the
  shared context is ENRICHED, so filtering the shared result changes the HARD set
  `quick_check` sees on contract fast-path requests — decision-affecting there, covered by
  the noise-floor gate (which must include contract cases and FAST_PATH transitions).

### Intent-prompt refactor

- `prompts.py`: append the 5-line SEMANTIC ANALYSIS GUIDELINES block verbatim to
  `INTENT_CONTEXT_SYSTEM_PROMPT` (`:26-345`). `INTENT_CONTEXT_PROMPT_TEMPLATE` (`:347-353`)
  stays `REQUEST` + `{constitution_context}`; `constitution_context` now carries only the
  variable principles list.
- `estimator.py`: delete `:504-511` from the `formatted_context` f-string; keep the
  `RELEVANT ETHICAL PRINCIPLES` / `HARD` / `SOFT` sections.

### Caching

Static-first ordering preserved: the moved guidelines join the static system prefix
(cacheable); the variable principles remain in the user-message tail. No reordering of user
content.

### top_k slicing (problem C)

Single retrieval at `max(risk, critic)`. Risk formats `list[:self._top_k]`. Critic reuse
slices `list(precomputed_analysis.relevant_principles)[:critic_top_k]`
(`deliberation_runner.py:2828`) so no consumer is ever **widened** beyond its configured
`top_k` when the unified `top_k` exceeds it.

### Observability / events (problem D)

Exactly one `RELEVANT_PRINCIPLES_RETRIEVED` per request, emitted at the **controller/risk-
carrier level** when the risk-owned retrieval succeeds — NOT only from the runner. Rationale
(Codex v4.2): non-deliberative routes (FAST_PATH, COMPLIANCE_FAST_PATH) consume the risk
retrieval without entering `run_deliberative_path` (`controller.py:2120,2163`), so a
runner-only emit would leave those requests unobservable. The runner emits the event only on
the **fallback** path (when it performed its own retrieval). `RELEVANT_PRINCIPLES_REUSED` is
still emitted per consumer reuse — best-effort. Guarantee exactly-one emit by making the
emit site the retrieval owner (controller on success; runner on fallback), never both. `retrieval_phase`: the single wave runs under the default `"risk_routing"`
(`store.py:873`), which is now the *true* phase for the domain **prefilter** (which already
threads the phase — `retriever.py:421,557`). **But enhanced/legacy domain AGENTS do not:**
they call `_call_openai(prompt)` without a phase (`retriever.py:807,830`) and persist under
the default (`retriever.py:69,906`). This must be fixed in
`moralstack/constitution/retriever.py` (added to files-to-modify): thread `retrieval_phase`
through the enhanced and legacy domain-agent calls and their persistence, so a risk-owned
wave labels agent `llm_calls` `"risk_routing"` and the fallback wave labels them
`"deliberation_retrieval"`. Covered by new phase-label tests for both agent kinds.

### Fallback behavior (problem B — fail safe, never fail open)

- No risk estimator (`controller.py:857-860`, default `RiskEstimation`, no payload) →
  `relevant_principles` empty → controller passes `request_analysis=None` → runner falls
  back to its own retrieval (today's behavior).
- Risk has no `constitution_store` (`estimator.py:461-462`) → empty payload → same fallback.
- Retrieval raises inside risk (`estimator.py:513-515`) → empty payload → same fallback.
In every degraded case deliberation still retrieves principles; it never silently drops them
or crashes.

## Alternatives considered (rejected)

- **Design B — defer principles from the intent mini.** Rejected per the user: the intent
  mini must keep receiving the principles list for classification. (Analysis showed the
  intent *system* prompt never references the principles — `prompts.py:26-345` — so B was
  *plausibly* safe, but the user chose to keep them; the guidelines-move captures the
  separable caching win without dropping principles.)
- **Option 2 — controller owns retrieval, injects into both.** Rejected: serializes
  retrieval ahead of the speculative overlap (latency regression), moves the
  `get_debug_info`/`runtime_domain` derivation out of risk, and still forces a risk API
  change. Option 1 achieves the same single-wave with a backward-compatible defaulted API
  and no latency cost.
- **Keep `domain=request.get_domain()` for the unified retrieval.** Rejected: the domain is
  not yet resolved at risk time (routing runs after risk); `domain=None` (prefilter-driven)
  is today's wave-1 behavior and is what produces `runtime_domain`.
- **v3 conservative prefilter-only dedup / field-for-field gate.** Superseded: the noise
  measurement showed field-for-field is not achievable against a temp-0.1 baseline; the
  statistical noise gate is the coherent replacement.

## Files to modify (smallest-diff framing)

- `moralstack/models/risk/prompts.py` — append guidelines to `INTENT_CONTEXT_SYSTEM_PROMPT`
  (`:26-345`); leave `INTENT_CONTEXT_PROMPT_TEMPLATE` (`:347-353`) structurally intact.
- `moralstack/models/risk/estimator.py` — remove guidelines from `_get_principles_context`
  (`:504-511`); add `retrieval_query`/`retrieval_top_k` params and return the full list +
  snapshot (`:452-515`); thread params through `estimate` (`:374`), `_semantic_analysis`,
  `_parallel_mini_analysis` (`:711`, `:737-742`); populate new `RiskEstimation` fields
  (`:1005-1017`).
- `moralstack/models/risk/schema.py` — add defaulted retrieval-carrier fields (incl. the
  `retrieval_attempted`/`retrieval_succeeded`/`retrieval_error` status flags) to
  `RiskEstimation`.
- `moralstack/orchestration/controller.py` — query policy + guarded/public `risk_top_k` +
  unified top_k in `_estimate_risk` (`:857-900`); build+pass `request_analysis` (authoritative
  even when empty) gated on `retrieval_succeeded` into `run_deliberative_path` (`:1800-1808`),
  `_route_fast_path` (`:1752`), and the FAST_PATH dispatch (`:2605`); route it through the
  COMPLIANCE_FAST_PATH branch (`:2120,2163`); **emit the single `RELEVANT_PRINCIPLES_RETRIEVED`
  here on success**; import `_build_enriched_retrieval_query` (accepted controller→runner
  helper coupling; consider promoting the helper to a shared module — `deliberation_runner`
  does not import controller, `:255`, so no cycle).
- `moralstack/orchestration/deliberation_runner.py` — `run_deliberative_path`/
  `_deliberative_path` accept `request_analysis` (authoritative even when empty) and fall
  back only when `None` (`:1378-1388`, emitting the event only on fallback); `run_fast_path`
  accepts + forwards `request_analysis` to `quick_check` and to the quick-check-failed
  `run_deliberative_path` (`:968-972`); promote `_retrieval_top_k_for_request` to a public
  accessor (`:446-454`); **change the critic reuse gate** so `use_precomputed` no longer
  requires `len(relevant_principles) > 0` (`:2808-2810`); slice critic reuse to critic
  top_k (`:2828`).
- `moralstack/runtime/modules/critic_module.py` — `quick_check` gains an optional
  pre-retrieved-principles param; when supplied, filter to `level == "hard"` instead of
  self-retrieving (`:613-643`); otherwise self-retrieve as today (fail-safe).
- `moralstack/constitution/retriever.py` — thread `retrieval_phase` through **all** agent
  sites: enhanced agent (`:807,830,906`), legacy agent (`:996,1006,1056`), and the parallel
  executor submit sites (`:1459,1484`) — so risk-owned vs fallback waves are labeled
  correctly for both agent kinds and cache-miss cases.
- **Docs (§8, same change):** `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/CODEBASE_FACTS.md`,
  `docs/TRACES/governance_decision_flow.md`, `docs/TRACES/observability_db_to_ui.md`,
  `docs/modules/risk_estimator.md` (+ deliberation module doc).

## Tests to add / modify

Offline, deterministic, mocked stores/policies — no network. Fixtures reuse existing
patterns: `_CapturingPolicy` (`test_static_prefix_stability.py:92-132`),
`mock_policy`/`_MINI_ESTIMATOR_JSON` (`test_multiturn_context_propagation.py:51-107`),
`_P` principle double (`test_request_analysis_reuse.py:27-31`). Store double must match the
real signature (`store.py:867-874`: `query, top_k=10, domain=None, *,
retrieval_phase="risk_routing"`) with any new kwargs keyword-only + defaulted.

### Unit

1. `tests/test_risk_estimator_runtime_domain.py` (extend) —
   `test_get_principles_context_tolerates_new_keyword_only_store_params`: the estimator must
   not force-pass a new kwarg the protocol declares optional (protects all existing store
   doubles). **[Gap 1]**
2. `tests/test_intent_guidelines_placement.py` (new) —
   `test_guidelines_present_in_intent_system_prompt` (all 5 lines are substrings of
   `INTENT_CONTEXT_SYSTEM_PROMPT`); `test_guidelines_absent_from_intent_user_prompt` (drive
   `_parallel_mini_analysis`, capture intent user message, assert no guideline line present
   while HARD/SOFT principle IDs still are). Catches partial move / duplication. **[Gap 6]**
3. `tests/test_signals_mini_principle_free.py` (new, §5.3 lock) —
   `test_signals_mini_receives_no_constitution_context_single_wave`: with a store returning
   non-empty hard principles, capture the HARM_SIGNAL system+user text, assert no retrieved
   principle IDs/titles appear. **[Gap 2]**
4. `tests/test_risk_estimation_carrier_fields.py` (new) — `benign`/`clearly_harmful`/
   `from_error` factories default all new carrier fields (`relevant_principles == ()`,
   `retrieval_metadata == {}`, counts `0`). Catches a non-defaulted field. **[Gap 7, A1]**
5. `tests/test_retrieval_top_k_reconciliation.py` (new) —
   `test_unified_topk_is_max_of_risk_and_critic` (single call with `top_k=20`);
   `test_risk_intent_formatting_slices_to_risk_topk` (intent lists only `[:10]` of 20);
   `test_critic_reuse_slices_to_critic_topk_when_smaller_than_unified` (critic top_k=5 →
   critic sees 5, not 20 — "no widening"). **[Gap 5]**
6. `tests/test_retrieval_query_policy.py` (new) — RAW when no contract/history; ENRICHED
   when contract present / history present / both (OR, not AND — catches inverted
   condition). Assert the query string actually passed to the store. **[Gap 4]**

### Integration

7. `tests/test_single_retrieval_wave_e2e.py` (new) — drive `controller.process(...)` with a
   call-counting shared store: `test_exactly_one_store_retrieval_on_happy_path` (one call
   total, risk + deliberation combined); `test_deliberation_reuses_risk_retrieved_principles_by_identity`
   (critic sees the same principle IDs/objects risk retrieved);
   `test_successful_empty_retrieval_is_authoritative_no_reretrieval` (store returns `[]`,
   risk records `retrieval_succeeded=True`, controller passes an **empty**
   `RequestAnalysisContext`, exactly one store call total, one `RELEVANT_PRINCIPLES_RETRIEVED`
   event, critic does NOT re-retrieve — locks the v4.1 blocker-1 fix). **[Gap 3]**
7b. `tests/test_retriever_phase_labeling.py` (new) — persisted `llm_calls.retrieval_phase`
   for enhanced AND legacy domain agents is `"risk_routing"` on the risk-owned wave and
   `"deliberation_retrieval"` on the fallback wave (`retriever.py:807,830,906`).
7c. `tests/test_controller_topk_protocol_only_estimator.py` (new) — controller computes the
   unified top_k with a **protocol-only** estimator (implements `estimate(prompt)` only, no
   `_top_k`; pattern from `tests/test_orchestrator.py:186-194`) and does not raise
   (`AttributeError`), falling back to `DEFAULT_RISK_TOP_K`. Locks the v4.1 blocker-3 fix.
7d. `tests/test_fast_path_single_retrieval.py` (new, v4.2 blocker) —
   `test_fast_path_quick_check_reuses_risk_context_no_reretrieval` (FAST_PATH: total store
   retrieval count == 1 when quick_check passes; quick_check filters the shared list to HARD,
   does not call the store); `test_fast_path_quick_check_failed_fallback_reuses_context`
   (quick-check fails → deliberative fallback reuses the risk-owned context, still 1 total
   retrieval); `test_compliance_fast_path_reuses_or_adds_no_retrieval`
   (COMPLIANCE_FAST_PATH adds no retrieval); `test_relevant_principles_retrieved_emitted_on_fast_path`
   (single event emitted at controller level on a non-deliberative route). Fail-safe:
   `test_quick_check_self_retrieves_when_no_context_supplied`.
   `test_controller_success_does_not_call_runner_retrieval_event` (a controller-supplied
   successful risk context must NOT call the runner's `_record_retrieval_start_and_event` —
   `deliberation_runner.py:1381,1384,542,546` — proving no double-emit).
   `test_quick_check_hard_fallback_when_shared_list_has_no_hard` (shared list with zero HARD
   principles → quick_check falls back to constitution HARD constraints, `critic_module.py:647-649`,
   check not skipped).
8. `tests/test_fallback_paths_no_risk_estimator.py` (new) — `risk_estimator=None`
   (`controller.py:857-860`): deliberation still retrieves (wave-2), non-empty principles
   reach the critic, no exception.
9. `tests/test_fallback_paths_no_constitution_store.py` (new) — risk with
   `constitution_store=None` (`estimator.py:461-462`): deliberation-layer store still
   retrieves; no crash.
10. `tests/test_fallback_paths_retrieval_raises.py` (new) — store raises inside the risk
    wave (`estimator.py:513-515`): `_estimate_risk` still returns a usable `RiskEstimation`
    (fail-safe, never fails open), deliberation retrieves independently. **[§5.6/§7]**
11. `tests/test_observability_relevant_principles_single_emit.py` (new) —
    `test_exactly_one_relevant_principles_retrieved_per_request_happy_path`;
    `test_relevant_principles_reused_still_emitted_on_critic_reuse`;
    `test_retrieval_phase_label_risk_routing_on_single_wave` /
    `..._deliberation_retrieval_on_fallback`. **[problem D]**

### Regression (must stay green, re-anchor with justification only where noted)

12. `tests/test_static_prefix_stability.py` — intent assertions re-anchor to the new
    constant automatically (compares to live constant); confirm green; the
    no-stray-braces assertion (`:216-217`) stays green (appended block has no literal braces).
13. `tests/governance_invariants/test_q17_hard_signal_invariant.py` (+ sibling
    `..._hard_signal_not_overridable_by_retrieval_wave.py`) — every member of the
    hard-signal set (Q4/Q5/Q8/Q9/Q10/Q11/Q12/Q17 — `path_router.py:17-26`), not only Q17,
    still forces `REFUSE` with byte-identical `hard_violation_codes` even when the shared
    retrieval returns only benign/soft principles. **[§5.3]**
14. `tests/test_system_prompt_byte_equality.py` — unchanged/green (delivered
    `POLICY_SYSTEM_PROMPT` untouched). **[§5.4]**
15. `tests/test_risk_persist_batch.py::test_three_mini_envelopes_have_local_15_key_payload`
    — unchanged/green; new `RiskEstimation` carrier fields must not leak into
    `LOCAL_LLM_CALL_PAYLOAD_KEYS`. **[wrong-layer field guard]**
16. `tests/test_request_analysis_reuse.py:109-209` — extend to assert single store retrieval
    end-to-end when a controller-supplied `request_analysis` is present, and fallback when
    absent.

### Edge cases

Empty prompt → single-wave retrieval never invoked (`estimator.py:406-407`); store returns
fewer than requested → slicing must not index-error; non-`hard`/`soft` levels → no crash;
falsy contract (present object, empty `raw_text`) → treated as "no contract"; empty history
`[]` vs `None` → both "no history"; critic `top_k` unset/`0` → sane default
(`deliberation_runner.py:446-454` guard); duplicate principle IDs across slices → no
double-count in `retrieval_count`.

### Noise-floor gate (offline release gate, NOT pytest)

Build the missing tooling first (nothing exists under `scripts/`/`ai/` yet). A
prompt-hash-matched comparator (e.g. `scripts/ai/noise_floor_compare.py`) that loads two
runs' `decision_traces` keyed by a stable **hash of the input prompt** (never the prompt
text), extracts **only** `final_action`, route/`path`, and `hard_violation_codes`, and
reports divergence broken down by transition pair. Steps:

1. **Validate the tool against HEAD-vs-HEAD** (known-good null): 4 benchmark runs on HEAD, 6
   pairs; confirm it reproduces the measured band (final_action mean 4.2% / max 8.4%, all
   `NORMAL↔SAFE`; route 0%; hard 1.4%). (Runs `81319498`, `79830edc`, `dceb24f8`, `60124777`
   already in `moralstack.db` establish this baseline.)
2. **Post-change:** run the same suite the same number of times on the branch; compare each
   post-change run against **each** HEAD baseline run.
3. **Pass iff** (hard gate, do not weaken):
   - `final_action` divergence ≤ ~8% **and** confined to `NORMAL_COMPLETE↔SAFE_COMPLETE`
     (any `REFUSE→`/`→REFUSE` transition = automatic fail);
   - `route`/path flips = **0** (exact);
   - the set of request-hashes routed to `REFUSE` is **identical** before/after (exact);
   - **hard-signal codes exact on the targeted safety suite.** On prompts that trip any
     member of the hard-signal set (Q4/Q5/Q8/Q9/Q10/Q11/Q12/Q17 — `path_router.py:17-26`),
     `hard_violation_codes` must be byte-identical before/after (our change does not feed the
     signals mini, so any movement here is a bug, not noise). The ~1.4% intrinsic hard-signal
     noise applies only as the *general-suite* baseline and must not worsen.
   - The gate MUST include **contract/history cases**, not only no-context prompts, since the
     enriched-query path only changes behavior there (Codex non-blocking note). Run the gate
     over a suite that exercises both.
   Note: 1–2 benchmark prompts are occasionally blocked upstream by OpenAI policy — they lack
   a FINAL trace and are excluded from matched pairs (handled by the hash-match approach).
4. **On failure:** do not adjust thresholds. Escalate to the user for an explicit, logged
   drift-acceptance decision, or narrow scope — never silently absorb.

## Risks & mitigations

- **R1.** Enriched query under a contract changes the intent mini's principles vs today
  (decision-affecting on contract cases). **Accepted**; control = the noise gate run before
  merge.
- **R2.** `RiskEstimation` field additions break a positional serializer. Mitigation: A1
  verification; all new fields defaulted + keyword-constructed; carrier-field factory test.
- **R3.** Double emission / dropped `RELEVANT_PRINCIPLES_RETRIEVED`. Mitigation: emit is
  owned by the retrieval owner — **controller on risk-success, runner only on fallback**,
  never both; a test asserts a controller-supplied success context does NOT call the runner's
  `_record_retrieval_start_and_event` (`deliberation_runner.py:1381,1384,542,546`). The
  controller-level event reuses the **existing runner payload shape exactly**
  (`principles_count`, `principle_ids`, retrieval metadata) so dashboards/queries are
  unchanged.
- **R4.** Fallback regression (principles silently dropped). Mitigation: explicit fallback
  test for each degraded construction path.
- **R5.** Byte-equality churn misread as a violation. Mitigation: justify re-anchoring in
  the commit message; confirm delivered `POLICY_SYSTEM_PROMPT` untouched.
- **R6 (security/audit — contract/history exposure).** Under single-wave, a request that has
  a developer contract but never deliberates (fast path) now sends the enriched query —
  including truncated contract + recent history text (`deliberation_runner.py:268-294`) —
  into the retrieval LLM and its persisted `llm_calls.prompt` (`retriever.py:90`). **Accepted
  and bounded:** the same contract text already reaches the same provider today via the risk
  minis (`estimator.py:746-749`), so this is a marginal expansion of an existing exposure,
  not a new class of leak; it is bounded by the existing truncation caps in
  `_build_enriched_retrieval_query`. Documented here as an explicit, accepted trade-off.
- **R7 (empty-context regression).** If the critic-reuse-gate change is done wrong, a
  legitimately-empty retrieval could either re-retrieve (breaks "exactly one") or drop the
  authoritative empty context (changes critic inputs). Mitigation: the successful-empty
  integration test below asserts one retrieval + no re-retrieval + one event.
- **Blast radius:** risk estimator, controller pipeline, deliberation runner, critic reuse,
  constitution retriever — all governance-central; hence the noise gate + full suite before
  done.

## Acceptance criteria

- [ ] Exactly one `get_relevant_principles` call per request on the happy path (risk-owned),
      **across ALL routes** — deliberative, FAST_PATH (quick_check reuses), FAST_PATH→
      deliberative fallback, and COMPLIANCE_FAST_PATH — including when the retrieval
      legitimately returns **zero** principles (authoritative empty context, no re-retrieval).
- [ ] Reuse-vs-fallback is decided by `retrieval_succeeded` (status flag), never by the
      emptiness of `relevant_principles`.
- [ ] Exactly one `RELEVANT_PRINCIPLES_RETRIEVED` event per request, emitted by the
      controller on success (runner only on fallback) — observable on non-deliberative routes.
- [ ] Guidelines present in `INTENT_CONTEXT_SYSTEM_PROMPT`, absent from the intent user
      prompt; principles list still present in the user tail.
- [ ] Retrieval query RAW iff no contract and no history, ENRICHED otherwise; single
      retrieval at `max(risk_top_k, critic_top_k)`; each consumer sliced to its own top_k.
- [ ] Fallback: with no risk estimator / no store / retrieval error, deliberation still
      retrieves principles (today's wave-2) and does not crash or skip principles.
- [ ] Delivered `POLICY_SYSTEM_PROMPT` byte-identical; signals mini remains principle-free
      (§5.3); `core` never a runtime overlay (§5.5); single retrieval passes `domain=None`.
- [ ] Full suite green: `python -m pytest`.
- [ ] Noise-floor gate passes: final_action ≤ ~8% confined to `NORMAL↔SAFE`; route flips =
      **0 (exact)**; `REFUSE` set **identical (exact)**; **hard-signal codes byte-identical**
      on the targeted safety suite (Q4/Q5/Q8/Q9/Q10/Q11/Q12/Q17); gate run over a suite
      including contract/history and FAST_PATH cases.

## Implementation checklist (ordered)

1. `prompts.py`: append guidelines to `INTENT_CONTEXT_SYSTEM_PROMPT`.
2. `estimator.py`: remove guidelines from `_get_principles_context`; add
   `retrieval_query`/`retrieval_top_k`; return full list + debug snapshot; slice to
   `self._top_k` for intent formatting.
3. Thread the params through `estimate` → `_semantic_analysis` → `_parallel_mini_analysis`.
4. `schema.py`: add defaulted carrier fields (incl. `retrieval_attempted`/
   `retrieval_succeeded`/`retrieval_error`); populate at construction (`estimator.py:1005-1017`).
5. `retriever.py`: thread `retrieval_phase` through enhanced (`:807,830,906`), legacy
   (`:996,1006,1056`), and parallel-executor submit (`:1459,1484`) sites.
6. `critic_module.py`: `quick_check` gains an optional pre-retrieved-principles param
   (filter to HARD when supplied; self-retrieve otherwise) (`:613-643`).
7. `deliberation_runner.py`: promote `_retrieval_top_k_for_request`; add `request_analysis`
   to `run_deliberative_path`/`_deliberative_path` (authoritative even when empty; emit event
   only on fallback) and to `run_fast_path` → forward to `quick_check` + the quick-check-
   failed `run_deliberative_path` (`:968-972`); change the critic reuse gate (`:2808-2810`)
   to not require `len > 0`; slice critic reuse to critic top_k.
8. `controller.py`: query policy + guarded `risk_top_k` + unified top_k in `_estimate_risk`;
   build+pass `request_analysis` gated on `retrieval_succeeded` into `run_deliberative_path`,
   `_route_fast_path`/FAST_PATH dispatch, and COMPLIANCE_FAST_PATH; emit the single
   `RELEVANT_PRINCIPLES_RETRIEVED` on success.
9. Build `scripts/ai/noise_floor_compare.py`; update/extend tests; update docs (§8).
10. Run scoped tests → full suite → noise-floor gate (baseline on HEAD, compare post-change).

## Rollback plan

No feature flag; revert is the commit(s). Because the runner retains
`_try_build_request_analysis_context` as the fallback and the estimator params default to
today's behavior, a **partial** revert is safe: reverting only the `controller.py` wiring
makes `request_analysis` always `None`, so the runner reverts to the two-wave path while the
estimator/prompt changes remain harmless (raw query, `self._top_k`, guidelines in system
prompt). Full revert restores the pre-change two-wave behavior exactly.
