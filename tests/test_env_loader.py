"""
Characterization tests for env_loader.

Documents current behavior of load_env and _find_project_root.
"""

import os
from pathlib import Path

import pytest

from moralstack.utils.env_loader import _find_project_root, load_env


def test_load_env_returns_bool():
    """load_env returns a boolean."""
    tracked_keys = (
        "MORALSTACK_DB_PATH",
        "MORALSTACK_PERSIST_MODE",
        "MORALSTACK_OBSERVABILITY_DB_PATH",
        "MORALSTACK_OBSERVABILITY_MODE",
    )
    old_values = {k: os.environ.get(k) for k in tracked_keys}
    try:
        result = load_env()
        assert isinstance(result, bool)
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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
