# Codex review guide

Codex CLI is the **independent reviewer** of plans (before implementation) and
diffs (after). It runs in a **read-only sandbox** — it inspects the repo but
cannot modify, commit, or push.

## Local CLI (verified)

- Binary: `codex` (v0.142.x), logged in via ChatGPT.
- Non-interactive review entry point used here:
  `codex exec -s read-only [-m <model>] -o <out.md> -` (prompt on stdin).
  `-s read-only` = read-only sandbox; `-o` writes the final review to a file.
- `codex` also has a purpose-built `codex review --uncommitted | --base <ref>`
  subcommand. We use `codex exec` instead so the **output follows our markdown
  templates** (`ai/prompts/codex-*-review-template.md`) verbatim.

## Models / effort

- Default: Codex uses its own configured model. On this machine that is
  `gpt-5.5` at `xhigh` reasoning effort — i.e. the high-effort review setting.
- Override per run:
  - `$env:CODEX_MODEL = "<model>"` → passed as `-m <model>`.
  - `$env:CODEX_REASONING_EFFORT = "high"` → passed as
    `-c model_reasoning_effort="high"`.
- We do **not** hardcode model names beyond these env hooks (PROJECT_SPEC:
  don't invent flags/models). List/inspect models with `codex --help` and your
  Codex account.

## Automatic run (preferred)

```powershell
# Plan review
pwsh scripts/ai/run_codex_plan_review.ps1 -PlanPath ai/plans/<task>.md
# Diff review (collects the diff if -DiffPath is omitted)
pwsh scripts/ai/run_codex_diff_review.ps1 -PlanPath ai/plans/<task>.md `
    -HandoffPath ai/handoffs/<task>-cursor-cli-handoff.md
```

Reports are written to `ai/reviews/codex-*-review-<task>-<timestamp>.md`. The
exact prompt sent is saved to `ai/prompts/generated-codex-*-<task>-<timestamp>.md`
for reproducibility.

Bash equivalents (WSL/Linux): `scripts/ai/run_codex_*_review.sh` with
`--plan`, `--diff`, `--handoff`, `--model`, `--dry-run`.

## Manual run (fallback)

If Codex CLI is missing or you set `-DryRun`, the script saves the generated
prompt and prints the command. Run it yourself:

```powershell
Get-Content -Raw ai/prompts/generated-codex-plan-review-<task>-<ts>.md |
    codex exec -s read-only -o ai/reviews/codex-plan-review-<task>-<ts>.md -
```

Or paste the generated prompt into an interactive Codex session. Either way the
review must follow the template structure and end with a verdict
(`APPROVE` / `APPROVE_WITH_CHANGES` / `BLOCK`).

## What Codex must check

See the templates and `docs/ai/REVIEW_POLICY.md`. In short: wrong assumptions,
blast radius, missing files/tests, regressions, architecture/coupling, security,
performance — and, for this repo specifically, the MoralStack invariants
(`docs/ai/INVARIANTS.md`). A governance change that fails **open** is BLOCKING.
