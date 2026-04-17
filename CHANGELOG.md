# CHANGELOG

## 0.2.0
17/04/2026

- Python SDK: `govern(client)` wraps any OpenAI-compatible client with MoralStack governance
- New public API: `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`
- `GovernedCompletions.create()` intercepts `chat.completions.create()` with pre-call deliberation
- Decision routing: NORMAL_COMPLETE passes through, SAFE_COMPLETE injects governance constraints, REFUSE skips OpenAI call entirely
- Streaming support: `GovernedStreamResponse` for normal/safe, `GovernedRefusalStream` for REFUSE
- Session tracking: `conversation_id` and `turn_index` persisted across calls on the same `GovernedClient`
- `GovernanceMetadata`: immutable audit snapshot of every deliberation (risk score, reason codes, triggered principles, counterfactual reasoning)
- `failure_policy`: configurable behavior on pipeline error (`refuse` or `passthrough`)
- Lazy imports in `moralstack/__init__.py`: `import moralstack` loads nothing at import time
- `moralstack/sdk/` package: `errors.py`, `config.py`, `session.py`, `response.py`, `bootstrap.py`, `wrapper.py`
- 106 new SDK tests (unit + integration with mock pipeline)
- Orchestration decoupling: `govern()` has zero FastAPI/uvicorn dependencies

## 0.1.0
30/03/2026

- \#1 Deliberative latency and observability: speculative overlap, structured outputs, and UI alignment
