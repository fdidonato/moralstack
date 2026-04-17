"""
CLI tool: validate one or more MoralStack overlay YAML files.

Entry point registered as ``moralstack-validate-overlay`` in pyproject.toml.

Usage::

    # Validate a single overlay
    moralstack-validate-overlay moralstack/constitution/data/overlays/my_domain.yaml

    # Validate all overlays in a directory
    moralstack-validate-overlay moralstack/constitution/data/overlays/

    # JSON output (for CI integration)
    moralstack-validate-overlay --json moralstack/constitution/data/overlays/my_domain.yaml

Exit codes:
    0 — all overlays valid
    1 — one or more validation errors
    2 — file/directory not found or I/O error
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class PrincipleSummary:
    """Compact summary of a validated principle."""

    id: str
    level: str
    priority: int
    title: str


@dataclass
class OverlayValidationResult:
    """Result of validating a single overlay file."""

    path: str
    domain: str
    valid: bool
    errors: list[str] = field(default_factory=list)

    # Populated only when valid
    description: str = ""
    keywords_count: int = 0
    keywords_source: str = ""  # "explicit" | "auto-extracted"
    sensitive: bool = False
    risk_floor: float | None = None
    excluded: bool = False
    priority_overrides: dict[str, int] = field(default_factory=dict)
    principles: list[PrincipleSummary] = field(default_factory=list)
    has_refusal_redirection: bool = False
    has_simulator_guidance: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "path": self.path,
            "domain": self.domain,
            "valid": self.valid,
        }
        if self.errors:
            d["errors"] = self.errors
        if self.valid:
            d["description"] = self.description
            d["keywords_count"] = self.keywords_count
            d["keywords_source"] = self.keywords_source
            d["sensitive"] = self.sensitive
            d["risk_floor"] = self.risk_floor
            d["excluded"] = self.excluded
            d["priority_overrides"] = self.priority_overrides
            d["principles_count"] = len(self.principles)
            d["principles_hard"] = sum(1 for p in self.principles if p.level == "hard")
            d["principles_soft"] = sum(1 for p in self.principles if p.level == "soft")
            d["has_refusal_redirection"] = self.has_refusal_redirection
            d["has_simulator_guidance"] = self.has_simulator_guidance
        return d


# ---------------------------------------------------------------------------
# Default risk floor (mirrors overlay_policy.py)
# ---------------------------------------------------------------------------

_DEFAULT_SENSITIVE_RISK_FLOOR = 0.35


# ---------------------------------------------------------------------------
# Core validation logic (no I/O side effects, testable)
# ---------------------------------------------------------------------------


def validate_overlay_file(path: Path) -> OverlayValidationResult:
    """
    Validate a single overlay YAML file.

    Loads the YAML with ruamel.yaml, validates against ``OverlayYAML`` Pydantic
    model, and returns a structured result.
    """
    domain = path.stem

    # --- Load YAML ---
    try:
        from moralstack.constitution.loader import load_yaml_file

        raw = load_yaml_file(path)
    except Exception as exc:
        return OverlayValidationResult(
            path=str(path),
            domain=domain,
            valid=False,
            errors=[f"YAML load error: {exc}"],
        )

    if raw is None:
        return OverlayValidationResult(
            path=str(path),
            domain=domain,
            valid=False,
            errors=["File is empty or contains no YAML data."],
        )

    # --- Pydantic validation ---
    try:
        from moralstack.constitution.schema import OverlayYAML

        overlay = OverlayYAML.model_validate(raw)
    except ValidationError as exc:
        errors: list[str] = []
        for e in exc.errors():
            loc = " → ".join(str(x) for x in e["loc"])
            errors.append(f"{loc}: {e['msg']}")
        return OverlayValidationResult(
            path=str(path),
            domain=domain,
            valid=False,
            errors=errors,
        )
    except Exception as exc:
        return OverlayValidationResult(
            path=str(path),
            domain=domain,
            valid=False,
            errors=[f"Unexpected validation error: {exc}"],
        )

    # --- Cross-validation: check priority_override IDs against core ---
    warnings = _check_priority_override_ids(overlay.priority_overrides)
    # Warnings are informational, not errors — overlay is still valid.

    # --- Build result ---
    keywords_explicit = len(overlay.keywords) > 0
    if keywords_explicit:
        kw_count = len(overlay.keywords)
        kw_source = "explicit"
    else:
        from moralstack.constitution.store import _extract_keywords_from_description

        auto_kw = _extract_keywords_from_description(overlay.description)
        kw_count = len(auto_kw)
        kw_source = "auto-extracted" if kw_count > 0 else "none"

    principles_summary = [
        PrincipleSummary(
            id=p.id,
            level=p.level,
            priority=p.priority,
            title=p.title,
        )
        for p in overlay.additional_principles
    ]

    risk_floor: float | None = None
    if overlay.sensitive:
        risk_floor = (
            overlay.sensitive_risk_floor if overlay.sensitive_risk_floor is not None else _DEFAULT_SENSITIVE_RISK_FLOOR
        )

    result = OverlayValidationResult(
        path=str(path),
        domain=domain,
        valid=True,
        description=overlay.description,
        keywords_count=kw_count,
        keywords_source=kw_source,
        sensitive=overlay.sensitive,
        risk_floor=risk_floor,
        excluded=overlay.excluded,
        priority_overrides=dict(overlay.priority_overrides),
        principles=principles_summary,
        has_refusal_redirection=bool(overlay.refusal_redirection.strip()),
        has_simulator_guidance=bool(overlay.simulator_domain_guidance.strip()),
    )

    # Attach warnings as non-blocking notes
    if warnings:
        result.errors = [f"⚠ Warning: {w}" for w in warnings]
        # Still valid — warnings are informational

    return result


def _check_priority_override_ids(overrides: dict[str, int]) -> list[str]:
    """Check if priority_override IDs look like valid core principle IDs."""
    warnings: list[str] = []
    for pid in overrides:
        # Core IDs follow the pattern CATEGORY.NAME.N (e.g., SOFT.HONEST.1, CORE.NM.1)
        parts = pid.split(".")
        if len(parts) < 2:
            warnings.append(f"Priority override '{pid}' does not follow the CATEGORY.NAME.N convention.")
        elif parts[0] not in ("CORE", "SOFT"):
            warnings.append(f"Priority override '{pid}' has prefix '{parts[0]}' — expected 'CORE' or 'SOFT'.")
    return warnings


def validate_directory(directory: Path) -> list[OverlayValidationResult]:
    """Validate all .yaml files in a directory."""
    results = []
    yaml_files = sorted(directory.glob("*.yaml"))
    if not yaml_files:
        return []
    for f in yaml_files:
        results.append(validate_overlay_file(f))
    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

# ANSI colors (disabled when output is not a TTY or --json is used)
_COLORS = {
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _c(text: str, color: str, use_color: bool = True) -> str:
    if not use_color:
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


def print_result(result: OverlayValidationResult, use_color: bool = True) -> None:
    """Print a human-readable validation result."""
    if result.valid:
        print(f"\n{_c('✔', 'green', use_color)} Overlay {_c(repr(result.domain), 'bold', use_color)} is valid.")
        print()
        _print_detail("Domain name", result.domain, use_color)
        desc_display = result.description[:60] + "..." if len(result.description) > 60 else result.description
        _print_detail("Description", desc_display or "(empty)", use_color)
        _print_detail("Keywords", f"{result.keywords_count} {result.keywords_source}", use_color)

        if result.sensitive:
            floor_str = f"{result.risk_floor}" if result.risk_floor is not None else "default"
            _print_detail("Sensitive", f"true (risk floor: {floor_str})", use_color)
        else:
            _print_detail("Sensitive", "false", use_color)

        _print_detail("Excluded", str(result.excluded).lower(), use_color)

        if result.priority_overrides:
            overrides_str = ", ".join(f"{k} → {v}" for k, v in result.priority_overrides.items())
            _print_detail("Priority overrides", f"{len(result.priority_overrides)} ({overrides_str})", use_color)
        else:
            _print_detail("Priority overrides", "none", use_color)

        n_hard = sum(1 for p in result.principles if p.level == "hard")
        n_soft = sum(1 for p in result.principles if p.level == "soft")
        _print_detail("Additional principles", f"{len(result.principles)} ({n_hard} hard, {n_soft} soft)", use_color)

        _print_detail("Refusal redirection", "provided" if result.has_refusal_redirection else "none", use_color)
        _print_detail("Simulator guidance", "provided" if result.has_simulator_guidance else "none", use_color)

        # Print warnings if any
        for w in result.errors:
            print(f"  {_c(w, 'yellow', use_color)}")
    else:
        print(f"\n{_c('✘', 'red', use_color)} Validation failed for {_c(repr(Path(result.path).name), 'bold', use_color)}:")
        print()
        for err in result.errors:
            print(f"  {_c(err, 'red', use_color)}")


def _print_detail(label: str, value: str, use_color: bool) -> None:
    padded = f"  {label + ':':<24}"
    print(f"{_c(padded, 'dim', use_color)}{value}")


def print_summary(results: list[OverlayValidationResult], use_color: bool = True) -> None:
    """Print a summary line after validating multiple overlays."""
    valid = sum(1 for r in results if r.valid)
    failed = len(results) - valid
    print()
    if failed == 0:
        print(_c(f"All {valid} overlay(s) valid.", "green", use_color))
    else:
        print(_c(f"{failed} overlay(s) failed", "red", use_color) + f", {valid} valid.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="moralstack-validate-overlay",
        description="Validate MoralStack domain overlay YAML files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  moralstack-validate-overlay moralstack/constitution/data/overlays/healthcare.yaml
  moralstack-validate-overlay moralstack/constitution/data/overlays/
  moralstack-validate-overlay --json moralstack/constitution/data/overlays/my_domain.yaml
        """,
    )
    parser.add_argument(
        "path",
        type=str,
        help="Path to an overlay YAML file or a directory of overlay files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON (for CI integration).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    Main entry point for the ``moralstack-validate-overlay`` CLI.

    Returns:
        0 if all overlays are valid, 1 if any failed, 2 on I/O error.
    """
    args = parse_args(argv)
    target = Path(args.path)

    if not target.exists():
        print(f"Error: path not found: {target}", file=sys.stderr)
        return 2

    use_color = not args.json_output and not args.no_color and sys.stdout.isatty()

    if target.is_dir():
        results = validate_directory(target)
        if not results:
            print(f"No .yaml files found in {target}", file=sys.stderr)
            return 2
    elif target.is_file():
        results = [validate_overlay_file(target)]
    else:
        print(f"Error: {target} is not a file or directory.", file=sys.stderr)
        return 2

    if args.json_output:
        output = [r.to_dict() for r in results]
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        for r in results:
            print_result(r, use_color=use_color)
        if len(results) > 1:
            print_summary(results, use_color=use_color)

    has_errors = any(not r.valid for r in results)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
