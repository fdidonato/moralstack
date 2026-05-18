"""
MoralStack v0.4 — Server proxy quickstart.

Launches the MoralStack server proxy (FastAPI app) on 0.0.0.0:8080.
Once running, any OpenAI-compatible client can point its base_url at
http://localhost:8080/v1 and receive governed responses with X-Moralstack-*
headers attached.

Run with:
    OPENAI_API_KEY=sk-... python examples/server_quickstart.py

Or (recommended for COMPL-AI ``llm_rules`` and other multi-turn benchmarks):

    uvicorn examples.server_quickstart:app --host 0.0.0.0 --port 8080

Use a single uvicorn worker/process for conversational multi-turn workloads with
the default in-memory session store and per-conversation locks. Each ``--workers N``
process has its own pipeline, session store, and lock namespace; routing different
HTTP turns to different workers breaks conversation continuity. Multiple workers
are only appropriate for stateless/single-turn traffic unless you introduce a
shared cross-process session store and distributed locking (for example Redis).

Then in another terminal (``model`` in the JSON may be any client alias; upstream
generation uses ``OPENAI_MODEL`` from ``.env``):
    curl -X POST http://localhost:8080/v1/chat/completions \\
      -H "Content-Type: application/json" \\
      -d '{"model":"any-alias","messages":[{"role":"user","content":"Hello"}]}'
"""

from __future__ import annotations

import logging
import os

import uvicorn
from openai import OpenAI

from moralstack.sdk.bootstrap import _bootstrap_pipeline, _resolve_model
from moralstack.sdk.config import GovernanceConfig
from moralstack.server import create_app
from moralstack.utils.env_loader import load_env

logger = logging.getLogger(__name__)


def build_app():
    """
    Construct the ASGI app for uvicorn or embedding.

    ``app = build_app()`` is defined at import time so ``uvicorn
    examples.server_quickstart:app`` works without a custom launcher module.
    """
    load_env()
    config = GovernanceConfig()
    upstream_model = _resolve_model(config)
    logger.info("Upstream generation model (OPENAI_MODEL): %s", upstream_model)
    # `_bootstrap_pipeline` returns the SDK-facing Orchestrator facade, which
    # forwards `.process(...)` to an internal OrchestrationController. The
    # proxy invokes only `.process(...)` so this duck-typed handoff is safe.
    orchestrator = _bootstrap_pipeline(config)
    upstream = OpenAI()  # Uses OPENAI_API_KEY env.

    return create_app(
        openai_client=upstream,
        orchestrator=orchestrator,
        config=config,
    )


app = build_app()


def main() -> None:
    host = os.getenv("MORALSTACK_OPENAI_COMPATIBLE_API_HOST", "localhost")
    port = os.getenv("MORALSTACK_OPENAI_COMPATIBLE_API_PORT", "8787")
    """Convenience single-process launcher (same constraints as uvicorn with one worker)."""
    print(f"Starting MoralStack server on http://{host}:{port}")
    print(f"Try: curl http://{host}:{port}/healthz")
    uvicorn.run(app, host=host, port=int(port))


if __name__ == "__main__":
    main()
