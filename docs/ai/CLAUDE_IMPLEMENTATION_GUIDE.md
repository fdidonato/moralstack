# Claude implementation guide

A Claude **Sonnet** sub-agent is the **implementer**. It receives an approved
plan as a handoff and edits the code in an isolated sub-agent context. The
orchestrator (the main Claude running `/ai-implement`) writes the handoff and
verifies the result; it does not write the feature code itself.

## Why a sub-agent (not the orchestrator, not an external CLI)

The plan→implement→review loop keeps the roles separate on purpose. The
implementer runs as a fresh **Claude Sonnet** sub-agent (`claude-implementer`,
`.claude/agents/claude-implementer.md`, `model: sonnet`), so:

- the orchestrator that wrote the plan is not the one that implements it (the
  handoff stays an arms-length contract, useful for the audit trail);
- there is no external CLI, no PowerShell runner, and no hook-suspension hack —
  the sub-agent runs inside Claude Code and its `Edit`/`Write`/`Bash` calls go
  through the repo's own `PreToolUse` guards (`guard_secrets.py`,
  `guard_dangerous_git.py`) like any other Claude tool call.

## How it runs (via `/ai-implement <plan>`)

1. The orchestrator reads the plan and the matching Codex plan review; it stops
   if the review is `BLOCK` or has unresolved BLOCKING items.
2. It writes the handoff to `ai/handoffs/<slug>-handoff.md` from
   `ai/prompts/claude-implementation-template.md`.
3. It snapshots HEAD, then launches the implementer:
   `Agent(subagent_type: "claude-implementer", model: "sonnet", …)` pointing it
   at the handoff path. The sub-agent reads the handoff and implements only what
   it allows.
4. The sub-agent's report is saved to
   `ai/handoffs/<slug>-implementation-report.md`.
5. The orchestrator collects the post-run diff to
   `ai/reviews/diff-after-<slug>-<ts>.md` (`scripts/ai/collect_git_diff.ps1`),
   and warns if HEAD moved (the sub-agent must not commit).

It **never** commits, pushes, or deletes files outside the implementer's own
edits.

## Input: the handoff

Built by the orchestrator from `ai/prompts/claude-implementation-template.md`. It
must carry: context, objective, approved plan, **files allowed to modify**,
**files NOT to modify**, invariants, checklist, required tests, acceptance
criteria, risks, and the required output format.

## Expected output from the implementer

Files modified · tests added · commands run · real results · deviations from the
plan · residual problems. The orchestrator then verifies only allowed files
changed and flags any deviation.

## Limits and guard rails

- The sub-agent inherits the repo's `PreToolUse` guards, so secret exposure and
  destructive git are blocked at the tool boundary — not only forbidden in the
  handoff text.
- It still must not commit/push; the orchestrator verifies HEAD did not move and
  that only allowed files changed. The collected diff is reviewed by Codex and
  the orchestrator before any commit.
- If the implementer hits an ambiguity or a blocking architectural problem, it
  STOPs and reports the blocker instead of working around it.
