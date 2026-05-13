#!/usr/bin/env python3
"""
MoralStack - Installation (OpenAI-only)
======================================
Installs the MoralStack package in editable mode with all dependencies
(core, dev, ui). Registers the moralstack, moralstack-ui, and moralstack-server CLI entry points.

USAGE:
    python scripts/install.py
    python scripts/install.py --skip-verify
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def print_colored(text: str, color: str = "") -> None:
    colors = {
        "green": "\033[92m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "cyan": "\033[96m",
        "reset": "\033[0m",
    }
    print(f"{colors.get(color, '')}{text}{colors['reset']}")


def run_command(
    cmd: list[str],
    check: bool = True,
    cwd: Path | str | None = None,
) -> tuple[int, str, str]:
    import subprocess

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except subprocess.CalledProcessError as e:
        return e.returncode, e.stdout or "", e.stderr or ""


def verify_installation() -> bool:
    ok = True
    try:
        import openai

        print_colored(f"  openai {getattr(openai, '__version__', 'ok')}", "green")
    except ImportError:
        print_colored("  openai NOT installed", "red")
        ok = False
    try:
        import pytest

        print_colored(f"  pytest {pytest.__version__}", "green")
    except ImportError:
        print_colored("  pytest NOT installed", "red")
        ok = False
    # Use subprocess: current process was started before pip install,
    # so its sys.path may not include the newly installed package.
    code, _, _ = run_command(
        [sys.executable, "-m", "moralstack.cli.run", "--help"],
        check=False,
    )
    if code == 0:
        print_colored("  moralstack CLI (entry point)", "green")
    else:
        print_colored("  moralstack package NOT installed", "red")
        ok = False
    return ok


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Install MoralStack (OpenAI-only)")
    parser.add_argument("--skip-verify", action="store_true", help="Skip final verification")
    args = parser.parse_args()

    print_colored("\nMoralStack - Installation (OpenAI-only)\n", "cyan")
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        print_colored("ERROR: pyproject.toml not found", "red")
        return 1
    print_colored("Installing package (editable) + all dependencies [dev, ui]...", "yellow")
    code, out, err = run_command(
        [sys.executable, "-m", "pip", "install", "-e", ".[dev,ui]"],
        check=False,
        cwd=ROOT,
    )
    if code != 0:
        print_colored(f"Installation error: {err}", "red")
        return 1
    print_colored("Package and dependencies installed.", "green")

    if not args.skip_verify:
        print_colored("\nVerification:", "cyan")
        if not verify_installation():
            return 1

    print_colored("\nNext steps:", "cyan")
    print_colored("  export OPENAI_API_KEY=sk-...  (or set in .env)", "green")
    print_colored("  moralstack                    # Start CLI", "green")
    print_colored("  moralstack --mock             # Test with mock", "green")
    print_colored("  moralstack-ui                 # Web UI (if MORALSTACK_DB_PATH set)", "green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
