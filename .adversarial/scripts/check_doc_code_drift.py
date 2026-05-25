#!/usr/bin/env python
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import command_exists, extract_keywords, load_json, read_text, run_command, trim, write_text

PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)(?![A-Za-z0-9_./-])")
SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{3,})`")


def rg(repo_root: Path, pattern: str, limit: int = 80) -> str:
    if not command_exists("rg"):
        return ""
    result = run_command(["rg", "-n", "--hidden", "--glob", "!.git", pattern, "."], cwd=repo_root, timeout=60, check=False)
    if result.returncode != 0:
        return ""
    lines = result.stdout.splitlines()
    return "\n".join(lines[:limit])


def collect_doc_text(run_dir: Path) -> str:
    manifest = load_json(run_dir / "01_baseline_manifest.json")
    parts: list[str] = []
    for group in ("documents", "trace_documents"):
        for doc in manifest.get(group, []):
            rel = doc.get("snapshot_path")
            if rel:
                parts.append(read_text(run_dir / rel))
    return "\n\n".join(parts)


def build_drift_report(repo_root: Path, run_dir: Path, task_path: Path, config: dict[str, Any]) -> Path:
    doc_text = collect_doc_text(run_dir)
    task_text = read_text(task_path)
    keywords = extract_keywords(task_text, limit=int(config.get("context_pack", {}).get("task_keyword_limit", 18)))

    paths = sorted(set(m.group(1) for m in PATH_RE.finditer(doc_text)))
    symbols = sorted(set(m.group(1) for m in SYMBOL_RE.finditer(doc_text)))

    existing_paths: list[str] = []
    missing_paths: list[str] = []
    for rel in paths:
        # Ignore obvious URLs and generated references.
        if rel.startswith(("http/", "https/")) or "://" in rel:
            continue
        if (repo_root / rel).exists():
            existing_paths.append(rel)
        else:
            # Only flag plausible repo paths to avoid noise from prose examples.
            if any(
                rel.endswith(ext)
                for ext in (".py", ".js", ".ts", ".tsx", ".java", ".md", ".json", ".yaml", ".yml", ".toml", ".sql")
            ) or rel.startswith(("moralstack/", "docs/", "tests/", "src/", "app/", "moralstack-ui/")):
                missing_paths.append(rel)

    symbol_matches: dict[str, str] = {}
    missing_symbols: list[str] = []
    for symbol in symbols[:120]:
        if len(symbol) < 5:
            continue
        result = rg(repo_root, re.escape(symbol), limit=8)
        if result.strip():
            symbol_matches[symbol] = result
        else:
            missing_symbols.append(symbol)

    task_grep: list[tuple[str, str]] = []
    for kw in keywords:
        result = rg(repo_root, re.escape(kw), limit=30)
        if result.strip():
            task_grep.append((kw, result))

    potentially_new_files = ""
    if keywords:
        pattern = "|".join(re.escape(k) for k in keywords[:10])
        potentially_new_files = rg(repo_root, pattern, limit=120)

    blocking = []
    if missing_paths:
        blocking.append(
            "Some documented file paths do not exist in the current repository. "
            "Review whether these are true drift or stale prose examples."
        )

    lines = [
        "# Documentation / Code Drift Report",
        "",
        "This report is generated before adversarial planning. "
        "It is heuristic: agents must inspect suspicious items before treating them as blocking architectural drift.",
        "",
        "## Task Keywords",
        "",
        ", ".join(keywords) if keywords else "No specific keywords extracted.",
        "",
        "## Strong Path Matches",
        "",
    ]
    lines.extend([f"- `{p}`" for p in existing_paths[:200]] or ["No documented paths were confirmed."])
    lines.extend(["", "## Potential Path Drift", ""])
    lines.extend([f"- `{p}`" for p in missing_paths[:200]] or ["No missing documented paths detected."])

    lines.extend(["", "## Symbol Matches", ""])
    if not command_exists("rg"):
        lines.append("_Note: symbol matching skipped because `rg` (ripgrep) is not installed._")
    elif symbol_matches:
        for symbol, matches in list(symbol_matches.items())[:40]:
            lines.extend([f"### `{symbol}`", "", "```text", trim(matches, 2000), "```", ""])
    else:
        lines.append("No documented symbol matches detected.")

    lines.extend(["", "## Potential Symbol Drift", ""])
    lines.extend([f"- `{s}`" for s in missing_symbols[:120]] or ["No missing documented symbols detected."])

    lines.extend(["", "## Task-Relevant Current Code Matches", ""])
    if not command_exists("rg"):
        lines.append("_Note: code matching skipped because `rg` (ripgrep) is not installed._")
    elif task_grep:
        for kw, matches in task_grep[:12]:
            lines.extend([f"### Keyword `{kw}`", "", "```text", trim(matches, 3000), "```", ""])
    else:
        lines.append("No task keyword matches found in current code.")

    lines.extend(["", "## New Relevant Files / Areas To Consider", ""])
    if potentially_new_files.strip():
        lines.extend(["```text", trim(potentially_new_files, 6000), "```"])
    else:
        lines.append("No additional relevant files discovered by keyword search.")

    lines.extend(["", "## Blocking Drift Candidates", ""])
    lines.extend([f"- {b}" for b in blocking] or ["None automatically detected. Review manually during planning."])

    lines.extend(["", "## Non-Blocking Drift Candidates", ""])
    lines.append(
        "- Heuristic symbol/path misses may include examples, prose references, renamed files, or optional modules. "
        "Treat them as investigation prompts, not automatic facts."
    )

    out = run_dir / "04_doc_code_drift_report.md"
    write_text(out, "\n".join(lines))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a heuristic documentation/code drift report.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_json(Path(args.config))
    out = build_drift_report(Path(args.repo_root).resolve(), Path(args.run_dir), Path(args.task), config)
    print(out)


if __name__ == "__main__":
    main()
