"""F1 (docs/traces casing hygiene) and F5(b) (testing-rule wording) checks.

These tests read the *real* repository (via the ``repo_root`` fixture), not a
throwaway ``project`` fixture — a check against a fake project would pass
vacuously and miss exactly the class of bug F1 was.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_DOC_REF_RE = re.compile(r"`(docs/[A-Za-z0-9_./-]*)`")


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case_correct_relative(base: Path, rel: str) -> bool:
    """True iff every component of ``rel`` matches the on-disk casing exactly,
    walked from the (assumed case-correct) ``base``. ``Path.exists()`` alone is
    NOT sufficient here — it is precisely how F1 (``docs/TRACES/`` vs the real
    ``docs/traces/``) shipped unnoticed on a case-insensitive filesystem."""
    cur = base
    for part in Path(rel).parts:
        try:
            listing = os.listdir(cur)
        except OSError:
            return False
        if part not in listing:
            return False
        cur = cur / part
    return True


# Documented alternative mechanism (not used here): ``p.resolve(strict=True)``
# then compare the resolved string's casing to the declared one — verified this
# session to also return the true on-disk casing on this NTFS box, and to
# distinguish "missing" from "wrong case" via the ``strict=True`` OSError.


def test_docs_targets_exist_on_disk_case_sensitive(repo_root):
    stop_gate = _load_module(repo_root / ".claude" / "hooks" / "stop_gate.py", "_docs_check_stop_gate")
    check_memory = _load_module(repo_root / "scripts" / "check_memory_updated.py", "_docs_check_memory_updated")

    files: set[str] = set(stop_gate._DOCS_ALWAYS)
    for _, hints in stop_gate._DOCS_HINTS:
        files.update(hints)
    dirs: set[str] = set()
    for prefix in stop_gate.MEMORY_DOC_PREFIXES:
        if prefix.endswith("/"):
            dirs.add(prefix.rstrip("/"))
        else:
            files.add(prefix)
    for _, docs in check_memory.BEHAVIOR_DOC_MAP:
        files.update(docs)

    bad: list[str] = []
    for rel in sorted(files):
        target = repo_root / rel
        if not target.is_file() or not _case_correct_relative(repo_root, rel):
            bad.append(rel)
    for rel in sorted(dirs):
        target = repo_root / rel
        if not target.is_dir() or not _case_correct_relative(repo_root, rel):
            bad.append(rel)

    assert not bad, f"doc targets missing or wrong-case on disk: {bad}"


def test_harness_docs_reference_existing_paths_case_sensitively(repo_root):
    targets = [
        repo_root / ".claude" / "hooks" / "README.md",
        repo_root / "PROJECT_SPEC.md",
        repo_root / "CLAUDE.md",
        repo_root / "docs" / "ai" / "ARCHITECTURE_MAP.md",
    ]
    targets.extend(sorted((repo_root / ".claude" / "rules").glob("*.md")))
    targets.extend(sorted((repo_root / ".claude" / "agents").glob("*.md")))

    bad: list[tuple[str, str]] = []
    for doc in targets:
        text = doc.read_text(encoding="utf-8")
        for match in _DOC_REF_RE.finditer(text):
            ref = match.group(1)
            if "*" in ref or "<" in ref or ">" in ref:
                continue
            target = repo_root / ref
            if not target.exists() or not _case_correct_relative(repo_root, ref):
                bad.append((str(doc.relative_to(repo_root)).replace("\\", "/"), ref))

    assert not bad, f"stale/wrong-case docs/ references: {bad}"


def test_no_stale_uppercase_docs_traces_references(repo_root):
    """No tracked file outside CHANGELOG.md (historical entries) spells the
    uppercase ``docs/TRACES`` casing. The needle is built at runtime — never
    spelled as one literal in this file — so this test itself, once staged,
    does not trip on its own text (review round 1, BLOCKING 2)."""
    needle = "docs/" + "traces".upper()
    proc = subprocess.run(
        [
            "git",
            "grep",
            "-lI",
            needle,
            "--",
            ".",
            ":!CHANGELOG.md",
            ":!tests/harness/test_harness_docs.py",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        pytest.skip(f"git grep unavailable/errored: {proc.stderr.strip()}")
    assert proc.returncode == 1, f"stale uppercase references found:\n{proc.stdout}"


def test_testing_rule_points_to_manual_slow_run(repo_root):
    text = (repo_root / ".claude" / "rules" / "testing.md").read_text(encoding="utf-8")
    assert "-m slow" in text
    assert "CI runs" not in text
    assert not (repo_root / ".github" / "workflows").exists()
