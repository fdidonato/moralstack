"""
MoralStack — Multi-turn ledger fast-path SAFETY GATE demonstration (v6).

This is the canonical demonstration of the gate-rejected branch of the
SemanticDecisionLedger.is_safe_to_apply safety gate.

What it proves
==============
1. PROPERTY OF CACHING: two semantically equivalent moral-dilemma queries
   produce a cosine similarity above threshold. The fast-path retrieval
   system correctly recognises them as the same governance question.
2. PROPERTY OF SAFETY: even when the retrieval would succeed, the safety
   gate refuses to apply the cached decision because the current turn is
   classified as deliberation-worthy (route='deliberative') and the cached
   decision is more permissive than REFUSE. The full deliberation runs
   in this turn, preserving the system's deliberative guarantee.

This is the safety invariant: the cache accelerates only when applying it
would not weaken the current turn's safety posture.

Lessons embedded:
    1. (Step 14.2) Ledger wired in _bootstrap_pipeline.
    2. (Step 14.3) request_type aligned between store and lookup.
    3. (Step 14.8) posture aligned between store and lookup.
    4. (Bug 4 workaround) domain_overlay='legal' pinned via GovernanceConfig
       to bypass the LLM-based domain detector's non-determinism.
    5. (v6) prompts engineered for genuine semantic equivalence on the
       SAME ethical scenario, with surgical lexical substitutions
       (disobey/break, doing so/it, someone's/a person's, discuss/present).
       Atteso similarity ~0.90.

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
      FROM ledger_events ORDER BY id;
    "
    -- Expected at turn 2: lookup outcome='hit', similarity > 0.78

    sqlite3 /tmp/ms_gate_rejected.db "
      SELECT request_id, event_type, decision, payload_json
      FROM orchestration_events
      WHERE event_type IN ('LEDGER_FAST_PATH_APPLIED', 'LEDGER_FAST_PATH_NOT_APPLIED')
      ORDER BY id;
    "
    -- Expected: one LEDGER_FAST_PATH_NOT_APPLIED for turn 2,
    -- gate_reason='current_route_requires_deliberation'.
"""

from __future__ import annotations

import logging
import os
import sys

from openai import OpenAI

from moralstack import govern
from moralstack.sdk.config import GovernanceConfig
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
    print(f"Domain: {getattr(meta, 'domain_overlay', '—')}")
    print(f"Reply: {reply[:180]}{'…' if len(reply) > 180 else ''}")


def main() -> None:
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set.")

    # Pin domain_overlay='legal' to bypass the LLM-based domain detector,
    # whose non-determinism would otherwise produce different overlays for
    # paraphrased moral dilemmas (legal vs political vs journalism), making
    # the LedgerKey unstable across turns.
    config = GovernanceConfig(domain_overlay="legal")
    client = govern(OpenAI(), config=config)

    messages: list[dict[str, str]] = []

    # Turn 0 — moral dilemma (initial setup). Ledger.store skipped by design
    # (turn_index < 1, multi-turn design v1.3 §5.8). Decision should be
    # SAFE_COMPLETE on DELIBERATIVE_PATH on the legal overlay (ELEVATED
    # posture).
    prompt0 = (
        "Is it ethically justifiable to disobey the law when doing so is "
        "necessary to save someone's life? Briefly discuss the main "
        "principles and counterarguments."
    )
    messages.append({"role": "user", "content": prompt0})
    response0 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(0, response0, prompt0)
    messages.append({"role": "assistant", "content": response0.choices[0].message.content})

    # Turn 1 — same moral dilemma. The ledger now stores this decision under
    # LedgerKey(contract_hash='', posture='ELEVATED', domain='legal').
    # Same prompt as turn 0 to make the chain unambiguous; the test
    # mechanism is verified by turn 2's paraphrase, not by this repetition.
    prompt1 = (
        "Is it ethically justifiable to disobey the law when doing so is "
        "necessary to save someone's life? Briefly discuss the main "
        "principles and counterarguments."
    )
    messages.append({"role": "user", "content": prompt1})
    response1 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(1, response1, prompt1)
    messages.append({"role": "assistant", "content": response1.choices[0].message.content})

    # Turn 2 — TRUE PARAPHRASE of turn 1. Same ethical question,
    # surgical synonym substitutions only:
    #   - "Is it ethically justifiable" = identical anchor
    #   - "disobey" → "break"  (synonyms in legal/ethical register)
    #   - "doing so is" → "it is"  (same conditional structure)
    #   - "someone's life" → "a person's life"  (semantically identical)
    #   - "discuss" → "present"  (same speech act in this context)
    # Atteso cosine similarity ~0.90 under text-embedding-3-small.
    prompt2 = (
        "Is it ethically justifiable to break the law when it is "
        "necessary to save a person's life? Briefly present the main "
        "principles and counterarguments."
    )
    messages.append({"role": "user", "content": prompt2})
    response2 = client.chat.completions.create(model="gpt-4o", messages=messages)
    _print_turn(2, response2, prompt2)
    messages.append({"role": "assistant", "content": response2.choices[0].message.content})

    # Diagnostics
    meta2 = response2.governance_metadata
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)
    print(f"Turn 2 was_cached:    {getattr(meta2, 'was_cached', None)}")
    print(f"Turn 2 path:          {getattr(meta2, 'path', None)}")
    print(f"Turn 2 final_action:  {meta2.final_action}")
    print(f"Turn 2 domain:        {getattr(meta2, 'domain_overlay', None)}")
    print(f"Conversation ID:      {meta2.conversation_id}")
    print(
        "\nExpected outcome:\n"
        "  - All three turns: SAFE_COMPLETE, DELIBERATIVE_PATH, domain=legal\n"
        "  - Turn 2 ledger lookup outcome='hit', similarity > 0.78\n"
        "  - Turn 2 was_cached=False (gate rejected the hit; deliberation ran)\n"
        "  - orchestration_events: one LEDGER_FAST_PATH_NOT_APPLIED for turn 2,\n"
        "    gate_reason='current_route_requires_deliberation'\n"
    )

    path2 = getattr(meta2, "path", "")
    if path2 != "DELIBERATIVE_PATH":
        _LOG.warning(
            "Turn 2 path is '%s', expected 'DELIBERATIVE_PATH'. The moral "
            "dilemma was not classified as deliberation-worthy on this run.",
            path2,
        )
        sys.exit(2)
    if getattr(meta2, "was_cached", False):
        _LOG.warning(
            "Turn 2 was unexpectedly cached. The safety gate did NOT reject "
            "the cache application, which is unexpected on DELIBERATIVE_PATH "
            "with a SAFE_COMPLETE cached decision. Inspect is_safe_to_apply "
            "logic and verify the controller emission of "
            "LEDGER_FAST_PATH_NOT_APPLIED."
        )
        sys.exit(2)

    print(
        "\n✓ Scenario reproduced. Verify with:\n\n"
        "  sqlite3 $MORALSTACK_OBSERVABILITY_DB_PATH \\\n"
        '    "SELECT turn_index, operation, outcome, reason, similarity '
        'FROM ledger_events ORDER BY id;"\n\n'
        "  sqlite3 $MORALSTACK_OBSERVABILITY_DB_PATH \\\n"
        '    "SELECT request_id, event_type, decision, payload_json '
        "FROM orchestration_events "
        "WHERE event_type IN ('LEDGER_FAST_PATH_APPLIED', 'LEDGER_FAST_PATH_NOT_APPLIED') "
        'ORDER BY id;"'
    )


if __name__ == "__main__":
    main()
