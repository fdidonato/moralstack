#!/usr/bin/env python3
"""Pre-commit gate: esige l'aggiornamento della memoria verificata quando cambia il behavior.

Perche' al commit e non allo Stop gate: lo Stop gate e' un *nudge* best-effort ancorato a
``.claude/.session-edits.json``, ed e' aggirabile (un test lo silenzia, gli edit via Bash o
MultiEdit non vi finiscono, non si resetta a meta' sessione). La garanzia va dove non puoi
saltarla: al commit, con fonte di verita' ``git diff --cached`` (cosa cambia QUESTO commit
rispetto a HEAD). I test NON contano come sostituto della memoria.

Regola (mappatura FINE, allineata a ``.claude/rules/docs-maintenance.md``): ogni prefisso di
codice-governance ha un insieme di doc-memoria che lo soddisfano. Se in stage c'e' un file
sotto un prefisso behavior ma NESSUNO dei suoi doc-memoria e' in stage, il commit e' bloccato.

Bypass di un'eccezione motivata: ``MEMORY_GUARD_SKIP=1``. Fail-open su errore git (e' una
garanzia di processo, non un controllo di sicurezza).
"""

from __future__ import annotations

import os
import subprocess
import sys

# Prefisso codice-governance -> doc-memoria che ne costituiscono l'aggiornamento.
# Un cambio sotto il prefisso richiede in stage ALMENO uno dei suoi doc.
# Tieni i prefissi allineati a .claude/hooks/stop_gate.py:BEHAVIOR_PREFIXES.
BEHAVIOR_DOC_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("moralstack/runtime/decision/", ("docs/decision_policy.md",)),
    ("moralstack/compliance/", ("docs/TRACES/complai_llm_rules_flow.md",)),
    (
        "moralstack/orchestration/",
        ("docs/modules/orchestrator.md", "docs/TRACES/governance_decision_flow.md"),
    ),
    ("moralstack/prompts/", ("docs/decision_policy.md",)),
    (
        "moralstack/observability/",
        ("docs/modules/observability.md", "docs/TRACES/observability_db_to_ui.md"),
    ),
    (
        "moralstack/server/",
        ("docs/TRACES/openai_compatible_multiturn.md", "docs/TRACES/observability_db_to_ui.md"),
    ),
    ("moralstack/constitution/", ("docs/modules/constitution_store.md", "docs/constitution.md")),
)


def _staged_files() -> list[str]:
    """File in stage (Added/Copied/Modified/Renamed)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    if os.environ.get("MEMORY_GUARD_SKIP") == "1":
        return 0

    try:
        staged = _staged_files()
    except (subprocess.SubprocessError, OSError) as exc:  # fail-open: non bloccare per errori di tooling
        sys.stderr.write(f"memory-guard: impossibile leggere lo stage git ({exc}); gate ignorato.\n")
        return 0

    staged_set = set(staged)
    unsatisfied: list[tuple[str, tuple[str, ...]]] = []
    for prefix, docs in BEHAVIOR_DOC_MAP:
        touched = any(f.startswith(prefix) for f in staged)
        if touched and not any(doc in staged_set for doc in docs):
            unsatisfied.append((prefix, docs))

    if not unsatisfied:
        return 0

    sys.stderr.write(
        "Behavior di governance modificato senza aggiornare la memoria verificata.\n"
        "Fonte di verita': git diff --cached. I test NON contano come memoria.\n\n"
    )
    for prefix, docs in unsatisfied:
        sys.stderr.write(f"  - {prefix} richiede in stage uno tra: {', '.join(docs)}\n")
    sys.stderr.write(
        "\nAggiorna il doc pertinente (vedi .claude/rules/docs-maintenance.md), mettilo in stage,\n"
        "oppure esporta MEMORY_GUARD_SKIP=1 per un'eccezione motivata (nessun contratto/flusso\n"
        "cambiato).\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
