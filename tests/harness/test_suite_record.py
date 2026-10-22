"""S2: ``scripts/ai/suite_record.py`` fingerprint, record and reuse semantics.

Everything runs against a throwaway git repository under ``tmp_path`` (copied from a
module-scoped base repo to keep the module fast on Windows). pytest itself is never
launched: ``_run_pytest`` is stubbed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ai" / "suite_record.py"
_SENTINEL = "sentinel-not-a-real-value-7f3a"
_GIT_ID = ["-c", "user.email=test@example.invalid", "-c", "user.name=test"]

if shutil.which("git") is None:  # pragma: no cover - environment dependent
    pytest.skip("git not available", allow_module_level=True)


def _load():
    spec = importlib.util.spec_from_file_location("_suite_record_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sr = _load()


def _clean_git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", *_GIT_ID, "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture(scope="module")
def base_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("suite_record_base") / "repo"
    root.mkdir()
    empty_cfg = root.parent / "empty.gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    env = {**_clean_git_env(), "GIT_CONFIG_GLOBAL": str(empty_cfg)}
    _git(root, "init", "-q", env=env)
    _git(root, "config", "core.autocrlf", "false", env=env)
    _git(root, "config", "core.safecrlf", "false", env=env)
    _git(root, "checkout", "-q", "-b", "main", env=env)
    (root / "a.txt").write_bytes(b"alpha\n")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_bytes(b"beta\n")
    (root / ".gitignore").write_bytes(b"ignored.txt\n")
    _git(root, "add", "-A", env=env)
    _git(root, "commit", "-q", "-m", "init", env=env)
    return root


@pytest.fixture
def repo(base_repo, tmp_path, monkeypatch) -> Path:
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)
    empty_cfg = tmp_path / "empty.gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    target = tmp_path / "work"
    shutil.copytree(base_repo, target)
    monkeypatch.chdir(target)
    monkeypatch.setattr(sr, "_package_lines", lambda: ["pkg==1"])
    return target


@pytest.fixture
def fast(tmp_path, monkeypatch) -> Path:
    """No git, no fingerprinting: for outcome/exit-code tests that ignore the fingerprint."""
    monkeypatch.setattr(sr, "repo_root", lambda: tmp_path)
    components = {"runtime": {"python": "3", "pytest": None, "pytest-xdist": None}}
    monkeypatch.setattr(sr, "compute", lambda _root, _args, _env: ("fp-const", components, []))
    return tmp_path


def fp(repo: Path, args=(), env=None) -> str:
    return sr.compute(repo, list(args), dict(env or {}))[0]


def commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "next")


# --- fingerprint: tracked files (D2) ------------------------------------------------


def test_compute_is_deterministic(repo):
    assert sr.compute(repo, [], {}) == sr.compute(repo, [], {})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: (r / "a.txt").write_bytes(b"alpha2\n"),
        lambda r: (r / "a.txt").write_bytes(b"alpha \n"),  # whitespace only
        lambda r: (r / "a.txt").write_bytes(b"alpha\r\n"),  # LF -> CRLF
        lambda r: (r / "bin.dat").write_bytes(b"\x00\x01\x03"),
        lambda r: (r / "sub" / "b.txt").write_bytes(b"beta2\n"),
        lambda r: (r / "a.txt").unlink(),
        lambda r: _git(r, "mv", "a.txt", "renamed.txt"),
        lambda r: (r / "a.txt").rename(r / "renamed.txt"),
    ],
    ids=["edit", "whitespace", "crlf", "binary", "subdir", "delete", "rename", "rename_unstaged"],
)
def test_tracked_change_changes_fingerprint(repo, mutate):
    before = fp(repo)
    mutate(repo)
    assert fp(repo) != before


def test_staging_same_content_keeps_fingerprint(repo):
    (repo / "a.txt").write_bytes(b"alpha2\n")
    before = fp(repo)
    _git(repo, "add", "a.txt")
    assert fp(repo) == before


def test_commit_of_identical_content_keeps_fingerprint(repo):
    (repo / "a.txt").write_bytes(b"alpha2\n")
    before_fp, before_comp, _ = sr.compute(repo, [], {})
    commit_all(repo)
    after_fp, after_comp, _ = sr.compute(repo, [], {})
    assert after_comp["git_head"] != before_comp["git_head"]
    assert after_fp == before_fp


def test_crlf_change_detected_with_autocrlf_true(repo):
    _git(repo, "config", "core.autocrlf", "true")
    before = fp(repo)
    (repo / "a.txt").write_bytes(b"alpha\r\n")
    assert fp(repo) != before


# --- fingerprint: untracked / ignored / marker ------------------------------------------


def test_untracked_file_and_its_edits_change_fingerprint(repo):
    before = fp(repo)
    (repo / "new.txt").write_bytes(b"x\n")
    added = fp(repo)
    assert added != before
    (repo / "new.txt").write_bytes(b"y\n")
    assert fp(repo) != added
    (repo / "new.txt").write_bytes(b"y\r\n")
    assert fp(repo) not in (before, added)


def test_untracked_nested_dir_changes_fingerprint(repo):
    before = fp(repo)
    (repo / "d1" / "d2").mkdir(parents=True)
    assert fp(repo) == before  # an empty untracked dir is a documented residual
    (repo / "d1" / "d2" / "f.txt").write_bytes(b"1")
    assert fp(repo) != before


def test_git_add_of_new_file_keeps_fingerprint(repo):
    (repo / "new.txt").write_bytes(b"x\n")
    before = fp(repo)
    _git(repo, "add", "new.txt")
    assert fp(repo) == before


def test_git_rm_cached_round_trip_keeps_fingerprint(repo):
    before = fp(repo)
    _git(repo, "rm", "--cached", "-q", "a.txt")
    assert fp(repo) == before
    _git(repo, "commit", "-q", "-m", "untrack")
    assert fp(repo) == before
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "retrack")
    assert fp(repo) == before


def test_worktree_deletion_matches_committed_deletion(repo):
    _, comp_before, _ = sr.compute(repo, [], {})
    before = fp(repo)
    (repo / "a.txt").unlink()
    deleted, comp_deleted, _ = sr.compute(repo, [], {})
    assert deleted != before
    assert comp_deleted["file_count"] == comp_before["file_count"] - 1
    _git(repo, "rm", "-q", "a.txt")
    assert fp(repo) == deleted
    _git(repo, "commit", "-q", "-m", "delete")
    assert fp(repo) == deleted


def test_unignored_file_appearing_changes_fingerprint(repo):
    before = fp(repo)
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a", encoding="utf-8") as handle:
        handle.write("excl.txt\n")
    (repo / "excl.txt").write_bytes(b"hidden\n")
    fp_a = fp(repo)
    assert fp_a == before
    exclude.write_text("", encoding="utf-8")
    assert fp(repo) != fp_a


def test_conflicted_merge_paths_are_deduplicated(repo):
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "a.txt").write_bytes(b"other\n")
    _git(repo, "commit", "-q", "-am", "other edit")
    _git(repo, "checkout", "-q", "main")
    (repo / "a.txt").write_bytes(b"main\n")
    _git(repo, "commit", "-q", "-am", "main edit")
    proc = subprocess.run(["git", *_GIT_ID, "-C", str(repo), "merge", "other"], check=False, capture_output=True)
    assert proc.returncode == 1  # conflict: a.txt is listed once per stage
    conflicted, comp, _ = sr.compute(repo, [], {})
    assert comp["file_count"] == 4
    _git(repo, "add", "a.txt")
    assert fp(repo) == conflicted


def test_unreadable_entry_hashes_sentinel():
    assert sr._file_digest(_SCRIPT.parent) == "<unreadable>"
    assert "<missing>" not in _SCRIPT.read_text(encoding="utf-8")


def test_ignored_marker_and_tmp_do_not_change_fingerprint(repo):
    before = fp(repo)
    (repo / "ignored.txt").write_bytes(b"z")
    (repo / ".claude").mkdir()
    (repo / ".claude" / ".suite-result.json").write_text("{}", encoding="utf-8")
    (repo / ".claude" / ".suite-result.json.tmp").write_text("{}", encoding="utf-8")
    assert fp(repo) == before


# --- fingerprint: env, runtime, args ---------------------------------------------------


@pytest.mark.parametrize(
    "name", ["MORALSTACK_X", "PYTEST_ADDOPTS", "SSL_CERT_FILE", "PATH", "PYTHONHASHSEED", "OPENAI_API_BASE"]
)
def test_relevant_env_changes_digest(name):
    base = sr._env_digest({})[0]
    assert sr._env_digest({name: "1"})[0] != base
    assert sr._env_digest({name: ""})[0] != base  # empty vs unset


@pytest.mark.parametrize(
    "name",
    [
        "CLAUDE_CODE_SESSION_ID",
        "FOO",
        "PYTEST_CURRENT_TEST",
        "PYTEST_XDIST_WORKER",
        "PYTEST_XDIST_TESTRUNUID",
        "PYTEST_XDIST_WORKER_COUNT",
        "PYTEST_VERSION",
    ],
)
def test_irrelevant_env_does_not_change_digest(name):
    assert sr._env_digest({name: "1"}) == sr._env_digest({})


def test_env_relevance_reaches_the_fingerprint(repo):
    assert fp(repo, env={"MORALSTACK_X": "1"}) != fp(repo, env={})


def test_package_and_pytest_version_change_fingerprint(repo, monkeypatch):
    before = fp(repo)
    monkeypatch.setattr(sr, "_package_lines", lambda: ["pkg==2"])
    assert fp(repo) != before
    monkeypatch.setattr(sr, "_package_lines", lambda: ["pkg==1"])
    real = sr._runtime()
    monkeypatch.setattr(sr, "_runtime", lambda: {**real, "pytest": "0.0.1"})
    assert fp(repo) != before


def test_args_change_fingerprint(repo):
    assert fp(repo, []) != fp(repo, ["-m", "slow"])


# --- secrets never reach the marker (no env values) -------------------------------------


def _stub_pytest(monkeypatch, rc=0, out="== 3 passed in 0.1s ==", side_effect=None, calls=None):
    def fake(cmd, cwd):
        if calls is not None:
            calls.append((cmd, cwd))
        if side_effect:
            side_effect()
        return rc, out

    monkeypatch.setattr(sr, "_run_pytest", fake)


def _marker(repo: Path) -> Path:
    return repo / ".claude" / ".suite-result.json"


def test_marker_and_output_never_hold_env_values(repo, monkeypatch, capsys):
    monkeypatch.setenv("MORALSTACK_PRINCIPAL_HMAC_SECRET", _SENTINEL)
    _stub_pytest(monkeypatch)
    assert sr.cmd_run([]) == 0
    assert sr.cmd_check([]) == 0
    captured = capsys.readouterr()
    text = _marker(repo).read_text(encoding="utf-8")
    assert _SENTINEL not in text
    assert _SENTINEL not in captured.out + captured.err
    record = json.loads(text)
    assert "MORALSTACK_PRINCIPAL_HMAC_SECRET" in record["env_names"]
    assert _SENTINEL not in " ".join(record["command"])


def test_run_passes_inherited_env_cwd_and_args_verbatim(repo, monkeypatch):
    calls: list = []
    _stub_pytest(monkeypatch, calls=calls)
    assert sr.cmd_run(["-m", "slow", "-x"]) == 0
    cmd, cwd = calls[0]
    assert cmd == [sys.executable, "-m", "pytest", "-m", "slow", "-x"]
    assert Path(os.path.realpath(cwd)) == Path(os.path.realpath(repo))


# --- check semantics -----------------------------------------------------------------


def test_check_reuses_matching_passed_record(repo, monkeypatch, capsys):
    _stub_pytest(monkeypatch)
    assert sr.cmd_run([]) == 0
    capsys.readouterr()
    assert sr.cmd_check([]) == 0
    assert "reuse PASSED record" in capsys.readouterr().out


def test_check_after_edit_names_changed_component_and_revert_restores(repo, monkeypatch, capsys):
    _stub_pytest(monkeypatch)
    sr.cmd_run([])
    (repo / "a.txt").write_bytes(b"changed\n")
    capsys.readouterr()
    assert sr.cmd_check([]) == 1
    out = capsys.readouterr().out
    assert "changed components: content_sha256;" in out
    assert "untracked" not in out
    (repo / "a.txt").write_bytes(b"alpha\n")
    assert sr.cmd_check([]) == 0


def test_check_after_adding_file_names_content_and_count(repo, monkeypatch, capsys):
    _stub_pytest(monkeypatch)
    sr.cmd_run([])
    (repo / "new.txt").write_bytes(b"x\n")
    capsys.readouterr()
    assert sr.cmd_check([]) == 1
    assert "changed components: content_sha256, file_count;" in capsys.readouterr().out


def test_check_reuses_record_after_committing_new_file(repo, monkeypatch):
    _stub_pytest(monkeypatch)
    (repo / "new.txt").write_bytes(b"x\n")
    (repo / "sub" / "n.txt").write_bytes(b"n\n")
    assert sr.cmd_run([]) == 0
    _, comp_before, _ = sr.compute(repo, [], {})
    commit_all(repo)
    # The marker is now tracked by git yet must stay out of the fingerprint.
    listed = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert sr.MARKER_REL in listed
    assert sr.cmd_check([]) == 0
    _, comp_after, _ = sr.compute(repo, [], {})
    assert comp_after["content_sha256"] == comp_before["content_sha256"]
    assert comp_after["file_count"] == comp_before["file_count"]
    assert comp_after["git_head"] != comp_before["git_head"]


def test_schema_1_record_is_rejected_cleanly(repo, monkeypatch, capsys):
    _stub_pytest(monkeypatch)
    sr.cmd_run([])
    data = json.loads(_marker(repo).read_text(encoding="utf-8"))
    data["schema"] = 1
    data["components"] = {"tracked_content_sha256": "x", "untracked_sha256": "y", "untracked_count": 0}
    _marker(repo).write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    assert sr.cmd_check([]) == 1
    out = capsys.readouterr().out
    assert "schema mismatch" in out and "Traceback" not in out


def test_check_different_args_names_args(repo, monkeypatch, capsys):
    _stub_pytest(monkeypatch)
    sr.cmd_run([])
    capsys.readouterr()
    assert sr.cmd_check(["-m", "slow"]) == 1
    assert "changed components: args" in capsys.readouterr().out


def test_check_env_difference_without_component_difference(repo, monkeypatch, capsys):
    _stub_pytest(monkeypatch)
    sr.cmd_run([])
    monkeypatch.setenv("MORALSTACK_NEW_FLAG", "1")
    capsys.readouterr()
    assert sr.cmd_check([]) == 1
    assert "environment differs" in capsys.readouterr().out


def test_failed_record_is_never_reused(repo, monkeypatch, capsys):
    _stub_pytest(monkeypatch, rc=1, out="== 1 failed, 2 passed in 0.5s ==")
    assert sr.cmd_run([]) == 1
    data = json.loads(_marker(repo).read_text(encoding="utf-8"))
    assert data["outcome"] == "FAILED"
    assert sr.cmd_check([]) == 1
    data["reusable"] = True  # hand-edited
    _marker(repo).write_text(json.dumps(data), encoding="utf-8")
    assert sr.cmd_check([]) == 1


def test_non_reusable_passed_record_is_not_reused(repo, monkeypatch):
    _stub_pytest(monkeypatch)
    sr.cmd_run([])
    data = json.loads(_marker(repo).read_text(encoding="utf-8"))
    data["reusable"] = False
    _marker(repo).write_text(json.dumps(data), encoding="utf-8")
    assert sr.cmd_check([]) == 1
    del data["reusable"]
    _marker(repo).write_text(json.dumps(data), encoding="utf-8")
    assert sr.cmd_check([]) == 1


def test_check_without_or_with_bad_marker_exits_1_without_traceback(repo, capsys):
    assert sr.cmd_check([]) == 1
    _marker(repo).parent.mkdir(exist_ok=True)
    for content in ("{not json", "[]", '{"schema": 1}', '"text"'):
        _marker(repo).write_text(content, encoding="utf-8")
        assert sr.cmd_check([]) == 1
    assert "Traceback" not in capsys.readouterr().out


def test_record_with_other_schema_is_rejected(repo, monkeypatch, capsys):
    _stub_pytest(monkeypatch)
    sr.cmd_run([])
    data = json.loads(_marker(repo).read_text(encoding="utf-8"))
    data["schema"] = sr.SCHEMA + 1
    _marker(repo).write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()
    assert sr.cmd_check([]) == 1
    assert "schema mismatch" in capsys.readouterr().out


def test_check_rejects_record_of_another_root(repo, monkeypatch):
    _stub_pytest(monkeypatch)
    sr.cmd_run([])
    data = json.loads(_marker(repo).read_text(encoding="utf-8"))
    data["root"] = "/somewhere/else"
    _marker(repo).write_text(json.dumps(data), encoding="utf-8")
    assert sr.cmd_check([]) == 1


# --- run outcomes (DC4) -----------------------------------------------------------------


def test_tree_change_during_run_is_not_reusable(repo, monkeypatch):
    _stub_pytest(monkeypatch, side_effect=lambda: (repo / "leftover.txt").write_text("x", encoding="utf-8"))
    assert sr.cmd_run([]) == sr.EXIT_NOT_REUSABLE
    data = json.loads(_marker(repo).read_text(encoding="utf-8"))
    assert data["outcome"] == "PASSED" and data["reusable"] is False
    assert "changed during the run" in data["reason"]


def test_failed_run_returns_pytest_rc(fast, monkeypatch):
    _stub_pytest(monkeypatch, rc=1, out="== 1 failed, 2 passed in 0.5s ==")
    assert sr.cmd_run([]) == 1


def test_refingerprint_failure_after_run_is_not_reusable_with_its_own_reason(fast, monkeypatch):
    calls = {"n": 0}
    original = sr.compute

    def flaky(root, args, env):
        calls["n"] += 1
        if calls["n"] > 1:
            raise sr.FingerprintError("git vanished")
        return original(root, args, env)

    monkeypatch.setattr(sr, "compute", flaky)
    _stub_pytest(monkeypatch)
    assert sr.cmd_run([]) == sr.EXIT_NOT_REUSABLE
    data = json.loads(_marker(fast).read_text(encoding="utf-8"))
    assert data["outcome"] == "PASSED" and data["reusable"] is False
    assert data["reason"] == "could not re-fingerprint after the run"


@pytest.mark.parametrize("rc", [1, 2, 3, 4, 5])
def test_nonzero_rc_is_failed_whatever_the_summary_says(fast, monkeypatch, rc):
    _stub_pytest(monkeypatch, rc=rc, out="== 5 passed in 0.5s ==")
    assert sr.cmd_run([]) == rc
    assert json.loads(_marker(fast).read_text(encoding="utf-8"))["outcome"] == "FAILED"


@pytest.mark.parametrize("out", ["== no tests ran in 0.01s ==", "", "== 4 skipped in 0.1s =="])
def test_rc0_without_a_passed_test_is_failed_exit_10(fast, monkeypatch, out):
    _stub_pytest(monkeypatch, rc=0, out=out)
    assert sr.cmd_run([]) == sr.EXIT_NOT_REUSABLE
    assert json.loads(_marker(fast).read_text(encoding="utf-8"))["outcome"] == "FAILED"


def test_os_replace_failure_exits_2_naming_tmp(fast, monkeypatch, capsys):
    _stub_pytest(monkeypatch)

    def boom(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(sr.os, "replace", boom)
    assert sr.cmd_run([]) == 2
    out = capsys.readouterr().out
    assert ".suite-result.json.tmp" in out and "Traceback" not in out


def test_run_pytest_interrupt_terminates_child(monkeypatch):
    events: list[str] = []

    class FakeProc:
        returncode = None

        def communicate(self):
            raise KeyboardInterrupt

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")

        def kill(self):
            events.append("kill")

    monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **k: FakeProc())
    with pytest.raises(KeyboardInterrupt):
        sr._run_pytest(["x"], Path("."))
    assert events[0] == "terminate"


# --- parse_summary -------------------------------------------------------------------


@pytest.mark.parametrize(
    "output, expected",
    [
        ("3 passed in 0.10s", {"passed": 3}),
        ("== 1 failed, 2 passed, 4 skipped, 1 warning in 2.0s ==", {"failed": 1, "passed": 2, "skipped": 4, "warnings": 1}),
        ("= 2 passed, 3 deselected, 2 errors in 1s =", {"passed": 2, "deselected": 3, "errors": 2}),
        ("FAILED t.py::a - 1 failed\n== 7 passed in 1.0s ==", {"passed": 7}),
        ("== no tests ran in 0.01s ==", {}),
    ],
)
def test_parse_summary(output, expected):
    _line, counts = sr.parse_summary(output)
    assert {k: v for k, v in counts.items() if v} == expected


# --- refusals (DC3, D3, RC2) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["tests/test_x.py"],
        ["a::b"],
        ["-k", "foo"],
        ["-kfoo"],
        ["--lf"],
        ["--last-failed"],
        ["--sw"],
        ["--deselect", "x"],
        ["--ignore=tests/harness"],
        ["--ignore-glob", "x"],
        ["-o", "testpaths=x"],
        ["-o", "addopts="],
        ["-c", "x.ini"],
        ["--rootdir", "x"],
        ["--pyargs"],
        ["--co"],
        ["--collect-only"],
        ["-qq"],
        ["--no-summary"],
        ["--noconftest"],
        ["-p", "no:terminal"],
        ["-pno:terminal"],
        ["--sw-skip"],
        ["--stepwise-skip"],
        ["--override-ini=testpaths=x"],
        ["-otestpaths=x"],
        ["--maxprocesses", "4"],
        ["--pdb"],
        ["..", "-q"],
        ["docs"],
        ["-m", "not ui"],
        ["-m", "slow or ui"],
    ],
)
@pytest.mark.parametrize("op", ["run", "check"])
def test_refused_args_exit_2_and_never_run_pytest(tmp_path, monkeypatch, op, args):
    calls: list = []
    _stub_pytest(monkeypatch, calls=calls)
    monkeypatch.setattr(sr, "repo_root", lambda: tmp_path)
    fn = sr.cmd_run if op == "run" else sr.cmd_check
    assert fn(args) == 2
    assert calls == []


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["tests"],
        ["."],
        ["-m", "slow"],
        ["-m", ""],
        ["-m", "not slow"],
        ["-mslow"],
        ["-x", "-q"],
        ["-n", "0"],
        ["--tb=short"],
    ],
)
def test_accepted_args(tmp_path, args):
    assert sr.validate_args(args, tmp_path) is None


def test_accepted_args_run_end_to_end(repo, monkeypatch):
    _stub_pytest(monkeypatch)
    assert sr.cmd_run(["-m", "slow", "tests"]) == 0


def test_pytest_addopts_is_validated_too(tmp_path, monkeypatch):
    calls: list = []
    _stub_pytest(monkeypatch, calls=calls)
    monkeypatch.setattr(sr, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k x")
    assert sr.cmd_run([]) == 2
    monkeypatch.setenv("PYTEST_ADDOPTS", '-k "unterminated')
    assert sr.cmd_run([]) == 2
    assert calls == []
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 0")
    assert sr._validate_all([], tmp_path) is None


# --- fingerprint errors -----------------------------------------------------------------


def test_repo_without_commits_is_a_fingerprint_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in [k for k in os.environ if k.startswith("GIT_") and k != "GIT_CONFIG_NOSYSTEM"]:
        monkeypatch.delenv(key, raising=False)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q")
    monkeypatch.chdir(fresh)
    with pytest.raises(sr.FingerprintError):
        sr.compute(fresh, [], {})
    _stub_pytest(monkeypatch)
    assert sr.cmd_run([]) == 2
    assert sr.cmd_check([]) == 1
    assert "Traceback" not in capsys.readouterr().out


def test_git_missing_is_a_fingerprint_error(tmp_path, monkeypatch, capsys):
    def no_git(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(sr.subprocess, "run", no_git)
    monkeypatch.chdir(tmp_path)
    assert sr.cmd_run([]) == 2
    assert sr.cmd_check([]) == 2
    assert "Traceback" not in capsys.readouterr().out


def test_outside_a_repo_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.chdir(tmp_path)
    assert sr.cmd_run([]) == 2


def test_repo_root_from_a_subdirectory(repo, monkeypatch):
    monkeypatch.chdir(repo / "sub")
    assert Path(os.path.realpath(sr.repo_root())) == Path(os.path.realpath(repo))


def test_space_separated_option_value_refusal_hints_at_equals(tmp_path):
    message = sr.validate_args(["--maxprocesses", "4"], tmp_path)
    assert message is not None and "must use '='" in message
