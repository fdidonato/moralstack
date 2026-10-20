# Prompt overrides (deployer guide)

> **Status: not yet wired. Overrides take effect from step A3.** This is the A1 skeleton: the catalog
> (`moralstack/prompts/catalog.py`) and the packaged defaults (`moralstack/prompts/data/*.yaml`) exist and are
> tested. From step A2b the deliberative modules (critic, simulator, hindsight, perspectives) and
> `message_context` read their prompts from a `PromptSet` handed to their constructors (default: the packaged
> set; the prompt constants stay as default re-exports), and the module caches carry `PromptSet.cache_tag_for(ctx)`
> (empty for the default set). Nothing builds a non-default set at runtime yet: `GovernanceConfig.prompts_dir` does not exist yet and
> `MORALSTACK_PROMPTS_DIR` is not read by any entry point. Nothing in this document changes behavior today.
> The full guide (complete fragment table, coherent `when` groups, audit walkthrough) is written in A3 and
> finalized in A5.

MoralStack sends many LLM-facing prompts (risk estimation, critic, simulator, hindsight, perspectives, policy,
refusal, DCCL, retrieval). Every one of them is a named *fragment* in a packaged YAML file. A deployer will be able
to point `prompts_dir` (or `MORALSTACK_PROMPTS_DIR`) at a folder of partial override files that change the
`text` and/or the `when` condition of existing fragments. With no `prompts_dir`, every byte sent to every model is
identical to the packaged defaults, which are byte-identical to the pre-externalization prompts.

**Risk statement.** Overrides may weaken governance, including the prompts that make the models report hard
signals. This is an accepted, deployer-side vector (user decision of 2026-10-19). It is audited, not blocked: see
"Audit" below. The code-level gates (`path_router.is_hard_signal_refuse`, the DCCL safety-override category check
and the response schemas) are not overridable.

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
- A missing path, a path that is not a directory, or a directory with no `*.yaml` file is an error. For the empty
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
- Block scalars: `|` keeps one trailing newline, `|-` none, `|+` keeps all trailing blank lines; `|2` states that
  the first line starts with two spaces of content. Trailing spaces inside a block scalar are content, so do not
  leave any by accident. Leading empty lines are content too.

## `when` conditions

A closed vocabulary, no expressions: `always` (default), `never`, `contract_present`, `contract_absent`,
`history_present`, `history_absent`.

- `contract_present`: the call received a developer contract whose text is non-empty after `strip()`.
- `history_present`: the call received a non-empty conversation history.
- **Edge case (whitespace-only contract).** A contract made only of whitespace still produces a developer message
  and the context preface for the deliberative modules, but `contract_present` is false, so fragments gated on
  `contract_present` are dropped.
- **Edge case (estimator legacy branch).** The risk estimator's legacy `generate()` branch drops the contract
  while `contract_present` stays true; with the default configuration this changes nothing.
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
coherent `when` groups) is added in A3/A5.

**Deferred to A2c.** Five revision-guidance fragments from `orchestration/guidance_builder.py` are not packaged yet:
`guidance.balance.maintain`, `guidance.balance.both_sides`, `guidance.simulator.negative_valence` (template
`{valence}`), `guidance.hindsight.low_score` (template `{score}`) and `guidance.fallback`. They will live in
`policy.yaml` (they reach `OpenAIPolicy.rewrite` as `REVISION FEEDBACK:`); the bracket tags stay in code.

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
and in the `run.started` JSONL record from A4: set and default fingerprints, `ps` (first 16 hex of the
fingerprint), per-fragment `sha256` / `when` / `origin`, the override files with their own `sha256`, the
overridden text of every overridden fragment, and `safety_fragments_overridden`.

- Safety-relevant fragments are the packaged IDs under `risk.`, `critic.`, `compliance.` and `refusal.`, plus
  `policy.safe_complete_instruction`, `policy.constrained_instruction`, `policy.hard_violation_guidance`,
  `policy.hard_violation_guidance.unspecified`, `policy.canary_instruction`, `policy.anti_leak_rules` and
  `retrieval.prefilter.system` (code-owned list `SAFETY_FRAGMENT_IDS`, evaluated lazily on first access). Overriding one logs one WARNING per
  fragment and lists it in `safety_fragments_overridden`.
- **Override texts are persisted verbatim** in the audit record and in `run.started.jsonl`. Never put secrets in
  a prompt.
- Overrides change the static prompt prefixes, so the first requests miss the provider's prompt cache, and a
  `when`-conditional fragment splits the cache hit rate across up to 2 (contract) or 4 (contract x history)
  distinct prefixes per module.
