"""F2 (Stop timeout budget), F3 (interpreter wrapper) and F4 (deny-list pins)
registration checks against the real ``.claude/settings.json``.

``conftest.py`` bypasses ``settings.json`` entirely (hooks are loaded by path and
driven in-process), so these are the only tests that pin the actual registered
commands.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

GUARDS = {"guard_dangerous_git.py", "guard_secrets.py"}

# Plain-text wrapper template (Proposed design F3). `{code}` is 2 for the two
# PreToolUse guards, 1 for every other hook; `{script}` is the hook file name.
WRAPPER = (
    'sh -c \'for p in python python3; do r=$(command -v "$p" 2>/dev/null) || continue; '
    'case "$r" in "") continue;; */WindowsApps/*) "$r" -c "" >/dev/null 2>&1 || continue;; '
    'esac; exec "$r" "$0"; done; echo "[hook] $0: no working python or python3 on PATH '
    '(see .claude/hooks/README.md)" >&2; exit {code}\' "$CLAUDE_PROJECT_DIR/.claude/hooks/{script}"'
)

_KNOWN_SCRIPTS = {
    "guard_dangerous_git.py",
    "guard_secrets.py",
    "format_on_edit.py",
    "stop_gate.py",
    "session_start.py",
    "precompact_snapshot.py",
    "session_end.py",
    "user_prompt_submit.py",
    "log_instructions.py",
}


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _command_hooks(settings_json: dict):
    """Yield (event, matcher, hook_dict) for every ``type: command`` registration."""
    for event, groups in settings_json["hooks"].items():
        for group in groups:
            matcher = group.get("matcher")
            for hook in group["hooks"]:
                if hook.get("type") == "command":
                    yield event, matcher, hook


def _script_of(command: str) -> str | None:
    for script in _KNOWN_SCRIPTS:
        if command.endswith(f'.claude/hooks/{script}"'):
            return script
    return None


# ---- F3: registration string + shell field -----------------------------------


def test_every_command_hook_uses_wrapper_and_bash_shell(settings_json, repo_root):
    hooks = list(_command_hooks(settings_json))
    assert len(hooks) == 10

    guard_events: set[str] = set()
    seen_scripts: list[str] = []
    for event, matcher, hook in hooks:
        assert hook.get("shell") == "bash", f"{event}/{matcher}: missing shell:bash"
        command = hook["command"]
        script = _script_of(command)
        assert script is not None, f"{event}/{matcher}: cannot identify hook script in {command!r}"
        seen_scripts.append(script)
        code = 2 if script in GUARDS else 1
        expected = WRAPPER.format(code=code, script=script)
        assert command == expected, f"{event}/{script}: wrapper string mismatch"
        assert (repo_root / ".claude" / "hooks" / script).exists()
        if script in GUARDS:
            guard_events.add(event)

    assert guard_events == {"PreToolUse"}, "the two guards must only be registered under PreToolUse"
    assert seen_scripts.count("guard_secrets.py") == 2, "guard_secrets is registered for both Bash and Edit|Write|MultiEdit"


def test_stop_hook_timeout_covers_precommit_budget(settings_json, repo_root):
    stop_gate = _load_module(repo_root / ".claude" / "hooks" / "stop_gate.py", "_settings_stop_gate")
    stop_hooks = settings_json["hooks"]["Stop"][0]["hooks"]
    assert len(stop_hooks) == 1
    timeout = stop_hooks[0]["timeout"]
    assert timeout == 300
    assert timeout >= stop_gate.PRECOMMIT_TIMEOUT_SECONDS + 60


# ---- F3: wrapper subprocess behavior -------------------------------------------

_BASH = shutil.which("bash") or (
    str(Path(shutil.which("git")).parent.parent / "bin" / "bash.exe") if shutil.which("git") else None
)
_HAS_BASH = bool(_BASH and Path(_BASH).exists())

_GOOD_SHIM = (
    "#!/bin/sh\n"
    'if [ "$1" != "-c" ]; then printf \'%s\\n\' "$(basename "$0")" >> "$USED_FILE"; fi\n'
    'exec "$REAL_PYTHON" "$@"\n'
)
_STUB_SHIM = (
    "#!/bin/sh\n" "echo 'Python was not found; run without arguments to install from the Microsoft Store.' >&2\n" "exit 49\n"
)


def _make_shim(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_wrapper(
    tmp_path: Path, repo_root: Path, *, present: set[str], windowsapps: str | None = None, script: str, code: int
):
    """Run the F3 wrapper snippet in an isolated PATH. ``present`` is the set of
    good ``python``/``python3`` shim names to place on PATH; ``windowsapps`` is
    None, "stub" (the Microsoft Store alias, exits 49) or "real" (a Store-installed
    real interpreter, exits 0 on ``-c ""``). Returns (returncode, used, stderr).

    Platform truth these tests pin: a non-zero exit from the wrapper is loud (visible
    to Claude) only for the two guards (exit 2) and the other eight hooks (exit 1) —
    and only when the wrapper itself detects the missing-interpreter case. When the
    shell cannot be spawned at all, or the resolved interpreter is found but `exec`
    fails (`sh` exits 126/127), no wrapper can help: the guards fail **open** for
    that call (README "Interpreter & shell prerequisites")."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    used_file = tmp_path / "used.txt"
    used_file.write_text("", encoding="utf-8")

    for name in present:
        _make_shim(bin_dir, name, _GOOD_SHIM)

    path_entries = [str(bin_dir)]
    if windowsapps == "stub":
        wa_dir = tmp_path / "WindowsApps"
        _make_shim(wa_dir, "python3", _STUB_SHIM)
        path_entries.append(str(wa_dir))
    elif windowsapps == "real":
        wa_dir = tmp_path / "WindowsApps"
        _make_shim(wa_dir, "python3", _GOOD_SHIM)
        path_entries.append(str(wa_dir))

    # `sh` itself must stay resolvable: the wrapper's outer command is `sh -c
    # '...'`, spawned as a subprocess of bash. Append the directory bash/sh
    # lives in — verified this session (Git's usr/bin) to contain neither
    # python nor python3. If it did (POSIX branch, unverified on this host),
    # a python/python3 found there would be indistinguishable from "present"
    # and this helper would need to copy sh into an isolated dir instead.
    sh_dir = Path(_BASH).parent
    if (sh_dir / "python").exists() or (sh_dir / "python3").exists():
        pytest.skip("sh's own directory carries a python/python3 (POSIX branch, unverified here)")
    path_entries.append(str(sh_dir))

    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(path_entries)
    env["USED_FILE"] = str(used_file)
    env["REAL_PYTHON"] = sys.executable
    env["CLAUDE_PROJECT_DIR"] = str(repo_root)

    command = WRAPPER.format(code=code, script=script)
    proc = subprocess.run(
        [_BASH, "-c", command],
        input="{}",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    used = [line for line in used_file.read_text(encoding="utf-8").splitlines() if line]
    return proc.returncode, used, proc.stderr


pytestmark_bash = pytest.mark.skipif(not _HAS_BASH, reason="bash not found on PATH; F3 wrapper subprocess tests skipped")


@pytestmark_bash
@pytest.mark.parametrize(
    "case, present, windowsapps, expected_used",
    [
        ("python3-only", {"python3"}, None, ["python3"]),
        ("python-only", {"python"}, None, ["python"]),
        ("both", {"python", "python3"}, None, ["python"]),
        ("windowsapps-stub-then-python3", {"python3"}, "stub", ["python3"]),
    ],
)
def test_wrapper_prefers_python_then_python3(tmp_path, repo_root, case, present, windowsapps, expected_used):
    rc, used, stderr = _run_wrapper(
        tmp_path, repo_root, present=present, windowsapps=windowsapps, script="guard_dangerous_git.py", code=2
    )
    assert rc == 0, f"{case}: stderr={stderr!r}"
    assert used == expected_used, f"{case}: used={used!r}"


@pytestmark_bash
@pytest.mark.parametrize(
    "case, windowsapps, script, code, expected_rc",
    [
        ("guard", None, "guard_dangerous_git.py", 2, 2),
        ("non-guard", None, "session_start.py", 1, 1),
        ("windowsapps-stub-only", "stub", "guard_dangerous_git.py", 2, 2),
    ],
)
def test_wrapper_without_interpreter_is_loud(tmp_path, repo_root, case, windowsapps, script, code, expected_rc):
    rc, used, stderr = _run_wrapper(tmp_path, repo_root, present=set(), windowsapps=windowsapps, script=script, code=code)
    assert rc == expected_rc, f"{case}: stderr={stderr!r}"
    assert not used
    assert "no working python or python3 on PATH" in stderr


@pytestmark_bash
def test_wrapper_uses_store_python(tmp_path, repo_root):
    """A WindowsApps/python3 shim that exits 0 on `-c ""` (a Store-installed real
    interpreter, not the stub) is probed and used, not skipped blindly."""
    rc, used, stderr = _run_wrapper(
        tmp_path, repo_root, present=set(), windowsapps="real", script="guard_dangerous_git.py", code=2
    )
    assert rc == 0, f"stderr={stderr!r}"
    assert used == ["python3"]


def test_guard_docstring_has_no_py_fallback_claim(repo_root):
    guard = _load_module(repo_root / ".claude" / "hooks" / "guard_dangerous_git.py", "_settings_guard_dangerous_git")
    doc = guard.__doc__ or ""
    assert "Fallback interpreter" not in doc
    assert "``py``" not in doc


# ---- F4: deny-list pins ---------------------------------------------------------


def test_deny_list_pins_env_patterns(settings_json):
    deny = settings_json["permissions"]["deny"]
    for pattern in (
        "Bash(cat .env*)",
        "Bash(type .env*)",
        "Read(.env)",
        "Read(.env.local)",
        "Read(.env.production)",
        "Read(.env.development)",
        "Bash(cat .env)",
        "Bash(type .env)",
    ):
        assert pattern in deny, f"missing deny pattern: {pattern}"
    assert "Read(**/.env*)" not in deny, "would deny the tracked .env.template/.env.minimal too"
    allow = settings_json["permissions"]["allow"]
    assert "Bash(cat:*)" in allow, "the hook, not the allow list, is the control for cat"


def test_read_matcher_absent_by_decision(settings_json):
    """Decided 2026-09-18 (Q4): the Read tool is not routed through guard_secrets;
    the deny list is the only control for it. A future lot that adds the hook
    must flip this test deliberately."""
    for group in settings_json["hooks"].get("PreToolUse", []):
        matcher = group.get("matcher") or ""
        if "Read" in matcher.split("|"):
            pytest.fail(f"a Read matcher was added to PreToolUse ({matcher}); guard_secrets is not wired to it yet")
