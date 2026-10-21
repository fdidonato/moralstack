# Prompt overrides (deployer guide)

> **Status: effective since step A3.** Every prompt text is read from a `PromptSet`; one set is built per
> composition root and handed to every component (policy, risk estimator, critic, simulator, hindsight,
> perspectives, constitution store / retriever, orchestrator). The audit record is persisted per run from step A4,
> every LLM call carries the exact wire it sent (A4b), `scripts/verify_prompt_audit.py` checks both (A4c), and the
> complete fragment table below was generated from the packaged defaults at A5.

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
- Files are read as UTF-8 (a BOM is tolerated); CRLF and LF load the same content. Your own files may use either
  line ending. The packaged defaults are kept LF (`.gitattributes`: `moralstack/prompts/data/*.yaml text eol=lf`),
  because a block scalar is byte-exact and a CRLF checkout would otherwise change every prompt.
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
- Block scalars (the form every multi-line `text` should use):
  - `|` keeps exactly one trailing newline; `|-` strips it; `|+` keeps every trailing blank line.
  - A block scalar whose first line starts with spaces that are content needs an explicit indentation indicator:
    `|2` (keep the newline) or `|2-` (strip it); without it YAML takes the first line's indent as the block indent
    and the spaces are lost. Example: `risk.intent.offensive_contract_note` starts with two content spaces.
  - Trailing spaces inside a block scalar are content, so do not leave any by accident. Leading empty lines are
    content too.
  - A `text` fragment is sent as written: if the default ends with a newline inside its text (for example
    `hindsight.batch.instructions`), keep it with `|` rather than `|-`, or the glue between parts changes.
  - To see exactly what a model will receive, render the set (`load_prompt_set(<dir>)`, then `.text(id)` or
    `.render(id, ctx)`) and compare; the audit's per-fragment `sha256` is the hash of the loaded text.

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
`.composition_ids()` (the counts are not repeated here so they cannot drift); the tables below were generated from
them at A5.

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

### Complete fragment table

Columns: **kind** (see "Fragment kinds"); **compositions** (the ordered system prompts that include the fragment, or
`standalone` for a fragment that is sent on its own: user templates, blocks, instructions); **`when` allowed**
(`yes` only when the fragment sits in at least one composition and every composition it sits in is context-aware;
otherwise a non-`always` `when` is a load error); **safety** (member of `SAFETY_FRAGMENT_IDS`: an override is
logged and listed in `safety_fragments_overridden`). Every packaged fragment has `when: always` by default.

| fragment ID | file | kind | compositions that include it | `when` allowed | safety |
|---|---|---|---|---|---|
| `common.output_json_only` | `common.yaml` | text | `critic.full.system`, `hindsight.batch.system`, `perspectives.system_body`, `simulator.batch.system` | yes |  |
| `retry.prefix` | `common.yaml` | text | `critic.retry`, `hindsight.retry.single`, `hindsight.retry.batch`, `simulator.retry` | no |  |
| `compliance.dccl.system` | `compliance.yaml` | text | standalone | no | yes |
| `compliance.dccl.native_trailer` | `compliance.yaml` | template | standalone | no | yes |
| `compliance.dccl.no_draft` | `compliance.yaml` | text | standalone | no | yes |
| `compliance.dccl.fallback_user` | `compliance.yaml` | template | standalone | no | yes |
| `compliance.dccl.transcript_truncated_note` | `compliance.yaml` | text | standalone | no | yes |
| `compliance.dccl.transcript_complete_note` | `compliance.yaml` | text | standalone | no | yes |
| `compliance.dccl.transcript_fallback` | `compliance.yaml` | template | standalone | no | yes |
| `compliance.draft_match.system` | `compliance.yaml` | text | standalone | no | yes |
| `compliance.draft_match.user` | `compliance.yaml` | template | standalone | no | yes |
| `compliance.safety_classifier.system` | `compliance.yaml` | text | standalone | no | yes |
| `context.module_preface` | `context.yaml` | text | standalone | no |  |
| `critic.base` | `critic.yaml` | text | `critic.full.system`, `critic.quick.system` | no | yes |
| `critic.rules.head` | `critic.yaml` | text | `critic.full.system` | yes | yes |
| `critic.rules.devcontract` | `critic.yaml` | text | `critic.full.system` | yes | yes |
| `critic.rules.tail` | `critic.yaml` | text | `critic.full.system` | yes | yes |
| `critic.retry.body` | `critic.yaml` | text | `critic.retry` | no | yes |
| `critic.full.user` | `critic.yaml` | template | standalone | no | yes |
| `critic.quick.user` | `critic.yaml` | template | standalone | no | yes |
| `critic.full.previous_violations_block` | `critic.yaml` | template | standalone | no | yes |
| `critic.full.previous_guidance_block` | `critic.yaml` | template | standalone | no | yes |
| `hindsight.base` | `hindsight.yaml` | text | `hindsight.batch.system`, `hindsight.single.system` | no |  |
| `hindsight.devcontract_evaluation` | `hindsight.yaml` | text | `hindsight.batch.system` | yes |  |
| `hindsight.batch.instructions` | `hindsight.yaml` | text | `hindsight.batch.system` | yes |  |
| `hindsight.single.rubric` | `hindsight.yaml` | text | `hindsight.single.system` | no |  |
| `hindsight.batch.user` | `hindsight.yaml` | template | standalone | no |  |
| `hindsight.single.user` | `hindsight.yaml` | template | standalone | no |  |
| `hindsight.retry.single.body` | `hindsight.yaml` | text | `hindsight.retry.single` | no |  |
| `hindsight.retry.batch.body` | `hindsight.yaml` | text | `hindsight.retry.batch` | no |  |
| `perspectives.base` | `perspectives.yaml` | text | standalone | no |  |
| `perspectives.risk_context_interpretation` | `perspectives.yaml` | text | `perspectives.system_body` | yes |  |
| `perspectives.devcontract_evaluation` | `perspectives.yaml` | text | `perspectives.system_body` | yes |  |
| `perspectives.common_instructions` | `perspectives.yaml` | text | `perspectives.system_body` | yes |  |
| `perspectives.identity_block` | `perspectives.yaml` | template | standalone | no |  |
| `perspectives.turn_context` | `perspectives.yaml` | template | standalone | no |  |
| `perspectives.identity.user` | `perspectives.yaml` | text | standalone | no |  |
| `perspectives.identity.vulnerable` | `perspectives.yaml` | text | standalone | no |  |
| `perspectives.identity.observer` | `perspectives.yaml` | text | standalone | no |  |
| `perspectives.identity.adversary` | `perspectives.yaml` | text | standalone | no |  |
| `perspectives.identity.compliance` | `perspectives.yaml` | text | standalone | no |  |
| `perspectives.retry` | `perspectives.yaml` | text | standalone | no |  |
| `policy.system` | `policy.yaml` | text | standalone | no |  |
| `policy.language_rules` | `policy.yaml` | text | standalone | no |  |
| `policy.anti_leak_rules` | `policy.yaml` | text | standalone | no | yes |
| `policy.canary_instruction` | `policy.yaml` | template | standalone | no | yes |
| `policy.safe_complete_instruction` | `policy.yaml` | text | standalone | no | yes |
| `policy.constrained_instruction` | `policy.yaml` | text | standalone | no | yes |
| `policy.language_prefix.explicit` | `policy.yaml` | template | standalone | no |  |
| `policy.language_prefix.implicit` | `policy.yaml` | template | standalone | no |  |
| `policy.hard_violation_guidance` | `policy.yaml` | template | standalone | no | yes |
| `policy.hard_violation_guidance.unspecified` | `policy.yaml` | text | standalone | no | yes |
| `policy.rewrite.user` | `policy.yaml` | template | standalone | no |  |
| `policy.rewrite.default_system` | `policy.yaml` | text | standalone | no |  |
| `policy.rewrite.constraints` | `policy.yaml` | text | standalone | no |  |
| `policy.contract_regeneration.user` | `policy.yaml` | template | standalone | no |  |
| `policy.contract_regeneration.transcript_block` | `policy.yaml` | template | standalone | no |  |
| `guidance.balance.maintain` | `policy.yaml` | text | standalone | no |  |
| `guidance.balance.both_sides` | `policy.yaml` | text | standalone | no |  |
| `guidance.simulator.negative_valence` | `policy.yaml` | template | standalone | no |  |
| `guidance.hindsight.low_score` | `policy.yaml` | template | standalone | no |  |
| `guidance.fallback` | `policy.yaml` | text | standalone | no |  |
| `refusal.system` | `refusal.yaml` | text | standalone | no | yes |
| `refusal.default_redirection` | `refusal.yaml` | text | standalone | no | yes |
| `refusal.user` | `refusal.yaml` | template | standalone | no | yes |
| `refusal.evidence_block` | `refusal.yaml` | template | standalone | no | yes |
| `refusal.legacy_rationale_block` | `refusal.yaml` | template | standalone | no | yes |
| `refusal.legacy_domain_block` | `refusal.yaml` | template | standalone | no | yes |
| `refusal.legacy_domain_redirection` | `refusal.yaml` | template | standalone | no | yes |
| `refusal.request_context_block` | `refusal.yaml` | template | standalone | no | yes |
| `refusal.leak_avoidance_block` | `refusal.yaml` | template | standalone | no | yes |
| `refusal.category_hints.reputational` | `refusal.yaml` | lines | standalone | no | yes |
| `refusal.category_hints.financial` | `refusal.yaml` | lines | standalone | no | yes |
| `refusal.category_hints.physical` | `refusal.yaml` | lines | standalone | no | yes |
| `refusal.category_hints.child_exploitation` | `refusal.yaml` | lines | standalone | no | yes |
| `refusal.category_hints.illegal` | `refusal.yaml` | lines | standalone | no | yes |
| `refusal.category_hints.emotional_relational` | `refusal.yaml` | lines | standalone | no | yes |
| `refusal.category_hints.fallback` | `refusal.yaml` | lines | standalone | no | yes |
| `exclusion.system` | `refusal.yaml` | text | standalone | no |  |
| `exclusion.user` | `refusal.yaml` | template | standalone | no |  |
| `refusal.final_revalidation_rationale` | `refusal.yaml` | text | standalone | no | yes |
| `refusal.assembler_harmful_rationale` | `refusal.yaml` | text | standalone | no | yes |
| `retrieval.agent.enhanced.system` | `retrieval.yaml` | text | standalone | no |  |
| `retrieval.agent.legacy.system` | `retrieval.yaml` | text | standalone | no |  |
| `retrieval.agent.enhanced.user` | `retrieval.yaml` | template | standalone | no |  |
| `retrieval.agent.legacy.user` | `retrieval.yaml` | template | standalone | no |  |
| `retrieval.agent.negative_section` | `retrieval.yaml` | template | standalone | no |  |
| `retrieval.agent.negative_examples.financial` | `retrieval.yaml` | lines | standalone | no |  |
| `retrieval.agent.negative_examples.research` | `retrieval.yaml` | lines | standalone | no |  |
| `retrieval.agent.negative_examples.journalism` | `retrieval.yaml` | lines | standalone | no |  |
| `retrieval.agent.negative_examples.enterprise` | `retrieval.yaml` | lines | standalone | no |  |
| `retrieval.agent.negative_examples.mental_health` | `retrieval.yaml` | lines | standalone | no |  |
| `retrieval.agent.negative_examples.medical` | `retrieval.yaml` | lines | standalone | no |  |
| `retrieval.prefilter.system` | `retrieval.yaml` | template | standalone | no | yes |
| `retrieval.prefilter.user` | `retrieval.yaml` | template | standalone | no |  |
| `risk.intent.head` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.devcontract_principle` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.step0` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.step1_banner` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.step1_step0_note` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.step1_framings_a` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.offensive_contract_note` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.step1_framings_b` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.esi_contract_note` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.intent.tail` | `risk.yaml` | text | `risk.intent.system` | yes | yes |
| `risk.signals.head` | `risk.yaml` | text | `risk.signals.system_head` | yes | yes |
| `risk.signals.devcontract_scope` | `risk.yaml` | text | `risk.signals.system_head` | yes | yes |
| `risk.operational.head` | `risk.yaml` | text | `risk.operational.system` | yes | yes |
| `risk.operational.devcontract_scope` | `risk.yaml` | text | `risk.operational.system` | yes | yes |
| `risk.operational.tail` | `risk.yaml` | text | `risk.operational.system` | yes | yes |
| `risk.intent.user` | `risk.yaml` | template | standalone | no | yes |
| `risk.signals.user` | `risk.yaml` | template | standalone | no | yes |
| `risk.operational.user` | `risk.yaml` | template | standalone | no | yes |
| `risk.signals.static_suffix` | `risk.yaml` | template | standalone | no | yes |
| `risk.intent.principles_block` | `risk.yaml` | template | standalone | no | yes |
| `risk.intent.principles_none_found` | `risk.yaml` | text | standalone | no | yes |
| `risk.context_preface` | `risk.yaml` | text | standalone | no | yes |
| `risk.signals.evaluation_order_item` | `risk.yaml` | template | standalone | no | yes |
| `risk.signals.output_schema_header` | `risk.yaml` | template | standalone | no | yes |
| `risk.signals.output_schema_footer` | `risk.yaml` | text | standalone | no | yes |
| `simulator.base` | `simulator.yaml` | text | `simulator.batch.system`, `simulator.seeded.system` | yes |  |
| `simulator.batch.contract_intro` | `simulator.yaml` | text | `simulator.batch.system` | yes |  |
| `simulator.devcontract_evaluation` | `simulator.yaml` | text | `simulator.batch.system` | yes |  |
| `simulator.batch.constraints` | `simulator.yaml` | text | `simulator.batch.system` | yes |  |
| `simulator.harm_type_mapping_rule` | `simulator.yaml` | text | `simulator.batch.system`, `simulator.seeded.system` | yes |  |
| `simulator.batch.skeleton_heading` | `simulator.yaml` | text | `simulator.batch.system` | yes |  |
| `simulator.schema_skeleton` | `simulator.yaml` | text | `simulator.batch.system`, `simulator.seeded.system`, `simulator.retry` | no |  |
| `simulator.enums` | `simulator.yaml` | text | `simulator.batch.system`, `simulator.seeded.system`, `simulator.retry` | no |  |
| `simulator.seeded.rubric_intro` | `simulator.yaml` | text | `simulator.seeded.system` | yes |  |
| `simulator.seeded.rubric_close` | `simulator.yaml` | text | `simulator.seeded.system` | yes |  |
| `simulator.seeded.output_footer` | `simulator.yaml` | text | `simulator.seeded.system` | yes |  |
| `simulator.retry.values_heading` | `simulator.yaml` | text | `simulator.retry` | no |  |
| `simulator.retry.footer` | `simulator.yaml` | text | `simulator.retry` | no |  |
| `simulator.batch.user` | `simulator.yaml` | template | standalone | no |  |
| `simulator.seeded.user` | `simulator.yaml` | template | standalone | no |  |
| `simulator.seeded.seeds` | `simulator.yaml` | lines | standalone | no |  |
| `simulator.domain_guidance.cybersecurity` | `simulator.yaml` | text | standalone | no |  |
| `simulator.domain_guidance.default` | `simulator.yaml` | text | standalone | no |  |

Context-aware compositions (the only ones that can drop a part): `critic.full.system`, `hindsight.batch.system`,
`perspectives.system_body`, `risk.intent.system`, `risk.signals.system_head`, `risk.operational.system`,
`simulator.batch.system`, `simulator.seeded.system`. The others (`critic.quick.system`, `critic.retry`,
`hindsight.single.system`, `hindsight.retry.single`, `hindsight.retry.batch`, `simulator.retry`) always render the
same bytes.

### Coherent `when` groups (developer-contract blocks)

`when: contract_present` (or `contract_absent`) is meant for the developer-contract blocks. Set it on a whole group,
never on part of one, or the remaining text refers to something that is no longer there:

| composition | fragments to gate together | why |
|---|---|---|
| `risk.intent.system` | `risk.intent.devcontract_principle`, `risk.intent.step0`, `risk.intent.step1_step0_note`, `risk.intent.offensive_contract_note`, `risk.intent.esi_contract_note` | they reference "invariant 6", "STEP 0" and "(6.c)"; with all five dropped no remaining fragment mentions them (checked at A0, pinned by a test) |
| `risk.signals.system_head` | `risk.signals.devcontract_scope` | one fragment |
| `risk.operational.system` | `risk.operational.devcontract_scope` | one fragment |
| `critic.full.system` | `critic.rules.devcontract` | one fragment; the quick check shares `critic.base` but not this rule |
| `hindsight.batch.system` | `hindsight.devcontract_evaluation` | one fragment |
| `perspectives.system_body` | `perspectives.devcontract_evaluation` | one fragment |
| `simulator.batch.system` | `simulator.batch.contract_intro`, `simulator.devcontract_evaluation` | the intro announces the evaluation block; gate both |

The grouping for the risk prompts was verified against the text at A0; for the other rows the pairing is the
reading of the default text, so check the rendered result when you set a `when` there. A group dropped for a request
without a contract changes the static prefix of that module's calls (see "Audit" for the cache effect).

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
  recoverable from the `wire` record (`wire_calls` for refusals) that step A4b persists with every LLM call, whose
  `sha256` lets a reader check it. `python scripts/verify_prompt_audit.py --db <path> [--jsonl-dir <dir>] [--strict]`
  rebuilds each wire from the persisted rows, checks the run's `prompt_config` against the loaded set and reports
  `defaults_differ`, `safety_fragments_overridden` and any tampering; it opens the database read-only.
  It is an integrity check, not a signature: someone who edits a wire **and** its `sha256` consistently is not
  detected.
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
