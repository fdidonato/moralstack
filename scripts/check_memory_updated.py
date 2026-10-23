#!/usr/bin/env python3
"""Pre-commit gate: requires the verified memory to be updated when behavior changes.

Why at commit and not at the Stop gate: the Stop gate is a best-effort *nudge* anchored to
``.claude/.session-edits.json``, and it can be bypassed (a test silences it, edits via Bash or
MultiEdit do not end up there, it does not reset mid-session). The guarantee belongs where you cannot
skip it: at commit, with ``git diff --cached`` as the source of truth (what THIS commit changes
relative to HEAD). Tests do NOT count as a substitute for memory.

Rule (FINE mapping, aligned with ``.claude/rules/docs-maintenance.md``): every governance-code prefix has
a set of memory docs that satisfy it. If a file under a behavior prefix is staged but NONE of its
memory docs is staged, the commit is blocked.

Bypass for a justified exception: ``MEMORY_GUARD_SKIP=1``. Fail-open on git error (it is a process
guarantee, not a security control).
"""

from __future__ import annotations

import os
import subprocess
import sys

# Governance-code prefix -> memory docs that constitute its update.
# A change under the prefix requires AT LEAST one of its docs staged.
# Keep the prefixes aligned with .claude/hooks/stop_gate.py:BEHAVIOR_PREFIXES.
BEHAVIOR_DOC_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("moralstack/runtime/decision/", ("docs/decision_policy.md",)),
    ("moralstack/compliance/", ("docs/traces/complai_llm_rules_flow.md",)),
    (
        "moralstack/orchestration/",
        ("docs/modules/orchestrator.md", "docs/traces/governance_decision_flow.md"),
    ),
    ("moralstack/prompts/", ("docs/decision_policy.md", "docs/prompt_overrides.md")),
    (
        "moralstack/observability/",
        ("docs/modules/observability.md", "docs/traces/observability_db_to_ui.md"),
    ),
    (
        "moralstack/server/",
        ("docs/traces/openai_compatible_multiturn.md", "docs/traces/observability_db_to_ui.md"),
    ),
    ("moralstack/constitution/", ("docs/modules/constitution_store.md", "docs/constitution.md")),
    # Runtime deliberative modules: each has its own long-form contract in
    # docs/modules/. Per-module mapping (the prefix covers both <mod>_module.py and
    # <mod>_config_loader.py) because a change to the critic must update critic.md,
    # not just any module doc. These modules decide: the critic is the only
    # producer of violated_hard, and without a hard violation the delivery-point guard never
    # fires. The last row is a safety net for shared code and future modules.
    ("moralstack/runtime/modules/critic_", ("docs/modules/critic.md",)),
    ("moralstack/runtime/modules/simulator_", ("docs/modules/simulator.md",)),
    ("moralstack/runtime/modules/perspective_", ("docs/modules/perspectives.md",)),
    ("moralstack/runtime/modules/hindsight_", ("docs/modules/hindsight.md",)),
    (
        "moralstack/runtime/modules/",
        (
            "docs/modules/critic.md",
            "docs/modules/simulator.md",
            "docs/modules/perspectives.md",
            "docs/modules/hindsight.md",
        ),
    ),
)


def _staged_files() -> list[str]:
    """Staged files (Added/Copied/Modified/Renamed)."""
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
    except (subprocess.SubprocessError, OSError) as exc:  # fail-open: do not block on tooling errors
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
