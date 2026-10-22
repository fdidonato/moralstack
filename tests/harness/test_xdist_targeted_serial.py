"""S1: targeted pytest selections run serially (``tests/conftest.py`` xdist hook).

The hook ``pytest_xdist_auto_num_workers`` resolves ``-n auto`` (from ``addopts``) to 0
workers when the command selects part of the suite. These tests cover the pure helpers,
the hook's decision table and, through real child pytest processes, the registration
order with xdist (a unit test alone cannot prove the hook takes effect).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
_THIS = Path(__file__).resolve()
_IS_WINDOWS = sys.platform == "win32"
_NESTED = "MSTACK_NESTED_E2E"


@pytest.fixture(scope="module")
def root_conftest(request):
    """The already-loaded ``tests/conftest.py`` plugin (no re-import: it would
    double-wrap ``sqlite3.connect`` and the httpx/Jinja patches)."""
    want = os.path.normcase(os.path.realpath(_REPO / "tests" / "conftest.py"))
    found = [
        plugin
        for plugin in request.config.pluginmanager.get_plugins()
        if getattr(plugin, "__file__", None) and os.path.normcase(os.path.realpath(plugin.__file__)) == want
    ]
    assert len(found) == 1, f"expected exactly one loaded tests/conftest.py plugin, got {len(found)}"
    return found[0]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tests" / "harness").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("", encoding="utf-8")
    return root


def _targeted(mod, tree: Path, args, *, keyword="", inv: Path | None = None) -> bool:
    return mod._is_targeted_selection(args, keyword, tree, inv or tree, ["tests"])


# --- _is_targeted_selection -------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["tests"],
        ["tests/"],
        ["./tests"],
        ["."],
        ["tests", "tests/harness"],
        ["tests/../tests"],
        ["tests/test_x.py", "tests"],
    ],
)
def test_full_selection_is_not_targeted(root_conftest, tree, args):
    assert _targeted(root_conftest, tree, args) is False


def test_absolute_root_is_not_targeted(root_conftest, tree):
    assert _targeted(root_conftest, tree, [str(tree)]) is False


@pytest.mark.parametrize(
    "args",
    [
        ["tests/test_x.py"],
        ["tests/test_x.py::TestA::test_b[1]"],
        ["tests/test_x.py::T::t[a::b]"],
        ["tests/harness"],
        ["tests/harness/"],
        ["tests/does_not_exist.py"],
    ],
)
def test_partial_selection_is_targeted(root_conftest, tree, args):
    assert _targeted(root_conftest, tree, args) is True


def test_absolute_file_is_targeted(root_conftest, tree):
    assert _targeted(root_conftest, tree, [str(tree / "tests" / "test_x.py")]) is True


def test_keyword_makes_it_targeted_even_with_full_root(root_conftest, tree):
    assert _targeted(root_conftest, tree, ["tests"], keyword="foo") is True


def test_invocation_dir_inside_tests_subdir_is_targeted(root_conftest, tree):
    inv = tree / "tests" / "harness"
    assert _targeted(root_conftest, tree, [str(inv)], inv=inv) is True


def test_invocation_dir_tests_root_is_not_targeted(root_conftest, tree):
    inv = tree / "tests"
    assert _targeted(root_conftest, tree, [str(inv)], inv=inv) is False


def test_invocation_dir_outside_root_does_not_raise(root_conftest, tree, tmp_path):
    assert _targeted(root_conftest, tree, ["x.py"], inv=tmp_path.parent) is True


@pytest.mark.skipif(not _IS_WINDOWS, reason="backslashes and case-insensitivity are Windows semantics")
def test_windows_path_forms(root_conftest, tree):
    assert _targeted(root_conftest, tree, ["tests\\"]) is False
    assert _targeted(root_conftest, tree, ["TESTS"]) is False
    assert _targeted(root_conftest, tree, ["tests\\harness"]) is True
    drive_flipped = str(tree / "tests" / "test_x.py")
    drive_flipped = drive_flipped[0].swapcase() + drive_flipped[1:]
    assert _targeted(root_conftest, tree, [drive_flipped]) is True


# --- _explicit_numprocesses (DC7) -------------------------------------------------


@pytest.mark.parametrize(
    "tokens",
    [["-n", "4"], ["-nauto"], ["-n4"], ["-nlogical"], ["--numprocesses=2"], ["--numprocesses", "auto"]],
)
def test_explicit_numprocesses_detected(root_conftest, tokens):
    assert root_conftest._explicit_numprocesses(tokens) is True


@pytest.mark.parametrize(
    "tokens",
    [
        ["-q", "-x", "-m", "slow", "-rf"],
        ["--no-header"],
        ["--dist=load"],
        ["-p", "no:xdist"],
        ["--numproc=2"],  # argparse abbreviation: documented as unsupported
    ],
)
def test_non_numprocesses_tokens_not_detected(root_conftest, tokens):
    assert root_conftest._explicit_numprocesses(tokens) is False


# --- hook decision table ------------------------------------------------------------


def _config(tree: Path, cli, args, *, keyword="", testpaths=("tests",)):
    return SimpleNamespace(
        invocation_params=SimpleNamespace(args=tuple(cli), dir=tree),
        args=list(args),
        option=SimpleNamespace(keyword=keyword),
        rootpath=tree,
        getini=lambda name: list(testpaths),
    )


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)


def test_hook_declares_optionalhook(root_conftest):
    marker = getattr(root_conftest.pytest_xdist_auto_num_workers, "pytest_impl", None)
    assert marker is not None and marker.get("optionalhook") is True


def test_hook_targeted_returns_zero(root_conftest, tree, clean_env):
    cfg = _config(tree, ["tests/test_x.py"], ["tests/test_x.py"])
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) == 0


def test_hook_full_selection_returns_none(root_conftest, tree, clean_env):
    assert root_conftest.pytest_xdist_auto_num_workers(_config(tree, [], ["tests"])) is None
    for flags in (["-q"], ["-m", "slow"], ["-rf"], ["--lf"], ["--ff"], ["--sw"], ["--deselect", "x"]):
        assert root_conftest.pytest_xdist_auto_num_workers(_config(tree, flags, ["tests"])) is None


def test_hook_slow_marker_with_file_path_is_serial(root_conftest, tree, clean_env):
    cfg = _config(tree, ["-m", "slow", "tests/test_x.py"], ["tests/test_x.py"])
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) == 0


def test_hook_keyword_none_is_treated_as_no_keyword(root_conftest, tree, clean_env):
    assert root_conftest.pytest_xdist_auto_num_workers(_config(tree, [], ["tests"], keyword=None)) is None


def test_hook_empty_testpaths_uses_rootpath(root_conftest, tree, clean_env):
    cfg = _config(tree, [], [str(tree)], testpaths=())
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) is None


def test_hook_env_worker_count_wins(root_conftest, tree, monkeypatch, clean_env):
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "3")
    cfg = _config(tree, ["tests/test_x.py"], ["tests/test_x.py"])
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) is None


@pytest.mark.parametrize("cli", [["-n", "auto"], ["-nauto"], ["--numprocesses=4"], ["--numprocesses", "4"]])
def test_hook_explicit_cli_numprocesses_wins(root_conftest, tree, clean_env, cli):
    cfg = _config(tree, [*cli, "tests/test_x.py"], ["tests/test_x.py"])
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) is None


@pytest.mark.parametrize("addopts", ["-n auto", "-n4", "-nauto", "--numprocesses=4"])
def test_hook_explicit_addopts_numprocesses_wins(root_conftest, tree, monkeypatch, clean_env, addopts):
    monkeypatch.setenv("PYTEST_ADDOPTS", addopts)
    cfg = _config(tree, ["tests/test_x.py"], ["tests/test_x.py"])
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) is None


def test_hook_non_numprocesses_addopts_still_serial(root_conftest, tree, monkeypatch, clean_env):
    monkeypatch.setenv("PYTEST_ADDOPTS", "--dist=load -p no:xdist")
    cfg = _config(tree, ["tests/test_x.py"], ["tests/test_x.py"])
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) == 0


def test_hook_keyword_with_full_root_is_serial(root_conftest, tree, clean_env):
    cfg = _config(tree, ["tests/", "-k", "foo"], ["tests/"], keyword="foo")
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) == 0


@pytest.mark.parametrize("addopts", ['-k "foo', "--basetemp=C:\\x\\y"])
def test_hook_malformed_or_backslash_addopts_never_raises(root_conftest, tree, monkeypatch, clean_env, addopts):
    monkeypatch.setenv("PYTEST_ADDOPTS", addopts)
    cfg = _config(tree, ["tests/test_x.py"], ["tests/test_x.py"])
    result = root_conftest.pytest_xdist_auto_num_workers(cfg)
    assert result in (None, 0)
    if addopts.startswith("-k"):
        assert result is None  # ValueError from shlex -> default parallel behaviour


def test_hook_failure_returns_none_and_reports_on_stderr(root_conftest, tree, monkeypatch, clean_env, capfd):
    def boom(*_a, **_k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(root_conftest, "_is_targeted_selection", boom)
    cfg = _config(tree, ["tests/test_x.py"], ["tests/test_x.py"])
    assert root_conftest.pytest_xdist_auto_num_workers(cfg) is None
    err = capfd.readouterr().err
    assert "[tests/conftest] xdist auto-worker hook failed, keeping default:" in err
    assert "synthetic failure" in err


# --- end to end: real registration order with xdist (F3, RC1) -----------------------

_UNIT_NODE = f"{_THIS}::test_explicit_numprocesses_detected"


def _child_env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "COV_CORE_", "COVERAGE_")) and k != "MSTACK_NESTED_E2E"
    }
    env[_NESTED] = "1"
    env.update(extra)
    return env


def _run_child(args: list[str], tmp_path: Path, **env_extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'bt'}", *args],
        cwd=str(_REPO),
        env=_child_env(**env_extra),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


@pytest.fixture
def not_nested():
    if os.environ.get(_NESTED):
        pytest.skip("recursion guard: already inside a nested e2e child")


def test_e2e_targeted_unit_test_runs_serially(not_nested, tmp_path):
    proc = _run_child([_UNIT_NODE], tmp_path)
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert "created:" not in out
    assert "bringing up nodes" not in out
    assert "passed" in out


def test_e2e_explicit_n_still_starts_workers(not_nested, tmp_path):
    """Negative control: without this the serial assertion above could pass vacuously."""
    proc = _run_child([_UNIT_NODE, "-n", "2"], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "created: 2/2 workers" in proc.stdout


def test_e2e_full_selection_stays_parallel(not_nested, tmp_path):
    """Flags only (args resolve to the testpaths root): workers must be created.

    ``--ignore=tests`` is NOT usable here: it ignores nothing and would run the whole suite.
    Ignoring the sub-paths does narrow collection (cheaper child); ``-m no_such_marker``
    stays as a guard so the child can never run real tests.
    """
    narrow = ["--ignore-glob=tests/test_*.py"] + [
        f"--ignore=tests/{name}" for name in ("governance_invariants", "harness", "orchestration", "prompts_golden")
    ]
    proc = _run_child(["-m", "no_such_marker", *narrow], tmp_path, PYTEST_ADDOPTS="--maxprocesses=2")
    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "created: 2/2 workers" in proc.stdout


def test_e2e_plugin_absent_does_not_crash(not_nested, tmp_path):
    proc = _run_child(["-p", "no:xdist", "-o", "addopts=", _UNIT_NODE], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "INTERNALERROR" not in proc.stdout + proc.stderr
