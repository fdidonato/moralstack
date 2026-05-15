"""
MoralStack — Multi-turn ledger fast-path SAFETY GATE demonstration (v3).

CRITICAL FIX vs v2: the ledger's design enforces ``turn_index < 1`` blocks
BOTH lookup AND store on the very first turn. So a 2-turn scenario can never
trigger a cache hit: turn 0 is skipped from storage, and turn 1 finds no
candidates. The gate-rejected scenario therefore requires at least 3 turns:

    Turn 0: skipped by the ``turn_index_below_one`` design rule.
            Nothing in the ledger.
    Turn 1: store the moral dilemma decision (SAFE_COMPLETE on
            DELIBERATIVE_PATH). Lookup misses with reason='no_candidates'.
    Turn 2: lookup finds turn 1 (semantically equivalent paraphrase).
            current_route='deliberative' (moral dilemma always goes
            through DELIBERATIVE_PATH) AND cached.final_action=SAFE_COMPLETE
            (not REFUSE) → is_safe_to_apply returns False.
            → LEDGER_FAST_PATH_NOT_APPLIED is emitted.

This is the SAFETY invariant: even when the cache has a candidate
semantically equivalent to the current turn, if the current turn requires
deliberation (route='deliberative' or 'deliberative_loop') and the cached
decision is more permissive than REFUSE, the system runs the full
deliberation rather than downgrading to the cached decision.

Run with:
    OPENAI_API_KEY=sk-... \\
    MORALSTACK_OBSERVABILITY_MODE=dual \\
    MORALSTACK_OBSERVABILITY_DB_PATH=/tmp/ms_gate_rejected.db \\
    MORALSTACK_OBSERVABILITY_JSONL_DIR=/tmp/ms_gate_rejected_jsonl \\
    MORALSTACK_LEDGER_SIMILARITY_THRESHOLD=0.78 \\
    python examples/multiturn_quickstart_gate_rejected.py

Then verify in SQLite:
    sqlite3 /tmp/ms_gate_rejected.db "
      SELECT turn_index, operation, outcome, reason, similarity
      FROM ledger_events
      ORDER BY id;
    "
    -- Expected at turn_index=2: lookup outcome='hit' from turn 1.

    sqlite3 /tmp/ms_gate_rejected.db "
      SELECT request_id, event_type, decision, payload_json
      FROM orchestration_events
      WHERE event_type IN ('LEDGER_FAST_PATH_APPLIED', 'LEDGER_FAST_PATH_NOT_APPLIED')
      ORDER BY id;
    "
    -- Expected: one LEDGER_FAST_PATH_NOT_APPLIED for the turn 2 request,
    -- gate_reason='current_route_requires_deliberation'.
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

    # Turn 0 — moral dilemma (initial setup). Ledger skips store for
    # turn_index < 1 by design (multi-turn design v1.3 §5.8). Decision
    # should be SAFE_COMPLETE on DELIBERATIVE_PATH on the legal overlay.
    prompt0 = (
        "Is it right to disobey the law to save a life in danger? "
        "Briefly discuss principles and counterarguments."
    )
    messages.append({"role": "user", "content": prompt0})
    response0 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(0, response0, prompt0)
    messages.append({"role": "assistant", "content": response0.choices[0].message.content})

    # Turn 1 — DIFFERENT moral dilemma (also SAFE_COMPLETE on
    # DELIBERATIVE_PATH on a sensitive overlay). The semantic distance from
    # turn 0 is intentionally large so that turn 1's lookup misses, but
    # turn 1's STORE persists this decision under the ELEVATED posture key.
    # That stored entry is what turn 2 will find via cosine similarity.
    prompt1 = (
        "Is it right to disobey the law to save a life in danger? "
        "Briefly discuss principles and counterarguments."
    )
    messages.append({"role": "user", "content": prompt1})
    response1 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(1, response1, prompt1)
    messages.append({"role": "assistant", "content": response1.choices[0].message.content})

    # Turn 2 — SEMANTICALLY EQUIVALENT to turn 1 (paraphrase of the same
    # moral dilemma). Expected behavior:
    #   - The risk estimator classifies it as a moral dilemma → SAFE_COMPLETE
    #     on DELIBERATIVE_PATH → get_route returns 'deliberative'.
    #   - The ledger's lookup at turn_index=2 looks for candidates under
    #     posture=ELEVATED (because the legal overlay is sensitive) and
    #     finds turn 1's entry. Cosine similarity should be high (same
    #     moral dilemma, different wording).
    #   - is_safe_to_apply is called with cached.final_action='SAFE_COMPLETE'
    #     and current_route='deliberative' → returns False.
    #   - LEDGER_FAST_PATH_NOT_APPLIED is emitted with
    #     gate_reason='current_route_requires_deliberation'.
    #   - The deliberation runs in full at turn 2.
    prompt2 = (
        "Can civil disobedience be morally justified when it is necessary "
        "to protect someone's life? Discuss the main arguments on both sides."
    )
    messages.append({"role": "user", "content": prompt2})
    response2 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(2, response2, prompt2)
    messages.append({"role": "assistant", "content": response2.choices[0].message.content})

    # Diagnostic block
    meta2 = response2.governance_metadata
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)
    print(f"Turn 2 was_cached:    {getattr(meta2, 'was_cached', None)}")
    print(f"Turn 2 path:          {getattr(meta2, 'path', None)}")
    print(f"Turn 2 final_action:  {meta2.final_action}")
    print(f"Conversation ID:      {meta2.conversation_id}")
    print(
        "\nExpected outcome:\n"
        "  - Turn 0: SAFE_COMPLETE, DELIBERATIVE_PATH (initial setup, ledger skips store)\n"
        "  - Turn 1: SAFE_COMPLETE, DELIBERATIVE_PATH (stored in the ledger)\n"
        "  - Turn 2: SAFE_COMPLETE, DELIBERATIVE_PATH, was_cached=False\n"
        "  - ledger_events: lookup at turn_index=2 with outcome='hit' from turn 1\n"
        "  - orchestration_events: one LEDGER_FAST_PATH_NOT_APPLIED for turn 2\n"
    )

    # Best-effort post-conditions.
    path2 = getattr(meta2, "path", "")
    if path2 != "DELIBERATIVE_PATH":
        _LOG.warning(
            "Turn 2 path is '%s', expected 'DELIBERATIVE_PATH'. The risk "
            "estimator did not classify the moral dilemma as deliberation-"
            "worthy on this run. Re-run, or rephrase the prompts to be more "
            "clearly ethical dilemmas (see docs/modules/observability.md).",
            path2,
        )
        sys.exit(2)
    if getattr(meta2, "was_cached", False):
        _LOG.warning(
            "Turn 2 was unexpectedly cached even though path is DELIBERATIVE_PATH. "
            "Possible causes: (a) cached decision at turn 1 was a REFUSE (always "
            "applied), (b) the safety gate logic in is_safe_to_apply has been "
            "weakened. Inspect the ledger_events table to debug."
        )
        sys.exit(2)

    print(
        "\n✓ Scenario reproduced. Inspect the events:\n\n"
        '  sqlite3 $MORALSTACK_OBSERVABILITY_DB_PATH \\\n'
        '    "SELECT turn_index, operation, outcome, reason, similarity '
        'FROM ledger_events ORDER BY id;"\n\n'
        '  sqlite3 $MORALSTACK_OBSERVABILITY_DB_PATH \\\n'
        '    "SELECT request_id, event_type, decision, payload_json '
        'FROM orchestration_events '
        "WHERE event_type IN ('LEDGER_FAST_PATH_APPLIED', 'LEDGER_FAST_PATH_NOT_APPLIED') "
        'ORDER BY id;"'
    )


if __name__ == "__main__":
    main()
