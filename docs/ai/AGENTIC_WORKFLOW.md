# MoralStack agentic workflow — Claude orchestrates, Codex reviews, Cursor implements

Three CLIs, coordinated, for working on this large Python codebase:

| Tool | Role | Never does |
| --- | --- | --- |
| **Claude Code** | Orchestrator: analyzes the codebase, writes plans, prepares handoffs, integrates reviews | Be the final reviewer; implement application features (unless you explicitly ask) |
| **Codex** (via the official Claude Code plugin) | Independent reviewer of plans and diffs | Implement the change |
| **Cursor CLI** (`cursor-agent`) | Headless implementer of approved plans | Commit, push, refactor out of scope |

## The loop

```
USER REQUEST
  → Claude analyzes the codebase            (codebase-cartographer)
  → Claude produces a technical plan        (architect-planner + test-strategist) → ai/plans/<task>.md
  → Codex reviews the plan                  (/ai-review-plan-with-codex → codex:rescue) → ai/reviews/codex-plan-review-*.md
  → Claude integrates blocking feedback     (revises ai/plans/<task>.md)
  → Claude produces a Cursor handoff        (cursor-cli-implementation-coordinator) → ai/handoffs/<task>-cursor-cli-handoff.md
  → Cursor CLI implements                   (run_cursor_implementation.ps1)        → code edits + log + diff
  → Claude collects the diff                (collect_git_diff.ps1)                 → ai/reviews/diff-after-cursor-*.md
  → Codex reviews the diff                  (/ai-review-diff-with-codex → codex:rescue) → ai/reviews/codex-diff-review-*.md
  → Claude produces the final synthesis     (final-integrator)                     → READY / NEEDS_FIXES / BLOCKED
```

## Claude commands (slash commands)

| Command | Does |
| --- | --- |
| `/ai-plan <request>` | Map the area, plan the change, design tests → `ai/plans/<slug>.md` |
| `/ai-review-plan-with-codex <plan>` | Codex reviews the plan; Claude integrates blocking feedback |
| `/ai-implement-with-cursor <plan>` | Build the handoff, run Cursor CLI headless, collect log + diff |
| `/ai-review-diff-with-codex <plan>` | Collect the diff, Codex reviews it vs the plan |
| `/ai-finalize <plan>` | Synthesize everything into a final status |

`/ai-review-plan-with-codex` and `/ai-review-diff-with-codex` run inline (not
via a coordinator subagent) and call Codex through the official **OpenAI Codex
Claude Code plugin** (`github.com/openai/codex-plugin-cc`, installed via
`/plugin install codex@openai-codex`), using its `codex:rescue` entry point —
see `docs/ai/CODEX_REVIEW_GUIDE.md`. The other commands still delegate to a
subagent under `.claude/agents/`: `codebase-cartographer`, `architect-planner`,
`test-strategist`, `cursor-cli-implementation-coordinator`, `final-integrator`.
The existing **pre-commit-verifier** agent runs the full `python -m pytest` +
`pre-commit run -a` gate before anything is declared READY.

## Supporting scripts (`scripts/ai/`, PowerShell primary, `.sh` equivalents)

| Script | Does |
| --- | --- |
| `detect_python_quality_commands.ps1` | Report this repo's real test/lint/format/typecheck commands |
| `collect_git_diff.ps1` | Save the working-tree diff to `ai/reviews/` (never commits) |
| `run_cursor_implementation.ps1` | Run Cursor CLI headless on a handoff; capture log + diff |

Codex is no longer invoked by a script in this repo — `/ai-review-plan-with-codex`
and `/ai-review-diff-with-codex` call the Codex Claude Code plugin's own
`codex:rescue` skill directly.

Config via environment variables:
- `CURSOR_CMD` (default: auto-resolve `cursor-agent`), `CURSOR_MODEL` (default `auto`).

## Worked example

```powershell
# 1. Plan
/ai-plan "make the proxy honor OPENAI_MODEL override for streaming responses"

# 2. Review the plan with Codex (independent)
/ai-review-plan-with-codex ai/plans/proxy-openai-model-streaming.md
#    → if BLOCK, Claude revises the plan; re-run until APPROVE/APPROVE_WITH_CHANGES

# 3. Implement with Cursor CLI (headless)
/ai-implement-with-cursor ai/plans/proxy-openai-model-streaming.md
#    → ai/handoffs/...-cursor-cli-handoff.md, cursor-agent runs, diff collected

# 4. Review the diff with Codex
/ai-review-diff-with-codex ai/plans/proxy-openai-model-streaming.md

# 5. Verify + finalize
#    (run the pre-commit-verifier agent), then:
/ai-finalize ai/plans/proxy-openai-model-streaming.md
```

Manual (script-direct) equivalent for the Cursor implementation step (Codex
review has no script anymore — it always goes through the `/ai-review-*-with-codex`
commands, which call the Codex plugin directly):

```powershell
pwsh scripts/ai/run_cursor_implementation.ps1 -HandoffPath ai/handoffs/<task>-cursor-cli-handoff.md
```

## Guard rails (inherited from the repo)

- No `git push`, no auto-commit, no destructive git in any script.
  `guard_dangerous_git.py` (PreToolUse) and `guard_secrets.py` enforce this.
- Cursor CLI runs only on **allowed** files from the handoff; the coordinator
  flags any out-of-scope edit or HEAD move.
- Codex runs in a **read-only** sandbox: it can inspect but never mutate the
  repo (the `/ai-review-*-with-codex` commands never add `--write` to `codex:rescue`).
- See `docs/ai/REVIEW_POLICY.md`, `docs/ai/CODEX_REVIEW_GUIDE.md`,
  `docs/ai/CURSOR_CLI_IMPLEMENTATION_GUIDE.md`, `docs/ai/INVARIANTS.md`,
  `docs/ai/ARCHITECTURE_MAP.md`.
