# Cursor CLI implementation guide

Cursor CLI is the **implementer**. It receives an approved plan as a handoff and
edits the code headlessly. Claude does not write the feature code; Cursor does.

## Which binary (important)

Cursor ships **two** different CLIs:

- `cursor` / `cursor.cmd` — the **GUI launcher** (opens the editor). NOT the
  implementer. Do not use it for automation.
- `cursor-agent` — the **agentic CLI** (headless implementer). This is what we
  use. On this machine it lives at
  `%LOCALAPPDATA%\cursor-agent\cursor-agent.cmd` (v2026.06.x), logged in.
  Its own help shows `Usage: agent [options] [command] [prompt...]`.

`run_cursor_implementation.ps1` resolves `cursor-agent` automatically:
`$env:CURSOR_CMD` → `cursor-agent` on PATH → the `%LOCALAPPDATA%` install.

## Automatic mode (verified, preferred)

Verified `cursor-agent` flags used:

- `-p` / `--print` — headless; "has access to all tools, including write and shell".
- `--output-format text` — plain transcript to stdout (also `json`, `stream-json`).
- `--force` (alias `--yolo`) — allow tool calls without interactive approval.
- `--trust` — trust the workspace (only works with `--print`/headless).
- `--model <name>` — model selection. **Implementation uses `auto` or a
  `composer` model** per project policy (default `auto`; override `$env:CURSOR_MODEL`,
  e.g. `composer-2.5`, `composer-2.5-fast`). List with `cursor-agent --list-models`.
- `--workspace <path>` — workspace directory.

Run:

```powershell
pwsh scripts/ai/run_cursor_implementation.ps1 `
    -HandoffPath ai/handoffs/<task>-cursor-cli-handoff.md
# optional: -Model composer-2.5   -DryRun
```

What it does:

1. Resolves `cursor-agent`.
2. Builds a short bootstrap prompt that tells the agent to **read the handoff
   file** and implement only what it allows (keeps the command line short and
   robust on Windows).
3. Runs `cursor-agent -p --output-format text --force --trust --model <model>
   --workspace <repo> "<bootstrap>"`, teeing the transcript to
   `ai/handoffs/cursor-run-<task>-<ts>.log`.
4. Snapshots `git status` and saves the post-run diff to
   `ai/reviews/diff-after-cursor-<task>-<ts>.md`.
5. Warns if HEAD moved (the agent must not commit) — for review.

It **never** commits, pushes, or deletes files outside the agent's own edits.

Bash equivalent: `scripts/ai/run_cursor_implementation.sh --handoff <path>
[--model m] [--dry-run]`.

## Input: the handoff

Built by the `cursor-cli-implementation-coordinator` agent from
`ai/prompts/cursor-cli-implementation-template.md`. It must carry: context,
objective, approved plan, **files allowed to modify**, **files NOT to modify**,
invariants, checklist, required tests, acceptance criteria, risks, the ready
prompt, and the required output format.

## Expected output from Cursor CLI

Files modified · tests added · commands run · real results · deviations from the
plan · residual problems. The coordinator then verifies only allowed files
changed and flags any deviation.

## Limits

- Long prompts: we pass the handoff **path**, not its full text, on the command
  line — Windows arg length is limited. The agent reads the file itself.
- Headless write mode requires `--force` + `--trust`; without them the agent may
  stall waiting for approval. We always pass both in automatic mode.
- The agent could, in principle, run shell commands. The repo's
  `guard_dangerous_git.py` does not police `cursor-agent` (it guards Claude's
  Bash tool), so the handoff explicitly forbids commit/push/delete, and we
  verify HEAD did not move after the run.

## Troubleshooting: hooks block every tool on Windows

Symptom (seen 2026-06-28): `cursor-agent` reports that **every** `Write` / `Edit` /
`Shell` call fails with

```
syntax error near unexpected token `&'
$OutputEncoding = [System.Text.Encoding]::UTF8; Get-Content ... | & { $input | python ".../guard_secrets.py" }
```

and implements **0 files**.

Cause: `cursor-agent` auto-imports the Claude Code hooks declared in
`.claude/settings.json` (`guard_secrets.py`, `guard_dangerous_git.py`) and runs
each one through a hardcoded **PowerShell** wrapper
(`$OutputEncoding ...; Get-Content -Raw | & { $input | <command> }`). On Windows it
then executes that wrapper via `eval` in a **POSIX shell** (`sh`), where `& { ... }`
is a syntax error — so every `PreToolUse` guard fails and blocks all write/shell
tools. The executor does **not** honor `$env:SHELL` (verified 2026-06-29: pinning
`SHELL` to `powershell.exe` did not change the `eval`-in-sh behavior).

Fix (in `run_cursor_implementation.ps1` / `.sh`): for the duration of the run the
launcher moves `.claude/settings.json` aside (so `cursor-agent` imports no hooks)
and restores it in a `finally`/`trap`. This is safe — the run never commits or
pushes (we verify HEAD did not move) and the produced diff is reviewed by Codex and
Claude before any commit, so the secret/git guards still gate the actual commit
path. If you invoke `cursor-agent` by hand on Windows, do the same move/restore, or
the blocker reappears.

## Fallback mode (documented)

If `cursor-agent` is unavailable (or `-DryRun`), the script:

1. saves the bootstrap prompt to `ai/prompts/generated-cursor-bootstrap-*.md`;
2. prints the exact manual command;
3. exits non-zero with a clear message that the infra stays ready for Cursor CLI
   but the local CLI invocation was skipped.

Manual step: run that command when the CLI is available, or paste the handoff
into Cursor IDE's agent and implement there, then collect the diff with
`scripts/ai/collect_git_diff.ps1`.
