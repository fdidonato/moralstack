#!/usr/bin/env python3
"""Pre-commit gate: requires a CHANGELOG.md update when non-infra files change.

Blocks the commit if the staged files include at least one file outside the excluded
prefixes (AI/test infra: ``.claude/``, ``ai/``, ``tests/``) while ``CHANGELOG.md`` is NOT
staged. If ``CHANGELOG.md`` is already updated in the commit, the gate passes.

Intentional bypass (e.g. a purely infrastructural commit the classifier does not
recognize): export ``CHANGELOG_GUARD_SKIP=1``.

Fail-open: if ``git`` cannot be queried (anomalous context), the gate does not block the
commit — it is a process convenience, not a security control.
"""

from __future__ import annotations

import os
import subprocess
import sys

CHANGELOG = "CHANGELOG.md"
# Path prefixes that do NOT require a changelog entry (AI/test infra).
EXCLUDED_PREFIXES = (".claude/", "ai/", "tests/")


def _staged_files() -> list[str]:
    """Names of staged files (Added/Copied/Modified/Renamed)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    if os.environ.get("CHANGELOG_GUARD_SKIP") == "1":
        return 0

    try:
        staged = _staged_files()
    except (subprocess.SubprocessError, OSError) as exc:  # fail-open: do not block on tooling errors
        sys.stderr.write(f"changelog-guard: impossibile leggere lo stage git ({exc}); gate ignorato.\n")
        return 0

    if CHANGELOG in staged:
        return 0  # changelog already updated in this commit

    triggering = [f for f in staged if not any(f.startswith(p) for p in EXCLUDED_PREFIXES)]
    if not triggering:
        return 0  # only AI/test infra changes staged

    sys.stderr.write(
        "CHANGELOG.md non e' stato aggiornato, ma queste modifiche in stage richiedono una voce:\n"
        + "".join(f"  - {f}\n" for f in triggering)
        + "\nAggiungi un bullet nella sezione [Unreleased] di CHANGELOG.md e mettilo in stage,\n"
        "oppure esporta CHANGELOG_GUARD_SKIP=1 per un commit intenzionalmente solo-infra.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
