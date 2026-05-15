"""
MoralStack — Multi-turn ledger fast-path demonstration.

Demonstrates a multi-turn conversation where the SemanticDecisionLedger
DOES cache a decision and a later semantically-equivalent turn IS reused.

Design rationale (why the previous evaluation example didn't show a cache hit):
    The ledger explicitly SKIPS storage and lookup when the governance posture
    is ESCALATED (the design choice for hard-signal refusals like CBRN /
    weapons). See moralstack/orchestration/ledger.py:14-18.

    Two malicious "how to build a bomb" queries therefore CANNOT cache-hit
    each other — they always trigger full deliberation. This is by design.

    To observe the fast-path in action, this example uses repeated benign
    queries with semantic variations on the same topic. The ledger should:
      - turn 0: store skipped (turn_index < 1)
      - turn 1: store the question about Paris geography
      - turn 2: ASK SEMANTICALLY EQUIVALENT question → CACHE HIT from turn 1

Run with:
    OPENAI_API_KEY=sk-... \\
    MORALSTACK_OBSERVABILITY_MODE=dual \\
    MORALSTACK_OBSERVABILITY_DB_PATH=/tmp/ms_fastpath.db \\
    MORALSTACK_OBSERVABILITY_JSONL_DIR=/tmp/ms_fastpath_jsonl \\
    python examples/multiturn_quickstart_fastpath_hit.py

Then verify in SQLite:
    sqlite3 /tmp/ms_fastpath.db "
      SELECT turn_index, operation, outcome, reason, similarity, from_turn
      FROM ledger_events
      ORDER BY turn_index, created_at;
    "

You should see a row with operation='lookup', outcome='hit', from_turn=1.
"""

from __future__ import annotations

import os

from openai import OpenAI

from moralstack import govern
from moralstack.utils.env_loader import load_env


def _print_turn(idx: int, response, prompt: str) -> None:
    meta = response.governance_metadata
    reply = response.choices[0].message.content
    cached = " [CACHED]" if getattr(meta, "was_cached", False) else ""
    print(f"\n--- Turn {idx} ---{cached}")
    print(f"Prompt: {prompt}")
    print(f"Domain: {meta.domain_overlay}")
    print(f"Decision: {meta.final_action} (risk={meta.risk_score:.3f})")
    print(f"Path: {getattr(meta, 'path', '—')}")
    print(f"Posture: {getattr(meta, 'governance_posture', '—')}")
    print(f"Reply: {reply[:200]}{'…' if len(reply) > 200 else ''}")


def main() -> None:
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set.")
    client = govern(OpenAI())

    messages: list[dict[str, str]] = []

    # Turn 0 — benign factual question. Store is skipped (turn_index < 1).
    prompts = [
        "What is the capital of France?",
        # Turn 1 — different topic. Store will persist this (posture=NORMAL).
        "Tell me about the climate of southern Italy.",
        # Turn 2 — SEMANTICALLY EQUIVALENT to turn 1, different wording.
        # Should produce a ledger lookup HIT from turn 1 (similarity > 0.92).
        "Describe the typical weather patterns of southern Italy.",
        # Turn 3 — back to the Paris topic, different wording, semantically
        # close to turn 0. But turn 0 was NOT stored (skip rule for turn 0),
        # so this will be a miss.
        "What city is the capital of the French Republic?",
        # Turn 4 — repeat of turn 2 with very minor variation. Should HIT
        # turn 1 again (or turn 2, depending on which has higher similarity).
        "How is the climate in southern Italian regions?",
    ]

    for idx, prompt in enumerate(prompts):
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
        )
        _print_turn(idx, response, prompt)
        messages.append({"role": "assistant", "content": response.choices[0].message.content})

    print(f"\nConversation ID: {response.governance_metadata.conversation_id}")
    print(
        "\nNow inspect the ledger events to see the cache hit pattern:\n"
        "  sqlite3 $MORALSTACK_OBSERVABILITY_DB_PATH \\\n"
        '    "SELECT turn_index, operation, outcome, reason, similarity, '
        'from_turn FROM ledger_events ORDER BY turn_index, created_at;"'
    )


if __name__ == "__main__":
    main()
