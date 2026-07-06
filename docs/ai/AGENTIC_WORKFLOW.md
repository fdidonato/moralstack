# MoralStack agentic workflow — Claude orchestrates, Codex reviews, a Claude Sonnet sub-agent implements

Coordinated roles for working on this large Python codebase:

| Role | Who | Never does |
| --- | --- | --- |
| **Orchestrator** | **Claude Code** (main context): analyzes the codebase, writes plans, prepares handoffs, integrates reviews | Be the final reviewer; write the feature code itself |
| **Reviewer** | **Codex** (via the official Claude Code plugin): independent reviewer of plans and diffs | Implement the change |
| **Implementer** | **Claude Sonnet sub-agent** (`claude-implementer`, `.claude/agents/claude-implementer.md`): headless implementer of approved plans in an isolated context | Commit, push, refactor out of scope |

## The loop

```
USER REQUEST
  → Claude analyzes the codebase            (codebase-cartographer)
  → Claude produces a technical plan        (architect-planner + test-strategist) → ai/plans/<task>.md
  → Codex reviews the plan                  (/ai-review-plan-with-codex → codex:rescue) → ai/reviews/codex-plan-review-*.md
  → Claude integrates blocking feedback     (revises ai/plans/<task>.md)
  → Claude produces the handoff             (/ai-implement orchestrator)           → ai/handoffs/<task>-handoff.md
  → Claude Sonnet sub-agent implements      (claude-implementer)                   → code edits + implementation report
  → Claude collects the diff                (collect_git_diff.ps1)                 → ai/reviews/diff-after-*.md
  → Codex reviews the diff                  (/ai-review-diff-with-codex → codex:rescue) → ai/reviews/codex-diff-review-*.md
  → Claude produces the final synthesis     (final-integrator)                     → READY / NEEDS_FIXES / BLOCKED
```

## Claude commands (slash commands)

| Command | Does |
| --- | --- |
| `/ai-plan <request>` | Map the area, plan the change, design tests → `ai/plans/<slug>.md` |
| `/ai-review-plan-with-codex <plan>` | Codex reviews the plan; Claude integrates blocking feedback |
| `/ai-implement <plan>` | Build the handoff, run the Claude Sonnet implementer sub-agent, collect the diff |
| `/ai-review-diff-with-codex <plan>` | Collect the diff, Codex reviews it vs the plan |
| `/ai-finalize <plan>` | Synthesize everything into a final status |

`/ai-review-plan-with-codex` and `/ai-review-diff-with-codex` run inline (not
via a coordinator subagent) and call Codex through the official **OpenAI Codex
Claude Code plugin** (`github.com/openai/codex-plugin-cc`, installed via
`/plugin install codex@openai-codex`), using its `codex:rescue` entry point —
see `docs/ai/CODEX_REVIEW_GUIDE.md`. The other commands still delegate to a
subagent under `.claude/agents/`: `codebase-cartographer`, `architect-planner`,
`test-strategist`, `final-integrator`. The **implementation** step
(`/ai-implement`) runs inline in the main Claude context (the orchestrator): it
writes the handoff, launches the `claude-implementer` Sonnet sub-agent, and
verifies the diff — there is no external implementer CLI. The existing
**pre-commit-verifier** agent runs the full `python -m pytest` +
`pre-commit run -a` gate before anything is declared READY.

## Supporting scripts (`scripts/ai/`, PowerShell primary, `.sh` equivalents)

| Script | Does |
| --- | --- |
| `detect_python_quality_commands.ps1` | Report this repo's real test/lint/format/typecheck commands |
| `collect_git_diff.ps1` | Save the working-tree diff to `ai/reviews/` (never commits) |

Neither implementation nor review is invoked by a bespoke launcher script in this
repo: `/ai-implement` uses the native `claude-implementer` sub-agent, and
`/ai-review-plan-with-codex` / `/ai-review-diff-with-codex` call the Codex Claude
Code plugin's own `codex:rescue` skill directly.

## Worked example

```powershell
# 1. Plan
/ai-plan "make the proxy honor OPENAI_MODEL override for streaming responses"

# 2. Review the plan with Codex (independent)
/ai-review-plan-with-codex ai/plans/proxy-openai-model-streaming.md
#    → if BLOCK, Claude revises the plan; re-run until APPROVE/APPROVE_WITH_CHANGES

# 3. Implement with the Claude Sonnet sub-agent
/ai-implement ai/plans/proxy-openai-model-streaming.md
#    → ai/handoffs/...-handoff.md, claude-implementer runs, diff collected

# 4. Review the diff with Codex
/ai-review-diff-with-codex ai/plans/proxy-openai-model-streaming.md

# 5. Verify + finalize
#    (run the pre-commit-verifier agent), then:
/ai-finalize ai/plans/proxy-openai-model-streaming.md
```

Both the implementation and the review steps run inside Claude Code now — there
is no script-direct equivalent to invoke by hand. `/ai-implement` drives the
`claude-implementer` sub-agent; the `/ai-review-*-with-codex` commands call the
Codex plugin directly.

## Guard rails (inherited from the repo)

- No `git push`, no auto-commit, no destructive git in any script.
  `guard_dangerous_git.py` (PreToolUse) and `guard_secrets.py` enforce this.
- The `claude-implementer` sub-agent runs inside Claude Code, so its
  `Edit`/`Write`/`Bash` calls pass through those same `PreToolUse` guards; it
  edits only **allowed** files from the handoff, and the orchestrator flags any
  out-of-scope edit or HEAD move.
- Codex runs in a **read-only** sandbox: it can inspect but never mutate the
  repo (the `/ai-review-*-with-codex` commands never add `--write` to `codex:rescue`).
- See `docs/ai/REVIEW_POLICY.md`, `docs/ai/CODEX_REVIEW_GUIDE.md`,
  `docs/ai/CLAUDE_IMPLEMENTATION_GUIDE.md`, `docs/ai/INVARIANTS.md`,
  `docs/ai/ARCHITECTURE_MAP.md`.
