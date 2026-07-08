#!/usr/bin/env python3
"""Pre-commit gate: richiede un aggiornamento di CHANGELOG.md quando cambiano file non-infra.

Blocca il commit se tra i file in stage c'e' almeno un file al di fuori dei prefissi
esclusi (infra AI/test: ``.claude/``, ``ai/``, ``tests/``) mentre ``CHANGELOG.md`` NON
e' in stage. Se ``CHANGELOG.md`` e' gia' aggiornato nel commit, il gate passa.

Bypass intenzionale (es. commit puramente infrastrutturale che il classificatore non
riconosce): esporta ``CHANGELOG_GUARD_SKIP=1``.

Fail-open: se ``git`` non e' interrogabile (contesto anomalo), il gate non blocca il
commit — e' una comodita' di processo, non un controllo di sicurezza.
"""

from __future__ import annotations

import os
import subprocess
import sys

CHANGELOG = "CHANGELOG.md"
# Prefissi di path che NON richiedono una voce di changelog (infra AI/test).
EXCLUDED_PREFIXES = (".claude/", "ai/", "tests/")


def _staged_files() -> list[str]:
    """Nomi dei file in stage (Added/Copied/Modified/Renamed)."""
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
    except (subprocess.SubprocessError, OSError) as exc:  # fail-open: non bloccare per errori di tooling
        sys.stderr.write(f"changelog-guard: impossibile leggere lo stage git ({exc}); gate ignorato.\n")
        return 0

    if CHANGELOG in staged:
        return 0  # changelog gia' aggiornato in questo commit

    triggering = [f for f in staged if not any(f.startswith(p) for p in EXCLUDED_PREFIXES)]
    if not triggering:
        return 0  # solo modifiche infra AI/test in stage

    sys.stderr.write(
        "CHANGELOG.md non e' stato aggiornato, ma queste modifiche in stage richiedono una voce:\n"
        + "".join(f"  - {f}\n" for f in triggering)
        + "\nAggiungi un bullet nella sezione [Unreleased] di CHANGELOG.md e mettilo in stage,\n"
        "oppure esporta CHANGELOG_GUARD_SKIP=1 per un commit intenzionalmente solo-infra.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
