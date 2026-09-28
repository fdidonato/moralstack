"""F4: guard_secrets._check_bash blocks any Bash command that names a
secret-bearing file outside a narrow safe-program exemption (ls/stat/test/
echo/printf), and the pre-F4 Edit/Write/git-stage/inline-credential behavior
is unchanged.

Block cases are (command, expected-basename-in-stderr) pairs: exit code 2 is
not enough by itself (the pre-commit "fail" convention would make this a
silent non-blocking error), the message must actually name the file.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def guard_secrets(load_hook):
    return load_hook("guard_secrets")


def _bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def _write(path: str, content: str = "x = 1\n") -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


# ---- block table --------------------------------------------------------------
# (command, basename expected in the stderr message)

# Built at runtime, not typed as a `.env` literal, so the cases below can also be copied into a
# probe script run through the Bash tool (review round 4, suggested fix 4): the hazard the
# guard defends against is a Bash command/heredoc *naming* the path — Edit/Write are never
# scanned for secret *paths* in content (`_check_edit` only scans for real-looking credential
# *values*), so editing this file with the Edit/Write tools is unaffected either way.
_ENV = "." + "env"

BLOCK_CASES: list[tuple[str, str]] = [
    ("cat .env", ".env"),
    ("cat ./.env", ".env"),
    ("cat ../.env", ".env"),
    ("cat ~/.env", ".env"),
    ('cat "$CLAUDE_PROJECT_DIR/.env"', ".env"),
    ("cat '.env'", ".env"),
    ('cat ".env"', ".env"),
    ("cat .env.local", ".env.local"),
    ("cat .env*", ".env"),
    ("cat [.]env", ".env"),
    (r"cat .en\v", ".env"),
    ("cat .ENV", ".ENV"),
    ("type .env", ".env"),
    ("more .env", ".env"),
    ("less .env", ".env"),
    ("head -n 3 .env", ".env"),
    ("tail .env", ".env"),
    ("strings .env", ".env"),
    ("xxd .env", ".env"),
    ("od -c .env", ".env"),
    ("grep OPENAI .env", ".env"),
    ("grep -r OPENAI_API_KEY . --include=.env", ".env"),
    ("Get-Content .env.local", ".env.local"),
    (r"gc C:\Users\me\.env", ".env"),
    ("code .env", ".env"),
    ("python -c \"print(open('.env').read())\"", ".env"),
    ("python - < .env", ".env"),
    ("cp .env.template .env", ".env"),
    ("cat < .env", ".env"),
    ("cat .env > out.txt", ".env"),
    ("cat .env | head", ".env"),
    ('bash -c "cat .env"', ".env"),
    ("sudo cat .env", ".env"),
    ("sed -n 1p .env", ".env"),
    ("awk 1 .env", ".env"),
    ("echo KEY=1 > .env", ".env"),
    ("echo $(cat .env)", ".env"),
    ("(cat .env)", ".env"),
    (r"find . -name .env -exec cat {} \;", ".env"),
    ("docker run --env-file=.env img", ".env"),
    ("mv .env .env.bak", ".env"),
    ("test -f .env && cat .env", ".env"),
    ("ls .env && cat .env", ".env"),
    ("cat credentials.json", "credentials.json"),
    ("cat id_rsa", "id_rsa"),
    ("cat foo.pem", "foo.pem"),
    # review round 1 leaks
    ("cat $(echo .env)", ".env"),
    ("cat `echo .env`", ".env"),
    ("cat $(ls .env)", ".env"),
    ("ls .env | xargs cat", ".env"),
    ("echo .env | xargs cat", ".env"),
    ("cat .e'n'v", ".env"),
    ('cat .e"n"v', ".env"),
    # review round 2 leaks
    ("echo .env > names.txt && xargs cat < names.txt", ".env"),
    ("echo .env > n && xargs -a n cat", ".env"),
    ("echo 'cat .env' > run.sh && bash run.sh", ".env"),
    ("printf 'cat .env' > s; . s", ".env"),
    ("ls .env > n; xargs cat < n", ".env"),
    ("ls .env > >(xargs cat)", ".env"),
    ("echo X > .e'n'v", ".env"),
    ('echo X > "$PWD"/.env', ".env"),
    ('echo X > ./.e"n"v', ".env"),
    # review round 3 leaks (revision 4)
    ("ls .env &> n && xargs cat < n", ".env"),
    ("echo .env &>n; xargs -a n cat", ".env"),
    ("echo 'cat .env' >& s; sh s", ".env"),
    ("echo .env >& n && xargs cat < n", ".env"),
    ("echo .env &>> n && xargs cat < n", ".env"),
    ("ls .env &> n", ".env"),
    ("echo 'cat .env' >> .gitignore && sh .gitignore", ".env"),
    ("echo 'cat .env' >> .gitignore; bash .gitignore", ".env"),
    ("echo 'cat .env' >> .gitignore && . .gitignore", ".env"),
    ("echo .env >> .gitignore && xargs cat < .gitignore", ".env"),
    ("echo .env >> .gitignore && xargs -a .gitignore cat", ".env"),
    ("echo 'cat .env' > /tmp/.gitignore && sh /tmp/.gitignore", ".env"),
    ("echo .env > x/.gitignore; xargs cat < x/.gitignore", ".env"),
    ('printf -v x .env; cat "$x"', ".env"),
    ("printf -v x 'cat .env'; eval \"$x\"", ".env"),
    ('[[ .env =~ (.*) ]] && cat "${BASH_REMATCH[0]}"', ".env"),
    ('[[ .env =~ (.*) ]]; cat "$BASH_REMATCH"', ".env"),
    (r"cat .en\v*", ".env"),
    (r"cat .e\n[v]", ".env"),
    (r"cat .e\nv?", ".env"),
    # relays found while revising for round 3
    ("echo 'cat .env' >> .gitignore && sh .giti*", ".env"),
    ("printf '-v' x .env; cat \"$x\"", ".env"),
    ('ls .env; cat "$_"', ".env"),
    ("test -f .env && cat $_", ".env"),
    ("echo .env >/dev/null; cat ${_}", ".env"),
    ("{ ls .env; } > n; xargs cat < n", ".env"),
    ("( ls .env; ) > n; xargs cat < n", ".env"),
    ("exec 3> n; echo .env >&3; xargs cat < n", ".env"),
    ("exec 1> n; echo .env; xargs cat < n", ".env"),
    ("echo .env > .gitignore", ".env"),
    # review round 4 leaks, closed by the revision-5 structural rule
    ("ls .env; cat ${_:-y}", ".env"),
    ('ls .env; cat "${_:-y}"', ".env"),
    ('ls .env && cat "${_##*/}"', ".env"),
    ('ls .env; head "${_%.bak}"', ".env"),
    ('ls .env; cat "${_/X/Y}"', ".env"),
    ('ls .env; cat "${_:0}"', ".env"),
    ('echo .env; cat "${_#}"', ".env"),
    ('printf %s .env; cat "${_:-y}"', ".env"),
    ('stat .env; cat "${_:-y}"', ".env"),
    ('test -e .env && cat "${_:-y}"', ".env"),
    ('[[ -e .env ]] && cat "${_:-y}"', ".env"),
    ('ls .env; n=_; cat "${!n}"', ".env"),
    # literal-$_ controls kept as block pins so a future void-regex change
    # cannot silently drop them while the structural rule is what blocks them
    ("ls .env; cat ${_}", ".env"),
    ('ls .env; cat "${_}"', ".env"),
    # revision 7 (diff review, BLOCKING 1): the structural rule reads the first word of
    # a segment as the program it runs — true only for a *simple command*. A same-command
    # function definition rebinds a safe name and runs its (unsafe) body instead; closed by
    # `_FUNCTION_DEF` (empty-paren function syntax voids the exemption everywhere in the
    # command).
    ('ls() { cat "$@"; }; ls .env', ".env"),
    ('ls(){ cat "$@";}; ls .env', ".env"),
    ("true() { cat .env; }; true", ".env"),
    ('ls() (cat "$@"); ls .env', ".env"),
    ('echo() { cat "$@"; } && echo .env', ".env"),
    ('{ ls() { cat "$@"; }; }; ls .env', ".env"),
    ('ls .env; ls() { cat "$@"; }; ls .env', ".env"),
    ('[ -f .env ] && ls() { cat "$@"; } && ls .env', ".env"),
    ('ls ( ) { cat "$@"; }; ls .env', ".env"),
    # regression pin: `function name { … }` is already void because `function` is not a
    # safe program (predates revision 7) — kept so a future `_FUNCTION_DEF` change cannot
    # accidentally start relying on it for coverage it never provided.
    ('function ls { cat "$@"; }; ls .env', ".env"),
    # revision 8 (diff review round 2, BLOCKING 1): `_program` used to return
    # `_basename(word)`, and `_basename('.') == ''` — the same value the structural
    # rule reads as "keyword-only segment, runs nothing" — so the `.` (source)
    # builtin was invisible as a consumer, and a path-qualified safe program
    # (`./ls`, `/tmp/x/ls`) was reduced to its safe basename. Closed by `_program`
    # returning the raw word instead of its basename.
    ('ls .env; . "${_:-x}"; echo "$OPENAI_API_KEY"', ".env"),
    ('ls .env; . ${_:-x}; echo "$OPENAI_API_KEY"', ".env"),
    ('ls .env && . "${_:-x}" && echo "$OPENAI_API_KEY"', ".env"),
    ('test -f .env && . "${_:-x}" && echo "${!OPENAI@}"', ".env"),
    ('echo .env; . "${_:-x}"; echo "$OPENAI_API_KEY"', ".env"),
    ('ls .env; . "${_##*/}"; echo "$OPENAI_API_KEY"', ".env"),
    ('stat .env; . "${_:-x}"; printf %s "$OPENAI_API_KEY"', ".env"),
    ('if . "${_:-x}"; then echo "$OPENAI_API_KEY"; fi; ls .env', ".env"),
    (". ./x; ls .env", ".env"),
    ("./ls .env", ".env"),
    ("/tmp/x/ls .env", ".env"),
    # documented over-blocks (stderr message, never a leak)
    ("find . -name .env", ".env"),  # name lookup only, but find can -exec
    ("git commit -m 'chore: ignore .env'", ".env"),  # commit message names the file
    ("ls .env > listing.txt", ".env"),  # ls is safe, but the redirect writes elsewhere
    ("echo .env 2> err.log", ".env"),  # a stderr file is readable back, not a discard
    # the `.gitignore` append carve-out is exempt only as a lone command
    ("echo .env >> .gitignore && git add .gitignore", ".env"),
    ("echo .env >> .gitignore; cat .gitignore", ".env"),
    ("time ls .env", ".env"),  # `time` is not a transparent keyword
    # revision 5 (2026-09-22): moved here from the allow list when the per-program safe
    # list was replaced by the structural rule — `sleep` is not a safe program, and the
    # rule requires every segment's program to be safe (or empty).
    ("while [ -f .env ]; do sleep 1; done", ".env"),
    ("test -f .env && python -m pytest tests/harness -q", ".env"),  # same cause, everyday shape
    # revision 6 (review round 5): `case`/`in` are neither safe nor transparent (only `esac` is)
    ('case "$x" in .env) echo skip;; esac', ".env"),
    # revision 6: a leading redirect becomes the program word, not `ls`
    ("2>/dev/null ls .env", ".env"),
    # revision 9 (diff review round 3, BLOCKING 1): the program locator used to run on
    # `_WORD_SPLIT` tokens, which split on `=`, `{`, `,`, `}` too — an assignment prefix or a
    # brace expansion could show a safe *sub-token* as the first word while bash actually ran
    # an unsafe consumer. Closed by `_blank_program` (bash-blank-delimited tokens only).
    # (a) assignment prefix whose variable name is a safe/transparent word
    (f"ls=1 cat {_ENV}", ".env"),
    (f"echo=1 cat {_ENV}", ".env"),
    (f"true=1 cat {_ENV}", ".env"),
    (f"ls= cat {_ENV}", ".env"),
    (f"ls=1 . {_ENV}", ".env"),
    (f"if=ls cat {_ENV}", ".env"),
    (f"then=ls cat {_ENV}", ".env"),
    (f'ls {_ENV}; ls=1 cat "${{_:-x}}"', ".env"),
    (f'[ -f {_ENV} ] && ls=1 cat "${{_:-x}}"', ".env"),
    # (b) brace expansion with an empty first alternative and a safe second one
    (f"{{,ls}}cat {_ENV}", ".env"),
    (f"{{,[}}cat {_ENV}", ".env"),
    (f"{{,ls}}{{,x}}cat {_ENV}", ".env"),
    (f'ls {_ENV}; {{,ls}}cat "${{_:-x}}"', ".env"),
    # documented over-blocks (revision 9, orchestrator decision, safe direction): the blank-
    # token locator requires the raw first blank-delimited word to equal a safe name exactly,
    # so grouping/quoting/escaping around it now also voids the exemption. None of these read
    # the file (bash runs the real `ls`); pinned so a later change cannot silently reopen the
    # class the other way.
    (f"{{ ls {_ENV}; }}", ".env"),
    (f"(ls {_ENV})", ".env"),
    (f'"ls" {_ENV}', ".env"),
    (chr(92) + f"ls {_ENV}", ".env"),
    # revision 10 (diff review round 4, BLOCKING 1): the secret-word scan does not recognise
    # the name when a `$`-expression is glued to it — bash expands `$IFS` to a blank and an
    # unset/empty parameter to nothing, so the literal `.env` reappears in the running command
    # even though the raw text does not contain it contiguously.
    # (a) unbraced `$IFS` read as a blank (`_IFS_RE`, both tokenizers and `_blank_program`)
    (f"cat$IFS{_ENV}", ".env"),
    (f"cat$IFS$IFS{_ENV}", ".env"),
    (f"head$IFS{_ENV}", ".env"),
    (f"xxd$IFS{_ENV}|head", ".env"),
    (f"cat$IFS-A$IFS{_ENV}", ".env"),
    (f"cat$IFS{_ENV}.local", ".env.local"),
    (f"cat <$IFS{_ENV}", ".env"),
    (f'ls$IFS{_ENV}; cat "$_"', ".env"),
    (f'ls$IFS{_ENV}; cat "${{_:-x}}"', ".env"),
    # a `${IFS…}` modifier form is not matched by `_IFS_RE`; it blocks only because
    # `_WORD_SPLIT` splits on `{`/`}` and leaves the name whole (review round 5, missing test 2)
    (f"cat${{IFS:0:1}}{_ENV}", ".env"),
    # (b) a `$name`/`${name}`/positional/special parameter glued to either end of the name
    # (`_PARAM_EXPANSION_RE`, the third word list only — not the program locator)
    (f"cat $u{_ENV}", ".env"),
    (f"cat {_ENV}$u", ".env"),
    (f"cat {_ENV}${{u}}", ".env"),
    (f"head $u{_ENV}", ".env"),
    (f"cat {_ENV}$@", ".env"),
    (f"cat {_ENV}$1", ".env"),
    (f"cat $*{_ENV}", ".env"),
    (f"cat <$u{_ENV}", ".env"),
    (f"cp $u{_ENV} /tmp/x", ".env"),
    (f'ls {_ENV}$u; cat "${{_:-x}}"', ".env"),
    # the documented residual this closes: a variable-built name typed with the `.env` half
    # literal (not built via `_ENV` — there is no contiguous `.env` substring in the source,
    # the `${x}` splits it)
    ("cat .${x}env", ".env"),
    # cross-segment grouping over-block (review round 4, non-blocking 2): unrelated to the
    # $IFS/parameter fix above — `{ }` in the *other* segment is not a safe/transparent
    # program word, so the structural rule already voids the exemption everywhere and the
    # first segment's own `.env` argument gets scanned. Pinned so a later "make `{`/`}`
    # transparent" change cannot silently reopen it.
    (f"[ -f {_ENV} ] && {{ echo yes; }}", ".env"),
]


@pytest.mark.parametrize("cmd, needle", BLOCK_CASES)
def test_bash_secret_access_blocked(guard_secrets, run_hook, project, capsys, cmd, needle):
    code, _ = run_hook(guard_secrets, _bash(cmd), project)
    captured = capsys.readouterr()
    assert code == 2, f"expected block (2, not 1) for: {cmd!r}, got {code}"
    assert needle in captured.err, f"{cmd!r}: stderr does not mention {needle!r}: {captured.err!r}"


def test_bash_secret_access_blocked_multiline(guard_secrets, run_hook, project, capsys):
    cmd = "\n".join(["bash <<'EOF'", "cat " + ".env", "EOF"])
    code, _ = run_hook(guard_secrets, _bash(cmd), project)
    captured = capsys.readouterr()
    assert code == 2
    assert ".env" in captured.err


def test_bash_secret_access_blocked_function_def_multiline(guard_secrets, run_hook, project, capsys):
    """Regression pin (revision 7): a function definition whose body sits on its own
    line, not joined with `;`, was already blocked before `_FUNCTION_DEF` existed —
    the newline in `_SEGMENT_SPLIT` puts `cat "$@"` in its own segment, whose program
    word (`cat`) is not safe. Kept so a future segmentation change cannot silently
    reopen it."""
    cmd = "\n".join(["ls() {", 'cat "$@"', "}", "ls " + ".env"])
    code, _ = run_hook(guard_secrets, _bash(cmd), project)
    captured = capsys.readouterr()
    assert code == 2
    assert ".env" in captured.err


def test_bash_secret_access_blocked_function_def_backslash_newline(guard_secrets, run_hook, project, capsys):
    """Revision 8 (suggestion 1): `_FUNCTION_DEF` allows whitespace or a backslash-newline
    line continuation inside empty parens — a shell-legal way to spread `ls (` and
    `) { ... }` across two physical lines. Built with `chr(92)` so the source contains an
    actual backslash character followed by an actual newline, not an escaped pair."""
    cmd = "ls (" + chr(92) + "\n" + ') { cat "$@"; }; ls ' + ".env"
    code, _ = run_hook(guard_secrets, _bash(cmd), project)
    captured = capsys.readouterr()
    assert code == 2
    assert ".env" in captured.err


# ---- allow table ----------------------------------------------------------------

ALLOW_CASES: list[str] = [
    "python .claude/skills/improve-moralstack-ui/scripts/ui_login.py",
    "ls .env",
    "ls -la .env",
    "ls -la",
    "stat .env",
    "test -f .env && echo yes",
    "[ -f .env ] && echo present",
    "[[ -f .env ]] && echo present",
    "[ -f .env ] || echo missing",
    "test -f .env || echo no",
    'echo ".env" >> .gitignore',
    r"printf '.env\n' >> .gitignore",
    "echo '.env' >> ./.gitignore",
    "ls .env || true",  # survives the revision-5 structural rule only because `true` is safe
    # revision-4 carve-outs (Q2)
    "ls .env 2>/dev/null",  # /dev/null is a discard target, not "elsewhere"
    "ls .env >/dev/null 2>&1 && echo present",  # 2>&1 is an fd dup, also a discard
    "ls .env 2>&1",
    "[ -f .env ] 2>&1",
    # these three survive the revision-5 structural rule only because `fi`/`!` are
    # transparent prefixes, so the trailing segment has an empty program word
    "if [ -f .env ]; then echo yes; fi",
    "! test -f .env",
    "if ! [ -f .env ]; then echo missing; fi",
    # ANSI-C residual, accepted and documented as allowed (user decision, round 4)
    "cat $'.en'v",
    # revision-10 residual class, documented as allowed (review round 5, non-blocking 1-2): the
    # parameter-expansion strip matches only non-nested `${…}` and leaves `$"…"` alone
    f"cat {_ENV}${{x:-${{y}}}}",
    f'cat {_ENV}$""',
    # inherent residuals, accepted and documented as allowed (no secret word in the command)
    "xargs cat < .gitignore",
    "sh .gitignore",
    "cat .env.template",
    "cat .env.example",
    "cat .env.minimal",
    "cat .envrc",
    "cat tests/fixtures/sample.env",
    "cp .env.template .env.local.example",
    "git status",
    "git add .gitignore",
    'git commit -m "x"',
    # revision 10: `$IFS` is read as a blank before the program locator too, so this reduces
    # to the already-allowed `ls .env` — bash runs the real, safe `ls .env` here as well.
    f"ls$IFS{_ENV}",
    'grep -rn "load_env" moralstack/',
    'grep -rn "os.environ" moralstack/',
    "cat README.md",
    "python -m pytest tests/harness -q",
]


@pytest.mark.parametrize("cmd", ALLOW_CASES)
def test_bash_secret_access_allowed(guard_secrets, run_hook, project, cmd):
    code, _ = run_hook(guard_secrets, _bash(cmd), project)
    assert code == 0, f"expected allow (0) for: {cmd!r}"


def test_cp_template_to_env_is_blocked(guard_secrets, run_hook, project, capsys):
    code, _ = run_hook(guard_secrets, _bash("cp .env.template .env"), project)
    captured = capsys.readouterr()
    assert code == 2
    assert ".env" in captured.err


def test_python_c_open_env_is_blocked(guard_secrets, run_hook, project, capsys):
    code, _ = run_hook(guard_secrets, _bash("python -c \"print(open('.env').read())\""), project)
    captured = capsys.readouterr()
    assert code == 2
    assert ".env" in captured.err


def test_block_message_names_blank_program_not_word_split_program(guard_secrets, run_hook, project, capsys):
    """Revision 10 (diff review round 4, suggested fix 1): the block message must name the
    token the exemption decision was actually taken on (`blank_program`, bash's first
    blank-delimited word) — not the old `_WORD_SPLIT`-based `program`, which for an
    assignment-prefixed segment like `ls=1 cat .env` used to say the misleading "`ls` names
    …" while bash actually runs `cat`."""
    code, _ = run_hook(guard_secrets, _bash(f"ls=1 cat {_ENV}"), project)
    captured = capsys.readouterr()
    assert code == 2
    assert "`ls=1`" in captured.err
    assert "`ls`" not in captured.err


def test_secret_access_predicate_is_pure(guard_secrets):
    assert guard_secrets._secret_access("cat .env") is not None
    assert guard_secrets._secret_access("ls .env") is None
    assert guard_secrets._secret_access("ls .env || true") is None
    assert guard_secrets._secret_access("cat .env.template") is None


# ---- existing-behavior locks (must not be weakened by F4) -----------------------


def test_edit_secret_path_blocked(guard_secrets, run_hook, project):
    code, _ = run_hook(guard_secrets, _write(".env"), project)
    assert code == 2


def test_edit_template_allowed(guard_secrets, run_hook, project):
    code, _ = run_hook(guard_secrets, _write(".env.template"), project)
    assert code == 0


def test_git_stage_env_blocked(guard_secrets, run_hook, project, capsys):
    code, _ = run_hook(guard_secrets, _bash("git add .env"), project)
    captured = capsys.readouterr()
    assert code == 2
    assert "staging" in captured.err


def test_inline_credential_blocked(guard_secrets, run_hook, project):
    fake_key = "sk-" + "a" * 30
    payload = _write("config.py", content=f"KEY = '{fake_key}'\n")
    code, _ = run_hook(guard_secrets, payload, project)
    assert code == 2


def test_placeholder_allowed(guard_secrets, run_hook, project):
    payload = _write("config.py", content="KEY = '${OPENAI_API_KEY}'\n")
    code, _ = run_hook(guard_secrets, payload, project)
    assert code == 0


def test_bash_block_exits_2_not_1(guard_secrets, run_hook, project):
    code, _ = run_hook(guard_secrets, _bash("cat .env"), project)
    assert code == 2
