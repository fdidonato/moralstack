"""
MoralStack — Multi-turn ledger fast-path SAFETY GATE demonstration.

This example exercises the THIRD branch of ConversationalFastPathRunner.is_safe_to_apply:
when the ledger has a candidate hit for the current turn, but the safety gate
refuses to apply it because the current turn requires deliberation (route is
'deliberative' or 'deliberative_loop') and the cached decision is not REFUSE.

In that case the cached decision is NOT applied: the deliberation runs in full,
and an orchestration.event with event_type=LEDGER_FAST_PATH_NOT_APPLIED is
emitted so the audit trail explains why deliberation ran even though the
ledger had a candidate.

This is the safety invariant: when the current run signals higher risk than
what was cached, the system NEVER downgrades to the cached (more permissive)
decision. The cache helps benign repeated queries; it never weakens safety.

Design rationale (why this scenario is hard to construct):
    The risk estimator is LLM-based, so triggering route='deliberative' from a
    benign-looking topic requires a query with operational/technical framing
    that elevates op_risk to LOW (not NONE, not HIGH). Examples that
    empirically work:
        - "How does GPS triangulation work and what's its accuracy?"
        - "Explain how credit card chargeback fraud is detected"
        - "Walk me through how DNS spoofing works defensively"

Run with:
    OPENAI_API_KEY=sk-... \\
    MORALSTACK_OBSERVABILITY_MODE=dual \\
    MORALSTACK_OBSERVABILITY_DB_PATH=/tmp/ms_gate_rejected.db \\
    MORALSTACK_OBSERVABILITY_JSONL_DIR=/tmp/ms_gate_rejected_jsonl \\
    MORALSTACK_LEDGER_SIMILARITY_THRESHOLD=0.78 \\
    python examples/multiturn_quickstart_gate_rejected.py

Then verify in SQLite:
    sqlite3 /tmp/ms_gate_rejected.db "
      SELECT request_id, event_type, decision, payload_json
      FROM orchestration_events
      WHERE event_type IN ('LEDGER_FAST_PATH_APPLIED', 'LEDGER_FAST_PATH_NOT_APPLIED')
      ORDER BY id;
    "

You should see one LEDGER_FAST_PATH_NOT_APPLIED row corresponding to turn 1,
with gate_reason='current_route_requires_deliberation' in the payload.

Best-effort note:
    Because the risk estimator is non-deterministic on borderline queries, the
    gate-rejected event may not appear on every run. If you don't see it, try:
    (a) re-running, (b) using a more technical framing for turn 1, or (c)
    lowering MORALSTACK_LEDGER_SIMILARITY_THRESHOLD further so the lookup
    finds candidates.
"""

from __future__ import annotations

import logging
import os
import sys

from openai import OpenAI

from moralstack import govern
from moralstack.utils.env_loader import load_env

_LOG = logging.getLogger(__name__)


def _print_turn(idx: int, response, prompt: str) -> None:
    meta = response.governance_metadata
    reply = response.choices[0].message.content
    cached = " [CACHED]" if getattr(meta, "was_cached", False) else ""
    print(f"\n--- Turn {idx} ---{cached}")
    print(f"Prompt: {prompt}")
    print(f"Decision: {meta.final_action} (risk={meta.risk_score:.3f})")
    print(f"Path: {getattr(meta, 'path', '—')}")
    print(f"Posture: {getattr(meta, 'governance_posture', '—')}")
    print(f"Reply: {reply[:180]}{'…' if len(reply) > 180 else ''}")


def main() -> None:
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set.")
    client = govern(OpenAI())

    messages: list[dict[str, str]] = []

    # Turn 0 — simple factual question. Cache is skipped (turn_index < 1).
    # Establishes context only; the orchestrator stores nothing in the ledger.
    prompt0 = "What's the climate of Sardinia like?"
    messages.append({"role": "user", "content": prompt0})
    response0 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(0, response0, prompt0)
    messages.append({"role": "assistant", "content": response0.choices[0].message.content})

    # Turn 1 — paraphrase of turn 0, but the storage will now persist this
    # entry. Decision should be NORMAL_COMPLETE on a non-deliberative route.
    prompt1 = "Describe the climate patterns and seasons typical of Sardinia."
    messages.append({"role": "user", "content": prompt1})
    response1 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(1, response1, prompt1)
    messages.append({"role": "assistant", "content": response1.choices[0].message.content})

    # Turn 2 — Same topic (climate / Sardinia), but with technical/operational
    # framing that should bump op_risk to LOW and risk_score above 0.3, pushing
    # the path router to ROUTE='deliberative'. The ledger should find a hit
    # against turn 1 (high cosine similarity on "climate of Sardinia"), but
    # the safety gate should REJECT it because the current route requires
    # deliberation and the cached decision is NORMAL_COMPLETE (not REFUSE).
    prompt2 = (
        "Walk me through how meteorological monitoring stations in Sardinia "
        "could be used to detect anomalous microclimate patterns and identify "
        "weak points in the regional climate surveillance infrastructure."
    )
    messages.append({"role": "user", "content": prompt2})
    response2 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(2, response2, prompt2)
    messages.append({"role": "assistant", "content": response2.choices[0].message.content})

    # Verification block: read back the meta to see what happened.
    meta2 = response2.governance_metadata
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)
    print(f"Turn 2 was_cached: {getattr(meta2, 'was_cached', None)}")
    print(f"Turn 2 path: {getattr(meta2, 'path', None)}")
    print(f"Turn 2 final_action: {meta2.final_action}")
    print(f"Conversation ID: {meta2.conversation_id}")
    print(
        "\nExpected outcome for this example:\n"
        "  - Turn 2 was_cached should be False (gate rejected the hit).\n"
        "  - Turn 2 path should be DELIBERATIVE_PATH.\n"
        "  - One row in orchestration_events with\n"
        "    event_type='LEDGER_FAST_PATH_NOT_APPLIED' for the turn 2 request.\n"
    )

    if getattr(meta2, "was_cached", False):
        _LOG.warning(
            "Turn 2 was unexpectedly cached. The risk estimator did not classify "
            "the technical framing as deliberation-worthy. This is a best-effort "
            "scenario; the gate-rejected branch could not be triggered on this run."
        )
        # Exit with non-zero to signal the scenario didn't manifest, but don't
        # fail outright (this is best-effort given LLM non-determinism).
        sys.exit(2)

    print("\nNow inspect the orchestration_events table to see the gate event:\n")
    print(
        '  sqlite3 $MORALSTACK_OBSERVABILITY_DB_PATH \\\n'
        '    "SELECT request_id, event_type, decision, payload_json '
        'FROM orchestration_events '
        "WHERE event_type IN ('LEDGER_FAST_PATH_APPLIED', 'LEDGER_FAST_PATH_NOT_APPLIED') "
        'ORDER BY id;"'
    )


if __name__ == "__main__":
    main()
