#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "[1/3] Validating .adversarial kit structure..."
python scripts/validate_artifacts.py --root "$ROOT"

echo "[2/3] Checking optional external tools..."
for bin in git rg claude codex; do
  if command -v "$bin" >/dev/null 2>&1; then
    echo "OK: $bin"
  else
    echo "WARN: $bin not found in PATH"
  fi
done

echo "[3/3] Done."
echo "Next: run from repo root: python .adversarial/scripts/adversarial_plan.py --task .adversarial/tasks/example_task.md --dry-run"
