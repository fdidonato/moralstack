# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.3.3
22/04/2026

- fixed a bug in the audit of sdk

## 0.3.1
21/04/2026

- create publish on pypi workflow

## 0.3.0
21/04/2026

### Added
- `examples/` directory with runnable scripts: quickstart, forced overlay, automatic detection, custom overlay, batch evaluation, audit export.
- PyPI publishing workflow (`.github/workflows/publish.yml`) using trusted publishing.
- Project metadata in `pyproject.toml`: license, keywords, classifiers, URLs.

### Fixed
- improved traceability in file_only mode when using SDK

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
