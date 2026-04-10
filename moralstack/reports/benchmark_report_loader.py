"""
Benchmark report loader for UI consumption.

Reads benchmark_{run_id}.json from MORALSTACK_BENCHMARK_OUTPUTS (default: benchmark_outputs).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _get_benchmark_outputs_dir() -> Path:
    """Returns the directory where benchmark reports are stored.

    Relative paths are resolved against the project root (parent of moralstack package)
    so that the UI finds reports regardless of the current working directory.
    """
    raw = os.getenv("MORALSTACK_BENCHMARK_OUTPUTS", "benchmark_outputs")
    p = Path(raw)
    if not p.is_absolute():
        root = Path(__file__).resolve().parent.parent.parent
        p = (root / raw).resolve()
    return p


def load_benchmark_report(run_id: str) -> dict[str, Any] | None:
    """
    Loads the benchmark report for a run from benchmark_{run_id}.json.

    Returns the deserialized dict or None if not found.
    """
    if not run_id or not run_id.strip():
        return None
    outdir = _get_benchmark_outputs_dir()
    path = outdir / f"benchmark_{run_id.strip()}.json"
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            return loaded if isinstance(loaded, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def get_benchmark_result_by_request_id(
    report: dict[str, Any],
    request_id: str,
) -> dict[str, Any] | None:
    """
    Returns the ComparisonResult dict for the given request_id (moralstack_request_id).

    Returns None if not found.
    """
    if not report or not request_id:
        return None
    results = report.get("results") or []
    for r in results:
        if not isinstance(r, dict):
            continue
        if (r.get("moralstack_request_id") or "").strip() == request_id.strip():
            return r
    return None


def get_questions_by_category(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """
    Groups benchmark results by category, ordered by question_id.

    Returns: {category: [result_dict, ...]} with results sorted by question_id,
    categories in alphabetical order.
    """
    if not report:
        return {}
    results = report.get("results") or []
    by_category: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        cat = (r.get("category") or "").strip() or "(uncategorized)"
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(r)
    for cat in by_category:
        by_category[cat].sort(key=lambda x: x.get("question_id", 0))
    return dict(sorted(by_category.items()))
