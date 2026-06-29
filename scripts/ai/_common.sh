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

# Resolve Codex CLI; override with $CODEX_CMD (default: codex). Echoes cmd or empty.
resolve_codex() {
  local cmd="${CODEX_CMD:-codex}"
  command -v "$cmd" >/dev/null 2>&1 && { printf '%s\n' "$cmd"; return 0; }
  return 1
}

# Resolve the cursor *agent* CLI (headless implementer), not the GUI launcher.
# Order: $CURSOR_CMD, cursor-agent on PATH, Windows LOCALAPPDATA install.
resolve_cursor_agent() {
  if [ -n "${CURSOR_CMD:-}" ]; then
    if command -v "$CURSOR_CMD" >/dev/null 2>&1 || [ -e "$CURSOR_CMD" ]; then
      printf '%s\n' "$CURSOR_CMD"; return 0
    fi
  fi
  command -v cursor-agent >/dev/null 2>&1 && { printf '%s\n' "cursor-agent"; return 0; }
  local win="${LOCALAPPDATA:-$HOME/AppData/Local}/cursor-agent/cursor-agent.cmd"
  [ -e "$win" ] && { printf '%s\n' "$win"; return 0; }
  return 1
}
