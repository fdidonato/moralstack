"""Phase 0a baseline measurement and static visibility audit.

This script does not call OpenAI and does not run server quickstarts. It reads
existing SQLite/JSONL observability artifacts and prints a Markdown report.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence

DEFAULT_JSONL_DIR = Path("logs/observability")


@dataclass(frozen=True)
class JsonlFileSummary:
    name: str
    bytes: int
    lines: int


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    db_path = _resolve_db_path(args.db)
    jsonl_dir = _resolve_jsonl_dir(args.jsonl_dir)
    report = build_report(db_path=db_path, jsonl_dir=jsonl_dir, root=Path(args.root))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
    else:
        print(report)
    return 0


def build_report(*, db_path: Path | None, jsonl_dir: Path, root: Path) -> str:
    lines: list[str] = [
        "# Phase 0a Token/Latency Baseline",
        "",
        "## Scope",
        "- External measurement only: no OpenAI calls, no COMPL-AI, no server quickstart execution.",
        "- Reads existing SQLite/JSONL observability artifacts when present.",
        "- Performs static entry-point and JSONL consumer audits.",
        "",
    ]
    lines.extend(_db_section(db_path))
    lines.extend(_jsonl_section(jsonl_dir))
    lines.extend(_entry_point_section(root))
    lines.extend(_jsonl_consumer_section(root))
    lines.extend(_phase0b_section())
    lines.extend(
        [
            "## Visibility Decision",
            "- Default: keep risk, retriever, and orchestration records synchronous for implementation phases.",
            "- Async migration remains opt-in and requires explicit acceptance of proxy eventual visibility.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _phase0b_section() -> list[str]:
    return [
        "## Phase 0b Gated Instrumentation",
        "- Status: implemented but disabled by default.",
        "- Enable with `MORALSTACK_PHASE0_TIMING=1`.",
        "- Optional JSONL output: set `MORALSTACK_PHASE0_TIMING_JSONL` to a file path.",
        "- Hook: `risk_estimator.mini_persist` measures the three synchronous mini-estimator persist calls.",
        "- Hook: `sdk.governed_completions.create` measures SDK request wall time including final observability flush.",
        "- Removal gate: keep disabled or remove these hooks before shipping later implementation phases.",
        "",
    ]


def _db_section(db_path: Path | None) -> list[str]:
    lines = ["## SQLite Baseline"]
    if db_path is None:
        lines.extend(["- No SQLite DB path configured or found.", ""])
        return lines
    if not db_path.is_file():
        lines.extend([f"- SQLite DB not found: `{db_path}`.", ""])
        return lines

    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            table_counts = _table_counts(conn)
            llm_rows = _fetch_llm_rows(conn)
            request_count = _distinct_request_count(conn)
    except sqlite3.Error as exc:
        lines.extend([f"- SQLite read failed for `{db_path}`: {exc}.", ""])
        return lines

    lines.append(f"- DB path: `{db_path}`")
    lines.append(f"- Distinct requests: {request_count}")
    lines.append(f"- LLM calls: {len(llm_rows)}")
    if request_count:
        lines.append(f"- LLM calls/request: {len(llm_rows) / request_count:.2f}")
    lines.append("")
    lines.append("### Table Counts")
    lines.extend(_markdown_table(["table", "rows"], [(name, str(count)) for name, count in table_counts]))
    lines.append("")
    lines.append("### Token Usage")
    token_totals = _token_totals(llm_rows)
    lines.extend(
        [
            f"- Prompt tokens: {token_totals['prompt_tokens']}",
            f"- Completion tokens: {token_totals['completion_tokens']}",
            f"- Total tokens: {token_totals['total_tokens']}",
        ]
    )
    if request_count:
        lines.append(f"- Total tokens/request: {token_totals['total_tokens'] / request_count:.2f}")
    lines.append("")
    lines.append("### LLM Duration By Module")
    duration_rows = _duration_by_module(llm_rows)
    if duration_rows:
        lines.extend(_markdown_table(["module", "calls", "avg_ms", "max_ms"], duration_rows))
    else:
        lines.append("- No duration data found.")
    lines.append("")
    lines.append("### DCCL Frequency")
    dccl_count = sum(1 for row in llm_rows if _is_dccl_row(row))
    lines.append(f"- DCCL/compliance LLM rows: {dccl_count}")
    if request_count:
        lines.append(f"- DCCL rows/request: {dccl_count / request_count:.2f}")
    lines.append("")
    return lines


def _jsonl_section(jsonl_dir: Path) -> list[str]:
    lines = ["## JSONL Baseline", f"- JSONL dir: `{jsonl_dir}`"]
    summaries = _summarize_jsonl_dir(jsonl_dir)
    if not summaries:
        lines.extend(["- No JSONL files found.", ""])
        return lines
    lines.extend(_markdown_table(["file", "bytes", "lines"], [(s.name, str(s.bytes), str(s.lines)) for s in summaries]))
    lines.append("")
    return lines


def _entry_point_section(root: Path) -> list[str]:
    entries = [
        (
            "SDK GovernedCompletions.create",
            root / "moralstack" / "sdk" / "wrapper.py",
            ["get_obs().flush", "finally"],
            "Flushes at request boundary.",
        ),
        (
            "CLI shell",
            root / "moralstack" / "cli" / "shell.py",
            ["get_obs().flush", "finally"],
            "Flushes at command boundary.",
        ),
        (
            "Compatible server script",
            root / "scripts" / "openai_compatible_server.py",
            ["get_obs().flush", "finally"],
            "Flushes in the script request wrapper.",
        ),
        (
            "Proxy app",
            root / "moralstack" / "server" / "proxy.py",
            ["per-request flush has been removed", "shutdown"],
            "No per-request flush; drains on shutdown.",
        ),
        (
            "examples/server_quickstart.py",
            root / "examples" / "server_quickstart.py",
            ["create_app", "uvicorn.run"],
            "Static audit only; inherits proxy visibility model.",
        ),
    ]
    rows = []
    for name, path, markers, decision in entries:
        present = _file_contains_all(path, markers)
        rows.append((name, str(path), "yes" if present else "no", decision))
    return [
        "## Entry-Point Visibility Audit",
        *_markdown_table(["entry", "file", "markers_found", "decision"], rows),
        "",
    ]


def _jsonl_consumer_section(root: Path) -> list[str]:
    search_roots = [
        root / "tests",
        root / "scripts",
        root / "moralstack" / "reports",
        root / "moralstack" / "ui",
    ]
    hits = _find_jsonl_consumers(search_roots)
    lines = ["## JSONL Consumer Map"]
    if not hits:
        lines.extend(["- No JSONL references found in audited roots.", ""])
        return lines
    lines.extend(_markdown_table(["file", "line", "snippet"], hits[:200]))
    if len(hits) > 200:
        lines.append(f"- Truncated: {len(hits) - 200} additional hits omitted.")
    lines.append("")
    return lines


def _table_counts(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    table_names = [
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        if not str(row["name"]).startswith("sqlite_")
    ]
    counts: list[tuple[str, int]] = []
    for name in table_names:
        try:
            counts.append((name, int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])))
        except sqlite3.Error:
            counts.append((name, -1))
    return counts


def _fetch_llm_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(conn, "llm_calls"):
        return []
    return list(
        conn.execute(
            """
            SELECT run_id, request_id, phase, module, action, model, duration_ms,
                   token_usage_json, call_kind
            FROM llm_calls
            """
        )
    )


def _distinct_request_count(conn: sqlite3.Connection) -> int:
    if _table_exists(conn, "requests"):
        return int(conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
    if _table_exists(conn, "llm_calls"):
        return int(conn.execute("SELECT COUNT(DISTINCT run_id || ':' || request_id) FROM llm_calls").fetchone()[0])
    return 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _token_totals(rows: Iterable[sqlite3.Row]) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for row in rows:
        usage = _parse_json(row["token_usage_json"])
        prompt = _first_int(usage, ["prompt_tokens", "input_tokens"])
        completion = _first_int(usage, ["completion_tokens", "output_tokens"])
        total = _first_int(usage, ["total_tokens", "tokens_used"])
        if total == 0:
            total = prompt + completion
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["total_tokens"] += total
    return totals


def _duration_by_module(rows: Sequence[sqlite3.Row]) -> list[tuple[str, str, str, str]]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        value = row["duration_ms"]
        if value is None:
            continue
        grouped.setdefault(str(row["module"] or "(blank)"), []).append(float(value))
    return [
        (module, str(len(values)), f"{mean(values):.1f}", f"{max(values):.1f}")
        for module, values in sorted(grouped.items())
    ]


def _is_dccl_row(row: sqlite3.Row) -> bool:
    values = [
        str(row["phase"] or ""),
        str(row["module"] or ""),
        str(row["action"] or ""),
        str(row["call_kind"] or ""),
    ]
    return any("compliance" in value or "dccl" in value.lower() for value in values)


def _summarize_jsonl_dir(path: Path) -> list[JsonlFileSummary]:
    if not path.is_dir():
        return []
    summaries: list[JsonlFileSummary] = []
    for file_path in sorted(path.glob("*.jsonl")):
        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = sum(1 for line in text.splitlines() if line.strip())
        summaries.append(JsonlFileSummary(file_path.name, file_path.stat().st_size, lines))
    return summaries


def _find_jsonl_consumers(roots: Iterable[Path]) -> list[tuple[str, str, str]]:
    hits: list[tuple[str, str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            if path.suffix.lower() not in {".py", ".md", ".txt", ".toml", ".yaml", ".yml"}:
                continue
            try:
                for idx, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if "jsonl" in line.lower():
                        hits.append((str(path), str(idx), line.strip()[:120]))
            except OSError:
                continue
    return hits


def _file_contains_all(path: Path, markers: Sequence[str]) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return all(marker in text for marker in markers)


def _parse_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_int(data: dict[str, Any], keys: Sequence[str]) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(str(cell)) for cell in row) + " |")
    return lines


def _md_cell(value: str) -> str:
    sanitized = value.replace("|", "\\|").replace("\n", " ")
    return sanitized.encode("ascii", "backslashreplace").decode("ascii")


def _resolve_db_path(value: str | None) -> Path | None:
    candidates = [
        value,
        os.getenv("MORALSTACK_OBSERVABILITY_DB_PATH"),
        os.getenv("MORALSTACK_DB_PATH"),
        "moralstack.db",
    ]
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if value or path.exists():
                return path
    return None


def _resolve_jsonl_dir(value: str | None) -> Path:
    return Path(value or os.getenv("MORALSTACK_OBSERVABILITY_JSONL_DIR") or DEFAULT_JSONL_DIR)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="SQLite observability DB path. Defaults to env vars or moralstack.db.")
    parser.add_argument("--jsonl-dir", help="JSONL observability directory. Defaults to env var or logs/observability.")
    parser.add_argument("--root", default=".", help="Repository root for static audits.")
    parser.add_argument("--output", help="Optional Markdown output path.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
