"""
Characterization tests for constitution loader.

Documents current behavior of load_yaml_file and ConstitutionLoadError.
"""

import tempfile
from pathlib import Path

import pytest

from moralstack.constitution import ConstitutionLoadError
from moralstack.constitution.loader import load_yaml_file


def test_load_yaml_file_valid():
    """Valid YAML file returns dict with expected keys."""
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = Path(tmp) / "test.yaml"
        yaml_path.write_text(
            "principles:\n  - id: X\n    level: soft\n    priority: 50\n    title: T\n    rule: R",
            encoding="utf-8",
        )
        result = load_yaml_file(yaml_path)
        assert isinstance(result, dict)
        assert "principles" in result
        assert len(result["principles"]) == 1
        assert result["principles"][0]["id"] == "X"
        assert result["principles"][0]["level"] == "soft"


def test_load_yaml_file_empty_raises():
    """Empty YAML file raises ConstitutionLoadError."""
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = Path(tmp) / "empty.yaml"
        yaml_path.write_text("", encoding="utf-8")
        with pytest.raises(ConstitutionLoadError) as exc_info:
            load_yaml_file(yaml_path)
        msg = str(exc_info.value).lower()
        assert "vuoto" in msg or "empty" in msg or "yaml" in msg
        assert exc_info.value.path == yaml_path


def test_load_yaml_file_invalid_root_raises():
    """YAML with non-dict root (e.g. list) raises ConstitutionLoadError."""
    with tempfile.TemporaryDirectory() as tmp:
        yaml_path = Path(tmp) / "list_root.yaml"
        yaml_path.write_text("- item1\n- item2", encoding="utf-8")
        with pytest.raises(ConstitutionLoadError) as exc_info:
            load_yaml_file(yaml_path)
        msg = str(exc_info.value).lower()
        assert "mapping" in msg or "root" in msg or "dict" in msg
