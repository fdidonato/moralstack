#!/usr/bin/env python3
"""Shared helpers for the MoralStack UI-improvement loop scripts.

Design rules that the rest of the loop depends on:

* No secret ever reaches stdout/stderr. ``.env`` is parsed in-process only, and
  only the *presence* of a key is reported.
* Every script is runnable from any cwd: paths are resolved from this file's
  location, not from the caller's working directory.
* Nothing here imports ``moralstack``; the loop must be able to run preflight
  even when the package import is broken.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# .claude/skills/improve-moralstack-ui/scripts/_common.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[4]

LOOP_DIR = REPO_ROOT / ".claude" / "ui-loop"
RUNTIME_DIR = LOOP_DIR / "runtime"
ARTIFACT_DIR = LOOP_DIR / "artifacts"
ITERATION_DIR = LOOP_DIR / "ITERATIONS"
STATE_PATH = LOOP_DIR / "STATE.json"
SCENARIO_PATH = RUNTIME_DIR / "scenarios.json"
STORAGE_STATE_PATH = RUNTIME_DIR / "storage-state.json"

DEFAULT_UI_PORT = "8765"

# Paths the loop is allowed to modify. Anything else in the diff is a scope
# violation and aborts the iteration.
ALLOWED_WRITE_GLOBS = (
    "moralstack/ui/*",
    "tests/test_ui_*.py",
    "CHANGELOG.md",
    ".claude/ui-loop/*",
)


class LoopError(RuntimeError):
    """Fatal, reportable condition. Scripts exit(1) with a single-line message."""


def read_dotenv() -> dict[str, str]:
    """Parse ``.env`` at the repo root. Values stay in memory, never printed."""
    env_path = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
            val = val[1:-1]
        values[key.strip()] = val
    return values


def config(name: str, default: str = "") -> str:
    """Process env wins over ``.env``; both are treated as trusted-but-secret."""
    from_process = os.environ.get(name)
    if from_process:
        return from_process
    return read_dotenv().get(name, default)


def ui_base_url() -> str:
    port = config("MORALSTACK_UI_PORT", DEFAULT_UI_PORT)
    return f"http://localhost:{port}"


def db_path() -> Path | None:
    raw = config("MORALSTACK_OBSERVABILITY_DB_PATH")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def ensure_dirs() -> None:
    for directory in (LOOP_DIR, RUNTIME_DIR, ARTIFACT_DIR, ITERATION_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, payload: Any) -> None:
    """Atomic replace: a crashed iteration can never leave a half-written state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise LoopError(f"missing file: {path.relative_to(REPO_ROOT)}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoopError(f"corrupt JSON in {path.relative_to(REPO_ROOT)}: {exc}") from exc


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)
