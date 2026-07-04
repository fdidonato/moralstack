"""Ensure benchmark script has no moralstack.persistence imports."""

from __future__ import annotations

import ast
from pathlib import Path


def test_benchmark_moralstack_has_no_persistence_imports():
    script = Path(__file__).resolve().parent.parent / "scripts" / "benchmark_moralstack.py"
    source = script.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "moralstack.persistence" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "moralstack.persistence" not in module
