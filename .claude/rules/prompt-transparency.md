---
paths:
  - "moralstack/orchestration/_policy_helpers.py"
  - "moralstack/prompts/**"
  - "tests/test_system_prompt_byte_equality.py"
---
# Invariant — System-prompt transparency & single-turn byte-equality (P0)

Load-bearing. If a change appears to require breaking either, stop and surface it to
the user rather than working around it.

**System-prompt transparency.** The developer-declared system prompt is never mutated
by governance. The system prompt composed for MoralStack's own policy generator
preserves the developer prompt verbatim; governance guidance (e.g. `SAFE_COMPLETE`) is
conveyed inside the governed pipeline, not by rewriting the developer system prompt.
(Plan 1: delivered text now always comes from the governed pipeline, so there is no
wrapped-client call whose messages payload must be preserved; the transparency
requirement applies to the prompt sent to the governed generator.) MoralStack's own
generator prompt (`POLICY_SYSTEM_PROMPT` in `orchestration/_policy_helpers.py`) carries
MoralStack-authored governance steering — the fairness clause and a non-toxicity clause
(paired with constitution `SOFT.STYLE.2`) — which yields to higher safety rules and to
explicit output-format constraints. Editing this MoralStack-owned base does not violate
the byte-equality contract: the `effective_system_for_request` resolver still adds no
extra per-request injection when there is no developer contract
(`tests/test_system_prompt_byte_equality.py` compares against the current
`POLICY_SYSTEM_PROMPT`, not a frozen literal).

**Single-turn prompt transparency.** With no `developer_contract` and no
`conversation_history`, the prompt/system composition the pipeline feeds to the governed
generator must stay byte-identical to the single-turn baseline (see
`tests/test_system_prompt_byte_equality.py`). This locks prompt transparency, not the
delivered answer bytes (which, per Plan 1, are produced by MoralStack's policy generator).
