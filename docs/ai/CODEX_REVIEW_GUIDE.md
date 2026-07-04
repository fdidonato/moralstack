# Codex review guide

Codex is the **independent reviewer** of plans (before implementation) and
diffs (after). It runs in a **read-only sandbox** — it inspects the repo but
cannot modify, commit, or push.

## Invocation mechanism (verified)

- We call Codex through the official **OpenAI Codex Claude Code plugin**
  (`github.com/openai/codex-plugin-cc`), using its `codex:rescue` entry point
  — there is no repo script that shells out to `codex` anymore.
- Install/verify: `/plugin marketplace add openai/codex-plugin-cc`, then
  `/plugin install codex@openai-codex`. Check readiness with `/codex:setup`
  (or `Skill(codex:setup)`).
- `/ai-review-plan-with-codex` and `/ai-review-diff-with-codex`
  (`.claude/commands/`) run **inline** in the main conversation (not via a
  coordinator subagent — the plugin's own `codex:rescue` docs warn that a
  forked/general-purpose subagent doesn't reliably keep Agent-tool access,
  which `codex:rescue` needs internally) and call:

      Skill(skill: "codex:rescue", args: "--wait --fresh <review request>")

  `--wait` runs it synchronously; `--fresh` skips the "continue previous
  thread?" prompt since each review is a one-shot request.
- The plugin's native `/codex:review` / `/codex:adversarial-review` commands
  are diff/working-tree-only (fixed `approve`/`needs-attention` + severity
  schema, no custom framing for `/codex:review`) and aren't exposed to
  automated invocation in this session, so they aren't used here — `codex:rescue`
  is the one entry point our commands can call themselves, for both plan and
  diff review.
- No `--write` is ever passed to `codex:rescue`, so its default write-capable
  behavior doesn't kick in — the review request explicitly says "read-only,
  do not modify anything," which is what the plugin's own `codex-cli-runtime`
  skill treats as the carve-out for a read-only run.
- We still hand Codex the same rubric we always did
  (`ai/prompts/codex-plan-review-template.md` /
  `codex-diff-review-template.md`, MoralStack invariant framing, and the
  `docs/ai/REVIEW_POLICY.md` taxonomy) — as the natural-language request text,
  not a file a script concatenates. Codex reads the plan/diff/handoff files
  itself (it has repo read access), so we no longer paste their full content
  into the prompt.
- The command saves Codex's verbatim response to
  `ai/reviews/codex-{plan,diff}-review-<slug>-<timestamp>.md` and the exact
  composed request to `ai/prompts/generated-codex-*-<slug>-<timestamp>.md`,
  same audit convention as before.

## Models / effort

- `codex:rescue` accepts `--model <model|spark>` and `--effort
  <none|minimal|low|medium|high|xhigh>`. Our commands leave both unset by
  default (Codex plugin default); pass them through only if the user
  explicitly asks for a specific model/effort for a given review.

## What Codex must check

See the templates and `docs/ai/REVIEW_POLICY.md`. In short: wrong assumptions,
blast radius, missing files/tests, regressions, architecture/coupling, security,
performance — and, for this repo specifically, the MoralStack invariants
(`docs/ai/INVARIANTS.md`). A governance change that fails **open** is BLOCKING.

## If the plugin is unavailable

`/ai-review-plan-with-codex` / `/ai-review-diff-with-codex` must tell the user
to run `/plugin install codex@openai-codex` and stop — never fabricate a
review or fall back to Claude's own judgment presented as Codex's.
