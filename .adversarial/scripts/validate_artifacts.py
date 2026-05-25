#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import py_compile
from pathlib import Path

REQUIRED_FILES = [
    "README.md",
    "config.json",
    "baseline/manifest.json",
    "baseline/trust_policy.md",
    "prompts/shared_rules.md",
    "prompts/01_planner_claude.md",
    "prompts/02_planner_codex.md",
    "prompts/03_reviewer_codex.md",
    "prompts/04_reviewer_claude.md",
    "prompts/05_synthesizer.md",
    "prompts/06_final_gate_codex.md",
    "prompts/07_revision_synthesizer.md",
    "schemas/review.schema.json",
    "schemas/final_gate.schema.json",
    "schemas/baseline_manifest.schema.json",
    "schemas/issue_matrix.schema.json",
    "scripts/common.py",
    "scripts/build_baseline_manifest.py",
    "scripts/build_doc_digest.py",
    "scripts/check_doc_code_drift.py",
    "scripts/build_context_pack.py",
    "scripts/adversarial_plan.py",
    "scripts/validate_artifacts.py",
    "requirements.txt",
    "setup.sh",
    "setup.ps1",
    "Makefile.snippet",
]

JSON_FILES = [
    "config.json",
    "baseline/manifest.json",
    "schemas/review.schema.json",
    "schemas/final_gate.schema.json",
    "schemas/baseline_manifest.schema.json",
    "schemas/issue_matrix.schema.json",
]

PYTHON_FILES = [
    "scripts/common.py",
    "scripts/build_baseline_manifest.py",
    "scripts/build_doc_digest.py",
    "scripts/check_doc_code_drift.py",
    "scripts/build_context_pack.py",
    "scripts/adversarial_plan.py",
    "scripts/validate_artifacts.py",
]


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            errors.append(f"Missing required file: {rel}")

    for rel in JSON_FILES:
        path = root / rel
        if path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"Invalid JSON {rel}: {exc}")

    for rel in PYTHON_FILES:
        path = root / rel
        if path.exists():
            try:
                py_compile.compile(str(path), doraise=True)
            except Exception as exc:
                errors.append(f"Python compile failed {rel}: {exc}")

    manifest = root / "baseline" / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not data.get("required_documents"):
            errors.append("Manifest has no required_documents entries.")
        if "trust_policy" not in data:
            errors.append("Manifest missing trust_policy.")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate .adversarial kit completeness.")
    parser.add_argument("--root", default=".adversarial", help="Path to .adversarial directory.")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    errors = validate(root)
    if errors:
        print("VALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("VALIDATION PASSED")


if __name__ == "__main__":
    main()
