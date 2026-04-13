"""
Tests for moralstack.cli.validate_overlay — overlay validation CLI.

Covers:
- Valid overlay validation (simple, full-featured)
- Invalid overlay detection (bad YAML, schema violations, bad priority)
- Directory validation (batch mode)
- JSON output mode
- Edge cases (empty file, missing fields, extra fields)
- Priority override warnings
- Auto-extracted keywords
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moralstack.cli.validate_overlay import (
    main,
    validate_directory,
    validate_overlay_file,
)

# ---------------------------------------------------------------------------
# Fixtures: temporary overlay files
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_overlays(tmp_path: Path) -> Path:
    """Create a temporary directory for overlay files."""
    return tmp_path


def _write(directory: Path, name: str, content: str) -> Path:
    p = directory / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Valid overlay tests
# ---------------------------------------------------------------------------


class TestValidOverlays:
    def test_minimal_valid_overlay(self, tmp_overlays: Path) -> None:
        """An overlay with only defaults should validate."""
        path = _write(tmp_overlays, "empty_domain.yaml", "description: 'A minimal domain.'\n")
        result = validate_overlay_file(path)
        assert result.valid
        assert result.domain == "empty_domain"
        assert result.description == "A minimal domain."
        assert result.sensitive is False
        assert result.excluded is False
        assert result.principles == []

    def test_full_featured_overlay(self, tmp_overlays: Path) -> None:
        """A fully-featured overlay with all fields should validate."""
        content = """\
description: "Testing domain with all fields."
keywords:
  - test
  - validation
sensitive: true
sensitive_risk_floor: 0.4
excluded: false
refusal_redirection: |
  Please consult a professional.
simulator_domain_guidance: |
  Consider testing implications.
priority_overrides:
  SOFT.HONEST.1: 95
  CORE.NM.1: 100
additional_principles:
  - id: "TEST.HARD.1"
    level: hard
    priority: 100
    title: "Test Hard Principle"
    rule: "This is a test hard rule."
    examples_allow:
      - "Good example"
    examples_deny:
      - "Bad example"
    keywords:
      - "test"
  - id: "TEST.SOFT.1"
    level: soft
    priority: 70
    title: "Test Soft Principle"
    rule: "This is a test soft rule."
"""
        path = _write(tmp_overlays, "full_domain.yaml", content)
        result = validate_overlay_file(path)

        assert result.valid
        assert result.domain == "full_domain"
        assert result.sensitive is True
        assert result.risk_floor == 0.4
        assert result.excluded is False
        assert result.keywords_count == 2
        assert result.keywords_source == "explicit"
        assert len(result.principles) == 2
        assert result.principles[0].id == "TEST.HARD.1"
        assert result.principles[0].level == "hard"
        assert result.principles[1].id == "TEST.SOFT.1"
        assert result.principles[1].level == "soft"
        assert result.has_refusal_redirection is True
        assert result.has_simulator_guidance is True
        assert len(result.priority_overrides) == 2
        assert result.priority_overrides["SOFT.HONEST.1"] == 95

    def test_sensitive_default_risk_floor(self, tmp_overlays: Path) -> None:
        """When sensitive=true and no custom floor, use default 0.35."""
        content = "description: 'Sensitive domain.'\nsensitive: true\n"
        path = _write(tmp_overlays, "sensitive_default.yaml", content)
        result = validate_overlay_file(path)
        assert result.valid
        assert result.sensitive is True
        assert result.risk_floor == 0.35

    def test_auto_extracted_keywords(self, tmp_overlays: Path) -> None:
        """When keywords is empty, they should be auto-extracted from description."""
        content = "description: 'Healthcare services patient care medical facilities.'\n"
        path = _write(tmp_overlays, "auto_kw.yaml", content)
        result = validate_overlay_file(path)
        assert result.valid
        assert result.keywords_source == "auto-extracted"
        assert result.keywords_count > 0

    def test_no_keywords_no_description(self, tmp_overlays: Path) -> None:
        """When both keywords and description are empty, keywords_source should be 'none'."""
        content = "description: ''\n"
        path = _write(tmp_overlays, "no_kw.yaml", content)
        result = validate_overlay_file(path)
        assert result.valid
        assert result.keywords_source == "none"
        assert result.keywords_count == 0


# ---------------------------------------------------------------------------
# Invalid overlay tests
# ---------------------------------------------------------------------------


class TestInvalidOverlays:
    def test_empty_file(self, tmp_overlays: Path) -> None:
        """Empty file should fail validation."""
        path = _write(tmp_overlays, "empty.yaml", "")
        result = validate_overlay_file(path)
        assert not result.valid
        assert len(result.errors) > 0

    def test_invalid_yaml_syntax(self, tmp_overlays: Path) -> None:
        """Malformed YAML should fail."""
        content = "description: [unclosed bracket\n"
        path = _write(tmp_overlays, "bad_syntax.yaml", content)
        result = validate_overlay_file(path)
        assert not result.valid

    def test_extra_field_forbidden(self, tmp_overlays: Path) -> None:
        """Unknown fields should be rejected (extra='forbid')."""
        content = """\
description: "Valid description."
unknown_field: true
"""
        path = _write(tmp_overlays, "extra_field.yaml", content)
        result = validate_overlay_file(path)
        assert not result.valid
        assert any("unknown_field" in e or "extra" in e.lower() for e in result.errors)

    def test_invalid_priority_range(self, tmp_overlays: Path) -> None:
        """Principle with priority > 100 should fail."""
        content = """\
description: "Test domain."
additional_principles:
  - id: "BAD.PRIORITY.1"
    level: hard
    priority: 200
    title: "Bad Priority"
    rule: "This has an invalid priority."
"""
        path = _write(tmp_overlays, "bad_priority.yaml", content)
        result = validate_overlay_file(path)
        assert not result.valid
        assert any("priority" in e.lower() for e in result.errors)

    def test_invalid_level(self, tmp_overlays: Path) -> None:
        """Principle with level not in ('hard', 'soft') should fail."""
        content = """\
description: "Test domain."
additional_principles:
  - id: "BAD.LEVEL.1"
    level: medium
    priority: 50
    title: "Bad Level"
    rule: "This has an invalid level."
"""
        path = _write(tmp_overlays, "bad_level.yaml", content)
        result = validate_overlay_file(path)
        assert not result.valid

    def test_invalid_sensitive_risk_floor(self, tmp_overlays: Path) -> None:
        """sensitive_risk_floor outside 0.0-1.0 should fail."""
        content = """\
description: "Test domain."
sensitive: true
sensitive_risk_floor: 1.5
"""
        path = _write(tmp_overlays, "bad_floor.yaml", content)
        result = validate_overlay_file(path)
        assert not result.valid
        assert any("sensitive_risk_floor" in e.lower() or "floor" in e.lower() for e in result.errors)

    def test_missing_principle_required_fields(self, tmp_overlays: Path) -> None:
        """Principle missing 'rule' should fail."""
        content = """\
description: "Test domain."
additional_principles:
  - id: "MISSING.RULE.1"
    level: hard
    priority: 90
    title: "Missing Rule"
"""
        path = _write(tmp_overlays, "missing_rule.yaml", content)
        result = validate_overlay_file(path)
        assert not result.valid

    def test_nonexistent_file(self) -> None:
        """Validating a file that doesn't exist should handle gracefully."""
        result = validate_overlay_file(Path("/nonexistent/overlay.yaml"))
        assert not result.valid


# ---------------------------------------------------------------------------
# Priority override warnings
# ---------------------------------------------------------------------------


class TestPriorityOverrideWarnings:
    def test_unusual_prefix_warning(self, tmp_overlays: Path) -> None:
        """Priority overrides with non-standard prefix should produce warning."""
        content = """\
description: "Test domain."
priority_overrides:
  CUSTOM.PRINCIPLE.1: 90
"""
        path = _write(tmp_overlays, "warn_prefix.yaml", content)
        result = validate_overlay_file(path)
        assert result.valid  # Still valid — warnings are informational
        assert any("CUSTOM" in e for e in result.errors)

    def test_valid_prefixes_no_warning(self, tmp_overlays: Path) -> None:
        """Priority overrides with CORE. or SOFT. prefix should not produce warnings."""
        content = """\
description: "Test domain."
priority_overrides:
  CORE.NM.1: 100
  SOFT.HONEST.1: 90
"""
        path = _write(tmp_overlays, "valid_prefix.yaml", content)
        result = validate_overlay_file(path)
        assert result.valid
        assert len(result.errors) == 0


# ---------------------------------------------------------------------------
# Directory validation
# ---------------------------------------------------------------------------


class TestDirectoryValidation:
    def test_validate_directory_mixed(self, tmp_overlays: Path) -> None:
        """Validating a directory with valid and invalid overlays."""
        _write(tmp_overlays, "good.yaml", "description: 'Valid domain.'\n")
        _write(
            tmp_overlays,
            "bad.yaml",
            "description: 'Valid.'\nunknown_field: true\n",
        )

        results = validate_directory(tmp_overlays)
        assert len(results) == 2
        valid_count = sum(1 for r in results if r.valid)
        assert valid_count == 1

    def test_validate_empty_directory(self, tmp_overlays: Path) -> None:
        """Validating an empty directory returns empty list."""
        results = validate_directory(tmp_overlays)
        assert results == []

    def test_validate_all_existing_overlays(self) -> None:
        """All 19 existing overlays in the repo should validate."""
        overlays_dir = Path("config/constitution/overlays")
        if not overlays_dir.exists():
            pytest.skip("Overlay directory not found (not running from repo root)")
        results = validate_directory(overlays_dir)
        assert len(results) >= 19
        for r in results:
            assert r.valid, f"Overlay {r.domain} failed: {r.errors}"


# ---------------------------------------------------------------------------
# to_dict serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_valid_result_to_dict(self, tmp_overlays: Path) -> None:
        content = """\
description: "Test."
keywords:
  - test
sensitive: true
additional_principles:
  - id: "T.1"
    level: hard
    priority: 90
    title: "Test"
    rule: "Test rule."
"""
        path = _write(tmp_overlays, "ser.yaml", content)
        result = validate_overlay_file(path)
        d = result.to_dict()

        assert d["valid"] is True
        assert d["domain"] == "ser"
        assert d["keywords_count"] == 1
        assert d["principles_count"] == 1
        assert d["principles_hard"] == 1
        assert d["principles_soft"] == 0
        # Must be JSON-serializable
        json.dumps(d)

    def test_invalid_result_to_dict(self, tmp_overlays: Path) -> None:
        path = _write(tmp_overlays, "bad.yaml", "unknown_field: true\n")
        result = validate_overlay_file(path)
        d = result.to_dict()

        assert d["valid"] is False
        assert "errors" in d
        assert len(d["errors"]) > 0
        json.dumps(d)


# ---------------------------------------------------------------------------
# CLI main() integration tests
# ---------------------------------------------------------------------------


class TestCLIMain:
    def test_main_valid_file(self, tmp_overlays: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = _write(tmp_overlays, "valid.yaml", "description: 'Test domain.'\n")
        exit_code = main([str(path)])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "valid" in captured.out.lower()

    def test_main_invalid_file(self, tmp_overlays: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = _write(tmp_overlays, "invalid.yaml", "unknown_field: true\n")
        exit_code = main([str(path)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower() or "error" in captured.out.lower()

    def test_main_nonexistent_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = main(["/nonexistent/path.yaml"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower()

    def test_main_json_output(self, tmp_overlays: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = _write(tmp_overlays, "json_test.yaml", "description: 'JSON test.'\n")
        exit_code = main(["--json", str(path)])
        assert exit_code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["valid"] is True
        assert data[0]["domain"] == "json_test"

    def test_main_directory(self, tmp_overlays: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(tmp_overlays, "a.yaml", "description: 'Domain A.'\n")
        _write(tmp_overlays, "b.yaml", "description: 'Domain B.'\n")
        exit_code = main([str(tmp_overlays)])
        assert exit_code == 0

    def test_main_empty_directory(self, tmp_overlays: Path, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = main([str(tmp_overlays)])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "no .yaml" in captured.err.lower()

    def test_main_no_color_flag(self, tmp_overlays: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = _write(tmp_overlays, "nc.yaml", "description: 'No color test.'\n")
        exit_code = main(["--no-color", str(path)])
        assert exit_code == 0
        captured = capsys.readouterr()
        # No ANSI escape codes
        assert "\033[" not in captured.out
