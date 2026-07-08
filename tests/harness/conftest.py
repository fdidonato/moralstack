"""Fixtures for testing the ``.claude/hooks/*`` scripts in isolation.

The hooks are standalone scripts (not an importable package): they read a JSON
event on stdin and write an optional JSON directive on stdout. These helpers load
a hook module by path and drive its ``main()`` with a simulated event against a
throwaway project directory, so every hook is unit-testable offline.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "hooks"


def _load_hook(name: str) -> ModuleType:
    path = _HOOKS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_hook_{name}", path)
    assert spec and spec.loader, f"cannot load hook {name}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def load_hook():
    return _load_hook


@pytest.fixture
def run_hook(monkeypatch):
    """Run ``module.main()`` with ``payload`` on stdin against ``project``.

    Returns ``(exit_code, parsed_stdout_or_None)``. ``raw`` payloads (str/bytes)
    are fed verbatim to exercise malformed-input handling."""

    def _run(module: ModuleType, payload, project: Path, *, raw: bool = False):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        text = payload if raw else json.dumps(payload)
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = module.main()
        out = buffer.getvalue()
        parsed = None
        if out.strip():
            try:
                parsed = json.loads(out)
            except ValueError:
                parsed = None
        return code, parsed

    return _run


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".claude").mkdir()
    return tmp_path


def write_session_edits(project: Path, session_id: str, paths: list[str]) -> None:
    (project / ".claude" / ".session-edits.json").write_text(
        json.dumps({"session_id": session_id, "paths": paths}), encoding="utf-8"
    )


def write_code_file(project: Path, rel: str, content: str = "x = 1\n") -> None:
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def stub_subprocess(monkeypatch, module, *, returncode: int = 0, stdout: str = "", recorder: list | None = None):
    """Replace ``module.subprocess.run`` with a recording stub."""

    def _fake_run(args, **kwargs):
        if recorder is not None:
            recorder.append(args)
        return FakeProc(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(module.subprocess, "run", _fake_run)


# Re-export helpers on the module so tests can ``from conftest import ...`` too.
os.environ.setdefault("PYTHONUTF8", "1")
