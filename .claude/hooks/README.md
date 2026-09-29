# `.claude/hooks/` — harness hooks inventory

Every hook is **fail-open**: it parses the event JSON on stdin inside a
`try/except` and returns exit 0 on any error, so a harness bug can never wedge a
turn. They are plain Python (no third-party imports) and unit-tested in
`tests/harness/`. All resolve the repo via `CLAUDE_PROJECT_DIR` (falling back to
`cwd`).

| Hook | Event | Blocks? | Purpose |
| --- | --- | --- | --- |
| `guard_dangerous_git.py` | PreToolUse(Bash) | yes | Blocks git shortcuts forbidden by §9: no-verify (long and `-n`), no-gpg-sign, force-push (all forms, with-lease included), removal of test files/dirs. Does **not** block a hard `git reset` (destructive-but-confirmable; the user's confirmation rule applies, and a hook cannot ask). |
| `guard_secrets.py` | PreToolUse(Bash/Edit/Write) | yes | Blocks secret exposure in commands/edits. |
| `format_on_edit.py` | PostToolUse(Edit/Write/MultiEdit) | no | Records edited paths → `.session-edits.json`; ruff+black on the file. |
| `stop_gate.py` | Stop | docs-gate only | Non-blocking verify (deduped) + blocking docs-gate with nudge cap + docs stub. |
| `precompact_snapshot.py` | PreCompact (`async`) | no | Snapshots in-flight context → `.context-snapshot.md` before compaction. |
| `session_start.py` | SessionStart | no | Situational brief; re-injects `.context-snapshot.md` on resume/compact. |
| `session_end.py` | SessionEnd | no (can't) | Appends an UNVERIFIED session digest → `session-diary.md` (staging). |
| `user_prompt_submit.py` | UserPromptSubmit | no | On plan/context keywords, injects the snapshot (only if written in the last 24 h) + active plans; silent otherwise. |
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

- **Verify** runs `pre-commit run --files <changed>` (150 s budget under the 300 s
  Stop timeout) only when the edit-set changed since the last passing run (content
  fingerprint) and `stop_hook_active` is False. It runs **no tests** and says so in
  every report — tests are the `pre-commit-verifier` agent's job.
- **Docs-gate** blocks when governance behavior files change without touching a
  **verified-memory ledger** (`docs/CODEBASE_FACTS.md`,
  `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/traces/`, `docs/modules/`), at most
  `MSTACK_DOCS_NUDGE_CAP` (default 1) times per session. A **test does NOT** satisfy
  it, and an arbitrary `docs/` file does not either. The full behavior→docs mapping
  lives in `.claude/rules/docs-maintenance.md`.
- This gate is a **best-effort session nudge** (keyed on `.session-edits.json`, which
  misses Bash edits and never resets mid-session). The hard, non-bypassable guarantee
  lives at **commit time** in `scripts/check_memory_updated.py` (a pre-commit hook,
  fine per-prefix mapping, source of truth `git diff --cached`; bypass
  `MEMORY_GUARD_SKIP=1`). `scripts/check_changelog_updated.py` is the sibling
  changelog gate.

## Interpreter & shell prerequisites

Every command hook in `settings.json` is registered with `"shell": "bash"` and a
POSIX wrapper, not a bare `python "$CLAUDE_PROJECT_DIR/..."` call:

```
sh -c 'for p in python python3; do r=$(command -v "$p" 2>/dev/null) || continue; case "$r" in "") continue;; */WindowsApps/*) "$r" -c "" >/dev/null 2>&1 || continue;; esac; exec "$r" "$0"; done; echo "[hook] $0: no working python or python3 on PATH (see .claude/hooks/README.md)" >&2; exit {code}' "$CLAUDE_PROJECT_DIR/.claude/hooks/{script}"
```

- Order is **`python` then `python3`**: on this project's Windows hosts,
  `command -v python3` resolves to the Microsoft Store alias stub
  (`.../WindowsApps/python3.exe`), which prints an error and exits 49; `python`
  resolves to the real interpreter. Trying `python` first avoids bricking every
  hook on such a host.
- A resolution landing under `*/WindowsApps/*` is not skipped blindly — it is
  **probed** with `"$r" -c ""` first, because a Store-*installed* real Python also
  lives under that path. Only the stub (which fails the probe) is skipped.
- `{code}` is **2** for the two `PreToolUse` guards (`guard_dangerous_git.py`,
  `guard_secrets.py`) — a missing interpreter blocks the tool call — and **1** for
  every other hook — a missing interpreter is a visible non-blocking error. An
  exit 2 on a hook with no blocking semantics (e.g. `stop_gate.py`) could re-wake
  Claude in a loop, which exit 1 cannot.
- The wrapper uses the first `python`/`python3` found on the **hook's own PATH**
  (as resolved by the shell Claude Code spawns), **not** the project's `venv`.
- `shell: "bash"` is required: without it the fallback shell is PowerShell, which
  parses the bash snippet with PowerShell semantics and reads `$CLAUDE_PROJECT_DIR`
  as `$null`. Git Bash is therefore a **prerequisite on Windows** (already true
  before this change, since `$CLAUDE_PROJECT_DIR` substitution only worked when a
  shell ran it). Claude Code **≥ 2.1.270** is required for the `shell` field
  itself; verify after any Claude Code upgrade with a restart smoke: a guard
  still blocks a forbidden command and a normal command still passes (last
  smoke: 2.1.283 on 2026-09-27 — forced push blocked by the wrapped guard,
  `git status` passed).
- **Platform limits the wrapper cannot close:** if Claude Code cannot spawn a
  shell at all (Windows with no Git Bash), the hook is reported as a non-blocking
  error and the guards **cannot** fail closed — this is a Claude Code limit, not
  something the wrapper can fix. If the resolved interpreter is found but `exec`
  itself fails (permissions, a broken shim), `sh` exits 126/127 → also reported as
  a non-blocking error → the guards fail **open** for that one call.

## Secrets: what is deterministic and what is declarative

- **`guard_secrets` (Bash) is the deterministic guarantee.** Its exact guarantee,
  stated precisely because the precise wording matters: **no single Bash command
  *names* a secret path outside the `ls`/`stat`/`test`/`echo`/`printf` exemption,
  and that exemption holds only when every segment of the command is a simple
  command running one of those programs by its bare name, as the first
  blank-delimited word of the segment** — no function
  definition (`ls() { cat "$@"; }; ls .env` rebinds `ls` and is blocked) — with
  no substitution, pipe, variable relay (`printf -v`, `=~`, `BASH_REMATCH`,
  `$_`, `exec`) and no output
  redirect to anything readable back (`/dev/null`, an fd, or a lone
  `>> .gitignore` are fine). Name detection is **textual**: the scanner matches
  the literal name after the normalisations it models — quote deletion,
  backslash removal, glob metacharacters removed, `$IFS` read as a blank, and
  parameter expansions (`$name`, non-nested `${…}`, positional/special
  parameters) removed. Any other shell spelling that produces the name at run
  time — ANSI-C or locale quoting, a decoded name, a `${X:-.env}` default, a
  nested `${…}` glued to the name (`cat .env${x:-${y}}`), a glob the scanner
  cannot expand, … — is a **documented residual class, not a defect**; the residual
  list below is illustrative, not exhaustive. This is **not** the same as "no
  single Bash command can read one" and **not** "no secret value can be
  printed" — see the residuals below.
- **`permissions.deny` is defence in depth**, not the primary control:
  `Bash(cat .env*)` / `Bash(type .env*)` (wildcards — they also match
  `.env.template`, which the Read tool still serves) plus the four pre-existing
  `Read(.env…)` entries.
- **The `Read` tool has no `guard_secrets` hook** — by decision (2026-09-18, Q4).
  For `Read`, the enumerated deny list above is the **only** control; a variant
  not on that list (`.env.test`, `.env.staging`, `.env.prod`, `.env.backup`, …) is
  not denied. Hooking `Read` is deferred to a later lot.
- Both guards scan the **raw Bash command text**, heredocs included — write files
  that quote forbidden git flags or a secret path with the Write/Edit tools, not a
  Bash heredoc.

**Known over-blocks** (a stderr message asking the user to confirm/rephrase; no
data effect): `grep -n .env .gitignore` and `rg '\.env' .` (the *pattern* word
looks like the path — write `rg 'env' --glob '!.env*'` or ask the user), `git log
-- .env`, `git check-ignore .env`, `find . -name .env` (name lookup only, but
`find` can `-exec`), `git commit -m 'chore: ignore .env'` (a commit message
naming the file — write "env file"), `LC_ALL=C ls .env` (revision 9: any
assignment prefix, brace expansion, quoting, escaping or grouping around the
program word voids the exemption too — only a bare safe name as the first
blank-delimited word of the segment counts, so `LC_ALL=C ls .env`,
`{ ls .env; }`, `(ls .env)` and `"ls" .env` / `'ls' .env` all block even though
bash runs the real, safe `ls` in every one; `\ls .env` / `l\s .env` were
already blocked under revision 8, before the structural rule existed — not a
revision-9 consequence), a safe-program command grouped in a **different**
segment of the same command (`[ -f .env ] && { echo yes; }`, `test -f .env ||
{ echo missing; }` — any `{ }` / `( )` anywhere in the command makes that
segment's program word `{`/`(`, which is neither safe nor transparent, so the
structural rule voids the exemption everywhere, including the segment that
actually names `.env`), `time ls .env` (`time` is not a transparent keyword), deleting
`.env` with `rm`, `ls .env | wc -l`, `ls .env > listing.txt`, `echo .env 2>
err.log` (a stderr file is readable back; only `/dev/null` and fd-dups are
discards), `ls .env && echo done > log.txt`, `echo .env >> .gitignore && git add
.gitignore` and `echo .env >> .gitignore; cat .gitignore` (the `.gitignore`
append is exempt only as a lone command — run the `git add` as a second call),
`echo .env > .gitignore` (overwrite is not appending), `while [ -f .env ]; do
sleep 1; done` and any other safe-program chain whose *other* segment runs
something outside the safe set (`test -f .env && python -m pytest` — split it
into two calls, or drop the `.env` test), `docker compose --env-file .env up`
(the space form of `--env-file=.env`), `case "$f" in .env) echo skip;; esac`
(`case`/`in` are neither safe nor transparent — only `esac` is), the
leading-redirect form `2>/dev/null ls .env` / `> log ls .env` (the program word
resolves to the redirect operator, not `ls`; write the redirect after the
program), a quoted or literal glue that bash would read as a *different*
name (`'$u'.env` — bash's real file is `$u.env`; `.env$$` — bash's real file
is `.env<pid>` — revision 10: the parameter-expansion strip cannot tell a
quoted literal `$u`/`$$` from an unquoted one, so it removes both and the
scanner sees `.env`), and the two word-boundary over-voids in `_EXEMPTION_VOID`: `\bexec\b`
also fires on file names containing `exec` (`exec.sh`, `exec-foo`, `exec/bar`)
and `\bprintf…-v\b` on any `printf … -v` in the segment, so a safe-program
command that also names `.env` next to such a word is blocked. `. file` /
`source file` and a path-qualified safe program (`./ls .env`, `/bin/ls .env`)
are never exempt — the exemption needs a bare safe name (revision 8). A
safe-program command that also contains an empty `()` pair anywhere in its
text (`echo "f()"; ls .env`) is blocked too — revision 7's function-definition
rule fires on any empty parens in the command, not only a genuine function
definition.

**Known residuals** (the guard cannot see these; documented, not a defect):
`cat .*` and `cat .en*` (any glob the scanner cannot expand, e.g. `cat
.[e-e]nv`), NTFS 8.3 short names on Windows (`cat ENV~1`), brace expansion `cat
.{env,x}`, **ANSI-C quoting in general** — not only hex escapes like `cat
$'.\x65nv'` but any `$'…'` splice, e.g. `cat $'.en'v`, `cat $'.'env`, `cat
$'.e'nv` (`'` is a word separator, so the quote-splice defence that catches `cat
.e'n'v` is defeated because the quote-deleted pass glues the ANSI-C `$` to the
name); the same residual holds in **producer** position — `ls $'.en'v; cat "$_"`
— the ANSI-C word does not name `.env` recognizably, so no segment blocks, and
`$_` then carries the glued name to the reader; **locale quoting** `$"…"`, by
the same mechanism (`cat .e$"n"v`, `cat .env$""` — the parameter-expansion
strip deliberately leaves `$'`/`$"` alone); a **nested** `${…}` glued to the
name (`cat .env${x:-${y}}`, `cat .env${x//{/}` — the strip matches only
`${…}` without inner braces); a decoded name, with or without
`eval` (`cat "$(printf '\056env')"`), `grep -r KEY .` (a recursive read that may
include `.env`), parameter-expansion defaults (`cat ${X:-.env}`) — the
revision-10 strip removes a whole `${…}` expression (no nesting), so the
`.env`-looking text *inside* a default/alternative value is deleted along
with the braces around it and never reaches the scanner — colon-prefixed
forms (`git show HEAD:.env`, `docker cp ctr:.env .`), python-dotenv
auto-discovery (`python -m dotenv list`, `python -c "from dotenv import
dotenv_values; print(dotenv_values())"` — same class as a script that reads
`.env` in-process), the inherent `.gitignore` relay (`xargs cat < .gitignore`
reads `.env` today because `.gitignore` already lists it — no secret word in the
command), a script that reads `.env` from inside Python/Node (e.g.
`ui_login.py` — allowed by design), a script written with the Write tool or in a
previous Bash call and then executed, or a function or alias defined in a
previous Bash call (no per-command scanner can see it), and
**secret values that are not paths**: `printenv OPENAI_API_KEY`, `env`, `echo
$OPENAI_API_KEY`, `python -c "print(os.environ[...])"` — the hook guarantees
"no single Bash command *names* a secret path outside the exemption", not "no
single Bash command can read one" and not "no secret value can be printed".

## Registration

All wired in `.claude/settings.json`. `precompact_snapshot` is registered
`async: true` so it never delays compaction (it reads the transcript from disk).
Adding a hook: drop the script here, register it in `settings.json`, keep it
fail-open, and add a `tests/harness/` test.
