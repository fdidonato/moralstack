# `.claude/hooks/` — harness hooks inventory

Every hook is **fail-open**: it parses the event JSON on stdin inside a
`try/except` and returns exit 0 on any error, so a harness bug can never wedge a
turn. They are plain Python (no third-party imports) and unit-tested in
`tests/harness/`. All resolve the repo via `CLAUDE_PROJECT_DIR` (falling back to
`cwd`).

| Hook | Event | Blocks? | Purpose |
| --- | --- | --- | --- |
| `guard_dangerous_git.py` | PreToolUse(Bash) | yes | Blocks destructive git (`--no-verify`, force-push, hard reset, …). |
| `guard_secrets.py` | PreToolUse(Bash/Edit/Write) | yes | Blocks secret exposure in commands/edits. |
| `format_on_edit.py` | PostToolUse(Edit/Write) | no | Records edited paths → `.session-edits.json`; ruff+black on the file. |
| `stop_gate.py` | Stop | docs-gate only | Non-blocking verify (deduped) + blocking docs-gate with nudge cap + docs stub. |
| `precompact_snapshot.py` | PreCompact (`async`) | no | Snapshots in-flight context → `.context-snapshot.md` before compaction. |
| `session_start.py` | SessionStart | no | Situational brief; re-injects `.context-snapshot.md` on resume/compact. |
| `session_end.py` | SessionEnd | no (can't) | Appends an UNVERIFIED session digest → `session-diary.md` (staging). |
| `user_prompt_submit.py` | UserPromptSubmit | no | On plan/context keywords, injects snapshot + active plans; silent otherwise. |
| `log_instructions.py` | InstructionsLoaded | no | Logs which instruction files loaded → `.instructions-loaded.log`. |

## Local marker files (all under `.claude/`, all gitignored)

| File | Written by | Read by | Meaning |
| --- | --- | --- | --- |
| `.session-edits.json` | `format_on_edit` | `stop_gate`, `precompact`, `session_end` | `{session_id, paths}` edited this session. |
| `.last-verified.json` | `stop_gate` | `stop_gate`, `precompact`, `session_end` | `{session_id, fingerprint, outcome}` — dedup key for verify. |
| `.nudge-count.json` | `stop_gate` | `stop_gate` | `{session_id, count}` — cross-chain docs-nudge cap. |
| `.docs-stub.md` | `stop_gate` | human/Claude | Touched-symbols → likely docs targets; review, promote, delete. |
| `.context-snapshot.md` | `precompact` | `session_start`, `user_prompt_submit` | Pre-compaction context digest. |
| `session-diary.md` | `session_end` | human | Append-only UNVERIFIED digests to promote (never a verified fact, §4). |

## Stop-gate specifics

- **Verify** runs `pre-commit --files <changed>` + scoped `pytest` only when the
  edit-set actually changed since the last passing run (content fingerprint) and
  `stop_hook_active` is False. `MSTACK_STOP_RUN_PYTEST=1` forces the full suite.
- **Docs-gate** blocks when governance behavior files change without a matching
  docs/tests edit, at most `MSTACK_DOCS_NUDGE_CAP` (default 1) times per session.
  The full behavior→docs mapping lives in `.claude/rules/docs-maintenance.md`.

## Registration

All wired in `.claude/settings.json`. `precompact_snapshot` is registered
`async: true` so it never delays compaction (it reads the transcript from disk).
Adding a hook: drop the script here, register it in `settings.json`, keep it
fail-open, and add a `tests/harness/` test.
