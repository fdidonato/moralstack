---
paths:
  - "moralstack/compliance/**"
  - "moralstack/orchestration/path_router.py"
  - "moralstack/prompts/**"
  - "moralstack/pipeline/deliberation_stack.py"
---
# Invariant — Hard-signal supremacy (P0)

Load-bearing. If a change appears to require breaking this, stop and surface it to
the user rather than working around it.

**Hard-signal supremacy (P0).** Hard topical signals (self-harm, child safety,
weapons, physical harm) must not be overridable by a developer contract, a domain
overlay, or a cached ledger decision. See `path_router.is_hard_signal_refuse` and the
DCCL Safety Override (`moralstack/compliance/safety_override.py`).

**Accepted exception: deployer-side prompt overrides (user decision 2026-10-19).** A
deployer-configured `prompts_dir` (`GovernanceConfig.prompts_dir` /
`MORALSTACK_PROMPTS_DIR`) may replace the *text* (and the `when` condition) of any
prompt fragment, including the safety-relevant ones (`SAFETY_FRAGMENT_IDS` in
`moralstack/prompts/catalog.py`: every `risk.`, `critic.`, `compliance.` and `refusal.`
fragment, plus the listed `policy.*` safety instructions and `retrieval.prefilter.system`). This is an accepted, audited vector that only the
deployer controls; it is not a developer-contract, overlay or cache vector. Each
overridden safety fragment is logged as one WARNING at load and listed under
`safety_fragments_overridden` in the set's audit summary (`PromptSet.audit_summary()`), the
`prompt_config` record that the run audit persists (step A4). It changes what the models are told, never
the code-level gates, which no prompt, contract, overlay or cache can override:
`path_router.is_hard_signal_refuse`, the DCCL safety-override category check, the
code-owned response schemas/formats, and the ledger hard-signal guard.
