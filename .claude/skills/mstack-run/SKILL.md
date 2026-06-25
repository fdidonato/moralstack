---
name: mstack-run
description: >-
  Launch the MoralStack app locally — interactive CLI shell, web UI dashboard, or
  the OpenAI-compatible governance proxy — using the project venv and .env. Use when
  asked to run, start, launch, or open MoralStack, the UI dashboard, or the
  governance proxy/server, or to smoke-test a change against the running app.
---

# Running MoralStack locally

Prereqs: package installed editable (`pip install -e ".[dev,ui]"`), a `.env` in the
repo root with at least `OPENAI_API_KEY` (loaded automatically at startup). On Windows
use the venv interpreters under `venv/Scripts/`; console scripts below resolve there
once the venv is active (`venv\Scripts\activate`) — otherwise prefix with
`venv/Scripts/`.

> `model=` in any request is a **requested alias only**; the governed answer model is
> `OPENAI_MODEL` from `.env` (see `.claude/rules/governed-delivery.md`).

## 1. Interactive CLI shell (most common)

```bash
moralstack            # real launch — requires OPENAI_API_KEY in .env
moralstack --mock     # mock modules, no API key, no network
moralstack --verbose  # full deliberation trace
```
Type a prompt; the engine evaluates risk and responds (NORMAL_COMPLETE / SAFE_COMPLETE
/ REFUSE). Legacy wrapper: `python scripts/mstack_run.py`.

## 2. Web UI dashboard (browse runs / export reports)

Requires `MORALSTACK_OBSERVABILITY_DB_PATH` set (enables persistence) plus
`MORALSTACK_UI_USERNAME` / `MORALSTACK_UI_PASSWORD` for Basic Auth.

```bash
moralstack-ui          # serves http://localhost:8765/  (port: MORALSTACK_UI_PORT)
```
Open http://localhost:8765/ → login → dashboard at `/runs`.
**401 troubleshooting:** run from the repo root so `.env` is found; if the password
contains `#` or `=`, quote it in `.env` (`MORALSTACK_UI_PASSWORD="pa#ss"`).

## 3. OpenAI-compatible governance proxy

Serves governed responses to any OpenAI client at `…/v1`. Defined in
`examples/server_quickstart.py` (`build_app()` / `app`).

```bash
# Recommended for multi-turn / COMPL-AI llm_rules — SINGLE worker only:
uvicorn examples.server_quickstart:app --host 0.0.0.0 --port 8080
# Or the script form:
python examples/server_quickstart.py
```
Point a client at `http://localhost:8080/v1`. Use **one** uvicorn worker for
conversational/multi-turn traffic — each `--workers N` process has its own in-memory
session store and per-conversation locks, so multiple workers break conversation
continuity (default store is in-memory). Smoke test:

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"any-alias","messages":[{"role":"user","content":"Hello"}]}'
```

## 4. Benchmark

```bash
python scripts/benchmark_moralstack.py   # see docs/modules/benchmark.md for flags
```

## Notes
- All runtime tuning is env-only (`MORALSTACK_*` in `.env` / `.env.template`); there is
  no CLI override for module models/thresholds.
- The proxy never forwards live upstream tokens; `stream=True` replays governed text as
  synthetic SSE.
