#!/usr/bin/env python
"""List the tests and docs a change to the given files touches (read-only, deterministic).

``impact_for_paths.py [--rev REV] PATH...``

For every repo-relative ``PATH``:

- **tests** — for a module under ``moralstack/``: files under ``tests/`` that import it
  (``moralstack.x.y`` anywhere, which also covers ``patch("moralstack.x.y.f")`` targets),
  that do ``from <package> import ... <module>``, or that import from the module's package
  and name the module (re-exports such as ``from moralstack.compliance import (...)``).
  For any other file (templates, YAML, ``scripts/``, hooks) the files under ``tests/``
  that name it (its file name, without directory). The section title says which search
  ran.
- **docs** — the docs the commit-time memory-guard requires for the path's prefix
  (``BEHAVIOR_DOC_MAP`` in ``scripts/check_memory_updated.py``, the single source), every
  ``docs/`` file that cites a ``<file name>:<line>`` anchor into the file (anchors drift
  when lines move), and the two always-updated ledgers.

Known limits of the module search: a test that imports only a re-exported symbol whose
name differs from the module (``from moralstack.compliance import Evaluator`` for
``dccl.py``) is missed; a test that imports from the package and mentions a common module
name (``core``, ``app``) in any text is over-reported. The list is a regression surface,
not the pinning tests: the reader picks those.

The search runs on the working tree (tracked and untracked, non-ignored files) or, with
``--rev``, on that commit. It never writes. Absolute paths are made repo-relative (outside
the repo: exit 2). A path that is not a file there with this exact case (a directory, a
typo, ``moralstack/Compliance/...`` on a case-insensitive disk) is reported on stderr and in
its section, and makes the exit code 2.

Exit codes: 0 report printed; 2 usage error, git failure, or a path not found (the report
for the other paths is still printed).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ALWAYS_DOCS = ("docs/MORALSTACK_CODEBASE_INDEX.md", "docs/CODEBASE_FACTS.md")
MODULE_ROOT = "moralstack/"
_MEMORY_GUARD = Path(__file__).resolve().parents[1] / "check_memory_updated.py"


class ImpactError(Exception):
    """git could not be run or failed; the report would be incomplete."""


def _load_memory_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_memory_guard_map", _MEMORY_GUARD)
    if spec is None or spec.loader is None:
        raise ImpactError(f"cannot load {_MEMORY_GUARD}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "--no-optional-locks", "-C", str(root), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise ImpactError(f"cannot run git: {exc}") from exc


def _grep(root: Path, pattern: str, where: str, rev: str | None, ignore_case: bool = False) -> set[str]:
    """Files under ``where`` matching the extended regex ``pattern`` (git grep -l)."""
    args = ["grep", "-l", "-E"]
    if ignore_case:
        args.append("-i")
    args += [pattern, rev] if rev else ["--untracked", pattern]
    args += ["--", where]
    proc = _run_git(root, args)
    if proc.returncode == 1:  # git grep: no match
        return set()
    if proc.returncode != 0:
        raise ImpactError(f"git grep failed (rc {proc.returncode}): {proc.stderr.strip()}")
    # With a revision git prefixes every hit with "<rev>:".
    return {line.split(":", 1)[1] if rev else line for line in proc.stdout.splitlines() if line}


def _ls_files(root: Path, flags: list[str], path: str) -> list[str]:
    proc = _run_git(root, ["ls-files", *flags, "--", path])
    if proc.returncode != 0:
        raise ImpactError(f"git ls-files failed (rc {proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.splitlines()


def _case_exact_file_on_disk(root: Path, path: str) -> bool:
    """Every component of ``path`` matches the on-disk casing, and it ends in a file."""
    current = root
    for part in path.split("/"):
        try:
            if part not in os.listdir(current):
                return False
        except OSError:
            return False
        current = current / part
    return current.is_file()


def path_exists(root: Path, path: str, rev: str | None) -> bool:
    """Exact-case check: a file git tracks or would track (working tree), or a blob at ``rev``.

    ``Path.is_file()`` alone is not used: it is case-insensitive on NTFS/APFS, while every
    later search and the memory-guard prefixes compare exact strings. ``ls-files --cached``
    matches the index exactly; ``--others`` under ``core.ignorecase=true`` echoes the path
    with the case it was given, so an untracked hit is re-checked against the disk listing.
    """
    if rev is not None:
        # `cat-file -e` also succeeds for a tree (a directory): ask for the object type.
        return _run_git(root, ["cat-file", "-t", f"{rev}:{path}"]).stdout.strip() == "blob"
    if path in _ls_files(root, ["--cached"], path):
        return True
    untracked = _ls_files(root, ["--others", "--exclude-standard"], path)
    return path in untracked and _case_exact_file_on_disk(root, path)


def to_repo_relative(root: Path, raw: str) -> str | None:
    """Repo-relative POSIX path for ``raw`` (relative or absolute); None if outside the repo."""
    text = raw.replace("\\", "/").removeprefix("./")
    candidate = Path(text)
    if not candidate.is_absolute():
        return text
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _word_bounded(literal: str) -> str:
    """Regex for ``literal`` with ``\\b`` only where its edge is a word character
    (``\\b.env.template`` would never match ``".env.template"``)."""
    escaped = re.escape(literal)
    start = r"\b" if re.match(r"\w", literal) else ""
    end = r"\b" if re.search(r"\w$", literal) else ""
    return f"{start}{escaped}{end}"


def module_parts(path: str) -> tuple[str, str, str] | None:
    """``moralstack/a/b.py`` -> (``moralstack.a.b``, ``moralstack.a``, ``b``); None if not a package module."""
    if not (path.startswith(MODULE_ROOT) and path.endswith(".py")):
        return None
    dotted = path[:-3].replace("/", ".")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    if "." not in dotted:
        return None
    package, name = dotted.rsplit(".", 1)
    return dotted, package, name


def tests_for(root: Path, path: str, rev: str | None) -> tuple[str, set[str]]:
    """(how the search was done, test files found)."""
    parts = module_parts(path)
    if parts is None:
        file_name = path.rsplit("/", 1)[-1]
        return f"Tests that name `{file_name}`", _grep(root, _word_bounded(file_name), "tests/", rev)
    dotted, package, name = parts
    pkg, mod = re.escape(package), re.escape(name)
    found = _grep(root, rf"{re.escape(dotted)}\b|from {pkg} import .*\b{mod}\b", "tests/", rev)
    via_package = _grep(root, rf"from {pkg} import", "tests/", rev)
    if via_package:
        found |= via_package & _grep(root, rf"\b{mod}\b", "tests/", rev, ignore_case=True)
    return "Tests that import, patch or re-import it", found


def docs_for(root: Path, path: str, rev: str | None, doc_map: tuple[tuple[str, tuple[str, ...]], ...]) -> dict[str, str]:
    """doc -> why it is listed."""
    docs = {doc: "always updated (index / facts ledger)" for doc in ALWAYS_DOCS}
    for prefix, mapped in doc_map:
        if path.startswith(prefix):
            for doc in mapped:
                docs[doc] = f"memory-guard requires it for {prefix}"
    file_name = path.rsplit("/", 1)[-1]
    for doc in _grep(root, rf"{re.escape(file_name)}[:#] ?L?[0-9]", "docs/", rev):
        docs.setdefault(doc, f"cites {file_name}:<line> anchors")
    return docs


def report(root: Path, paths: list[str], rev: str | None) -> tuple[str, list[str]]:
    """(markdown report, paths not found)."""
    doc_map = _load_memory_guard().BEHAVIOR_DOC_MAP
    out = [f"# Impact of {len(paths)} path(s) — {'rev ' + rev if rev else 'working tree'}", ""]
    missing: list[str] = []
    all_tests: set[str] = set()
    all_docs: set[str] = set()
    for path in paths:
        out.append(f"## {path}")
        if not path_exists(root, path, rev):
            missing.append(path)
            out += [
                "(path not found as a tracked or non-ignored file with this exact case — nothing searched;"
                " fix the path and re-run)",
                "",
            ]
            continue
        how, tests = tests_for(root, path, rev)
        docs = docs_for(root, path, rev, doc_map)
        all_tests |= tests
        all_docs |= set(docs)
        out.append(f"{how} ({len(tests)}):")
        out += [f"- {t}" for t in sorted(tests)] or ["- none (the search above ran and matched nothing)"]
        out.append(f"Docs ({len(docs)}):")
        out += [f"- {doc} — {why}" for doc, why in sorted(docs.items())]
        out.append("")
    if len(paths) > 1:
        out.append(f"## Union: {len(all_tests)} distinct test files (listed above), {len(all_docs)} docs")
        out += [f"- {doc}" for doc in sorted(all_docs)]
    return "\n".join(out).rstrip() + "\n", missing


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="impact_for_paths.py", description="Tests and docs a change to the given files touches."
    )
    parser.add_argument("--rev", help="search this commit instead of the working tree")
    parser.add_argument("paths", nargs="+", metavar="PATH", help="repo-relative file path")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        ns = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:  # argparse exits 2 on usage errors, 0 on --help
        return int(exc.code or 0)
    try:
        root_proc = _run_git(Path.cwd(), ["rev-parse", "--show-toplevel"])
        if root_proc.returncode != 0 or not root_proc.stdout.strip():
            raise ImpactError("not inside a git work tree")
        root = Path(root_proc.stdout.strip())
        if ns.rev and _run_git(root, ["rev-parse", "--verify", "--quiet", f"{ns.rev}^{{commit}}"]).returncode != 0:
            raise ImpactError(f"unknown revision: {ns.rev}")
        paths = []
        for raw in ns.paths:
            rel = to_repo_relative(root, raw)
            if rel is None:
                raise ImpactError(f"path outside the repository: {raw}")
            paths.append(rel)
        text, missing = report(root, paths, ns.rev)
    except ImpactError as exc:
        sys.stderr.write(f"impact_for_paths: {exc}\n")
        return 2
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")
    sys.stdout.write(text)
    for path in missing:
        sys.stderr.write(f"impact_for_paths: path not found: {path}\n")
    return 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
