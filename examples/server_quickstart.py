"""
MoralStack v0.4 — Server proxy quickstart.

Launches the MoralStack server proxy (FastAPI app) on localhost:8080.
Once running, any OpenAI-compatible client can point its base_url at
http://localhost:8080/v1 and get governed responses with X-Moralstack-*
headers attached.

Run with:
    OPENAI_API_KEY=sk-... python examples/server_quickstart.py

Then in another terminal:
    curl -X POST http://localhost:8080/v1/chat/completions \\
      -H "Content-Type: application/json" \\
      -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}'
"""

from __future__ import annotations

import uvicorn
from openai import OpenAI

from moralstack.sdk.bootstrap import _bootstrap_pipeline
from moralstack.sdk.config import GovernanceConfig
from moralstack.server import create_app


def main() -> None:
    config = GovernanceConfig()
    # `_bootstrap_pipeline` returns the SDK-facing Orchestrator facade, which
    # forwards `.process(...)` to an internal OrchestrationController. The
    # proxy invokes only `.process(...)` so this duck-typed handoff is safe.
    orchestrator = _bootstrap_pipeline(config)
    upstream = OpenAI()  # Uses OPENAI_API_KEY env.

    app = create_app(
        openai_client=upstream,
        orchestrator=orchestrator,
        config=config,
    )

    print("Starting MoralStack server on http://0.0.0.0:8080")
    print("Try: curl http://localhost:8080/healthz")
    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
