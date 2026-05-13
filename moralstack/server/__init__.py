"""
MoralStack server proxy: FastAPI app exposing OpenAI-compatible endpoints
governed by MoralStack.

Per design v1.3 §4.2, the proxy is a thin HTTP wrapper on the already-validated
SDK (GovernedClient). It does NOT re-implement governance logic.

Usage:
    from moralstack.server import create_app
    app = create_app(openai_client=..., orchestrator=..., config=...)
    # Run: uvicorn module_with_app:app
"""

from __future__ import annotations

from moralstack.server.proxy import create_app

__all__ = ["create_app"]
