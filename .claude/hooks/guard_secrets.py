#!/usr/bin/env python3
"""PreToolUse guard: keep secrets out of files, content and git staging.

Enforces the global security rule ("mai segreti/.env nel codice, nei log o nei
commit") deterministically rather than as guidance. Registered for both
``Edit|Write`` and ``Bash``; it inspects ``tool_name`` and applies the relevant
checks:

* **Edit / Write / MultiEdit**
  - block writing to a secret-bearing path (``.env`` and variants, ``*.pem``,
    ``*.key``, ``id_rsa``, ``credentials.json`` …); templates/examples are allowed.
  - block writing *content* that contains a real-looking credential
    (``sk-…`` keys, AWS ``AKIA…``, PEM private-key blocks). Documentation files
    (``.md``/``docs/``/``.claude/``) are exempt — they legitimately show patterns.

* **Bash**
  - block ``git add`` / ``git commit`` that stages a ``.env`` / secret file.
  - block a command that embeds a real-looking credential inline.
  - block any command that names a secret-bearing file (read, copy, move, open,
    redirect), templates exempt. Name detection is **textual**: the scanner
    matches the literal name after quote deletion, backslash removal, glob
    metacharacter removal, ``$IFS`` read as a blank, and parameter expansions
    (``$name``, non-nested ``${…}``, positional/special parameters) removed — any other
    shell spelling that produces the name at run time is a documented residual
    (see ``.claude/hooks/README.md``), not a defect. Only ``ls``/``stat``/``test``/
    ``[``/``[[``/``echo``/``printf`` may name a secret path without reading it,
    and only when every segment of the
    command is a **simple command** running one of those programs **by its bare
    name, as the first blank-delimited word of the segment** — no function
    definition — and the command has no relay channel: no
    substitution, pipe, variable relay (``printf -v``, ``=~``, ``BASH_REMATCH``,
    ``$_``, ``exec``) or
    output redirect to anything readable back (``/dev/null``, an fd, or a lone
    ``>> .gitignore`` are fine).

Blocks with exit code 2 (stderr is shown to Claude). Fails **open** on any
malformed input or unexpected error, so a hook bug can never wedge the session.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import PurePosixPath

# --- secret-bearing file paths -------------------------------------------------

_SECRET_BASENAMES = {
    "id_rsa",
    "id_ed25519",
    "id_dsa",
    "id_ecdsa",
    "credentials.json",
    ".netrc",
    ".pgpass",
    ".htpasswd",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
# Suffixes that mark a non-secret template/example, even on a secret-looking name.
_TEMPLATE_MARKERS = (".template", ".example", ".sample", ".dist", ".minimal")

# --- real-looking credentials in content/commands ------------------------------

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI/Anthropic-style key", re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{24,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("PEM private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
)
# Obvious placeholders that must never trip the content scanner.
_PLACEHOLDER = re.compile(r"\$\{|<your|your-?key|xxxx|placeholder|example|changeme|\.\.\.", re.IGNORECASE)


def _basename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


def _is_template(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(m) or m + "." in lower for m in _TEMPLATE_MARKERS)


def _is_secret_path(path: str) -> bool:
    name = _basename(path)
    lower = name.lower()
    if _is_template(name):
        return False
    if lower == ".env" or lower.startswith(".env."):
        return True
    if name in _SECRET_BASENAMES:
        return True
    return any(lower.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _is_doc_path(path: str) -> bool:
    lower = path.replace("\\", "/").lower()
    return (
        lower.endswith((".md", ".rst", ".txt"))
        or "/docs/" in lower
        or lower.startswith("docs/")
        or "/.claude/" in lower
        or lower.startswith(".claude/")
    )


# --- secret-bearing paths named by a Bash command ------------------------------
# Command separators / substitution openers: each piece is scanned as its own simple command.
# A bare `&` (background list) splits; the `&` of a redirection (`&>`, `&>>`, `>&`, `2>&1`)
# does not — revision 4, review round 3 BLOCKING 1(a): `ls .env &> n` must stay one segment
# so the redirect is seen next to the secret word.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|\$\(|[;|`\n]|(?<!>)&(?!>)")
# Redirection operators; the following word is the file, checked for every program. The
# alternation order matters: `&>`/`&>>`/`>&` are tried before `\d?>>?`, so `>& file` is one
# output-redirect operator (its target a file) and `2>&1` splits into `2>` + `&1`.
_REDIRECT_RE = re.compile(r"(&>>?|>&|\d?>>?|<<?)")
# Word separators for the SECRET-WORD SCAN only (not the program locator — see
# `_blank_program` below): whitespace, quotes, parens/braces, comma, '=' (so --file=.env
# splits and `.env` is found inside it). Deliberately NOT '[' ']' so that `[ -f .env ]` keeps
# `[` as its program word in the (separate) "secret-bearing file used as a command" check.
# Revision 9 (diff review round 3, BLOCKING 1): this tokenizer over-splits on purpose for the
# secret-word scan, which is *wrong* for locating the program — `ls=1 cat .env` split on `=`
# shows a safe `ls` first token while bash runs `cat`, and `{,ls}cat .env` split on `{`/`,`/`}`
# does the same. The program word must be bash's first *blank*-delimited token instead.
_WORD_SPLIT = re.compile(r"[\s'\"(){},=]+")
# Revision 10 (diff review round 4, BLOCKING 1(a)): unquoted `$IFS` always expands to a
# separator in a fresh shell — an `IFS=` assignment in the same command is itself an unsafe
# program word and already voids the exemption; a previous-call `IFS=` change is the
# documented cross-call residual. Read as a blank before either tokenizer or the program
# locator sees the segment, so `cat$IFS.env` splits exactly like bash splits it.
_IFS_RE = re.compile(r"\$IFS(?![A-Za-z0-9_])|\$\{IFS\}")
# Revision 10 (BLOCKING 1(b)): a `$name`/`${name}`/positional/special parameter glued to
# either end of the literal name (`cat $u.env`, `cat .env$u`, `cat .env$@`) hides it from
# `_secret_word` because the glued word no longer equals `.env`. Used only for the third word
# list below — NOT the program locator (`$x ls .env` must keep an unsafe first token, since
# bash's real first word depends on the runtime value of `$x`). Deliberately does not match
# `$(` (already void via `_EXEMPTION_VOID`), `$'` (ANSI-C — stays a documented residual) or
# `$"`; no nesting inside `${...}`.
_PARAM_EXPANSION_RE = re.compile(r"\$\{[^{}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9]|\$[@*#?$!-]")
# Programs allowed to *name* a secret file without reading it (spec F4 exceptions).
# `true`/`false` are no-op builtins that never read anything; they are here so that the
# structural rule below (every segment's program must be safe) does not turn the round-3 allow
# pin `ls .env || true` into a block — revision 5.
_SECRET_PATH_SAFE_PROGRAMS = frozenset({"ls", "stat", "test", "[", "[[", "echo", "printf", "true", "false"})
# Shell keywords that precede a simple command without running anything themselves
# (`if [ -f .env ]`, `! test -f .env`, `; then echo yes`): skipped when locating the program
# word — revision 4 (round 3, non-blocking 1; user decision Q2). Revision 5 adds the block
# terminators `fi`/`done`/`esac`: a segment made only of these has an *empty* program word,
# runs nothing, and therefore cannot consume a relay — it counts as safe for the structural
# rule (`if [ -f .env ]; then echo yes; fi` must stay allowed).
_TRANSPARENT_PREFIXES = frozenset({"if", "then", "elif", "else", "while", "until", "do", "fi", "done", "esac", "!"})
# Output redirections only (a safe program that writes its output to a file other than
# .gitignore may be relaying a secret file name — review round 2, BLOCKING 1a).
_OUT_REDIRECT_RE = re.compile(r"&>>?|>&|\d?>>?")
# Output targets nothing can be read back from: /dev/null and fd duplications (`2>&1` →
# `2>` + `&1`; `>&2` → `>&` + `2`). Only these are exempt from "writes elsewhere" —
# `/dev/stderr`, `/dev/tty`, `/dev/fd/N`, `>&-` all count as elsewhere (over-block by design).
_DISCARD_TARGET_RE = re.compile(r"&\d+|/dev/null")
_FD_TARGET_RE = re.compile(r"\d+")
# The one output destination the spec exempts ("appending .env to .gitignore"): `>>` exactly,
# the repo-root file exactly (a `.gitignore` in another directory was a relay medium — round 3,
# BLOCKING 1(b)); `>` (overwrite) is not appending (round 3, non-blocking 3; user decision Q3).
_GITIGNORE_TARGETS = frozenset({".gitignore", "./.gitignore"})
# If the command contains a substitution, a process substitution or a pipe, the exemption is
# void everywhere: the safe program's *output* (a file name) can feed a reader —
# `cat $(echo .env)`, `ls .env | xargs cat`, `ls .env > >(xargs cat)` (rounds 1-2). `||` is
# a list operator, never a data path, and is excluded (lookarounds). Revision 4 (round 3,
# BLOCKING 1(c), plus two relays found while revising): a safe program can also leave the name
# in a shell variable — `printf -v x .env`, `[[ .env =~ (.*) ]]` / `BASH_REMATCH`, and `$_`
# (the previous command's last argument: `ls .env; cat "$_"`) — and `exec` can re-point stdout
# or open an fd for every later segment (`exec 3> n; echo .env >&3; xargs cat < n`); those
# spellings void it too. Matched on the raw text AND on the quote-deleted text (`printf '-v'`).
_EXEMPTION_VOID = re.compile(
    r"\$\(|`|>\(|<\(|(?<!\|)\|(?!\|)" r"|=~|BASH_REMATCH|\$_(?![A-Za-z0-9_])|\$\{_\}|\bexec\b" r"|\bprintf\b[^;|&\n]*\s-v\b"
)
# Revision 7 (diff review, BLOCKING 1): the structural rule reads the first word of a segment
# as the program it runs — true only for a *simple command*. A function definition in the
# same command rebinds a safe name (`ls() { cat "$@"; }; ls .env`, `true() { cat .env; };
# true`), so any function-definition parens void the exemption. Empty parens with only
# whitespace / line continuations inside; `$(` is already void, and `function name { … }` is
# void because `function` is not a safe program.
_FUNCTION_DEF = re.compile(r"\((?:\s|\\\n)*\)")


def _secret_word(word: str) -> str | None:
    """Basename if ``word`` — as typed, with backslash escapes removed (``.en\\v``), with glob
    metacharacters removed (``.env*``, ``[.]env``), or with both removed in that order
    (``.en\\v*``, ``.e\\n[v]`` — round 3, BLOCKING 2) — is a secret path."""
    unescaped = word.replace("\\", "")
    for form in (word, unescaped, re.sub(r"[\[\]*?]", "", word), re.sub(r"[\[\]*?]", "", unescaped)):
        if form and _is_secret_path(form):
            return _basename(form)
    return None


def _word_lists(segment: str) -> tuple[list[str], list[str], list[str]]:
    """Three word lists of a segment, each scanned on its own so a hit in any one blocks
    (round 2, BLOCKING 1b; revision 10 adds the third). All three start from the segment with
    `$IFS` read as a blank (revision 10, BLOCKING 1(a)) and redirect operators spaced out:
    quotes as separators (finds `.env` inside `open('.env')`), quotes deleted (finds `.env`
    spliced as `.e'n'v`), and quotes-deleted-with-parameter-expansions-removed (finds `.env`
    glued to a `$name`/`${name}`/positional/special parameter — revision 10, BLOCKING 1(b)).
    Each extra list can only add a block or a void, never remove one the others found."""
    normalized = _IFS_RE.sub(" ", segment)
    spaced = _REDIRECT_RE.sub(r" \1 ", normalized)
    split = [w for w in _WORD_SPLIT.split(spaced) if w]
    unquoted_text = spaced.replace("'", "").replace('"', "")
    unquoted = [w for w in _WORD_SPLIT.split(unquoted_text) if w]
    stripped_text = _PARAM_EXPANSION_RE.sub("", unquoted_text)
    stripped = [w for w in _WORD_SPLIT.split(stripped_text) if w]
    return split, unquoted, stripped


def _redirect_hit(words: list[str]) -> str | None:
    """Basename of a secret-bearing redirection target, if any."""
    for i in range(len(words) - 1):
        if _REDIRECT_RE.fullmatch(words[i]):
            hit = _secret_word(words[i + 1])
            if hit:
                return hit
    return None


def _writes_output_elsewhere(words: list[str], single_segment: bool) -> bool:
    """True if the segment sends stdout/stderr anywhere it could be read back from. Not
    "elsewhere": /dev/null, an fd duplication, and — only when the whole command is this one
    segment — an append (`>>`) to the repo-root `.gitignore`, the one destination the spec
    exempts. A plant needs a consumer in the same command, and consumers cannot be enumerated
    (`sh .giti*`), so the exemption is simply unavailable in a multi-segment command
    (round 3, BLOCKING 1(b))."""
    for i in range(len(words) - 1):
        op, target = words[i], words[i + 1]
        if not _OUT_REDIRECT_RE.fullmatch(op):
            continue
        if single_segment and op == ">>" and target in _GITIGNORE_TARGETS:
            continue
        if _DISCARD_TARGET_RE.fullmatch(target) or (op == ">&" and _FD_TARGET_RE.fullmatch(target)):
            continue
        return True
    return False


def _program(words: list[str]) -> tuple[str, list[str]]:
    """Skip transparent shell keywords; return (program word as typed, words from the program
    on). Revision 8: the raw word, not its basename — the exemption needs a *bare* safe name.
    ``.`` is the source builtin, not an empty segment (``_basename('.') == ''``, which the
    structural rule below reads as "keyword-only segment, runs nothing" — the bug this revision
    closes), and ``./ls`` / ``/tmp/x/ls`` are path-qualified, not the ``ls`` program the safe
    set means. Empty stays reserved for an actual keyword-only segment."""
    i = 0
    while i < len(words) and words[i] in _TRANSPARENT_PREFIXES:
        i += 1
    if i == len(words):
        return "", []
    return words[i], words[i:]


def _blank_program(segment: str) -> str:
    """Program locator for the safe-program **exemption only** (revision 9, diff review round
    3, BLOCKING 1): bash's first blank-delimited word of the redirect-spaced segment (the same
    text `_word_lists` starts from), after skipping `_TRANSPARENT_PREFIXES` tokens (exact
    match). Split on real bash word separators only (`[ \t]+`) — NOT `_WORD_SPLIT`, which also
    splits on `=`, `{`, `,`, `}` and would let an assignment prefix (`ls=1 cat .env`) or a
    brace expansion (`{,ls}cat .env`) show a safe *sub-token* as the program while bash still
    runs `cat`. Returns `""` for a keyword-only segment (`fi`, `done`, …), which runs nothing
    and cannot consume a relay. Revision 10: `$IFS` is read as a blank first (BLOCKING 1(a)) —
    parameter expansions are deliberately NOT removed here, unlike `_word_lists`'s third list:
    `$x ls .env` must keep an unsafe first token, since bash's real first word depends on the
    runtime value of `$x`, not on the literal `ls`."""
    normalized = _IFS_RE.sub(" ", segment)
    spaced = _REDIRECT_RE.sub(r" \1 ", normalized)
    tokens = [t for t in re.split(r"[ \t]+", spaced) if t]
    i = 0
    while i < len(tokens) and tokens[i] in _TRANSPARENT_PREFIXES:
        i += 1
    return tokens[i] if i < len(tokens) else ""


def _secret_access(cmd: str) -> str | None:
    """Reason string if the command reads/writes/copies/opens a secret-bearing file."""
    unquoted_cmd = cmd.replace("'", "").replace('"', "")
    segments = [s for s in _SEGMENT_SPLIT.split(cmd) if s.strip()]
    single_segment = len(segments) == 1
    word_lists = [_word_lists(s) for s in segments]
    blank_programs = [_blank_program(s) for s in segments]
    # The exemption is a property of the whole command: any relay channel anywhere voids it
    # for every segment (substitution/pipe/variable — `_EXEMPTION_VOID` — or an output
    # redirect to anything readable back, in this or any other segment: `{ ls .env; } > n;
    # xargs cat < n` puts the redirect in a segment of its own).
    # Revision 7: a function definition (`ls() { … }`) also voids the exemption — the
    # structural rule below only reads the first word of each segment as the program it
    # runs, which a same-command function definition can rebind (see `_FUNCTION_DEF`).
    exemption_allowed = (
        _EXEMPTION_VOID.search(cmd) is None
        and _EXEMPTION_VOID.search(unquoted_cmd) is None
        and _FUNCTION_DEF.search(cmd) is None
        and _FUNCTION_DEF.search(unquoted_cmd) is None
    )
    # Revision 5 — the structural rule (review round 4; user decision Q1 reversed 2026-09-22).
    # The exemption survives only if **every** segment's program word is safe, or empty (a bare
    # `fi`/`done` runs nothing). A relay always needs a consumer, and a consumer is never a safe
    # program, so this closes the whole class at once — `${_:-y}`, `${_##*/}`, `${!n}` and any
    # future variable/fd channel — instead of naming channels one at a time, which four review
    # rounds proved cannot be completed. Revision 9: the program word is `_blank_program`'s
    # bash-blank-delimited token (one per segment), not a `_WORD_SPLIT` token — `_WORD_SPLIT`
    # also splits on `=` and `{`/`,`/`}`, which let an assignment prefix or a brace expansion
    # show a safe sub-token while bash ran an unsafe consumer (`ls=1 cat .env`,
    # `{,ls}cat .env`). This replaces the old per-word-list (`split`/`unquoted`) program check.
    if exemption_allowed:
        for blank_program in blank_programs:
            if blank_program and blank_program not in _SECRET_PATH_SAFE_PROGRAMS:
                exemption_allowed = False
                break
    # Revision 10: the third (parameter-expansion-stripped) word list is folded into every
    # site that used to iterate (split, unquoted) — it can only add a block or a void, never
    # remove one the first two already found (BLOCKING 1(b)).
    if exemption_allowed:
        for split, unquoted, stripped in word_lists:
            if (
                _writes_output_elsewhere(split, single_segment)
                or _writes_output_elsewhere(unquoted, single_segment)
                or _writes_output_elsewhere(stripped, single_segment)
            ):
                exemption_allowed = False
                break
    for (split, unquoted, stripped), blank_program in zip(word_lists, blank_programs):
        if not split:
            continue
        for words in (split, unquoted, stripped):
            hit = _redirect_hit(words)
            if hit:
                return f"redirection reads/writes a secret-bearing file ({hit})"
        program, split_cmd = _program(split)
        _, unquoted_cmd_words = _program(unquoted)
        _, stripped_cmd_words = _program(stripped)
        for words in (split_cmd, unquoted_cmd_words, stripped_cmd_words):
            if words:
                hit = _secret_word(words[0])
                if hit:
                    return f"secret-bearing file used as a command ({hit})"
        # Revision 9: the per-segment skip also uses `_blank_program`'s token, not `program`
        # (the `_WORD_SPLIT`-based one) — same reasoning as the structural rule above.
        if exemption_allowed and blank_program in _SECRET_PATH_SAFE_PROGRAMS:
            continue
        for words in (split, unquoted, stripped):
            for word in words[1:]:
                if _REDIRECT_RE.fullmatch(word):
                    continue
                hit = _secret_word(word)
                if hit:
                    # Revision 10 (review round 4, suggested fix 1): name the token the
                    # exemption decision was actually taken on (`blank_program`), not the old
                    # `_WORD_SPLIT`-based `program` — for `ls=1 cat .env` that used to say the
                    # misleading "`ls` names …" while bash actually runs `cat`.
                    name = blank_program or program
                    return (
                        f"`{name}` names a secret-bearing file ({hit}) — reading/copying/opening secrets is "
                        "blocked (only ls/stat/test/echo/printf may name one, and only in a command whose every "
                        "segment is one of those, with no substitution, pipe, variable relay or output redirect "
                        "to anything but /dev/null, an fd, or a lone `>> .gitignore`)"
                    )
    return None


def _scan_content(text: str) -> list[str]:
    found: list[str] = []
    for label, pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - 40) : m.end() + 40]
            if _PLACEHOLDER.search(window):
                continue
            found.append(label)
            break
    return found


def _check_edit(tool_input: dict) -> list[str]:
    msgs: list[str] = []
    path = tool_input.get("file_path")
    if isinstance(path, str) and path and _is_secret_path(path):
        msgs.append(
            f"writing to a secret-bearing file ({_basename(path)}) — secrets must live in env vars, never in tracked files."
        )

    if not (isinstance(path, str) and _is_doc_path(path)):
        chunks: list[str] = []
        for key in ("content", "new_string"):
            value = tool_input.get(key)
            if isinstance(value, str):
                chunks.append(value)
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
                    chunks.append(edit["new_string"])
        for label in _scan_content("\n".join(chunks)):
            msgs.append(f"content contains a real-looking credential ({label}) — use a placeholder / env var instead.")
    return msgs


def _check_bash(cmd: str) -> list[str]:
    msgs: list[str] = []
    stages = re.search(r"git\s+(?:add|commit)\b", cmd) is not None
    if stages and re.search(r"(?:^|\s)\.env(?:\.\w+)?(?:\s|$)", cmd):
        # Tieni le alternative allineate a _TEMPLATE_MARKERS.
        if not re.search(r"\.env\.(?:template|example|sample|dist|minimal)", cmd):
            msgs.append("staging a `.env` file for commit — never commit secrets.")
    for label in _scan_content(cmd):
        msgs.append(f"command embeds a real-looking credential ({label}) — pass it via an env var, not inline.")
    access = _secret_access(cmd)
    if access:
        msgs.append(access + ". Secrets live in env vars; ask the user if you need a value.")
    return msgs


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0
    if not isinstance(data, dict):
        return 0

    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    if tool in ("Edit", "Write", "MultiEdit"):
        violations = _check_edit(tool_input)
    elif tool == "Bash":
        cmd = tool_input.get("command")
        violations = _check_bash(cmd) if isinstance(cmd, str) and cmd.strip() else []
    else:
        return 0

    if violations:
        sys.stderr.write(
            "Blocked by guard_secrets (global security rule — no secrets in code/logs/commits):\n  - "
            + "\n  - ".join(violations)
            + "\nIf this is a false positive (e.g. a documented placeholder), tell the user; do not work around the guard.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
