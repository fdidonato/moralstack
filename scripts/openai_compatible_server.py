"""OpenAI-compatible FastAPI bridge server for MoralStack.

Exposes the same POST /v1/chat/completions (and /chat/completions) interface as
the OpenAI API while routing every request through MoralStack governance.

Run:
    python scripts/openai_compatible_server.py

Environment variables:
    MORALSTACK_OPENAI_COMPATIBLE_API_HOST  (default: localhost)
    MORALSTACK_OPENAI_COMPATIBLE_API_PORT  (default: 8787)
    MORALSTACK_OPENAI_COMPATIBLE_MAX_INFLIGHT (default: 8)
    MORALSTACK_OPENAI_COMPATIBLE_RETRY_AFTER_SECONDS (default: 10)
    OPENAI_API_KEY                          (required)
    OPENAI_MODEL                            (default: gpt-4o)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from moralstack.utils.env_loader import load_env

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

logger = logging.getLogger("moralstack-bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _generation_model() -> str:
    explicit = os.getenv("OPENAI_MODEL", "").strip()
    return explicit or "gpt-4o"


def _max_inflight_requests() -> int:
    value = _env_int("MORALSTACK_OPENAI_COMPATIBLE_MAX_INFLIGHT", 8)
    return max(1, value)


def _retry_after_seconds() -> int:
    value = _env_int("MORALSTACK_OPENAI_COMPATIBLE_RETRY_AFTER_SECONDS", 10)
    return max(1, value)


class ChatMessage(BaseModel):
    role: str
    content: str | list[Any] | None = None


class ChatCompletionRequest(BaseModel):
    model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o"))
    messages: list[ChatMessage]
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stream: bool = False


def _normalize_content(message: ChatMessage) -> str:
    if message.content is None:
        return ""
    if isinstance(message.content, list):
        parts: list[str] = []
        for item in message.content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return " ".join(p for p in parts if p)
    return str(message.content)


def _extract_prompt(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return _normalize_content(m)
    if messages:
        return _normalize_content(messages[-1])
    return ""


# =============================================================================
# MoralStack runner (same pattern as benchmark_moralstack.py MoralStackRunner)
# =============================================================================

_orchestrator: Any | None = None
_orchestrator_lock = threading.Lock()


def _get_orchestrator() -> Any:
    global _orchestrator
    if _orchestrator is not None:
        return _orchestrator
    with _orchestrator_lock:
        if _orchestrator is not None:
            return _orchestrator

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required to run the bridge server.")

        model = _generation_model()
        logger.info("Initializing MoralStack orchestrator (model=%s)...", model)

        from moralstack.constitution.openai_config import OpenAIClientConfig
        from moralstack.constitution.store import ConstitutionStore
        from moralstack.models.policy import OpenAIPolicy
        from moralstack.models.risk import LLMBasedRiskEstimator
        from moralstack.models.risk.config_loader import ENV_MODEL as RISK_ENV_MODEL
        from moralstack.models.risk.config_loader import get_risk_env_str
        from moralstack.orchestration.config_loader import load_orchestrator_config_from_env
        from moralstack.runtime.modules.critic_config_loader import ENV_MODEL as CRITIC_ENV_MODEL
        from moralstack.runtime.modules.critic_config_loader import get_critic_env_str
        from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic
        from moralstack.runtime.modules.hindsight_config_loader import (
            ENV_MODEL as HINDSIGHT_ENV_MODEL,
        )
        from moralstack.runtime.modules.hindsight_config_loader import get_hindsight_env_str
        from moralstack.runtime.modules.hindsight_module import LLMHindsightEvaluator
        from moralstack.runtime.modules.perspective_config_loader import (
            ENV_MODEL as PERSPECTIVES_ENV_MODEL,
        )
        from moralstack.runtime.modules.perspective_config_loader import get_perspective_env_str
        from moralstack.runtime.modules.perspective_module import create_minimal_ensemble
        from moralstack.runtime.modules.simulator_config_loader import (
            ENV_MODEL as SIMULATOR_ENV_MODEL,
        )
        from moralstack.runtime.modules.simulator_config_loader import get_simulator_env_str
        from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator
        from moralstack.runtime.orchestrator import Orchestrator

        policy = OpenAIPolicy(api_key=api_key, model=model)
        constitution_store = ConstitutionStore(
            use_llm_matching=True,
            openai_config=OpenAIClientConfig.with_env_fallback(api_key=api_key, model=model),
        )

        risk_model = get_risk_env_str(RISK_ENV_MODEL, "")
        policy_for_risk = OpenAIPolicy(api_key=api_key, model=risk_model) if risk_model else policy
        risk_estimator = LLMBasedRiskEstimator(
            policy=policy_for_risk,
            constitution_store=constitution_store,
        )

        critic_model = get_critic_env_str(CRITIC_ENV_MODEL, "")
        policy_for_critic = OpenAIPolicy(api_key=api_key, model=critic_model) if critic_model else policy
        critic = LLMConstitutionalCritic(policy=policy_for_critic, store=constitution_store)

        simulator_model = get_simulator_env_str(SIMULATOR_ENV_MODEL, "")
        policy_for_simulator = OpenAIPolicy(api_key=api_key, model=simulator_model) if simulator_model else policy
        simulator = LLMConsequenceSimulator(policy=policy_for_simulator)

        hindsight_model = get_hindsight_env_str(HINDSIGHT_ENV_MODEL, "")
        policy_for_hindsight = OpenAIPolicy(api_key=api_key, model=hindsight_model) if hindsight_model else policy
        hindsight = LLMHindsightEvaluator(policy=policy_for_hindsight)

        perspectives_model = get_perspective_env_str(PERSPECTIVES_ENV_MODEL, "")
        policy_for_perspectives = OpenAIPolicy(api_key=api_key, model=perspectives_model) if perspectives_model else policy
        perspectives = create_minimal_ensemble(policy=policy_for_perspectives)

        config = load_orchestrator_config_from_env()
        _orchestrator = Orchestrator(
            config=config,
            policy=policy,
            risk_estimator=risk_estimator,
            critic=critic,
            simulator=simulator,
            hindsight=hindsight,
            perspectives=perspectives,
            constitution_store=constitution_store,
        )
        logger.info("MoralStack orchestrator initialized")
        return _orchestrator


def _run_moralstack(prompt: str, domain_overlay: str | None = None) -> tuple[str, dict[str, Any]]:
    """Runs prompt through MoralStack orchestrator. Returns (content, metadata)."""
    from moralstack.core.types import UserContext
    from moralstack.observability.config import get_db_path
    from moralstack.observability.context import set_current_run_id
    from moralstack.observability.service import get_obs
    from moralstack.observability.sinks.sqlite_sink import create_run, end_run
    from moralstack.runtime.orchestrator import ProcessedRequest

    orchestrator = _get_orchestrator()
    user_context = UserContext(domain_overlay=domain_overlay) if domain_overlay else UserContext()
    request = ProcessedRequest(prompt=prompt, user_context=user_context)

    run_id = str(uuid.uuid4())
    db_active = bool(get_db_path())
    error_occurred = False

    if db_active:
        create_run(run_id, "single", {"prompt_preview": prompt[:100]})
        set_current_run_id(run_id)

    try:
        result = orchestrator.process(request)
    except Exception:
        error_occurred = True
        raise
    finally:
        if db_active:
            try:
                get_obs().flush(timeout=10.0)
            except Exception:
                pass
            try:
                end_run(run_id, status="error" if error_occurred else "ok")
            except Exception:
                pass
        set_current_run_id(None)

    meta = getattr(result, "response", None) and getattr(result.response, "metadata", None)
    metadata = {
        "final_action": getattr(meta, "final_action", "") or "",
        "risk_score": float(getattr(meta, "risk_score", 0.0) or 0.0),
        "risk_category": getattr(meta, "risk_category", "") or "",
        "path": getattr(result, "path", "") or "",
        "domain_overlay": getattr(meta, "domain_overlay", None) or domain_overlay or "",
        "deliberation_cycles": getattr(result, "total_cycles", 0) or 0,
        "triggered_principles": list(getattr(meta, "triggered_principles", None) or []),
        "processing_time_ms": 0.0,
        "request_id": request.request_id,
    }
    return result.response.content, metadata


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_env()
    _get_inflight_semaphore()
    logger.info(
        "Bridge inflight capacity=%d retry_after_seconds=%d",
        _max_inflight_requests(),
        _retry_after_seconds(),
    )
    try:
        from moralstack.observability.config import get_db_path
        from moralstack.observability.sinks.sqlite_sink import init_db

        db_path = get_db_path()
        if db_path:
            init_db(db_path)
            logger.info("Observability DB initialized at %s", db_path)
    except Exception:
        logger.warning("Observability DB init skipped (not configured or unavailable)")
    yield


app = FastAPI(title="MoralStack OpenAI-compatible Bridge", version="1.0.0", lifespan=lifespan)
_inflight_semaphore: asyncio.Semaphore | None = None


def _get_inflight_semaphore() -> asyncio.Semaphore:
    global _inflight_semaphore
    if _inflight_semaphore is None:
        _inflight_semaphore = asyncio.Semaphore(_max_inflight_requests())
    return _inflight_semaphore


@app.get("/")
async def health() -> dict[str, str]:
    return {"service": "moralstack-bridge", "status": "ok"}


@app.get("/v1/models")
@app.get("/models")
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "governed",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "moralstack",
            }
        ],
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(payload: ChatCompletionRequest) -> dict[str, Any]:
    requested_model = payload.model
    generation_model = _generation_model()
    prompt = _extract_prompt(payload.messages)
    if not prompt.strip():
        prompt = "Placeholder."

    logger.info(
        "chat.completions requested_model=%s generation_model=%s prompt_len=%d",
        requested_model,
        generation_model,
        len(prompt),
    )

    retry_after = _retry_after_seconds()
    inflight_semaphore = _get_inflight_semaphore()
    if inflight_semaphore.locked():
        raise HTTPException(
            status_code=503,
            detail="Bridge at inflight capacity; retry later.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        start = time.time()
        async with inflight_semaphore:
            content, meta = await asyncio.to_thread(_run_moralstack, prompt)
        elapsed_ms = (time.time() - start) * 1000
        meta["processing_time_ms"] = elapsed_ms
    except Exception as exc:
        logger.exception("Bridge error requested_model=%r generation_model=%r", requested_model, generation_model)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    request_id = meta.get("request_id") or f"chatcmpl-{uuid.uuid4().hex[:12]}"
    prompt_tokens = len(prompt.split())
    completion_tokens = len((content or "").split())

    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content or ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "moralstack_metadata": {
            "final_action": meta["final_action"],
            "risk_score": meta["risk_score"],
            "risk_category": meta["risk_category"],
            "path": meta["path"],
            "domain_overlay": meta["domain_overlay"],
            "deliberation_cycles": meta["deliberation_cycles"],
            "triggered_principles": meta["triggered_principles"],
            "processing_time_ms": meta["processing_time_ms"],
            "requested_model": requested_model,
            "generation_model": generation_model,
        },
    }


def main() -> None:
    host = os.getenv("MORALSTACK_OPENAI_COMPATIBLE_API_HOST", "localhost")
    port = _env_int("MORALSTACK_OPENAI_COMPATIBLE_API_PORT", 8787)
    logger.info("Starting MoralStack bridge on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
