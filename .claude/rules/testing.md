---
paths:
  - "tests/**"
---
# Testing expectations

- Tests live in `tests/` and are extensive (~120 files). Run the relevant subset for any
  change, and the full suite before declaring a task done: `python -m pytest` (or a
  scoped `python -m pytest tests/test_<area>.py`).
- Tests marked `@pytest.mark.slow` (performance benchmarks, e.g.
  `test_persistence_load.py::test_throughput_new_not_slower_than_legacy`) are **excluded
  from the default run** via `addopts = "-ra -m 'not slow'"` in `pyproject.toml`. The
  default `python -m pytest` therefore does not run them; opt in with `python -m pytest
  -m slow` (or `-m ""` to run everything). CI runs them in a dedicated "Perf benchmarks
  (slow)" step.
- Behavior-locking tests exist for: byte-equality
  (`test_system_prompt_byte_equality.py`), governance invariants
  (`tests/governance_invariants/`), decision policy (`test_decide_action.py`,
  `test_safe_complete_*.py`), observability contracts (`test_observability_*.py`),
  proxy/correlation (`test_server_proxy.py`, `test_conversation_correlation.py`), and the
  ledger (`test_ledger*.py`).
- Do **not** weaken or delete a test to make a change pass. If a test must change, justify
  why in the PR/commit message.
- Tests that hit the network/OpenAI use doubles/mocks; keep new tests offline and
  deterministic.
