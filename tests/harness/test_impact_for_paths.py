"""``scripts/ai/impact_for_paths.py``: tests and docs a change to a file touches.

Runs against a throwaway git repository under ``tmp_path``; the doc map is the real
``BEHAVIOR_DOC_MAP`` of ``scripts/check_memory_updated.py`` (the script's single source).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ai" / "impact_for_paths.py"
_GIT_ID = ["-c", "user.email=test@example.invalid", "-c", "user.name=test"]

if shutil.which("git") is None:  # pragma: no cover - environment dependent
    pytest.skip("git not available", allow_module_level=True)


def _load():
    spec = importlib.util.spec_from_file_location("_impact_for_paths_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ifp = _load()

_FILES = {
    "moralstack/orchestration/__init__.py": "from .widget import Widget\n",
    "moralstack/orchestration/widget.py": "class Widget:\n    pass\n",
    "moralstack/orchestration/other.py": "def other():\n    return 1\n",
    "tests/test_direct_import.py": "from moralstack.orchestration.widget import Widget\n",
    "tests/test_patch_target.py": (
        'from unittest.mock import patch\n\n@patch("moralstack.orchestration.widget.Widget")\ndef test_x(m):\n    pass\n'
    ),
    "tests/test_from_package.py": "from moralstack.orchestration import widget\n",
    "tests/test_reexport.py": "from moralstack.orchestration import (\n    Widget,\n)\n\n# exercises the widget path\n",
    "tests/test_unrelated_package_import.py": "from moralstack.orchestration import other\n",
    "tests/test_prefix_only.py": "import moralstack.orchestration.widgetry\n",
    "moralstack/ui/templates/page.html": "<p>page</p>\n",
    "tests/test_template_by_name.py": 'TEMPLATE = "page.html"\n',
    "tests/test_symbol_only.py": "from moralstack.orchestration import WidgetFactory\n",
    ".lint-notes.cfg": "[notes]\n",
    "tests/test_dotfile.py": 'NOTES = ".lint-notes.cfg"\n',
    "docs/anchors.md": "See `widget.py:2` for the class.\n",
    "docs/no_anchor.md": "The widget module is described elsewhere.\n",
}


def _git(root: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", *_GIT_ID, "-C", str(root), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel, text in _FILES.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def test_module_parts():
    assert ifp.module_parts("moralstack/a/b.py") == ("moralstack.a.b", "moralstack.a", "b")
    assert ifp.module_parts("moralstack/a/__init__.py") == ("moralstack.a", "moralstack", "a")
    assert ifp.module_parts("moralstack/ui/templates/request.html") is None
    assert ifp.module_parts("scripts/ai/suite_record.py") is None  # not an importable package module
    assert ifp.module_parts("setup.py") is None


def test_tests_for_finds_imports_patches_and_reexports(repo):
    how, found = ifp.tests_for(repo, "moralstack/orchestration/widget.py", None)
    assert how == "Tests that import, patch or re-import it"
    assert found == {
        "tests/test_direct_import.py",
        "tests/test_patch_target.py",
        "tests/test_from_package.py",
        "tests/test_reexport.py",
    }


def test_known_gap_reexported_symbol_with_a_different_name_is_missed(repo):
    # Documented limit: `from <package> import Widget` without naming the module.
    _, found = ifp.tests_for(repo, "moralstack/orchestration/widget.py", None)
    assert "tests/test_symbol_only.py" not in found


def test_non_module_files_are_searched_by_file_name(repo):
    how, found = ifp.tests_for(repo, "moralstack/ui/templates/page.html", None)
    assert how == "Tests that name `page.html`"
    assert found == {"tests/test_template_by_name.py"}


def test_docs_for_unions_memory_guard_map_anchors_and_ledgers(repo):
    doc_map = ifp._load_memory_guard().BEHAVIOR_DOC_MAP
    docs = ifp.docs_for(repo, "moralstack/orchestration/widget.py", None, doc_map)
    gates = [d for prefix, d in doc_map if prefix == "moralstack/orchestration/"]
    assert gates, "BEHAVIOR_DOC_MAP no longer has the moralstack/orchestration/ prefix this test relies on"
    expected_gate = gates[0]
    assert set(expected_gate) <= set(docs)
    assert "docs/anchors.md" in docs and "anchors" in docs["docs/anchors.md"]
    assert "docs/no_anchor.md" not in docs
    assert set(ifp.ALWAYS_DOCS) <= set(docs)


def test_rev_searches_the_commit_not_the_working_tree(repo):
    (repo / "tests" / "test_new_untracked.py").write_text("import moralstack.orchestration.widget\n", encoding="utf-8")
    assert "tests/test_new_untracked.py" in ifp.tests_for(repo, "moralstack/orchestration/widget.py", None)[1]
    assert "tests/test_new_untracked.py" not in ifp.tests_for(repo, "moralstack/orchestration/widget.py", "HEAD")[1]
    assert "tests/test_direct_import.py" in ifp.tests_for(repo, "moralstack/orchestration/widget.py", "HEAD")[1]


def test_main_prints_report_and_fails_cleanly(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert ifp.main(["moralstack/orchestration/widget.py", "moralstack/orchestration/other.py"]) == 0
    out = capsys.readouterr().out
    assert "## moralstack/orchestration/widget.py" in out
    assert "- tests/test_unrelated_package_import.py" in out  # imports `other`
    assert "## Union:" in out
    assert ifp.main([]) == 2
    assert ifp.main(["--rev"]) == 2
    capsys.readouterr()
    assert ifp.main(["--rev", "no-such-rev", "moralstack/orchestration/widget.py"]) == 2
    assert "unknown revision: no-such-rev" in capsys.readouterr().err
    assert ifp.main(["moralstack/orchestration/widget.py", "--rev", "HEAD"]) == 0  # option after a path
    assert "rev HEAD" in capsys.readouterr().out


def test_missing_path_is_reported_never_silently_empty(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert ifp.main(["moralstack/orchestration/nope.py", "moralstack/orchestration/widget.py"]) == 2
    captured = capsys.readouterr()
    assert "path not found: moralstack/orchestration/nope.py" in captured.err
    assert "(path not found as a tracked or non-ignored file with this exact case — nothing searched" in captured.out
    assert "- tests/test_direct_import.py" in captured.out  # the other path is still reported
    assert ifp.main(["--rev", "HEAD", "moralstack/orchestration/nope.py"]) == 2


def test_windows_and_dot_slash_paths_are_normalized(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert ifp.main(["moralstack\\orchestration\\widget.py", "./moralstack/orchestration/other.py"]) == 0
    out = capsys.readouterr().out
    assert "## moralstack/orchestration/widget.py" in out
    assert "## moralstack/orchestration/other.py" in out


def test_main_outside_a_git_repo_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert ifp.main(["moralstack/orchestration/widget.py"]) == 2
    assert "not inside a git work tree" in capsys.readouterr().err


def test_absolute_paths_are_made_repo_relative(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    absolute = str(repo / "moralstack" / "orchestration" / "widget.py")
    assert ifp.main([absolute]) == 0
    out = capsys.readouterr().out
    assert "## moralstack/orchestration/widget.py" in out
    assert "- tests/test_direct_import.py" in out  # module search, not the basename fallback
    assert "memory-guard requires it for moralstack/orchestration/" in out


def test_wrong_case_path_is_not_found_even_on_case_insensitive_disks(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert ifp.main(["moralstack/Orchestration/widget.py"]) == 2
    captured = capsys.readouterr()
    assert "path not found: moralstack/Orchestration/widget.py" in captured.err
    assert "none (the search above ran" not in captured.out
    # Untracked files take the `ls-files --others` branch, which echoes the given case.
    (repo / "moralstack" / "orchestration" / "fresh.py").write_text("", encoding="utf-8")
    assert ifp.main(["moralstack/orchestration/fresh.py"]) == 0
    capsys.readouterr()
    assert ifp.main(["moralstack/orchestration/Fresh.py"]) == 2
    assert "path not found: moralstack/orchestration/Fresh.py" in capsys.readouterr().err


def test_directory_and_outside_repo_paths_exit_2(repo, tmp_path_factory, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert ifp.main(["moralstack/orchestration"]) == 2
    assert ifp.main(["--rev", "HEAD", "moralstack/orchestration"]) == 2  # a tree, not a blob
    outside = tmp_path_factory.mktemp("outside") / "x.py"
    outside.write_text("", encoding="utf-8")
    capsys.readouterr()
    assert ifp.main([str(outside)]) == 2
    assert "path outside the repository" in capsys.readouterr().err


def test_dot_leading_file_names_are_found(repo):
    how, found = ifp.tests_for(repo, ".lint-notes.cfg", None)
    assert how == "Tests that name `.lint-notes.cfg`"
    assert found == {"tests/test_dotfile.py"}


def test_git_grep_failure_fails_loudly(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    real = ifp._run_git

    def failing_grep(root, args):
        if args and args[0] == "grep":
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: simulated")
        return real(root, args)

    monkeypatch.setattr(ifp, "_run_git", failing_grep)
    assert ifp.main(["moralstack/orchestration/widget.py"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "git grep failed (rc 128): fatal: simulated" in captured.err
