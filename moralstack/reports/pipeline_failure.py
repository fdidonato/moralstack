"""
Shared pipeline-failure predicate (pure, no UI or web-framework imports).

A pipeline failure is a request whose controller failed closed in ``process()``
(see ``OrchestratorController._pipeline_failure_result`` and the generic except)
before reaching a governed FINAL decision. It is recognized from STRUCTURED
signals only:

* ``meta_json.triggered_principles`` records one of the synthetic failure
  principles in ``PIPELINE_FAILURE_PRINCIPLES``; and
* no ``FINAL`` decision-trace row was ever written.

It must never be inferred from the delivered text (e.g. the ``[SYSTEM_ERROR]``
placeholder): ``final_action`` and every audit flag come from structured
signals only (PROJECT_SPEC section 5.1).

The benchmark keeps its own narrower set {SYSTEM.ERROR, SYSTEM.TIMEOUT}
(``scripts/benchmark_moralstack.py``, D10a): there FAIL_SAFE stays a governance
REFUSE for metric comparability. This module is for diagnostics (UI, reports).
"""

from __future__ import annotations

from typing import Any

PIPELINE_FAILURE_PRINCIPLES: tuple[str, ...] = ("SYSTEM.ERROR", "SYSTEM.TIMEOUT", "SYSTEM.FAIL_SAFE")


def pipeline_failure_principle(
    traces: list[dict[str, Any]],
    meta_json: dict[str, Any] | None,
) -> str | None:
    """Return the recorded failure principle, or ``None`` if the request is not a pipeline failure.

    When several failure principles are present the first one in
    ``PIPELINE_FAILURE_PRINCIPLES`` order wins (deterministic). A FINAL trace row
    (stage compared stripped and upper-cased) means a governed decision was
    produced, so the request is not a pipeline failure.
    """
    if not isinstance(meta_json, dict):
        return None
    triggered = meta_json.get("triggered_principles")
    if not isinstance(triggered, list):
        return None
    principle = next((p for p in PIPELINE_FAILURE_PRINCIPLES if p in triggered), None)
    if principle is None:
        return None
    if any((t.get("stage") or "").strip().upper() == "FINAL" for t in traces):
        return None
    return principle
