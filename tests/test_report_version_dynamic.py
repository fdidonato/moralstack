"""
Tests for the dynamic package version.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import moralstack


def _pyproject_version() -> str:
    """Reads the declared version from pyproject.toml (the single source of truth)."""
    pyproject_path = Path(moralstack.__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


class TestVersionIsDynamic:
    def test_pyproject_version_matches_package(self):
        """The package __version__ must match the pyproject declared version.

        The expected value is read from pyproject.toml rather than hardcoded, so a
        release bump does not require editing this test. A mismatch means the
        installed package metadata is stale (run `pip install -e .`) or the bump was
        applied inconsistently.
        """
        assert moralstack.__version__ == _pyproject_version()
