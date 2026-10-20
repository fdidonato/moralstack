# Prompt overrides (deployer guide)

> **Status: effective since step A3.** Every prompt text is read from a `PromptSet`; one set is built per
> composition root and handed to every component (policy, risk estimator, critic, simulator, hindsight,
> perspectives, constitution store / retriever, orchestrator). The audit record is persisted per run from step A4,
> and the complete fragment table is finalized in A5.

MoralStack sends many LLM-facing prompts (risk estimation, critic, simulator, hindsight, perspectives, policy,
refusal, DCCL, retrieval). Every one of them is a named *fragment* in a packaged YAML file. A deployer
points `prompts_dir` (or `MORALSTACK_PROMPTS_DIR`) at a folder of partial override files that change the
`text` and/or the `when` condition of existing fragments. With no `prompts_dir`, every byte sent to every model is
identical to the packaged defaults, which are byte-identical to the pre-externalization prompts.

**Risk statement.** Overrides may weaken governance, including the prompts that make the models report hard
signals. This is an accepted, deployer-side vector (user decision of 2026-10-19). It is audited, not blocked: see
"Audit" below. The code-level gates (`path_router.is_hard_signal_refuse`, the DCCL safety-override category check,
the response schemas and the ledger hard-signal guard) are not overridable.

## Enabling overrides

| where | how |
|---|---|
| SDK (`govern()`) | `GovernanceConfig(prompts_dir="/etc/moralstack/prompts")` |
| any process | `MORALSTACK_PROMPTS_DIR=/etc/moralstack/prompts` (a value in the project's `.env` works: it is read after `load_env()`) |
| proxy | no flag of its own: it uses the SDK bootstrap, but the shipped launcher (`examples/server_quickstart.py`) builds a bare `GovernanceConfig()`, so it is env only unless you edit the launcher to pass `prompts_dir` |
| CLI (`mstack_run.py`) | env var only (no flag); ignored with `--mock`, whose modules are test doubles |
| benchmark (`scripts/benchmark_moralstack.py`) | env var only; the set is loaded in `BenchmarkRunner.__init__`, before any LLM client exists, so a bad directory fails a paid run **before it spends anything** |

- **Precedence:** `GovernanceConfig.prompts_dir`, then `MORALSTACK_PROMPTS_DIR`, then the packaged defaults. A
  blank value (`""`, whitespace-only) counts as **unset** and falls through to the next source; the env var is also
  purged by `load_env()` when empty.
- **One set per process start.** The directory is read once, when the pipeline is built. Changing files afterwards
  has no effect until the process restarts. Every consumer of a root holds the *same* `PromptSet` object, so a request
  never mixes two sets. Hand-wiring modules with a different set than the orchestrator's is unsupported (the audit and
  the request link record the orchestrator's set).
- **Fail fast.** A bad `prompts_dir` raises `PromptConfigError` while the pipeline is built; through the SDK it is
  mapped to `GovernancePipelineError` ("Failed to initialize deliberation modules"), so no `GovernedClient` and no
  orchestrator exist and no request can be served (no passthrough; PROJECT_SPEC section 5.7).
- The programmatic surface is only `prompts_dir`. There is no Python API that edits a set; the `prompt_set=` keyword
  arguments inside MoralStack are internal plumbing, not a deployer surface.
- A packaged-default file that is itself broken fails at import, before any request.

## Override file format

```yaml
# <prompts_dir>/risk-contract-conditional.yaml
version: 1
fragments:
  risk.intent.devcontract_principle: {when: contract_present}
  risk.intent.step0: {when: contract_present}
  policy.system:
    text: |
      You are a thoughtful and knowledgeable assistant.
      ...
```

- The top level has `version: 1` (required) and `fragments` (a non-empty mapping). Nothing else.
- An entry changes `text`, `when`, or both (at least one). `kind`, `placeholders`, composition structure, `sep`
  and `end` are not overridable.
- An ID that does not exist, the ID of a composition, an unknown key, the same ID in two files, a malformed file or
  a wrong type is a load error (`PromptConfigError`) that names the file and the fragment ID. Messages never
  contain fragment text. The error is raised when the pipeline is built, never at request time.
- Files are read as UTF-8 (a BOM is tolerated); CRLF and LF load the same content.
- **Override-file `sha256`** (`override_files[].sha256` in the audit): decode the file as UTF-8 and strip a BOM,
  normalize CRLF and lone CR to LF, then take the SHA-256 of the UTF-8 bytes of that normalized text. It is therefore
  the same on LF and CRLF checkouts.

### Files in `prompts_dir`

- Only lowercase `*.yaml` files are loaded, in sorted order.
- A file that *looks like* an override but would be skipped is a **load error**, not silently ignored:
  `*.yml`, `*.YAML` (wrong case), hidden files (`.x.yaml`), editor leftovers (`.#x.yaml`, `x.yaml~`,
  `x.yaml.bak`, `.x.yaml.swp`). Remove or rename them.
- Other files (`README.md`, notes) and sub-directories are ignored.
- A missing path, a path that is not a directory, a blank path passed straight to `load_prompt_set`, or a
  directory with no `*.yaml` file is an error. For the empty
  case the message reads: `prompts_dir <path> contains no *.yaml override files; unset prompts_dir (or
  MORALSTACK_PROMPTS_DIR) to use the packaged defaults`.

## Fragment kinds, braces and placeholders

| kind | meaning | braces |
|---|---|---|
| `text` | sent byte for byte, never formatted | **wire form**: what the YAML holds is what the model receives |
| `template` | a Python `str.format` string with declared placeholders | `{{` and `}}` give literal braces; only bare `{name}` fields are allowed (no `{}`, `{0}`, `{a.b}`, `{a[0]}`, `{a!r}`, `{a:>5}`) |
| `lines` | one item per line (seeds, example lists); no empty items | use `|-` so the last line has no trailing newline |

- An override of a template may use only the placeholders the default declares. A declared placeholder that the
  override omits is allowed with a WARNING and is recorded in the audit (`missing_placeholders`).
- Two `text` fragments are stored with single braces because today's code collapses them before sending:
  `simulator.schema_skeleton` and `perspectives.common_instructions`. The simulator *retry* prompt re-doubles the
  skeleton (a `double_braces` transform on that one part), preserving today's retry text byte for byte.
- **Shared fragments.** A few fragments are used by several compositions, so an override of their text reaches all of
  them: `critic.base` is both the full critic system prompt and the quick check's, and `simulator.enums` /
  `simulator.schema_skeleton` are used by the batch, seeded and retry simulator prompts. This is also why a `when`
  on them is rejected (see below).
- **Templates and the contract.** `compliance.dccl.fallback_user` declares a `{contract}` placeholder, because the
  DCCL evaluator must read the developer contract to judge compliance. Omitting it (allowed, with a WARNING) blinds
  that fallback. No other template can interpolate the contract, and the developer's system prompt is never part of
  the prompt set: the governed generator's system prompt is always the verbatim contract, then MoralStack's base.
- `refusal.category_hints.*` are `lines` fragments and cannot be emptied (no empty items); an unknown `harm_type`
  uses `refusal.category_hints.fallback`. Two sentences that used to be code literals are now fragments too:
  `refusal.final_revalidation_rationale` (final revalidation) and `refusal.assembler_harmful_rationale` (response
  assembler); both are in the safety list below because they sit under the `refusal.` prefix.
- Block scalars: `|` keeps one trailing newline, `|-` none, `|+` keeps all trailing blank lines; `|2` states that
  the first line starts with two spaces of content. Trailing spaces inside a block scalar are content, so do not
  leave any by accident. Leading empty lines are content too.

## `when` conditions

A closed vocabulary, no expressions: `always` (default), `never`, `contract_present`, `contract_absent`,
`history_present`, `history_absent`.

- `contract_present`: the call received a developer contract whose text is non-empty after `strip()`.
- `history_present`: the call received a non-empty conversation history.
- **Edge case (whitespace-only contract).** `message_context.build_module_messages` sends the developer message
  when the contract text is truthy and the preface when a contract object exists or history is present
  (`moralstack/runtime/modules/message_context.py`), so a contract made only of whitespace still produces a
  developer message and the context preface for the deliberative modules. `PromptContext.contract_present`, however,
  is text based (`str.strip()`, the same predicate as `DeveloperContract.is_empty()`), so it is false and fragments
  gated on `contract_present` are dropped. The risk estimator strips everywhere and is self-consistent.
- **Edge case (estimator legacy branch).** The risk estimator's legacy `generate()` branch drops the contract
  while `contract_present` stays true: a `when: contract_present` fragment is still rendered although the legacy
  call no longer carries the contract. With the default configuration this changes nothing, and the production
  `OpenAIPolicy` always has `generate_messages`, so the branch is reached only by minimal custom policies.
- A non-`always` `when` is legal only on a fragment that belongs to at least one composition **and** whose
  compositions are all context-aware (`context_aware: true`). Otherwise loading fails and names the compositions
  (examples: `critic.base` also feeds the quick check, `simulator.enums` and `simulator.schema_skeleton` also feed the
  simulator retry, `retry.prefix`, and every standalone fragment).
- A dropped fragment takes its own separator with it, and a composition's terminal whitespace (`end`) is always
  emitted, so dropping the last part never loses the final newline.
- **To remove a fragment together with its separator, use `when: never`.** An override with `text: ""` keeps the
  fragment's separator, so the separator is still emitted.
- `when` is designed for the developer-contract groups of the risk estimator, critic and similar compositions. On any
  other fragment the deployer owns the result. Example: `hindsight.batch.instructions` ends with a newline inside its
  text and `common.output_json_only` follows it with no separator, so `when: never` on the instructions yields
  `...JSON.Output ONLY valid JSON.` glued together.

## Fragment IDs (outline)

IDs are lowercase dotted names, in 11 files. The authoritative list is `default_prompt_set().fragment_ids()` and
`.composition_ids()` (the counts are not repeated here so they cannot drift). The complete table (with the
coherent `when` groups) is added in A5.

**Revision guidance (externalized in A2c).** Five revision-guidance fragments from `orchestration/guidance_builder.py`
live in `policy.yaml`: `guidance.balance.maintain`, `guidance.balance.both_sides`,
`guidance.simulator.negative_valence` (template `{valence}`, preformatted `:.2f` by the code),
`guidance.hindsight.low_score` (template `{score}`, same rule) and `guidance.fallback`. They reach
`OpenAIPolicy.rewrite` as `REVISION FEEDBACK:`. The bracket tags (`[BALANCE]`, `[CRITIC]`, `[SIMULATOR]`,
`[HINDSIGHT]`, `[PERSPECTIVES - ...]`) stay in code, because code matches on them; each fragment is the sentence
that follows its tag.

| prefix | file | covers |
|---|---|---|
| `common.`, `retry.prefix` | `common.yaml` | the JSON-only closure, the retry prefix |
| `risk.intent.*`, `risk.signals.*`, `risk.operational.*`, `risk.context_preface` | `risk.yaml` | the three risk-estimator system prompts (cut into fragments), user templates, principles block, renderer sentences |
| `critic.*` | `critic.yaml` | critic base, rules (incl. the developer-contract rules), retry, user templates |
| `perspectives.*` | `perspectives.yaml` | perspectives system body, identity texts, turn context, retry |
| `simulator.*` | `simulator.yaml` | batch / seeded / retry system prompts, enums, schema skeleton, seeds, domain guidance |
| `hindsight.*` | `hindsight.yaml` | batch / single system prompts, retries, user templates |
| `context.module_preface` | `context.yaml` | the preface sent before developer/history messages |
| `policy.*` | `policy.yaml` | policy system prompt, language rules, anti-leak rules, canary line, SAFE_COMPLETE / CONSTRAINED instructions, rewrite, hard-violation guidance, contract regeneration |
| `refusal.*`, `exclusion.*` | `refusal.yaml` | safe-refusal prompts and blocks, category hints, domain-exclusion message |
| `compliance.*` | `compliance.yaml` | DCCL system prompt and user texts, draft match, safety classifier |
| `retrieval.*` | `retrieval.yaml` | domain prefilter, enhanced and legacy domain agents, negative examples |

Compositions (the ordered parts, separators and `end`) are defined only in the packaged defaults and cannot be
overridden.

## Audit

The catalog builds a deterministic summary (`PromptSet.audit_summary()`), stored in `runs.meta_json.prompt_config`
and in the `run.started.jsonl` file record (one line per run in `file_only`/`dual`), written by every entry point (SDK, proxy, CLI, benchmark; library callers through the session fallback): set and default fingerprints, `ps` (first 16 hex of the
fingerprint), per-fragment `sha256` / `when` / `origin`, the override files with their own `sha256`, the
overridden text of every overridden fragment, and `safety_fragments_overridden`.

- Safety-relevant fragments are the packaged IDs under `risk.`, `critic.`, `compliance.` and `refusal.`, plus
  `policy.safe_complete_instruction`, `policy.constrained_instruction`, `policy.hard_violation_guidance`,
  `policy.hard_violation_guidance.unspecified`, `policy.canary_instruction`, `policy.anti_leak_rules` and
  `retrieval.prefilter.system` (code-owned list `SAFETY_FRAGMENT_IDS`, evaluated lazily on first access). Overriding one logs one WARNING per
  fragment and lists it in `safety_fragments_overridden`.
- A `policy.canary_instruction` override that omits `{canary}` additionally logs a specific WARNING ("system-prompt leak detection (canary) is DISABLED by this override").
- **Never put secrets in a prompt.** Override texts are persisted verbatim in the audit record (`runs.meta_json`
  and the `run.started.jsonl` file record), and exported JSONL keeps the configured `prompts_dir` path. Every request
  also carries a `prompt_set` link (`ps`, `is_default`) in `requests.meta_json` / `request.meta_updated.jsonl`.
- The audit identifies the **set**, not the per-call `when` variant: which of the four contexts a call used is
  recoverable from the wire record that step A4b adds.
- Overrides change the static prompt prefixes, so the first requests miss the provider's prompt cache, and a
  `when`-conditional fragment splits the cache hit rate across up to 2 (contract) or 4 (contract x history)
  distinct prefixes per module. Dropping the developer-contract blocks shrinks the intent system prompt by about 7k
  characters; it is expected, but not verified, to stay above the provider's 1024-token cache threshold. The module
  result caches (`_global_cache`) of a non-default set live in their own key space (`ps:<fp16>:<contract><history>`
  tag); default keys are unchanged.

## Operational notes

- `import moralstack.prompts` and `import moralstack.prompts.catalog` load nothing (the package resolves its builder
  names lazily). The packaged defaults are parsed on the first import of a prompt submodule or a runtime module, i.e.
  at process start, never inside a request.
- `catalog.SAFETY_FRAGMENT_IDS` is computed on first access from the packaged IDs, so importing `catalog` stays cheap.
- Rolling back is a configuration change: unset `prompts_dir` / `MORALSTACK_PROMPTS_DIR`. The packaged defaults are
  byte-identical to the pre-externalization prompts.
- The code-level gates never read prompt text and are not overridable: `path_router.is_hard_signal_refuse`, the DCCL
  safety-override category check, the response schemas and formats, and the ledger hard-signal guard. An override that
  makes a model emit unparseable output hits the existing fail-closed paths (the risk estimator falls back to
  DELIBERATE, the critic's failure is a governed REFUSE). The simulator, hindsight and perspectives keep their
  existing best-effort behavior on unparseable output.
- Known coupling (R8): `sanitize_policy_output` strips leaked lines containing "do not add translations" (and a few
  other markers); if you reword `policy.language_prefix.*`, its leaked lines will not be stripped.
- Known weakening (accepted): a `policy.canary_instruction` override that omits `{canary}` injects no token, so the
  output leak check has nothing to find. It is allowed, warned about and recorded in `missing_placeholders`.
