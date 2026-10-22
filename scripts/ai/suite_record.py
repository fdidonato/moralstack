#!/usr/bin/env python
"""Record the full-suite result per code state and reuse it (harness speed only).

``suite_record.py run [pytest args]``   run the full suite, write ``.claude/.suite-result.json``.
``suite_record.py check [pytest args]`` exit 0 (and print the record) only when a PASSED,
                                         reusable record with the current fingerprint exists.

The fingerprint covers everything the suite can read: the raw working-tree content of
every tracked file, the content of every untracked non-ignored file, the interpreter and
all installed packages, the pytest arguments and the test-relevant environment. Any
mismatch means "run again". A FAILED or non-reusable record is never reused, and partial
runs (``-k``, ``--lf``, file paths, config overrides, ...) are refused so they can never
be recorded as the full suite. Only environment variable NAMES are stored, never values.

Option values must be attached with ``=`` (``--tb=short``, ``--maxprocesses=4``): only
``-m``, ``-n``, ``-p`` and ``--tb`` may take their value as the next token. Any other
space-separated value is read as a positional and refused.

Exit codes: ``run``: 0 PASSED and reusable, pytest's rc when FAILED, 10 when rc 0 but not
reusable; ``check``: 0 reuse, 1 otherwise; 2 usage error / cannot fingerprint (both).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
MARKER_REL = ".claude/.suite-result.json"
EXIT_NOT_REUSABLE = 10

_ENV_PREFIXES = ("MORALSTACK_", "OPENAI_", "PYTEST_", "SSL_CERT_", "MSTACK_", "IFBENCH_", "COV_CORE_")
_ENV_EXACT = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "PYTHONUTF8",
        "PYTHONHASHSEED",
        "PYTHONIOENCODING",
        "CLAUDE_CODE_GIT_BASH_PATH",
        "MEMORY_GUARD_SKIP",
        "CHANGELOG_GUARD_SKIP",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    }
)
# Per-process pytest variables: they match the PYTEST_ prefix but vary per process.
_ENV_EXCLUDED = frozenset(
    {
        "PYTEST_CURRENT_TEST",
        "PYTEST_XDIST_WORKER",
        "PYTEST_XDIST_WORKER_COUNT",
        "PYTEST_XDIST_TESTRUNUID",
        "PYTEST_VERSION",
    }
)

# Options that would turn the run into a partial/altered one (prefix match unless noted).
_REFUSED_LONG_PREFIX = (
    "--keyword",
    "--last-failed",
    "--stepwise",
    "--deselect",
    "--ignore",  # also --ignore-glob
    "--pdb",
    "--trace",
    "--override-ini",
    "--config-file",
    "--rootdir",
    "--confcutdir",
    "--collect-only",
    "--collectonly",
)
_REFUSED_LONG_EXACT = frozenset({"--lf", "--sw", "--sw-skip", "--co", "--pyargs", "--noconftest", "--no-summary"})
_REFUSED_SHORT = ("-k", "-o", "-c")
_ALLOWED_MARKERS = frozenset({"slow", "", "not slow"})

_SUMMARY_COUNT_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)\b")
_SUMMARY_TIME_RE = re.compile(r"\bin \d")


class FingerprintError(RuntimeError):
    """The code state cannot be fingerprinted (no git, no commit, not a repo...)."""


# --- fingerprint ------------------------------------------------------------------


def _git(root: Path | str, *args: str) -> bytes:
    cmd = ["git", "--no-optional-locks", "-C", str(root), *args]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise FingerprintError(f"cannot run git: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise FingerprintError(f"git {' '.join(args)} failed (rc {proc.returncode}): {detail}")
    return proc.stdout


def repo_root() -> Path:
    out = _git(Path.cwd(), "rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
    if not out:
        raise FingerprintError("not inside a git work tree")
    return Path(os.path.realpath(out))


def _norm_root(root: Path) -> str:
    return os.path.normcase(os.path.realpath(root))


def _file_digest(path: Path) -> str:
    try:
        if not path.exists():
            return "<missing>"
        with open(path, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return "<unreadable>"


def _digest_entries(root: Path, entries: list[str]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries):
        digest.update(entry.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(_file_digest(root / entry).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _split_z(raw: bytes) -> list[str]:
    return [os.fsdecode(part) for part in raw.split(b"\0") if part]


def _tracked_content_digest(root: Path) -> str:
    """Hash of the raw working-tree bytes of every tracked file (deletions change it)."""
    return _digest_entries(root, _split_z(_git(root, "ls-files", "-z")))


def _untracked_digest(root: Path, exclude: frozenset[str]) -> tuple[str, int]:
    entries = [e for e in _split_z(_git(root, "ls-files", "--others", "--exclude-standard", "-z")) if e not in exclude]
    return _digest_entries(root, entries), len(entries)


def _runtime() -> dict[str, Any]:
    def _version(name: str) -> str | None:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    return {
        "executable": os.path.normcase(os.path.realpath(sys.executable)),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "pytest": _version("pytest"),
        "pytest-xdist": _version("pytest-xdist"),
    }


def _package_lines() -> list[str]:
    lines = {
        f"{dist.metadata['Name'].lower()}=={dist.version}"
        for dist in importlib.metadata.distributions()
        if dist.metadata["Name"]
    }
    return sorted(lines)


def _env_selected(name: str) -> bool:
    upper = name.upper()
    if upper in _ENV_EXCLUDED:
        return False
    return upper in _ENV_EXACT or upper.startswith(_ENV_PREFIXES)


def _env_digest(env: dict[str, str]) -> tuple[str, list[str]]:
    selected = sorted((name.upper(), value) for name, value in env.items() if _env_selected(name))
    payload = json.dumps(selected, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest(), [n for n, _ in selected]


def compute(root: Path, args: list[str], env: dict[str, str]) -> tuple[str, dict[str, Any], list[str]]:
    """Return ``(fingerprint, components, env_names)``. ``git_head`` is display-only."""
    exclude = frozenset({MARKER_REL, MARKER_REL + ".tmp"})
    untracked, untracked_count = _untracked_digest(root, exclude)
    components: dict[str, Any] = {
        "schema": SCHEMA,
        "git_head": _git(root, "rev-parse", "HEAD").decode("ascii", "replace").strip(),
        "tracked_content_sha256": _tracked_content_digest(root),
        "untracked_sha256": untracked,
        "untracked_count": untracked_count,
        "runtime": _runtime(),
        "packages_sha256": hashlib.sha256("\n".join(_package_lines()).encode("utf-8")).hexdigest(),
        "args": list(args),
    }
    env_hash, env_names = _env_digest(env)
    return _fingerprint(components, env_hash), components, env_names


def _fingerprint(components: dict[str, Any], env_hash: str) -> str:
    hashed = {k: v for k, v in components.items() if k != "git_head"}
    payload = json.dumps({**hashed, "env": env_hash}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- pytest output ----------------------------------------------------------------


def parse_summary(output: str) -> tuple[str, dict[str, int]]:
    counts = {k: 0 for k in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected", "warnings")}
    for line in reversed(output.splitlines()):
        if _SUMMARY_COUNT_RE.search(line) and _SUMMARY_TIME_RE.search(line):
            for number, word in _SUMMARY_COUNT_RE.findall(line):
                key = {"error": "errors", "warning": "warnings"}.get(word, word)
                counts[key] = int(number)
            return line.strip("= \t\r"), counts
    return "", counts


def _run_pytest(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """Run pytest with the inherited environment; terminate the child on interrupt."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out, _ = proc.communicate()
    except (KeyboardInterrupt, SystemExit):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    return proc.returncode, out.decode("utf-8", "replace")


# --- argument validation ----------------------------------------------------------


def _marker_value(token: str, nxt: str | None) -> tuple[str | None, bool]:
    """Return ``(value, consumed_next)`` for a ``-m`` token."""
    if token == "-m":
        return nxt, True
    value = token[2:]
    return (value[1:] if value.startswith("=") else value), False


def validate_args(args: list[str], root: Path) -> str | None:
    """Return a refusal message, or ``None`` when ``args`` are acceptable."""
    allowed_targets = {_norm_root(root), _norm_root(root / "tests")}
    skip_next = False
    for index, token in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        nxt = args[index + 1] if index + 1 < len(args) else None
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            if name in _REFUSED_LONG_EXACT or any(name.startswith(p) for p in _REFUSED_LONG_PREFIX):
                return f"refused option {token!r}: a partial or altered run must not be recorded as the full suite"
            if name == "--tb" and "=" not in token:
                skip_next = True
            continue
        if token.startswith("-") and token != "-":
            if any(token == s or token.startswith(s) for s in _REFUSED_SHORT):
                return f"refused option {token!r}: a partial or altered run must not be recorded as the full suite"
            if re.fullmatch(r"-q{2,}", token):
                return f"refused option {token!r}: reduced output hides the summary"
            if token.startswith("-m"):
                value, consumed = _marker_value(token, nxt)
                if value is None or value.strip() not in _ALLOWED_MARKERS:
                    return f"refused marker expression {value!r}: only 'slow', '' or 'not slow' are allowed"
                skip_next = consumed
            elif token == "-n" or token == "-p":
                if token == "-p" and nxt == "no:terminal":
                    return "refused '-p no:terminal': it hides the summary"
                skip_next = True
            elif token.startswith("-p") and token[2:] == "no:terminal":
                return "refused '-p no:terminal': it hides the summary"
            continue
        if "::" in token or token.endswith(".py"):
            return f"refused positional {token!r}: only the whole suite may be recorded"
        if ".." in Path(token).parts or _norm_root(root / token) not in allowed_targets:
            return (
                f"refused positional {token!r}: only '.' or 'tests' (the whole suite) may be recorded "
                "(option values must use '=', e.g. --maxprocesses=4; only -m/-n/-p/--tb take a separate value)"
            )
    return None


def _validate_all(args: list[str], root: Path) -> str | None:
    problem = validate_args(args, root)
    if problem:
        return problem
    try:
        addopts = shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
    except ValueError:
        return "malformed PYTEST_ADDOPTS"
    problem = validate_args(addopts, root)
    return f"PYTEST_ADDOPTS: {problem}" if problem else None


def _last_marker(tokens: list[str]) -> str | None:
    last: str | None = None
    for index, token in enumerate(tokens):
        if token == "-m" and index + 1 < len(tokens):
            last = tokens[index + 1]
        elif token.startswith("-m") and not token.startswith("--"):
            last = token[2:].lstrip("=")
    return last


def _pyproject_addopts(root: Path) -> str:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts", ""))
    except (OSError, tomllib.TOMLDecodeError):
        return ""


# --- commands ---------------------------------------------------------------------


def _out(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve(args: list[str]) -> tuple[Path, str | None]:
    root = repo_root()
    return root, _validate_all(args, root)


def cmd_run(args: list[str]) -> int:
    try:
        root, problem = _resolve(args)
    except FingerprintError as exc:
        _out(f"suite_record: cannot fingerprint ({exc}); run `python -m pytest` directly")
        return 2
    if problem:
        _out(f"suite_record: {problem}")
        return 2
    try:
        fp_before, components, env_names = compute(root, args, dict(os.environ))
    except FingerprintError as exc:
        _out(f"suite_record: cannot fingerprint ({exc}); run `python -m pytest` directly")
        return 2

    started = _now()
    t0 = time.monotonic()
    command = [sys.executable, "-m", "pytest", *args]
    rc, output = _run_pytest(command, root)
    duration = round(time.monotonic() - t0, 1)
    finished = _now()

    try:
        fp_after = compute(root, args, dict(os.environ))[0]
    except FingerprintError:
        fp_after = ""
        refingerprint_failed = True
    else:
        refingerprint_failed = False
    summary, counts = parse_summary(output)
    passed = rc == 0 and counts["passed"] > 0 and counts["failed"] == 0 and counts["errors"] == 0
    outcome = "PASSED" if passed else "FAILED"
    reusable = passed and fp_after == fp_before
    reason = ""
    if not passed:
        reason = "no test passed" if rc == 0 else f"pytest exit code {rc}"
    elif not reusable:
        reason = (
            "could not re-fingerprint after the run"
            if refingerprint_failed
            else "tree or environment changed during the run"
        )

    runtime = components["runtime"]
    addopts = _pyproject_addopts(root)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "root": _norm_root(root),
        "fingerprint": fp_before,
        "components": components,
        "env_names": env_names,
        "outcome": outcome,
        "reusable": reusable,
        "reason": reason,
        "exit_code": rc,
        "summary": summary,
        "counts": counts,
        "python": runtime["python"],
        "pytest": runtime["pytest"],
        "xdist": runtime["pytest-xdist"],
        "command": command,
        "marker_expression": _last_marker(shlex.split(addopts) + args),
        "addopts": addopts,
        "started_at": started,
        "finished_at": finished,
        "duration_s": duration,
    }
    marker = root / MARKER_REL
    tmp = marker.with_name(marker.name + ".tmp")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, marker)
    except OSError as exc:
        _out(f"suite_record: cannot write the record via {tmp}: {exc}")
        return 2

    lines = output.splitlines()
    _out("\n".join(lines if outcome == "FAILED" else lines[-40:]))
    _out(f"suite_record: {outcome} reusable={reusable} fingerprint={fp_before[:12]} {summary}")
    if reason:
        _out(f"suite_record: reason: {reason}")
    if outcome == "PASSED" and reusable:
        return 0
    if outcome == "FAILED" and rc != 0:
        return rc
    return EXIT_NOT_REUSABLE


def _load_record(marker: Path) -> tuple[dict[str, Any] | None, str]:
    if not marker.is_file():
        return None, "no record on disk"
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"record unreadable or corrupt ({type(exc).__name__})"
    required = ("schema", "fingerprint", "outcome", "reusable", "components", "root")
    if not isinstance(record, dict) or any(key not in record for key in required):
        return None, "record malformed"
    if record["schema"] != SCHEMA or not isinstance(record["components"], dict):
        return None, "record schema mismatch"
    return record, ""


def cmd_check(args: list[str]) -> int:
    try:
        root, problem = _resolve(args)
    except FingerprintError as exc:
        _out(f"suite_record: cannot fingerprint ({exc}); re-run the suite")
        return 2
    if problem:
        _out(f"suite_record: {problem}")
        return 2
    record, why = _load_record(root / MARKER_REL)
    if record is None:
        _out(f"suite_record: no reusable record: {why}; run `suite_record.py run`")
        return 1
    if record["root"] != _norm_root(root):
        _out("suite_record: no reusable record: the record belongs to another working tree; run again")
        return 1
    try:
        current, components, env_names = compute(root, args, dict(os.environ))
    except FingerprintError as exc:
        _out(f"suite_record: cannot fingerprint ({exc}); re-run the suite")
        return 2
    if current != record["fingerprint"]:
        stored = record["components"]
        differing = sorted(
            key for key in set(components) | set(stored) if key != "git_head" and components.get(key) != stored.get(key)
        )
        if differing:
            _out(f"suite_record: fingerprint mismatch, changed components: {', '.join(differing)}; run again")
        else:
            _out(f"suite_record: test-relevant environment differs (names: {', '.join(env_names)}); run again")
        return 1
    if record["outcome"] != "PASSED" or record["reusable"] is not True:
        _out(f"suite_record: record is {record['outcome']} / not reusable: {record.get('reason', '')}; run again")
        return 1
    _out(json.dumps(record, indent=2, sort_keys=True))
    _out(f"suite_record: reuse PASSED record from {record.get('finished_at')}: {record.get('summary')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    if not argv or argv[0] not in ("run", "check"):
        _out("usage: suite_record.py run|check [--] [pytest args]")
        return 2
    rest = argv[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    return cmd_run(rest) if argv[0] == "run" else cmd_check(rest)


if __name__ == "__main__":
    sys.exit(main())
