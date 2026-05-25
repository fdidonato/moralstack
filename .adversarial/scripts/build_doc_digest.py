#!/usr/bin/env python
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from common import extract_keywords, load_json, read_text, trim, write_text


def score_text(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(k.lower()) for k in keywords)


def split_sections(markdown: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^#{1,4}\s+.*$", markdown))
    if not matches:
        return [("Full Document", markdown)]
    sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        title = match.group(0).strip()
        sections.append((title, markdown[start:end].strip()))
    return sections


def select_relevant_sections(text: str, keywords: list[str], max_chars: int) -> str:
    if not keywords:
        return trim(text, max_chars)
    sections = split_sections(text)
    ranked = sorted(sections, key=lambda item: score_text(item[1], keywords), reverse=True)
    selected: list[str] = []
    total = 0
    for title, body in ranked:
        score = score_text(body, keywords)
        if score <= 0 and selected:
            continue
        chunk = trim(body, min(6000, max_chars))
        if total + len(chunk) > max_chars and selected:
            continue
        selected.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return "\n\n---\n\n".join(selected) if selected else trim(text, max_chars)


def build_digest(run_dir: Path, task_path: Path, config: dict[str, Any]) -> Path:
    manifest = load_json(run_dir / "01_baseline_manifest.json")
    task_text = read_text(task_path)
    keywords = extract_keywords(task_text, limit=int(config.get("context_pack", {}).get("task_keyword_limit", 18)))
    max_chars = int(config.get("limits", {}).get("max_digest_chars_per_document", 18000))

    lines: list[str] = [
        "# Baseline Digest",
        "",
        "This digest is generated from the trusted adversarial documentation baseline. "
        "It is task-specific and must be treated as architectural context, "
        "not as a substitute for current code verification.",
        "",
        "## Task Keywords",
        "",
        ", ".join(keywords) if keywords else "No specific keywords extracted.",
        "",
        "## Trust Policy",
        "",
        "```json",
    ]
    import json

    lines.append(json.dumps(manifest.get("trust_policy", {}), indent=2, ensure_ascii=False))
    lines.extend(["```", ""])

    for doc in manifest.get("documents", []):
        if not doc.get("exists"):
            continue
        snapshot_rel = doc.get("snapshot_path")
        if not snapshot_rel:
            continue
        path = run_dir / snapshot_rel
        text = read_text(path)
        always_include = doc.get("always_include", False)
        relevant = always_include or score_text(text, keywords) > 0
        if not relevant:
            continue
        lines.extend(
            [
                f"## Document: {doc['path']}",
                "",
                f"- Role: `{doc.get('role')}`",
                f"- Authority: `{doc.get('authority')}`",
                f"- SHA256: `{doc.get('sha256')}`",
                "",
                "### Relevant Extract",
                "",
                select_relevant_sections(text, keywords, max_chars=max_chars),
                "",
            ]
        )

    trace_docs = []
    for doc in manifest.get("trace_documents", []):
        snapshot_rel = doc.get("snapshot_path")
        if not snapshot_rel:
            continue
        path = run_dir / snapshot_rel
        text = read_text(path)
        score = score_text(text, keywords)
        if score > 0:
            trace_docs.append((score, doc, text))
    trace_docs.sort(key=lambda item: item[0], reverse=True)

    if trace_docs:
        lines.extend(["## Task-Relevant Trace Documents", ""])
        for _, doc, text in trace_docs[:10]:
            lines.extend(
                [
                    f"### Trace: {doc['path']}",
                    "",
                    f"- Role: `{doc.get('role')}`",
                    f"- Authority: `{doc.get('authority')}`",
                    f"- SHA256: `{doc.get('sha256')}`",
                    "",
                    select_relevant_sections(text, keywords, max_chars=min(10000, max_chars)),
                    "",
                ]
            )

    lines.extend(
        [
            "## Required Baseline Constraints",
            "",
            "- Use documentation as primary evidence for architectural intent and invariants.",
            "- Use current code as primary evidence for runtime behavior, exact file paths, symbols and tests.",
            "- Mark doc/code mismatches as `[DRIFT]` or `DOC_CODE_CONFLICT`.",
            "- Do not produce implementation steps without validation commands.",
            "- Include documentation maintenance updates in the final plan.",
            "",
        ]
    )

    out = run_dir / "03_baseline_digest.md"
    write_text(out, "\n".join(lines))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a task-specific digest from a baseline snapshot.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_json(Path(args.config))
    out = build_digest(Path(args.run_dir), Path(args.task), config)
    print(out)


if __name__ == "__main__":
    main()
