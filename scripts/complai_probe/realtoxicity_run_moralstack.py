"""
realtoxicity_run_moralstack.py — run the focused RealToxicityPrompts test set through
the MoralStack governed pipeline and capture the delivered text.

Phase A of the two-phase comparison (this runs in the **MoralStack** venv, which has the
SDK + OpenAI but NOT detoxify). It reads ``realtoxicity_test.jsonl`` (built by
``realtoxicity_build_test.py``), runs each prompt through the SAME governed pipeline the
OpenAI-compatible proxy uses, and writes the delivered governed completion plus its
routing metadata. Phase B (``realtoxicity_score.py``, run in the compl-ai venv) then
scores those completions with Detoxify using COMPL-AI's exact criteria.

Faithfulness: each item is an independent single-turn request (fresh conversation_id,
turn 0, no developer contract, no history) — exactly the shape COMPL-AI sends. The
delivered text is obtained via the pure ``finalize_delivery`` finalizer, i.e. the
governed-pipeline text the proxy would return (Plan 1 invariant), never an upstream call.

Run (from repo root, with OPENAI_API_KEY in env/.env):
    python scripts/complai_probe/realtoxicity_run_moralstack.py
    python scripts/complai_probe/realtoxicity_run_moralstack.py --workers 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from typing import Any


def _bootstrap():
    try:
        from moralstack.sdk.bootstrap import _bootstrap_pipeline
        from moralstack.sdk.config import GovernanceConfig
        from moralstack.utils.env_loader import load_env
    except Exception as e:  # pragma: no cover
        sys.exit(f"Import failed. Run from the MoralStack repo root (pip install -e .). Detail: {e}")
    load_env()
    config = GovernanceConfig()
    orchestrator = _bootstrap_pipeline(config)
    return orchestrator, config


def _g(obj: Any, name: str, default: Any = None) -> Any:
    val = getattr(obj, name, default)
    return getattr(val, "value", val)


@dataclass
class OutRow:
    sample_id: Any
    label: str
    input: str
    original_prompt: str
    gpt4o_completion: str
    gpt4o_toxicity: float
    moralstack_completion: str
    final_action: str
    path_taken: str
    risk_score: float
    error: str = ""


def _run_one(orchestrator: Any, finalize_delivery: Any, config: Any, s: dict[str, Any]) -> OutRow:
    from moralstack.orchestration.conversation_context import build_conversation_context, context_to_turns
    from moralstack.runtime.orchestrator import ProcessedRequest

    row = OutRow(
        sample_id=s.get("sample_id"),
        label=s.get("label", ""),
        input=s.get("input", ""),
        original_prompt=s.get("original_prompt", ""),
        gpt4o_completion=s.get("gpt4o_completion", ""),
        gpt4o_toxicity=float(s.get("gpt4o_toxicity", 0.0)),
        moralstack_completion="",
        final_action="",
        path_taken="",
        risk_score=0.0,
    )
    try:
        messages = s.get("messages") or [{"role": "user", "content": s.get("input", "")}]
        ctx = build_conversation_context(messages)
        processed = ProcessedRequest(
            prompt=ctx.final_user_message or s.get("input", ""),
            developer_contract=ctx.developer_contract,
            conversation_history=context_to_turns(ctx),
            conversation_context=ctx,
        )
        result = orchestrator.process(
            processed,
            conversation_id=f"rtp-{s.get('sample_id')}-{uuid.uuid4().hex[:8]}",
            turn_index=0,
        )
        delivery = finalize_delivery(result, config=config)
        meta = getattr(getattr(result, "response", None), "metadata", None)
        row.moralstack_completion = delivery.text
        row.final_action = delivery.final_action
        row.path_taken = str(_g(result, "path_taken", "") or "")
        row.risk_score = float(_g(meta, "risk_score", 0.0) or 0.0)
    except Exception as e:
        row.error = f"{type(e).__name__}: {e}"
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default=os.path.join(os.path.dirname(__file__), "realtoxicity_test.jsonl"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "realtoxicity_moralstack_outputs.jsonl"))
    ap.add_argument("--workers", type=int, default=4, help="parallel governed requests (default 4; pipeline fans out LLM calls, watch rate limits)")
    args = ap.parse_args()

    from moralstack.orchestration.delivery import finalize_delivery

    orchestrator, config = _bootstrap()

    with open(args.samples, encoding="utf-8") as f:
        samples = [json.loads(line) for line in f if line.strip()]
    print(f"Running {len(samples)} items through MoralStack with {args.workers} workers...\n")

    results: list[OutRow | None] = [None] * len(samples)

    def _work(i: int) -> int:
        results[i] = _run_one(orchestrator, finalize_delivery, config, samples[i])
        return i

    if args.workers <= 1:
        for i in range(len(samples)):
            _work(i)
            r = results[i]
            print(f"  id={r.sample_id:>4} [{r.label:7}] {r.final_action:15} :: {r.moralstack_completion[:70]!r}{'  ERR:'+r.error if r.error else ''}")
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_work, i): i for i in range(len(samples))}
            for fut in as_completed(futs):
                i = futs[fut]
                fut.result()
                r = results[i]
                print(f"  id={r.sample_id:>4} [{r.label:7}] {r.final_action:15} :: {r.moralstack_completion[:70]!r}{'  ERR:'+r.error if r.error else ''}")

    rows = [r for r in results if r is not None]
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    errs = [r for r in rows if r.error]
    from collections import Counter
    actions = Counter(r.final_action for r in rows if not r.error)
    print(f"\nDone. {len(rows)} items, {len(errs)} errors. final_action: {dict(actions)}")
    print(f"Wrote: {args.out}")
    print("Next: score with the compl-ai venv:")
    print('  & "C:/Users/fdidonato/Documents/progetti/compl-ai/.venv/Scripts/python.exe" scripts/complai_probe/realtoxicity_score.py')


if __name__ == "__main__":
    main()
