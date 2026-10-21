"""Packaging of the prompt defaults: files, package-data, hook safety, and the catalog's import discipline.

Structural tests: they fail only when files are restructured.
"""

from __future__ import annotations

import ast
import fnmatch
import shutil
import subprocess
import tomllib
from importlib import resources
from pathlib import Path

import pytest

from moralstack.prompts import catalog
from moralstack.prompts.catalog import default_prompt_set

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_FILES = {
    "common",
    "risk",
    "critic",
    "perspectives",
    "simulator",
    "hindsight",
    "context",
    "policy",
    "refusal",
    "compliance",
    "retrieval",
}


def _data_files() -> dict[str, bytes]:
    data = resources.files("moralstack.prompts") / "data"
    return {entry.name: entry.read_bytes() for entry in data.iterdir() if entry.is_file()}


def test_packaged_yaml_files_exist_via_importlib_resources() -> None:
    files = _data_files()
    assert {name.removesuffix(".yaml") for name in files} >= EXPECTED_FILES
    for name in sorted(EXPECTED_FILES):
        raw = files[f"{name}.yaml"]
        assert raw, f"{name}.yaml is empty"
        # .gitattributes pins eol=lf for these files, so no carriage return may appear at all.
        assert b"\r" not in raw, f"{name}.yaml contains a carriage return"
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{name}.yaml has a BOM"
        raw.decode("utf-8")


def test_gitattributes_pins_lf_for_the_packaged_yaml() -> None:
    """The rule that keeps the working-tree bytes platform independent (skipped when git is unavailable)."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    paths = [f"moralstack/prompts/data/{name}.yaml" for name in sorted(EXPECTED_FILES)]
    out = subprocess.run(
        [git, "check-attr", "eol", "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0:
        pytest.skip(f"git check-attr unavailable here: {out.stderr.strip()}")
    lines = out.stdout.strip().splitlines()
    assert len(lines) == len(paths), out.stdout
    for path, line in zip(paths, lines, strict=True):
        assert line.strip() == f"{path}: eol: lf", line


def test_pyproject_package_data_includes_prompts_yaml() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "moralstack.prompts" in package_data
    patterns = package_data["moralstack.prompts"]
    for name in sorted(EXPECTED_FILES):
        assert any(fnmatch.fnmatch(f"data/{name}.yaml", pattern) for pattern in patterns), name


def test_pyproject_package_data_includes_the_signal_registry_yaml() -> None:
    """The risk signal registry is built at import from ``config/signals.yaml``: a wheel without it cannot import
    the risk estimator (hence SDK, proxy, CLI)."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    config_dir = resources.files("moralstack.models.risk.config")
    yaml_names = sorted(entry.name for entry in config_dir.iterdir() if entry.name.endswith(".yaml"))
    assert "signals.yaml" in yaml_names
    patterns = package_data.get("moralstack.models.risk.config", [])
    for name in yaml_names:
        assert any(fnmatch.fnmatch(name, pattern) for pattern in patterns), name


def test_every_data_dir_yaml_is_loaded() -> None:
    """No orphan files in the data dir, no ``*.yml`` the loader would never read, and every ID is in the set."""
    files = _data_files()
    names = set(files)
    assert names == {f"{n}.yaml" for n in EXPECTED_FILES}, f"unexpected files in prompts/data: {sorted(names)}"
    assert not [n for n in names if n.endswith((".yml", ".YAML"))]

    from ruamel.yaml import YAML

    ds = default_prompt_set()
    loaded = set(ds.fragment_ids()) | set(ds.composition_ids())
    on_disk: set[str] = set()
    for name, raw in files.items():
        data = YAML(typ="safe").load(raw.decode("utf-8").replace("\r\n", "\n"))
        on_disk |= set(data.get("fragments") or {}) | set(data.get("compositions") or {})
    assert on_disk == loaded


def test_data_dir_is_reachable_from_the_installed_package_layout() -> None:
    data = resources.files("moralstack.prompts") / "data"
    assert data.is_dir()
    assert Path(str(data)) == Path(catalog.__file__).parent / "data"


def test_packaged_yaml_survives_the_whitespace_hooks_unchanged() -> None:
    """trailing-whitespace / end-of-file-fixer must never alter a default: a literal scalar is bytes."""
    for name, raw in _data_files().items():
        text = raw.decode("utf-8").replace("\r\n", "\n")
        assert text.endswith("\n") and not text.endswith("\n\n"), f"{name}: must end with exactly one newline"
        for number, line in enumerate(text.split("\n"), start=1):
            assert line == line.rstrip(" \t"), f"{name}:{number}: trailing whitespace"


def test_catalog_imports_nothing_from_moralstack() -> None:
    tree = ast.parse(Path(catalog.__file__).read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] == "moralstack"]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] == "moralstack":
                offenders.append(node.module or "")
            elif node.level > 0:
                offenders.append("." * node.level + (node.module or ""))
    assert not offenders, f"catalog.py must stay free of intra-package imports: {offenders}"


def test_importing_the_catalog_does_not_import_the_prompt_builders() -> None:
    """``catalog`` is a leaf: importing it must not drag the builder modules (no cycles with A2's re-exports)."""
    import sys

    code = (
        "import sys, importlib.util\n"
        "spec = importlib.util.spec_from_file_location('catalog_isolated', 'moralstack/prompts/catalog.py')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['catalog_isolated'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "loaded = [m for m in sys.modules if m.startswith('moralstack')]\n"
        "print(','.join(loaded))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120, check=True
    ).stdout.strip()
    assert out == "", f"catalog.py imported {out}"
