# MoralStack agentic workflow — Claude orchestrates, an isolated sub-agent reviews, a Claude Sonnet sub-agent implements

Coordinated roles for working on this large Python codebase:

| Role | Who | Never does |
| --- | --- | --- |
| **Orchestrator** | **Claude Code** (main context): analyzes the codebase, writes plans, prepares handoffs, integrates reviews | Be the final reviewer; write the feature code itself (outside the direct lane) |
| **Reviewer** | **`adversarial-reviewer` sub-agent** (`.claude/agents/adversarial-reviewer.md`): read-only, isolated context, on a model pinned in its frontmatter that differs from the implementer's; independent reviewer of plans and diffs | Implement the change; edit, commit, push |
| **Implementer** | **Claude Sonnet sub-agent** (`claude-implementer`, `.claude/agents/claude-implementer.md`): headless implementer of approved plans in an isolated context | Commit, push, refactor out of scope |

## The loop

```
USER REQUEST
  → Claude analyzes the codebase            (codebase-cartographer)
  → Claude produces a technical plan        (architect-planner + test-strategist) → ai/plans/<task>.md
  → The reviewer sub-agent reviews the plan (/ai-review-plan → adversarial-reviewer)  → ai/reviews/plan-review-*.md
  → Claude integrates blocking feedback     (revises ai/plans/<task>.md)
  → Claude produces the handoff             (/ai-implement orchestrator)           → ai/handoffs/<task>-handoff.md
  → Claude Sonnet sub-agent implements      (claude-implementer)                   → code edits + implementation report
  → Claude collects the diff                (collect_git_diff.ps1)                 → ai/reviews/diff-after-*.md
  → The reviewer sub-agent reviews the diff (/ai-review-diff → adversarial-reviewer)  → ai/reviews/diff-review-*.md
  → Claude produces the final synthesis     (final-integrator)                     → READY / NEEDS_FIXES / BLOCKED
```

## Lanes

`/ai-plan` picks a lane and records it as `Lane: full` / `Lane: lean` on the
plan's first line after the title; the later commands read it. Both lanes keep
every quality gate: plan review, diff review, pre-commit-verifier green.

| | **full** | **lean** |
| --- | --- | --- |
| When | Everything not lean, and always: `moralstack/**`, any `tests/**` outside `tests/harness/**`, a persisted payload or public API, a PROJECT_SPEC §5 invariant (including its text in `.claude/rules/**`, `PROJECT_SPEC.md`, `CLAUDE.md`), two or more viable designs. When in doubt, full. | Every touched file is on the lean allowlist below and there is a single viable design |
| Mapping / tests design | `codebase-cartographer` + `test-strategist` | the `architect-planner` alone |
| Reviewer effort | `xhigh` (frontmatter) | `high` (per-invocation `effort`) |
| Final synthesis | `final-integrator` | the orchestrator, same structure and evidence rules |

**Lean allowlist** (the only files a lean change may touch):
`.claude/agents/**`, `.claude/commands/**`, `.claude/hooks/**` except the guards,
`scripts/ai/**`, `scripts/setup_env.ps1`, `tests/harness/**`, `docs/ai/**`,
`ai/prompts/*-template.md`, plus the `CHANGELOG.md` entry that goes with the
change. Anything else is full, including the harness's own enforcement and
secret/release handling — `.claude/hooks/guard_*.py`, `.claude/settings*.json`
(hook wiring, permissions), `.claude/skills/**`, `scripts/check_*_updated.py`
(commit gates) — and the rest of `scripts/**` (benchmarks, `mstack_run.py`,
COMPL-AI probes), `examples/**`, and every other `docs/**`.

The lane is checked, not trusted: the review commands pick the reviewer's effort
from the files themselves (the plan's "Files to modify", the diff's changed
files), not from the `Lane:` line, and the reviewer classifies a lean plan or diff
that touches a file off the allowlist as BLOCKING.

Rules for both lanes, from the measured cost of past tracks (implementer
contexts of 250-370k tokens, the plan re-read 3-6 times per run, sub-agent prompt
cache of 5 minutes):

- All user decisions in one question round, before planning.
- Plan at or under ~20 KB; revisions edited in place plus a short "Revision N"
  changelog; nothing that affects
  correctness is cut, background goes to "Follow-ups".
- Review loops: after a second re-review that still has BLOCKING items, escalate
  to the user — never drop or downgrade a blocker to end the loop.
- One self-contained handoff per coherent slice, plan sections copied verbatim.
- Fix passes run a new implementer on a fix handoff; resume the old one only
  within 5 minutes of its last activity.
- The diff review and the pre-commit-verifier may run in parallel; if the
  verifier edited the tree (fixes applied, or `git status --short` differs from
  the reviewed snapshot), the diff is re-collected and the delta re-reviewed
  before finalize.

### Direct lane (no planner, no implementer sub-agent)

The orchestrator edits directly, then the change gets one independent diff review.
It exists because the planner → handoff → implementer chain costs more than it
catches on small changes (measured: implementer contexts of 250-370k tokens, half a
day for three harness fixes).

- **When**: every touched file is on the lean allowlist or under `docs/**`, plus its
  `CHANGELOG.md` entry, and there is a single obvious design.
- **Never direct** (use `/ai-plan`): `moralstack/**`, any `tests/**` outside
  `tests/harness/**`, a persisted payload or public API, a PROJECT_SPEC §5 invariant
  or its text (`.claude/rules/**`, `PROJECT_SPEC.md`, `CLAUDE.md`), the harness's own
  enforcement (`.claude/hooks/guard_*.py`, `.claude/settings*.json`,
  `scripts/check_*_updated.py`), two or more viable designs. When in doubt, `/ai-plan`.
- **Steps**: (1) write a short intent note `ai/plans/<slug>.md` — title, `Lane: direct`,
  goal, files, verification (no design sections); (2) edit, run the scoped tests, then
  the pre-commit-verifier gate; (3) `/ai-review-diff ai/plans/<slug>.md` — the
  reviewer judges the diff against the intent note, BLOCKING findings are fixed
  directly and the delta re-reviewed, and a second re-review that still blocks is
  escalated to the user; (4) the orchestrator writes the final synthesis with the
  `/ai-finalize` structure and evidence rules.
- The diff review, the pre-commit-verifier gate and the escalation rule are never
  skipped. The lane is checked like the others: a direct diff that touches a
  never-direct path is BLOCKING.

## Claude commands (slash commands)

| Command | Does |
| --- | --- |
| `/ai-plan <request>` | Map the area, plan the change, design tests → `ai/plans/<slug>.md` |
| `/ai-review-plan <plan>` | The `adversarial-reviewer` sub-agent reviews the plan; Claude integrates blocking feedback |
| `/ai-implement <plan>` | Build the handoff, run the Claude Sonnet implementer sub-agent, collect the diff |
| `/ai-review-diff <plan>` | Collect the diff, the `adversarial-reviewer` sub-agent reviews it vs the plan |
| `/ai-finalize <plan>` | Synthesize everything into a final status |

Every command delegates to a sub-agent under `.claude/agents/` through the
`Agent` tool (renamed from `Task` in Claude Code 2.1.63 per the sub-agent docs,
code.claude.com/docs/en/sub-agents; in 2.1.272 `Task` still resolves through a
legacy alias table read in the binary, but it is not canonical, so `allowed-tools`
lines and the tracked `settings*.json` allowlists say `Agent`): `codebase-cartographer`,
`architect-planner`, `test-strategist`, `adversarial-reviewer`,
`claude-implementer`, `final-integrator`. `/ai-review-plan` and
`/ai-review-diff` run inline in the main context to compose the review request
and save the artifacts, and launch `adversarial-reviewer` for the judgment
itself — see `docs/ai/REVIEW_GUIDE.md`. The **implementation** step
(`/ai-implement`) likewise runs inline in the orchestrator: it writes the
handoff, launches the `claude-implementer` Sonnet sub-agent, and verifies the
diff — there is no external implementer CLI and no external reviewer CLI. The
existing **pre-commit-verifier** agent runs the full suite through
`scripts/ai/suite_record.py` (`check`, then `run` on mismatch) + `pre-commit run -a`
before anything is declared READY.

## Code intelligence (prerequisite for the cartographer)

`.claude/settings.json` enables the official `pyright-lsp@claude-plugins-official`
plugin, which gives Claude Code's `LSP` tool (definitions, `incomingCalls`, symbols)
for Python. The plugin does not ship the language server: install it once per
machine, globally, so `pyright-langserver` is on the PATH of the shell that starts
`claude` (`npm install -g pyright`; check with `pyright --version`). Plugins load at
session start, so restart Claude Code after installing. Without the binary, per the
Claude Code plugin docs (not observed on this machine), the plugin shows
`Executable not found in $PATH` under `/plugin` → Errors; the `codebase-cartographer`
is told to fall back to Grep and say so in its map's first line.

Measured on three past tracks (a1, d10-h4, DCCL UI) against the files their commits
actually changed: the locator cartographer plus the verbatim
`scripts/ai/impact_for_paths.py` output took 2.4-3.7 min instead of 5.1-13.1, at about
$0.04 a run, and found code 10/12 (same as before), tests 9/9 (same) and docs 16/18
(before: 8/18). The test and doc lists come from the script, not from the model: Haiku
dropped or globbed them when left to it. Sonnet gave the same recall in the same time at
~17x the cost, so the cartographer stays on Haiku. `findReferences` under-reports in this
repo; the cartographer uses `incomingCalls`, plus Grep for dynamic dispatch, templates and
test patches.

## Supporting scripts (`scripts/ai/`, PowerShell primary, `.sh` equivalents)

| Script | Does |
| --- | --- |
| `detect_python_quality_commands.ps1` | Report this repo's real test/lint/format/typecheck commands |
| `collect_git_diff.ps1` | Save the working-tree diff to `ai/reviews/` (never commits) |
| `suite_record.py` | `run`/`check`: record the full-suite result per tree state and reuse a PASSED one (`.claude/.suite-result.json`) |
| `impact_for_paths.py` | `[--rev REV] PATH...`: read-only, deterministic list of the tests that import/patch/re-import each module (or name each other file) and the docs it maps to (memory-guard map, `file:line` anchors, ledgers); used by the `codebase-cartographer` |

Neither implementation nor review is invoked by a bespoke launcher script in this
repo: `/ai-implement` uses the native `claude-implementer` sub-agent, and
`/ai-review-plan` / `/ai-review-diff` use the native `adversarial-reviewer`
sub-agent.

## Worked example

```powershell
# 1. Plan
/ai-plan "make the proxy honor OPENAI_MODEL override for streaming responses"

# 2. Review the plan with the isolated reviewer sub-agent (independent)
/ai-review-plan ai/plans/proxy-openai-model-streaming.md
#    → if BLOCK, Claude revises the plan; re-run until APPROVE/APPROVE_WITH_CHANGES
#      (still BLOCKING after the second re-review → escalate to the user)

# 3. Implement with the Claude Sonnet sub-agent
/ai-implement ai/plans/proxy-openai-model-streaming.md
#    → ai/handoffs/...-handoff.md, claude-implementer runs, diff collected

# 4. Review the diff with the isolated reviewer sub-agent
/ai-review-diff ai/plans/proxy-openai-model-streaming.md

# 5. Verify + finalize
#    (run the pre-commit-verifier agent), then:
/ai-finalize ai/plans/proxy-openai-model-streaming.md
```

Both the implementation and the review steps run inside Claude Code — there is
no script-direct equivalent to invoke by hand. `/ai-implement` drives the
`claude-implementer` sub-agent; the `/ai-review-*` commands drive the
`adversarial-reviewer` sub-agent.

## Guard rails (inherited from the repo)

- No `git push`, no auto-commit, no destructive git in any script.
  `guard_dangerous_git.py` (PreToolUse) and `guard_secrets.py` enforce this.
- The `claude-implementer` sub-agent runs inside Claude Code, so its
  `Edit`/`Write`/`Bash` calls pass through those same `PreToolUse` guards; it
  edits only **allowed** files from the handoff, and the orchestrator flags any
  out-of-scope edit or HEAD move.
- The `adversarial-reviewer` sub-agent is read-only in two layers of unequal
  strength: file edits are blocked **structurally** (its frontmatter grants
  `Read, Grep, Glob, Bash` only — no `Edit`/`Write`), while `Bash` — which is
  write-capable — is constrained by **instruction** (its body forbids every
  mutating command) plus the same `PreToolUse` guards (`guard_dangerous_git.py`,
  `guard_secrets.py`). This is weaker than a sandbox: a `rm`, `sed -i` or
  redirection is forbidden, not impossible.
- The reviewer's independence is auditable: every review starts with a
  `Reviewer model: <id>` line, and the commands refuse to pass a `model:`
  override that would collapse the separation from the implementer.
- See `docs/ai/REVIEW_POLICY.md`, `docs/ai/REVIEW_GUIDE.md`,
  `docs/ai/CLAUDE_IMPLEMENTATION_GUIDE.md`, `docs/ai/INVARIANTS.md`,
  `docs/ai/ARCHITECTURE_MAP.md`.
