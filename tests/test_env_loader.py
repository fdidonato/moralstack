"""
Characterization tests for env_loader.

Documents current behavior of load_env and _find_project_root.
"""

from pathlib import Path

import pytest

from moralstack.utils.env_loader import _find_project_root, load_env


def test_load_env_returns_bool():
    """load_env returns a boolean."""
    result = load_env()
    assert isinstance(result, bool)


def test_find_project_root_returns_path_or_none():
    """_find_project_root returns Path or None."""
    result = _find_project_root()
    assert result is None or isinstance(result, Path)


def test_find_project_root_when_in_moralstack_project():
    """When run from moralstack project, _find_project_root finds root with pyproject.toml."""
    result = _find_project_root()
    if result is None:
        pytest.skip("Not running from moralstack project (no pyproject.toml found)")
    assert (result / "pyproject.toml").exists() or (result / ".env").exists()
