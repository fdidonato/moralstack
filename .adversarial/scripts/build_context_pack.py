#!/usr/bin/env python
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import extract_keywords, git_output, load_json, read_text, run_command, trim, write_text


def run_shell(repo_root: Path, command: list[str], timeout: int = 45) -> str:
    result = run_command(command, cwd=repo_root, timeout=timeout, check=False)
    text = result.stdout if result.returncode == 0 else (result.stdout + "\n" + result.stderr)
    return text.strip()


def rg(repo_root: Path, pattern: str, limit: int) -> str:
    result = run_command(["rg", "-n", "--hidden", "--glob", "!.git", pattern, "."], cwd=repo_root, timeout=60, check=False)
    if result.returncode != 0:
        return result.stdout.strip() or result.stderr.strip()
    return "\n".join(result.stdout.splitlines()[:limit])


def build_context_pack(repo_root: Path, run_dir: Path, task_path: Path, config: dict[str, Any]) -> Path:
    task_text = read_text(task_path)
    baseline_digest = read_text(run_dir / "03_baseline_digest.md")
    drift_report = read_text(run_dir / "04_doc_code_drift_report.md")
    ctx_cfg = config.get("context_pack", {})
    keywords = extract_keywords(task_text, limit=int(ctx_cfg.get("task_keyword_limit", 18)))
    max_matches = int(config.get("limits", {}).get("max_context_grep_matches", 250))

    lines: list[str] = [
        "# Documentation-Grounded Context Pack",
        "",
        "This context pack combines the trusted baseline digest, "
        "the doc/code drift report, and task-specific current repository evidence.",
        "",
        "## User Task",
        "",
        task_text,
        "",
        "## Baseline Digest",
        "",
        baseline_digest,
        "",
        "## Documentation / Code Drift Report",
        "",
        drift_report,
        "",
    ]

    if ctx_cfg.get("include_git_status", True):
        lines.extend(
            [
                "## Git State",
                "",
                "### Branch",
                "```text",
                git_output(repo_root, ["branch", "--show-current"]),
                "```",
                "",
                "### Commit",
                "```text",
                git_output(repo_root, ["rev-parse", "HEAD"]),
                "```",
                "",
                "### Status",
                "```text",
                git_output(repo_root, ["status", "--short"]),
                "```",
                "",
                "### Recent Commits",
                "```text",
                git_output(repo_root, ["log", "--oneline", f"-{int(ctx_cfg.get('include_recent_commits', 20))}"]),
                "```",
                "",
            ]
        )

    if ctx_cfg.get("include_file_tree", True):
        files = run_shell(repo_root, ["git", "ls-files"], timeout=45)
        lines.extend(["## Repository Files", "", "```text", trim(files, 20000), "```", ""])

    if ctx_cfg.get("include_project_markers", True):
        marker_cmd = [
            "find",
            ".",
            "-maxdepth",
            "4",
            "(",
            "-name",
            "pyproject.toml",
            "-o",
            "-name",
            "package.json",
            "-o",
            "-name",
            "pom.xml",
            "-o",
            "-name",
            "build.gradle",
            "-o",
            "-name",
            "pytest.ini",
            "-o",
            "-name",
            "tox.ini",
            "-o",
            "-name",
            "README.md",
            "-o",
            "-name",
            ".github",
            ")",
            "-print",
        ]
        markers = run_shell(repo_root, marker_cmd, timeout=45)
        lines.extend(["## Project Markers", "", "```text", markers, "```", ""])

    if ctx_cfg.get("include_tests_inventory", True):
        tests = run_shell(
            repo_root,
            ["find", ".", "-maxdepth", "5", "(", "-path", "*/test*", "-o", "-name", "*test*", ")", "-print"],
            timeout=45,
        )
        lines.extend(["## Test Inventory", "", "```text", trim(tests, 12000), "```", ""])

    if ctx_cfg.get("include_task_search_terms", True) and keywords:
        lines.extend(["## Task-Relevant Current Code Search", ""])
        for keyword in keywords[:12]:
            matches = rg(repo_root, re.escape(keyword), limit=max_matches)
            lines.extend([f"### `{keyword}`", "", "```text", trim(matches, 8000), "```", ""])

    if ctx_cfg.get("moralstack_extra_searches", True):
        moralstack_patterns = [
            "GovernanceMetadata|final_action|risk_score|deliberation_cycles",
            "conversation_id|turn_index|request_id|session_id",
            "decision_trace|llm_calls|observability|sqlite|export|dashboard",
            "SAFE_COMPLETE|REFUSE|NORMAL_COMPLETE|deliberative|fast_path",
            "chat.completions|streaming|OpenAI-compatible|compatible",
        ]
        lines.extend(["## MoralStack-Specific Safety Searches", ""])
        for pattern in moralstack_patterns:
            matches = rg(repo_root, pattern, limit=max_matches)
            lines.extend([f"### Pattern `{pattern}`", "", "```text", trim(matches, 8000), "```", ""])

    lines.extend(
        [
            "## Planning Constraints",
            "",
            "- Planning only: do not edit files.",
            "- Independent plans must not see each other before cross-review.",
            "- Important claims require evidence tags: [DOC], [CODE], [TEST], [DRIFT], [ASSUMPTION].",
            "- Unresolved documentation/code conflicts must block final acceptance.",
            "- The final plan must include documentation maintenance updates.",
            "",
        ]
    )

    out = run_dir / "05_context_pack.md"
    write_text(out, "\n".join(lines))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build final context pack for adversarial planning.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_json(Path(args.config))
    out = build_context_pack(Path(args.repo_root).resolve(), Path(args.run_dir), Path(args.task), config)
    print(out)


if __name__ == "__main__":
    main()
