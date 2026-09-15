# MoralStack agentic workflow — Claude orchestrates, an isolated sub-agent reviews, a Claude Sonnet sub-agent implements

Coordinated roles for working on this large Python codebase:

| Role | Who | Never does |
| --- | --- | --- |
| **Orchestrator** | **Claude Code** (main context): analyzes the codebase, writes plans, prepares handoffs, integrates reviews | Be the final reviewer; write the feature code itself |
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
existing **pre-commit-verifier** agent runs the full `python -m pytest` +
`pre-commit run -a` gate before anything is declared READY.

## Supporting scripts (`scripts/ai/`, PowerShell primary, `.sh` equivalents)

| Script | Does |
| --- | --- |
| `detect_python_quality_commands.ps1` | Report this repo's real test/lint/format/typecheck commands |
| `collect_git_diff.ps1` | Save the working-tree diff to `ai/reviews/` (never commits) |

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
