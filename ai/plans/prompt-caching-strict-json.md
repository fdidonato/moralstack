# Plan — prompt-caching-strict-json (cost/latency reduction on deliberative LLM modules)

> **Revision 2 (post Codex BLOCK, 2026-07-06).** Scope narrowed after the Codex plan
> review (`ai/reviews/codex-plan-review-prompt-caching-strict-json-20260706-101754.md`,
> verdict BLOCK) and the author's decisions:
> - **This plan is now Part A ONLY** — prompt reordering for OpenAI prompt caching, keeping
>   `response_format={"type":"json_object"}` everywhere. **Part B (strict `json_schema` +
>   `message.refusal` plumbing) is DEFERRED to a separate follow-up plan.** Rationale: every
>   Codex blocking issue lived in Part B (schema/parser conflicts, `_complete` refusal channel,
>   hard-signal field coverage). Removing Part B makes `moralstack/models/policy.py` and all
>   `*_config_loader`/parse-contract tests **untouched**, collapsing the blast radius to prompt
>   text moves.
> - **Module inventory expanded** per Codex to cover the paths the first draft missed:
>   Simulator **seeded** mode, Hindsight **single-scenario** and **batch** paths, and Perspective
>   A5a must also move `risk_signals` (not just REQUEST/RESPONSE) out of the shared system prompt.
> - Section "Deferred to Part B" at the end records exactly what a future strict-JSON plan must
>   pick up (including the verified fact that a Pydantic strict layer already exists in
>   `moralstack/utils/structured_output.py`, so a future plan must derive schemas from it, not
>   hand-author them).
> The full Codex review and the composed request are archived under `ai/reviews/` and
> `ai/prompts/` for auditability.
>
> **Revision 3 (post second Codex BLOCK, 2026-07-06,
> `ai/reviews/codex-plan-review-prompt-caching-strict-json-20260706-105223.md`).** Three precision
> blockers, all verified against the code and fixed here:
> - **Simulator batch and seeded share one `SIMULATOR_SYSTEM_PROMPT`** (`simulator_module.py:431`
>   batch, `:550` seeded). A single merged constant would feed seeded calls the batch-only "exactly
>   N / num_scenarios" contract. Fix: **path-specific system prompts** `SIMULATOR_BATCH_SYSTEM_PROMPT`
>   and `SIMULATOR_SEEDED_SYSTEM_PROMPT` (A3 rewritten).
> - **Hindsight single and batch share one `HINDSIGHT_SYSTEM_PROMPT`** (`hindsight_module.py:573`
>   single; batch path `:720`). Single output is a root object; batch is rooted at `"evaluations"` —
>   different validators. Fix: **path-specific** `HINDSIGHT_SINGLE_SYSTEM_PROMPT` and
>   `HINDSIGHT_BATCH_SYSTEM_PROMPT` (A4 rewritten).
> - **Perspective A5a missed the public `evaluate_single`** (`perspective_module.py:816-818`), which
>   rebuilds `shared_system` via the same ctx-dependent builder. Fix: A5 now updates `evaluate`,
>   `_evaluate_single_perspective`, AND `evaluate_single`, threading the dynamic context explicitly.
> - **DCCL/retriever downgraded to VERIFY-ONLY** (no edits this iteration): the retriever hashes exact
>   message payloads into `_domain_agent_cache_key` (`retriever.py:169`), so a system/user split would
>   change internal cache identity — out of scope for a pure caching win (A6 rewritten).
> - `prompt_cache_key` reframed from "unnecessary" to an optional measured follow-up.
> Implementation-style note (answering Codex's question): simulator/hindsight use **separate named
> system-prompt constants per path**, not builders returning `(system,user)` tuples — smallest diff,
> and it keeps each path's static contract physically distinct.
>
> **Revision 4 (post third Codex BLOCK, 2026-07-06,
> `ai/reviews/codex-plan-review-prompt-caching-strict-json-20260706-110924.md`).** One safety-relevant
> blocker (same path-contract class, missed for the critic), plus two non-blocking path completions:
> - **Critic quick-check shares `CRITIC_SYSTEM_PROMPT` with full critique but has a DIFFERENT contract.**
>   Quick-check asks for `{"violated": ...}` and parses `data.get("violated", False)`
>   (`critic_module.py:658-709`, system passed at `:675`); full critique asks for
>   `decision`/`violated_hard`/`violations`/`revision_guidance` (`prompts/critic_prompt.py:70-86`).
>   Extending `CRITIC_SYSTEM_PROMPT` in place with the full schema would make quick-check emit the full
>   shape, omit `violated`, default to False, and PASS — a hard-signal violation could silently slip the
>   fast path (`deliberation_runner.py:968-972`). **Fix:** A2 now introduces `CRITIC_FULL_SYSTEM_PROMPT`
>   for the two full-critique call sites ONLY; the quick-check is **left byte-for-byte unchanged** (its
>   own short system prompt + `{"violated"}` contract) and is explicitly NOT caching-optimized in Part A
>   (its prompt is ~256 max_tokens, below the 1024-token cache threshold — no payoff, and touching it is
>   unsafe). This answers Codex's question: quick-check stays unchanged; only full critique gets the
>   larger static prefix.
> - **Hindsight has a THIRD LLM path** — `_evaluate_individual` (non-batch aggregate, used when batch is
>   disabled or there is a single consequence, `hindsight_module.py:672-682`, `:843-867`) — which stores
>   `system_prompt` on the aggregate `HindsightResult` read by the runner (`deliberation_runner.py:3131-3149`).
>   A4 now names it explicitly: it uses the single-scenario contract, so it takes `HINDSIGHT_SINGLE_SYSTEM_PROMPT`
>   and its result metadata must reflect the path-specific prompt actually sent.
> - **`evaluate_single` cannot receive risk through its public signature** (`perspective_module.py:816-818`
>   builds `DelibContext(user_prompt=request, draft_text_full=response)` only). Its `risk_score` DEFAULTS to
>   `0.5`, rendering `risk_score=0.50` (`models/delib_context.py:38`, `:65-70`). A5a MOVES that exact rendered
>   block system -> user WITHOUT changing it (behavior-preserving); the test asserts request/response and
>   `risk_score=0.50` appear in its user message. (Answers Codex Q1: preserve `risk_score=0.50`, do NOT switch
>   to "none".)
>
> **Revision 5 (post fourth Codex review — APPROVE_WITH_CHANGES, 2026-07-06,
> `ai/reviews/codex-plan-review-prompt-caching-strict-json-20260706-112208.md`).** No blockers. Folded the
> non-blocking precision fixes: (a) DCCL is the INJECTED policy with a `GenerationConfig`, not a "direct SDK"
> call (wording corrected); (b) `evaluate_single` default risk renders `risk_score=0.50` — A5a preserves it
> (Codex Q1); (c) the critic quick-check unchanged-test now snapshots BOTH the `system` AND the `prompt`
> passed to `policy.generate` (the `{"violated"}` contract lives in the USER template `QUICK_CHECK_PROMPT_TEMPLATE`,
> not the system) and pins them as a literal snapshot (Codex Q2 — literal snapshot chosen); (d) content-preservation
> is explicitly NOT positional-equivalence — the reorder intentionally changes where dynamic context sits
> relative to the (now-static) steps, so behavioral equivalence is proven by the structured-output integration
> tests (merged RiskEstimation / final_action unchanged), not by positional identity. **Plan APPROVED — ready for
> `/ai-implement`.**

## Goal
Cut input-token cost and latency of every deliberative/governance LLM module by moving each
module's static content into a stable, byte-identical cacheable prefix (the system message),
leaving only per-request dynamic data in the user message — so OpenAI automatic prompt caching
engages on the static prefix. No change to governance behavior, JSON contracts, retries, or any
PROJECT_SPEC section 5 invariant. (JSON tightening is explicitly out of scope this iteration.)

## Web facts that ground the design (OpenAI, July 2026)
- Prompt caching is automatic, no code change, no extra fee; up to ~90% input-token cost and
  ~80% latency reduction ON THE CACHED PREFIX.
- Engages only for prompts >= 1024 tokens; cache hits in 128-token increments; ONLY exact-prefix
  matches. Static content (instructions, examples, schema skeleton) must be FIRST; dynamic
  (request, draft, retrieved principles, risk signals, seeds) must be LAST.
- The cacheable prefix is per-exact-prefix AND per-model. A per-request model override for a
  module defeats caching for that module.
- Keeping `response_format={"type":"json_object"}` is fine — it does not participate as a
  differentiator here and the JSON-mode contract is unchanged.

## Current behavior (verified this session)
All deliberative modules call OpenAI with `response_format={"type":"json_object"}` and parse with
tolerant/fuzzy JSON extraction inside a retry loop. The recurring anti-cache pattern is: the
**dynamic** field (`{request}`/`{response}`/`{seed}`) appears at the TOP of the user message and
the large **static** procedure/enums/schema-skeleton appears at the BOTTOM, so the cacheable
prefix ends at the (often short) system message and the big static block is never cached.

- **Risk minis:** `GenerationConfig` in `estimator.py:437-451` (rf `:447`); parallel calls in
  `_call_and_track` retry loop `estimator.py:800-855` (`generate_messages`/`generate` at
  `:804`/`:814`); static system prompts `models/risk/prompts.py:26-124` (intent), `:359-413`
  (signals), `:454-516` (operational). USER templates put dynamic `{request}` FIRST then the large
  static STEP/schema blocks: `prompts.py:126-352` (intent), `:415-447` (signals), `:518-677`
  (operational).
- **Critic:** gen config `critic_module.py:351-357` (rf `:356`), quick-check `:665-670` (rf
  `:669`); retry loop `:437-465`. System `CRITIC_SYSTEM_PROMPT` `critic_module.py:157-171`; user
  prompt `build_critic_prompt` puts static rules+schema first then dynamic fields
  (`prompts/critic_prompt.py:90-155`) — but all inside the USER message.
- **Simulator:** short `SIMULATOR_SYSTEM_PROMPT` `simulator_module.py:155-157`; **two** LLM paths:
  (i) batch via `build_simulator_prompt` (`prompts/simulator_prompt.py:99-186`), `SIMULATOR_FULL_STATIC_PREFIX`
  static-first but in the USER message; (ii) **seeded** mode `use_seeded_generation=True` uses the
  in-module `SEEDED_PROMPT_TEMPLATE` (`simulator_module.py:167-188`) — dynamic `{seed}/{request}/{response}`
  FIRST then static `SIMULATOR_ENUMS`/`SIMULATOR_SCHEMA_SKELETON`, with its own retry loop
  (`simulator_module.py:527-556`, verified via Codex citation). gen config `:308-314`.
- **Perspective:** gen config `perspective_module.py:454-460` (rf `:459`). `shared_system =
  PERSPECTIVE_SYSTEM_PROMPT + build_perspectives_system_prompt(ctx)` (`perspective_module.py:536`)
  — the shared "system" is **ctx-dependent**: it interpolates dynamic REQUEST/RESPONSE **and
  `risk_signals`/risk context** (`prompts/perspectives_prompt.py:78-98`), so there is no stable
  system prefix across requests (OPT-2, cache-defeating by design). Parsing requires only
  `approval_score` and defaults `concerns`/`suggestions`/`rationale` (`perspective_module.py:283-346`).
- **Hindsight:** short `HINDSIGHT_SYSTEM_PROMPT` `hindsight_module.py:309-313`; **two** paths:
  (i) single-scenario uses the in-module `HINDSIGHT_PROMPT_TEMPLATE` (`hindsight_module.py:315-355`)
  — dynamic REQUEST/RESPONSE/CONSEQUENCE FIRST then static rubric+schema; (ii) **batch** uses
  `generate_messages` (`hindsight_module.py:310-352`, `:734-745`, `:824-845`, per Codex). gen
  config `:524-530` (rf `:529`). (Note: the first draft wrongly said Hindsight has no
  `generate_messages` branch — corrected here.)
- **Retriever:** shared `_JSON_OBJECT_RESPONSE_FORMAT` `constitution/retriever.py:41`; direct SDK
  `.create()` at `:594/:601`, `:852/:856`, `:1028/:1034` with their own persistence paths.
- **DCCL:** DOES set `response_format={"type":"json_object"}` (`compliance/dccl.py:472-477`) and calls the
  INJECTED policy via `generate_messages`/`generate` with a `GenerationConfig` (`:468-493`) — NOT a direct
  OpenAI SDK client call — then parses with fuzzy `extract_json` (`:544`). (Corrects the cartographer map,
  which said it did not set `response_format`.)
- **Policy generator:** `_complete` passes `response_format` straight through
  (`models/policy.py:233-244`); `refuse`/`rewrite`/`generate`/`generate_messages` emit delivered
  text — **fully out of scope; untouched by this plan.**

Shared plumbing: `GenerationConfig.response_format: Any = None` (`models/base.py:24`);
`build_module_messages` orders system -> developer -> history -> user, appends retry_prompt to the
user message (`runtime/modules/message_context.py:19-39`); risk has its own `_risk_context_messages`
(`estimator.py:233-258`).

## Target behavior
Per module and per LLM path (batch AND seeded/single), the request is shaped so:
- The entire static portion (task instructions, rules, examples, allowed enums, JSON
  schema/skeleton, output-JSON-only preamble) sits at the FRONT as a byte-identical prefix across
  requests for the same module+model — i.e. in the system message.
- The user message carries ONLY per-request dynamic data (request text, draft/response, retrieved
  principles, risk signals, seed, consequence, per-perspective identity).
- `response_format`, retry loops, parsers, and all JSON contracts are UNCHANGED (`json_object`
  everywhere; no strict schema this iteration).
Governance outputs, decision routing, and delivered/refusal text are unchanged.

## Assumptions (each verifiable)
1. Byte-equality invariants #2/#4 are enforced only on the DELIVERED-ANSWER policy generator via
   `effective_system_for_request`/`POLICY_SYSTEM_PROMPT` (`tests/test_system_prompt_byte_equality.py:36-100`).
   No test pins byte-equality of any deliberative module prompt. Reordering deliberative prompts
   does NOT touch #4, and this plan does not touch `policy.py` at all. VERIFY by re-reading the test.
2. Downstream governance reads only the JSON fields already emitted; since `json_object` and the
   parsers are unchanged, no field can be dropped by this plan (that risk belonged to Part B).
3. Deliberative modules use a fixed per-module model in normal operation; risk minis may use
   dedicated models (`estimator.py:772-797`). Cache is per-exact-prefix AND per-model, so a
   per-request model override defeats caching for that mini. VERIFY the model slots are stable.
4. Static prefixes on the big modules (risk minis, critic, simulator batch/seeded) are large enough
   (>= 1024 tokens) for caching to engage; small modules (DCCL draft-match, retriever prefilter) may
   fall below the threshold and gain nothing from the reorder — acceptable (no-op there).

## Constraints (invariants section 5)
- **#1 decision/generation separation:** unaffected — no schema change, `final_action` still from
  structured signals; JSON contracts identical.
- **#2/#4 prompt transparency + single-turn byte-equality:** unaffected — `POLICY_SYSTEM_PROMPT`/
  `effective_system_for_request` and `policy.refuse/rewrite/generate` are untouched. This plan does
  not import or edit `policy.py`.
- **#3 hard-signal supremacy:** the risk/critic/operational prompts encode hard-signal safety
  overrides (`prompts.py:97-109`, `:502-507`; critic shared rules). Reordering must move these
  blocks **VERBATIM** into the system prefix — no rewording, no dropping. A content-preservation
  test guards this.
- **#6 observability best-effort:** persisted `system_prompt`/`prompt`/`message_sections`
  (`estimator.py:880-927`, DCCL `dccl.py:496-542`, and the simulator/hindsight/perspective emit
  sites) must reflect the new system<->user split so the audit trail still shows the full prompt.
- **Section 6 minimal change:** move text between existing constants/templates; do not reword prompt
  wording, do not restructure module control flow. `response_format` values stay `json_object`.
- **Compatibility:** keep `generate`/`generate_messages` signatures; batch, seeded, single, legacy,
  and quick-check branches must all stay behavior-equivalent.

## Proposed design — prompt reorder (static prefix / dynamic suffix)

General rule per module and per path: the system message holds 100% static text; the user message
holds 100% per-request text. Message order stays system -> (developer) -> (history) -> user. The
conditional `_CONTEXT_REFERENCE_INSTRUCTION` (`message_context.py:36-37`) stays at the top of the
user message (dynamic-presence) — do NOT move it into system. Developer contract stays in the
developer slot, conversation history in its slot (`message_context.py:24-39`) — never merged into
the module system prompt.

### A1. Risk minis (`models/risk/prompts.py`, `models/risk/signals/prompt_renderer.py`, `estimator.py`) — highest payoff
Relocate the static procedure/examples/output-schema blocks from each `*_PROMPT_TEMPLATE` into the
corresponding `*_SYSTEM_PROMPT`, leaving each user template as only dynamic fields.
- Intent: move STEP 0-3 + field reminders + output schema (`prompts.py:130-352`, everything except
  the `REQUEST:` line and the per-request `{constitution_context}`) into `INTENT_CONTEXT_SYSTEM_PROMPT`.
  User template becomes `REQUEST` + `{constitution_context}` (retrieved principles — dynamic/last).
- Signals: classify `{evaluation_order_section}`, `{signal_definitions_section}`,
  `{domain_sensitivity_section}`, `{coherence_rules_section}`, `{output_schema_section}`
  (`prompts.py:415-447`) as static vs per-request in `signals/prompt_renderer.py` before moving.
  Static -> system; anything per-request (e.g. domain-sensitivity dependent on detected domain) ->
  user suffix.
- Operational: move STEP 1-4 + output schema (`prompts.py:522-677`) into `OPERATIONAL_RISK_SYSTEM_PROMPT`;
  user template becomes `REQUEST` only.
Skeleton (intent, representative): BEFORE `system:[invariants]` / `user:REQUEST{req}+STEP0..3+schema`
-> AFTER `system:[invariants]+STEP0..3+schema` / `user:REQUEST{req}+{constitution_context}`.
Update the observability persistence (`estimator.py:888-924`) so `system_prompt`/`prompt` reflect
the split.

### A2. Critic — FULL critique only; quick-check UNCHANGED (`prompts/critic_prompt.py`, `critic_module.py`)
The full critique and the quick-check share `CRITIC_SYSTEM_PROMPT` today but have DIFFERENT JSON
contracts (full: `decision`/`violated_hard`/`violations`/`revision_guidance`; quick:
`{"violated": ...}`, parsed with `data.get("violated", False)` at `critic_module.py:680`). **Do NOT
extend `CRITIC_SYSTEM_PROMPT` in place** — that would poison the quick-check contract and could let a
hard violation silently pass the fast path (safety-relevant, section 5 #3).
- Introduce `CRITIC_FULL_SYSTEM_PROMPT` = the current critic system text + `CRITIC_SHARED_RULES` +
  `OUTPUT_JSON_ONLY` + the full Output-schema block moved out of `CRITIC_FULL_TEMPLATE`.
  `build_critic_prompt` then returns only the dynamic TASK/PRINCIPLES/TURN CONTEXT/REQUEST/RESPONSE/
  previous_guidance suffix. Use `CRITIC_FULL_SYSTEM_PROMPT` at the two FULL-critique call sites:
  `generate_messages` branch (`critic_module.py:444-453`) and legacy `generate` branch (`:455-465`).
  `context_block` is per-request -> stays user.
- **Quick-check (`critic_module.py:658-709`) is left BYTE-FOR-BYTE UNCHANGED**: it keeps its own short
  system prompt and `{"violated"}` contract. It is intentionally NOT caching-optimized in Part A (its
  prompt is tiny — `max_tokens=256` — and below the 1024-token cache threshold, so a reorder yields no
  cache benefit and only risks the contract). If a future measured case justifies it, give it a dedicated
  `CRITIC_QUICK_SYSTEM_PROMPT` carrying the `{"violated"}` contract statically — separate change.
  NOTE: if the quick-check currently references the same `CRITIC_SYSTEM_PROMPT` symbol that full critique
  used, keep that symbol pointing at the UNCHANGED short text and give full critique the NEW
  `CRITIC_FULL_SYSTEM_PROMPT`, so quick-check's sent bytes do not move.

### A3. Simulator — TWO PATHS, TWO PATH-SPECIFIC SYSTEM PROMPTS (`prompts/simulator_prompt.py`, `simulator_module.py`)
Both paths currently pass the SAME short `SIMULATOR_SYSTEM_PROMPT` (`simulator_module.py:431` batch,
`:550` seeded). Their static contracts DIFFER: batch requires "exactly N" consequences and carries
`num_scenarios` in the user prompt (`simulator_prompt.py:105`, `:164`); seeded generates ONE consequence
from a seed. **Do NOT merge both into one constant.** Introduce two path-specific system-prompt constants:
- `SIMULATOR_BATCH_SYSTEM_PROMPT`: gains `SIMULATOR_FULL_STATIC_PREFIX` (`simulator_prompt.py:99-162`) —
  the batch rubric (incl. "exactly N"), enums, schema skeleton. `build_simulator_prompt` (batch user
  template) keeps only TURN PARAMETERS/REQUEST/RESPONSE/RISK CONTEXT/DOMAIN/`num_scenarios`/`{domain_guidance}`.
  The batch call (`:431`) uses `SIMULATOR_BATCH_SYSTEM_PROMPT`.
- `SIMULATOR_SEEDED_SYSTEM_PROMPT`: gains the seeded static rubric ("Requirements/JSON only") +
  `SIMULATOR_ENUMS` + `SIMULATOR_SCHEMA_SKELETON` (currently trailing inside `SEEDED_PROMPT_TEMPLATE`,
  `simulator_module.py:167-188`). `SEEDED_PROMPT_TEMPLATE` becomes dynamic-only: `PERSPECTIVE:{seed}` +
  `REQUEST` + `RESPONSE`. The seeded call (`:550`) uses `SIMULATOR_SEEDED_SYSTEM_PROMPT`. Preserve the
  seeded retry loop (`:527-556`) behavior unchanged.
This guarantees a seeded call never receives batch-only "exactly N"/`num_scenarios` instructions and
each path's prefix is byte-stable independently.

### A4. Hindsight — TWO PATHS, TWO PATH-SPECIFIC SYSTEM PROMPTS (`hindsight_module.py`, `prompts/hindsight_prompt.py`)
Both paths currently pass the SAME `HINDSIGHT_SYSTEM_PROMPT` (`hindsight_module.py:573` single;
`:720` batch via `build_hindsight_prompt`). Their JSON ROOTS DIFFER: single output is a root object
(`safety`/`helpfulness`/`honesty`/probabilities, `hindsight_module.py:344`) parsed by one validator
(`:387`); batch output is rooted at `"evaluations"` (`prompts/hindsight_prompt.py:23`) parsed by another
(`:441`). **Do NOT merge their schemas into one system prompt.** Introduce two path-specific constants:
- `HINDSIGHT_SINGLE_SYSTEM_PROMPT`: gains the single-scenario 3-dimension rubric + the ROOT-OBJECT JSON
  skeleton from `HINDSIGHT_PROMPT_TEMPLATE` (`hindsight_module.py:315-355`). That template becomes
  dynamic-only: REQUEST/RESPONSE/CONSEQUENCE. Single call (`:573`) uses it.
- `HINDSIGHT_BATCH_SYSTEM_PROMPT`: gains the batch rubric + the `"evaluations"`-rooted skeleton +
  any `DEVELOPER_CONTRACT_EVALUATION` block. `build_hindsight_prompt` (`:720`) keeps only the per-request
  consequences list + turn context. Batch call uses it.
- **Non-batch aggregate path `_evaluate_individual`** (used when batch is disabled or there is a single
  consequence, `hindsight_module.py:672-682`, `:843-867`): it uses the single-scenario contract, so it
  takes `HINDSIGHT_SINGLE_SYSTEM_PROMPT`. Its aggregate `HindsightResult.system_prompt` (read by the runner
  at `deliberation_runner.py:3131-3149`) must be set to the path-specific prompt actually sent, not the old
  shared constant.
This guarantees the single/individual path never receives the batch `"evaluations"` schema and vice-versa,
and each path's prefix is byte-stable independently. (Hindsight therefore has THREE LLM entry points:
single-scenario `evaluate_scenario`, batch `generate_messages`, and non-batch aggregate `_evaluate_individual`
— the first and third share `HINDSIGHT_SINGLE_SYSTEM_PROMPT`.)

### A5. Perspective — A5a cache-first, INCLUDING risk_signals (author-approved)
Today OPT-2 puts dynamic REQUEST/RESPONSE **and `risk_signals`/risk context** inside the ctx-dependent
`shared_system` (`perspective_module.py:536`, body `prompts/perspectives_prompt.py:78-98`), so there is
no stable prefix. **A5a:** move REQUEST/RESPONSE **and the risk context/`risk_signals`** out of the shared
system into each per-perspective USER message; keep only the static interpretation guidance + JSON
skeleton in the system prompt. Effect: within one round the static system prefix is identical across the
N perspective calls -> 2nd..Nth cache-hit; across requests the prefix is stable. Cost: the draft +
risk context are re-sent N times (undoes the OPT-2 single-send saving, but cached input is ~90% cheaper).
Because parsing only requires `approval_score` (`perspective_module.py:283-346`), no parser change is
needed. `build_perspectives_system_prompt` must become ctx-INDEPENDENT (static only); a new per-perspective
user-message builder carries the dynamic block.

**All THREE call paths must be updated (Codex-flagged, verified):** the dynamic-context propagation must
be threaded explicitly, not just moved between builders:
- `evaluate` (multi-perspective, `perspective_module.py:536`) — builds `shared_system` per round.
- `_evaluate_single_perspective` — the shared worker both paths call; it must receive the dynamic
  request/response/risk block for the user message (today it only gets `shared_system`).
- `evaluate_single` (public API, `perspective_module.py:816-818`) — currently rebuilds `shared_system =
  PERSPECTIVE_SYSTEM_PROMPT + build_perspectives_system_prompt(ctx)` with the SAME ctx-dependent builder;
  it must construct the static system + a dynamic user message the same way `evaluate` does, or the
  request/response/risk context would vanish once the builder goes static. `evaluate_single` is a
  supported public method (exercised by `tests/test_perspective_module.py:387`,
  `tests/test_perspective_standalone.py:343`) and MUST retain full request/response/risk context.

### A6. Retriever (`constitution/retriever.py`) and DCCL (`compliance/dccl.py`) — VERIFY-ONLY, NO EDITS this iteration
Downgraded from "opportunistic" to verify-only after Codex flagged real blast radius:
- The retriever hashes the EXACT message payloads into `_domain_agent_cache_key` (`retriever.py:169`),
  so any system/user split would change internal cache identity (and invalidate the domain-agent cache)
  for a payoff that is likely below the 1024-token threshold. Not worth it in a pure-caching iteration.
- DCCL already has a static system prompt with dynamic later messages (`dccl.py:481`, `:568`) — it is
  already broadly compatible with the static-prefix pattern.
Action: **audit only** — confirm (with a test) that DCCL's and the retriever's system prompts are already
static and their dynamic data already lives in later messages; make NO code change. If a future measured
case shows they clear the threshold, handle them in a separate change with the cache-key impact addressed.

## Alternatives considered
- **Also doing strict `json_schema` now (former Part B):** rejected FOR THIS ITERATION per author
  decision — it carries all the governance risk (schema/parser reconciliation, hard-signal field
  coverage, `message.refusal` plumbing through the shared `_complete`). Deferred to a dedicated plan
  (see "Deferred to Part B").
- Rewriting/condensing prompts to shrink tokens — rejected: violates section 6 and risks section 5 #3
  hard-signal wording; the ask is reorder, not rewrite.
- Moving static text into a developer message — rejected: the developer slot is reserved for the
  deployer contract (`message_context.py:28-29`).
- Perspective A5b (keep OPT-2, no cache) — rejected by the author in favor of A5a.
- Manual `prompt_cache_key` — NOT required for automatic caching (which needs only prefix stability),
  but OpenAI guidance recommends passing a stable per-module `prompt_cache_key` to improve cache-routing
  hit-rate for shared prefixes. Treated here as an **optional measured follow-up**, not a goal of this
  iteration: land the reorder first, then measure `cached_tokens`/latency and decide whether a per-module
  cache key adds measurable lift. (Not a blocker; recorded so the performance case isn't overstated.)

## Files to modify
- `moralstack/models/risk/prompts.py` — move static STEP/schema blocks from the three `*_PROMPT_TEMPLATE`
  into the three `*_SYSTEM_PROMPT`.
- `moralstack/models/risk/signals/prompt_renderer.py` — classify signal sections static vs per-request;
  route static into system.
- `moralstack/models/risk/estimator.py` — observability persistence strings (`:888-924`). (No
  `response_format` change; retry loop unchanged.)
- `moralstack/runtime/modules/critic_module.py` — introduce `CRITIC_FULL_SYSTEM_PROMPT`; use it at the two
  FULL-critique call sites (`:444-453`, `:455-465`); leave the quick-check (`:658-709`) and its short system
  prompt UNCHANGED. (gen configs unchanged.)
- `moralstack/prompts/critic_prompt.py` — split static rules/schema out of `CRITIC_FULL_TEMPLATE` into
  `CRITIC_FULL_SYSTEM_PROMPT`.
- `moralstack/runtime/modules/simulator_module.py` — introduce `SIMULATOR_BATCH_SYSTEM_PROMPT` +
  `SIMULATOR_SEEDED_SYSTEM_PROMPT`; batch call (`:431`) and seeded call (`:550`) each pass its own
  constant; `SEEDED_PROMPT_TEMPLATE` (`:167-188`) becomes dynamic-only; preserve seeded retry loop
  (`:527-556`).
- `moralstack/prompts/simulator_prompt.py` — move `SIMULATOR_FULL_STATIC_PREFIX` into
  `SIMULATOR_BATCH_SYSTEM_PROMPT`; batch user template dynamic-only.
- `moralstack/runtime/modules/hindsight_module.py` — introduce `HINDSIGHT_SINGLE_SYSTEM_PROMPT` +
  `HINDSIGHT_BATCH_SYSTEM_PROMPT`; single call (`:573`) uses the single constant with the root-object
  skeleton; batch call (`:720`) uses the batch constant with the `"evaluations"` skeleton;
  `HINDSIGHT_PROMPT_TEMPLATE` (`:315-355`) becomes dynamic-only.
- `moralstack/prompts/hindsight_prompt.py` — move the batch static block into `HINDSIGHT_BATCH_SYSTEM_PROMPT`;
  `build_hindsight_prompt` dynamic-only.
- `moralstack/runtime/modules/perspective_module.py` — `shared_system` (`:536`) becomes ctx-independent;
  update `evaluate`, `_evaluate_single_perspective`, AND `evaluate_single` (`:816-818`) to thread the
  dynamic REQUEST/RESPONSE/risk block into the per-perspective user message.
- `moralstack/prompts/perspectives_prompt.py` — `build_perspectives_system_prompt` static-only; move
  REQUEST/RESPONSE/`risk_signals` (`:77-98`) into a new per-perspective user-message builder.
- `moralstack/constitution/retriever.py` — VERIFY-ONLY, no edit (cache-key coupling at `:169`).
- `moralstack/compliance/dccl.py` — VERIFY-ONLY, no edit (already static system + dynamic later messages).

_No new schema constants. No change to `moralstack/models/policy.py`, `moralstack/models/base.py`,
`moralstack/utils/structured_output.py`, `moralstack/utils/llm_parse_contract.py`, or any
`response_format`/retry/parse-contract test._

## Tests to add / modify

### Existing coverage — what each lock becomes (this iteration)
- `tests/test_system_prompt_byte_equality.py:36-100` — locks the delivered-answer generator only;
  never imports a deliberative module. Stays green UNCHANGED; run it as a named gate.
- `tests/test_llm_parse_contract.py:82-86` (risk `response_format == {"type":"json_object"}`) — STAYS
  GREEN UNCHANGED: this plan keeps `json_object`. (The former Part B would have changed it; it no longer
  does.)
- `*_config_loader` retry-default tests (`test_risk_config_loader.py:142`, `test_critic_config_loader.py:137-138`,
  `test_perspective_config_loader.py:142`, `test_hindsight_config_loader.py:147`, simulator equivalent) —
  UNCHANGED: retries are not retired this iteration.
- `tests/test_runtime_modules_retry_token_accounting.py:73-203` and `tests/test_perspective_module.py:544-580`
  — UNCHANGED: the malformed-JSON-then-retry paths still exist (we keep `json_object`). Re-run to prove the
  reorder didn't alter retry/token accounting.
- `tests/test_critic_prompt.py:39-53` (`test_build_critic_prompt_includes_enumerated_option_only_guard`) —
  WILL BREAK when A2 moves `CRITIC_SHARED_RULES` into `CRITIC_SYSTEM_PROMPT`. Rewrite (not delete) into two
  positive assertions: the guard text now lives verbatim in `CRITIC_SYSTEM_PROMPT`, AND is absent from
  `build_critic_prompt`'s output (proves the move; guards against duplication re-bloating user tokens).
  `test_build_critic_prompt_full_includes_risk_assessment` (`:11-36`) asserts dynamic fields that stay
  user-side — verify still green.
- `tests/test_prompt_audit_fixes.py:53-68` (`TestPerspectivesFullModeRiskContext`) — asserts
  `build_perspectives_system_prompt` contains dynamic `risk_score`/`risk_category`/`intent_to_harm`. Under
  A5a this WILL BREAK and must be rewritten to assert that data now lives in the per-perspective USER
  message and that the shared system string no longer varies with `ctx.risk_score`. `TestSimulatorDomainGuidance`
  (`:76-95`) stays user-side under A3 — verify.
- `tests/test_perspective_contract_injection.py:7-79` — locks contract/history in developer/user slots,
  never system. Must stay green after A5a (re-run explicitly).

### New unit tests — static-prefix stability (the whole point)
For EACH module AND EACH path, capture the system message via a capturing fake policy (reuse `_GenResult`
`tests/test_runtime_modules_retry_token_accounting.py:22-32`; `MockPolicyLLM` `tests/test_perspective_module.py:62-105`;
`fake_gen` `tests/test_llm_parse_contract.py:134-140`) across two requests whose EVERY dynamic field differs,
and assert the captured system message is BYTE-IDENTICAL between calls AND equals the module constant verbatim:
- Risk: `INTENT_CONTEXT_SYSTEM_PROMPT` / `HARM_SIGNAL_SYSTEM_PROMPT` (signals) / `OPERATIONAL_RISK_SYSTEM_PROMPT`.
- Critic — PATH-SPECIFIC: the two FULL-critique call sites (generate_messages, legacy) capture
  `CRITIC_FULL_SYSTEM_PROMPT`. **Quick-check must be UNCHANGED**: capture BOTH the `system` AND the `prompt`
  passed to `policy.generate` (`critic_module.py:673-677`) and pin them as a LITERAL snapshot equal to today's
  bytes. Assert the captured `system` does NOT contain the full `decision`/`violations` schema, and that the
  captured USER prompt still carries the `{"violated": ...}` contract (it lives in `QUICK_CHECK_PROMPT_TEMPLATE`,
  `critic_module.py:175-190` / `:658-662`, NOT in the system prompt). Plus the safety regression guard: a
  quick-check response `{"violated": true, ...}` still routes to `QuickCheckResult(passed=False)`
  (`critic_module.py:684-704`).
- Simulator — PATH-SPECIFIC: capture batch and seeded SEPARATELY. Batch system == `SIMULATOR_BATCH_SYSTEM_PROMPT`;
  seeded system == `SIMULATOR_SEEDED_SYSTEM_PROMPT`. NEGATIVE assertions: the seeded system prompt does NOT
  contain the batch-only "exactly N"/`num_scenarios` text, and the seeded USER prompt still contains the
  selected `PERSPECTIVE:{seed}` + REQUEST + RESPONSE (`simulator_module.py:167`, `:531`). Also assert the
  batch and seeded system constants are NOT equal.
- Hindsight — PATH-SPECIFIC: capture single and batch SEPARATELY. Single system == `HINDSIGHT_SINGLE_SYSTEM_PROMPT`
  and does NOT contain the batch `"evaluations"` root schema; batch system == `HINDSIGHT_BATCH_SYSTEM_PROMPT`
  and does NOT contain the single root-object schema (`hindsight_module.py:344`, `prompts/hindsight_prompt.py:23`).
  Assert the two constants are NOT equal.
- Perspective (A5a): the composed static system string is unaffected by `ctx.user_prompt`,
  `ctx.draft_text_full`, AND `ctx.risk_score`/risk signals across two requests — assert this for BOTH the
  multi-perspective `evaluate` path AND the public `evaluate_single` path, and assert each per-perspective
  USER message still contains request/response (proving A5a moved the data, not deleted it). For
  `evaluate_single` specifically: its public signature carries no risk parameter (`perspective_module.py:816-818`
  builds `DelibContext(user_prompt=request, draft_text_full=response)`), and `DelibContext.risk_score` DEFAULTS
  to `0.5` so `get_risk_signals_str()` renders `risk_score=0.50` (`models/delib_context.py:38`, `:65-70`).
  BEHAVIOR-PRESERVING requirement: A5a only MOVES this rendered risk block (system -> user), it must NOT change
  the string. Assert `evaluate_single`'s per-perspective user message contains request/response AND
  `risk_score=0.50` (the current default rendering) — i.e. the exact bytes that appear in today's system
  prompt now appear in the user message.
Each catches a future edit re-introducing a dynamic field into a system template (cache poisoning) or a
cross-path schema collision.

### New unit tests — content-preservation (golden substring diff)
For each module/path, pin — as LITERAL strings captured once from the pre-change files, not a re-import of
the constant — every STEP heading, every enum line, the JSON schema skeleton, every hard-signal safety-override
sentence, the `OUTPUT_JSON_ONLY` preamble; assert each is present in `concat(system, user)` post-refactor for a
fixed representative context. Explicit hard-signal assertions: `"SAFETY OVERRIDE — CONTRACTS NEVER AUTHORIZE
THESE OUTPUTS"` in the intent system prompt; the weapons/CBRN/CSAM/self-harm ceiling sentence in the operational
system prompt; the critic HARD-violation rule text (section 5 #3, section 6 verbatim-move).
NOTE (scope of this test, per Codex): content-preservation asserts PRESENCE of every static rule + dynamic
field, NOT positional identity. Part A intentionally changes WHERE dynamic context sits relative to the
now-static steps (e.g. intent `constitution_context` currently precedes the Step 3 coherence check,
`models/risk/prompts.py:265-268`; after the move it sits in the user suffix). Behavioral equivalence is
therefore proven by the STRUCTURED-OUTPUT integration tests below (merged `RiskEstimation` / `final_action` /
violation/consequence content unchanged for a fixed input), not by positional identity of the prompt text.

### New integration tests
- Risk end-to-end: drive `estimate()`/`_parallel_mini_analysis` with fixed valid payloads for all three minis
  across two different requests; assert the merged `RiskEstimation` is identical to pre-refactor and all three
  system prompts are the module constants regardless of request.
- Full deliberation cycle: reuse an existing multi-module harness (patterns in
  `tests/test_deliberation_runner_billable_provider_call.py` / `test_controller_conversational.py`), run one
  cycle with all LLM calls mocked; assert `final_action` + violation/consequence content unchanged.
- Multi-turn native-message shape: repeat the critic/perspective native-message tests
  (`test_perspective_contract_injection.py:41-79` pattern) with `developer_contract` AND
  `conversation_history` present; assert `messages[0]` is still the pure static prefix, `messages[1]` the
  contract verbatim, and `_CONTEXT_REFERENCE_INSTRUCTION` (`message_context.py:10-16`) still prepends the user
  content — `build_module_messages` ordering is out of scope for the reorder.
- Observability split: for risk assert the persisted `system_prompt` (`estimator.py:888,901,914`) now carries
  the moved STEP text and `prompt` does not duplicate it; extend equivalently for the deliberation-runner
  persistence of `prompt`/`system_prompt` from critic, simulator, hindsight, and perspectives
  (`orchestration/deliberation_runner.py:2904`, `:3017`, `:3131`, `:3239`) — assert each persisted
  `system_prompt` reflects the path-specific static prefix actually sent and `prompt` is dynamic-only
  (section 5 #6). Include a hindsight NON-BATCH aggregate case (`len(consequences) == 1` and
  `use_batch_evaluation=False`) asserting the returned `HindsightResult.system_prompt`
  (`deliberation_runner.py:3131-3149`) is the single-path `HINDSIGHT_SINGLE_SYSTEM_PROMPT` actually sent,
  not the old shared constant.

### Edge cases
- Empty dynamic fields (request `""`, draft `""`, no `constitution_context`, no `risk_signals`, empty
  consequence): assert the user message is well-formed (no dangling `"REQUEST:\n"` placeholders) and the
  system prompt is unaffected.
- Multi-turn vs single-turn: run the static-prefix-stability test both with and without
  `developer_contract`+`history`; assert the system message is identical in both — contract/history only ever
  in the developer slot / `_CONTEXT_REFERENCE_INSTRUCTION`-prefixed user, never merged into system.
- Per-request model override: assert `estimator.py:772-797` still selects intent/signals/operational models
  correctly post-refactor (locks the selection logic the caching win depends on; OpenAI-side caching is
  offline-untestable).
- Seeded simulator with each of the 5 `SCENARIO_SEEDS` (`simulator_module.py:159-165`): assert the system
  prefix is identical across all seeds and only the user seed differs.

### Fixtures / mocks
- Reuse the existing module-boundary doubles (do not mock the OpenAI SDK directly): `_GenResult`,
  `MockPolicyLLM`/`FailingMockPolicyLLM`, `fake_gen`. Factor a small `_CapturingPolicy` helper shared across
  the static-prefix-stability tests (check `tests/conftest.py` before adding a new file).
- Stay offline: monkeypatch `_obs_route_batch`/`async_persist_llm_call` as `test_llm_parse_contract.py:151`
  and `test_runtime_modules_retry_token_accounting.py:53-60` already do. Keep `EnsembleConfig.enable_caching=False`
  (its default, `perspective_module.py:401`) so static-prefix tests aren't order-dependent.

### Commands
Venv interpreter, scoped in checklist order, then full suite + quality gate:
```
.\venv\Scripts\python.exe -m pytest tests/test_llm_parse_contract.py -v
.\venv\Scripts\python.exe -m pytest tests/test_critic_prompt.py tests/test_prompt_audit_fixes.py tests/test_perspective_contract_injection.py -v
.\venv\Scripts\python.exe -m pytest tests/test_runtime_modules_retry_token_accounting.py tests/test_perspective_module.py -v
.\venv\Scripts\python.exe -m pytest tests/test_system_prompt_byte_equality.py -v
.\venv\Scripts\python.exe -m pytest tests/test_static_prefix_stability.py -v
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\python.exe -m pre_commit run -a
```
Per memory `precommit-head-drift`: if `pre-commit run -a` churns unrelated files, scope it with `--files`.

## Risks
- **Cache defeat by leakage:** any per-request token left in a system prefix kills caching. Mitigation:
  static-prefix-stability tests per module/path; special attention to Perspective A5a (must remove
  `risk_signals` too, not just REQUEST/RESPONSE) and the seeded/single in-module templates.
- **Section 5 #3 hard-signal wording loss during the move:** mitigate by moving safety-override blocks
  verbatim; content-preservation test diffs the concatenated (system+user) text against pinned literals.
- **Model drift:** per-request model overrides (`estimator.py:772-797`) -> per-model cache miss. Document/pin
  model slots; no code change but note in module docs.
- **Observability drift:** persisted `system_prompt`/`prompt` must reflect the new split or the audit trail
  misrepresents what was sent (section 5 #6). Update the persistence payloads in the same change and test them.
- **DCCL/retriever (verify-only):** the retriever hashes exact message payloads into its cache key
  (`retriever.py:169`); DCCL calls the injected policy with a `GenerationConfig` (not a raw SDK call). Both
  may be below the cache threshold. Do NOT edit them — audit only, and do not force a split that yields no
  cache benefit and would perturb the retriever cache identity.

## Acceptance criteria
- [ ] For each modified module AND path (risk x3, critic FULL x2 call sites, simulator batch+seeded, hindsight
      single+batch+individual-aggregate, perspective `evaluate`+`evaluate_single`), the system message rendered
      for two distinct requests is byte-identical and equals the path-specific constant.
- [ ] Critic quick-check is byte-for-byte UNCHANGED: its system prompt contains NO full `decision`/`violations`
      schema, and `{"violated": true}` still yields `passed=False` (fast-path safety guard intact).
- [ ] Simulator seeded system prompt contains NO batch-only "exactly N"/`num_scenarios` text; hindsight single
      system prompt contains NO `"evaluations"` root schema and batch contains NO single root-object schema
      (path contracts do not collide).
- [ ] Perspective `evaluate_single` keeps full request/response/risk in its per-perspective user message
      after A5a (capture test).
- [ ] DCCL and constitution retriever are UNCHANGED (verify-only); a test confirms their systems are already
      static and dynamic data is in later messages.
- [ ] For each modified path, `concat(system, user)` for a fixed input is semantically identical to the
      pre-change prompt (no static text dropped; hard-signal blocks intact per pinned literals).
- [ ] `tests/test_system_prompt_byte_equality.py` and `tests/test_llm_parse_contract.py` pass UNCHANGED.
- [ ] `response_format` stays `{"type":"json_object"}` everywhere; retry loops and parsers unchanged.
- [ ] Observability persistence reflects the new system/user split (tested).
- [ ] Full suite green: `python -m pytest`.
- [ ] No change to `moralstack/models/policy.py`, `models/base.py`, `utils/structured_output.py`,
      `utils/llm_parse_contract.py`.
- [ ] Docs updated per section 8.

## Implementation checklist
1. Confirm assumptions 1, 3, 4 by re-reading the cited lines.
2. Reorder per module/path (risk -> critic -> simulator batch+seeded -> hindsight single+batch -> perspective
   A5a; retriever/DCCL are verify-only, no edits): move static text system-ward, dynamic user-ward; update ALL call
   sites/branches; update observability persistence strings.
3. Add static-prefix-stability + content-preservation tests; rewrite `test_critic_prompt.py:39-53` and
   `test_prompt_audit_fixes.py:53-68`; run scoped module tests.
4. Run `python -m pytest` full; fix regressions at root cause (no test weakening).
5. Update docs (section 8).

## Rollback plan
Each module/path change is an independent prompt-constant move. Revert per module by restoring the prompt
constants. No `response_format`, schema, parser, or migration change is introduced, so rollback is a pure code
revert with no behavioral residue — the caching win simply disappears. Keep each module's reorder as its own
commit so a single module can be reverted in isolation.

## Docs to update (section 8)
- `docs/MORALSTACK_CODEBASE_INDEX.md` — note the system/user prompt split per module/path.
- `docs/CODEBASE_FACTS.md` — record verified facts: (a) DCCL DOES set `response_format={"type":"json_object"}`
  (`dccl.py:472-477`), correcting the cartographer map; (b) byte-equality covers only the policy generator;
  (c) a Pydantic strict-output layer already exists in `utils/structured_output.py` (relevant to the deferred
  Part B); (d) Hindsight has both single-scenario (in-module template) and batch (`generate_messages`) paths,
  and Simulator has a seeded in-module template — correcting the first cartography draft.
- `docs/TRACES/observability_db_to_ui.md` — reflect the moved `system_prompt`/`prompt` persistence.
- `docs/modules/*.md` — update the critic/simulator/perspective/hindsight/risk module contracts for the
  prefix/suffix split.

## Deferred to Part B (separate future plan — NOT this iteration)
A future strict-JSON plan must, and this plan deliberately does NOT:
1. Adopt `response_format={"type":"json_schema","strict":true}` — but **derive the schemas from the existing
   Pydantic models** in `moralstack/utils/structured_output.py:150-278` (`CriticOutput`,
   `SimulatorConsequenceOutput`/`SimulatorOutput`, `HindsightSingleEvaluationOutput`/`HindsightBatchOutput`,
   all `ConfigDict(extra="forbid")`), NOT hand-author them (Codex architecture concern — avoids drift).
2. Reconcile strict `required`-all with those models' DEFAULT fields (e.g. `scenario_type="social_impact"`,
   `helpfulness=0.0`, `benefit_probability=0.5`): either null-unions preserving today's missing-field defaults
   (with validator/parser + test changes, justified per section 7) or keep `json_object` where defaults matter.
3. Design the `message.refusal` fail-closed path: `_complete` (`models/policy.py:233-244`) has NO refusal
   channel today and `GenerationResult` (`models/base.py:83-104`) has no refusal field; any addition crosses the
   shared delivered-answer path and needs a byte-equality regression guard.
4. Cover the ACTUAL risk signal keys `q1_confidential`..`q17_minor_exploitation` used by
   `models/risk/calibration.py:120-161,780-807` (the `test_llm_parse_contract.py:91-120` fixture uses legacy
   names and omits q13-q17) — critical for hard-signal supremacy (`PROJECT_SPEC.md:70-72`,
   `.claude/rules/hard-signal-safety.md:8-10`).
5. Update `utils/llm_parse_contract.py:16` (`RESPONSE_CONTRACT_JSON_OBJECT`) so audit rows label strict-schema
   contracts correctly.
6. Decide strict-JSON for Perspective (parser needs only `approval_score` today) — likely low value.
