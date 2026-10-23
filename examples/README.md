# MoralStack Examples

These runnable examples show how to integrate MoralStack's Python SDK in real applications.
They are useful for developers integrating governed LLM calls, teams evaluating behavior,
and contributors who want a quick map of the public API in practice.

## Prerequisites

- Python >= 3.11
- Install from repo root with `pip install -e .` (or `pip install moralstack` after publication)
- Set `OPENAI_API_KEY` in your environment (copy the repo-root `.env.template` to `.env` and fill values, or export directly)
- Cost warning: each deliberative call can use 7-9 OpenAI requests. Running all examples can use ~30-50 calls, and a single deliberative query is often around ~70s.

## Example Index

| File | What it shows | Estimated time | LLM calls |
|---|---|---|---|
| `quickstart.py` | Minimal governed call and metadata inspection | ~70s | ~7-9 |
| `forced_overlay.py` | Force a specific overlay via `GovernanceConfig(domain_overlay=...)` | ~2-3 min | ~14-18 |
| `domain_detection.py` | Automatic overlay detection across multiple domains | ~4-6 min | ~28-45 |
| `batch_evaluation.py` | Small decision matrix with expected vs actual actions | ~6-10 min | ~56-72 |
| `audit_export.py` | Export JSONL observability events to a Markdown audit report | ~5-20s (file processing) | 0 |
| `custom_overlay/run_custom_overlay.py` | Temp-dir constitution pattern for a custom overlay | ~2-4 min | ~14-18 |

Proxy, multi-turn and regression examples:

| File | What it shows |
|---|---|
| `server_quickstart.py` | OpenAI-compatible governance proxy (`create_app`); run with `uvicorn examples.server_quickstart:app --port 8080` and a single worker |
| `multiturn_quickstart.py` | A 3-turn conversation on one `GovernedClient`, with the conversation id managed automatically |
| `multiturn_jailbreak_resistance.py` | A conversation that escalates from benign to harmful is detected and refused |
| `multiturn_audit_trail.py` | Export a conversation's audit trail as Markdown |
| `multiturn_quickstart_withcacheaed_evaluation.py` | Semantic decision ledger hit: a later equivalent turn reuses the cached decision |
| `multiturn_quickstart_gate_rejected.py` | Ledger safety gate: a similar cached decision is rejected and the turn is deliberated in full |
| `airbench/run_regression.py` | Replays failed AIR-Bench / XSTest / CoCoNot cases through the proxy, checking decision and delivered text (see `airbench/README.md`) |

**Important: overlays are always active.** MoralStack loads all 21 overlay YAML files at startup. For every query, the internal domain detector selects the most relevant overlay automatically. When an example "activates" an overlay, it is **forcing** a specific one via `GovernanceConfig(domain_overlay="healthcare")`, overriding the automatic detection. This is useful when:
- You want a guaranteed overlay regardless of query wording (for example, a telemedicine app should always apply `healthcare`).
- You are testing an overlay in isolation without relying on the LLM-based detector.

## Troubleshooting

- `OPENAI_API_KEY not set`: export it in your shell or load it from `.env`.
- `Domain X not found`: check available overlays in `moralstack/constitution/data/overlays/`.
- Calls are slow: this is expected for deliberative paths (~70s). For smoke checks, try prompts likely to stay in `FAST_PATH`.
- Custom overlay issues: review `docs/creating_overlays.md` and the pattern in `examples/custom_overlay/`.
