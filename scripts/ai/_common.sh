#!/usr/bin/env bash
# Shared helpers for the MoralStack AI agentic workflow scripts (bash).
# Source this: . "$(dirname "$0")/_common.sh"
# No side effects, no network, no commits, no destructive git.
set -euo pipefail

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
}

timestamp() { date +"%Y%m%d-%H%M%S"; }

ensure_dir() { mkdir -p "$1"; printf '%s\n' "$1"; }
