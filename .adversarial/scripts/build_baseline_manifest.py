#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import (
    AdversarialError,
    adversarial_root,
    copy_file_preserving_relative,
    find_repo_root,
    git_output,
    iter_markdown_files,
    load_json,
    sha256_file,
    write_json,
)


def snapshot_baseline(repo_root: Path, run_dir: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    snapshot_root = run_dir / "02_baseline_snapshot"
    snapshot_root.mkdir(parents=True, exist_ok=True)

    documents: list[dict[str, Any]] = []
    missing_required: list[str] = []

    for doc in manifest.get("required_documents", []):
        rel = doc["path"]
        src = repo_root / rel
        exists = src.exists() and src.is_file()
        record = {
            "path": rel,
            "role": doc.get("role"),
            "authority": doc.get("authority"),
            "required": bool(doc.get("required", False)),
            "always_include": bool(doc.get("always_include", False)),
            "exists": exists,
            "sha256": None,
            "snapshot_path": None,
        }
        if exists:
            dst = copy_file_preserving_relative(repo_root, rel, snapshot_root)
            record["sha256"] = sha256_file(src)
            record["snapshot_path"] = str(dst.relative_to(run_dir))
        elif record["required"]:
            missing_required.append(rel)
        documents.append(record)

    traces: list[dict[str, Any]] = []
    for trace_dir in manifest.get("trace_directories", []):
        rel_dir = trace_dir["path"]
        src_dir = repo_root / rel_dir
        if src_dir.exists() and src_dir.is_dir():
            for md in iter_markdown_files(src_dir):
                rel = str(md.relative_to(repo_root)).replace("\\", "/")
                dst = copy_file_preserving_relative(repo_root, rel, snapshot_root)
                traces.append(
                    {
                        "path": rel,
                        "role": trace_dir.get("role"),
                        "authority": trace_dir.get("authority"),
                        "required": bool(trace_dir.get("required", False)),
                        "exists": True,
                        "sha256": sha256_file(md),
                        "snapshot_path": str(dst.relative_to(run_dir)),
                    }
                )
        elif trace_dir.get("required", False):
            missing_required.append(rel_dir)

    if missing_required:
        raise AdversarialError("Missing required baseline documents/directories:\n- " + "\n- ".join(missing_required))

    snapshot_manifest = {
        "baseline": manifest.get("baseline", {}),
        "git": {
            "branch": git_output(repo_root, ["branch", "--show-current"]),
            "commit": git_output(repo_root, ["rev-parse", "HEAD"]),
            "status_short": git_output(repo_root, ["status", "--short"]),
        },
        "trust_policy": manifest.get("trust_policy", {}),
        "documents": documents,
        "trace_documents": traces,
    }
    write_json(run_dir / "01_baseline_manifest.json", snapshot_manifest)
    return snapshot_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a baseline snapshot for an adversarial run.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--manifest", default=None)
    args = parser.parse_args()

    repo_root = find_repo_root(Path(args.repo_root))
    adv = adversarial_root(repo_root)
    manifest_path = Path(args.manifest) if args.manifest else adv / "baseline" / "manifest.json"
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path

    snapshot = snapshot_baseline(repo_root, Path(args.run_dir), manifest_path)
    print(
        f"Baseline snapshot created "
        f"with {len(snapshot['documents'])} declared docs "
        f"and {len(snapshot['trace_documents'])} trace docs."
    )


if __name__ == "__main__":
    main()
