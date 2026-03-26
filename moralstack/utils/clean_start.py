"""
Clean-start utilities: remove artifacts from previous runs before starting.

Used by mstack_run.py and benchmark_moralstack.py when --clean-start is passed.
Supports --clean-db for db_only mode: clears DB tables or deletes DB file.
"""

from __future__ import annotations

import os
from pathlib import Path


def clean_db_artifacts(db_path: str | None = None) -> bool:
    """
    Cleans DB artifacts. When db_path is set, deletes the DB file.
    Returns True if something was cleaned.
    """
    try:
        from moralstack.persistence.config import get_db_path

        path = db_path or get_db_path()
        if not path:
            return False
        p = Path(path)
        if p.is_file():
            p.unlink()
            return True
        return False
    except ImportError:
        return False


def get_project_root() -> Path:
    """Returns the project root (directory containing moralstack/)."""
    return Path(__file__).resolve().parent.parent.parent


def clean_start_artifacts(root: Path | None = None, clean_db: bool = False) -> None:
    """
    Removes artifacts from previous runs before starting:
    - All .md files in reports/
    - All .md, .json, .jsonl files in benchmark_outputs/
    - logs/decision_trace.jsonl
    - .debug/debug.log
    - If clean_db=True and PERSIST_MODE=db_only: deletes DB file

    Args:
        root: Project root directory. If None, uses get_project_root().
        clean_db: If True and db_only mode, also clean DB.
    """
    if root is None:
        root = get_project_root()
    removed: list[str] = []

    if clean_db:
        try:
            from moralstack.persistence.config import get_db_path, get_persist_mode

            if get_persist_mode() == "db_only":
                path = get_db_path()
                if path and clean_db_artifacts(path):
                    removed.append(path)
        except ImportError:
            pass

    for dir_name, pattern in [
        ("reports", "*.md"),
        ("benchmark_outputs", "*.md"),
        ("benchmark_outputs", "*.json"),
        ("benchmark_outputs", "*.jsonl"),
    ]:
        dir_path = root / dir_name
        if dir_path.is_dir():
            for f in dir_path.glob(pattern):
                try:
                    f.unlink()
                    removed.append(str(f.relative_to(root)))
                except OSError as e:
                    if os.getenv("MORALSTACK_VERBOSE"):
                        print(f"  ⚠️  Unable to remove {f}: {e}")

    for rel_path in ["logs/decision_trace.jsonl", ".debug/debug.log"]:
        p = root / rel_path
        if p.is_file():
            try:
                p.unlink()
                removed.append(rel_path)
            except OSError as e:
                if os.getenv("MORALSTACK_VERBOSE"):
                    print(f"  ⚠️  Unable to remove {p}: {e}")

    if removed:
        print("\n🧹 Clean start: removed previous artifacts:")
        for r in removed:
            print(f"   • {r}")
        print()
