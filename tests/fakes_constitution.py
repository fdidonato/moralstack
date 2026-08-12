"""Shared constitution-store test doubles for the retrieval request-scoped-state suite.

``GatedSharedDebugInfoStore`` is the whole falsifiability argument for T1/T2
(ai/plans/retrieval-request-scoped-state.md): it implements BOTH the legacy
shared-attribute channel (``get_relevant_principles`` + ``get_debug_info``,
rebinding ``self._last_debug_info`` per call — mirroring
``retriever.py:1150,1214,1394-1396``, the channel that let one request read
another's ``detected_domain``) AND the new per-call ``retrieve()`` channel,
gated through the SAME ``_compute`` helper so the interleave happens
identically on both paths. A reader driven against this double is RED on
today's code (legacy channel, last-writer-wins) and GREEN only once it
switches to consuming ``retrieve()``'s return value. Getting the gate wrong
(e.g. only gating one of the two paths) makes the whole test suite prove
nothing — see plan §"the single most important detail in the test plan".
"""

from __future__ import annotations

import threading
from typing import Any

from moralstack.constitution.retrieval_result import PrincipleRetrievalResult


class GatedSharedDebugInfoStore:
    """Dual-channel constitution store double for deterministic concurrency tests.

    A query containing the literal substring ``"LEGAL"`` gates: it signals
    ``_entered`` and blocks on ``_release`` AFTER writing the shared
    ``_last_debug_info`` attribute but BEFORE returning — the exact window
    that separates the principles fetch from the debug-info read in the
    pre-fix estimator/runner code. Any other query returns immediately.
    """

    def __init__(self) -> None:
        self._entered = threading.Event()
        self._release = threading.Event()
        self._last_debug_info: dict[str, Any] = {}

    def _compute(self, query: str) -> list[str]:
        own = "legal" if "LEGAL" in query else "medical"
        self._last_debug_info = {"prefiltered_domains": ["core", own]}  # legacy rebind (retriever.py:1214)
        if "LEGAL" in query:  # gate: both get_relevant_principles and retrieve() route through here
            self._entered.set()
            assert self._release.wait(timeout=5.0), "release not signaled: broken test setup"
        return ["core", own]

    def get_relevant_principles(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = "risk_routing",
    ) -> list[Any]:
        """Legacy channel: principles by return value, domain via the shared
        instance attribute read back through ``get_debug_info()`` (unsafe)."""
        self._compute(query)
        return []

    def get_debug_info(self) -> dict[str, Any]:
        """Legacy channel read-back (mirrors ``retriever.py:1394-1396``)."""
        return dict(self._last_debug_info)

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = "risk_routing",
    ) -> PrincipleRetrievalResult:
        """New channel: everything travels on the return value, computed locally
        per call — never read back cross-thread."""
        own_domains = self._compute(query)
        return PrincipleRetrievalResult(
            principles=(),
            prefiltered_domains=tuple(own_domains),
            debug_info={"prefiltered_domains": list(own_domains)},
        )


class RetrieveLessPrincipleStore:
    """Legacy store: implements ``get_relevant_principles`` but has NO
    ``retrieve()`` at all (not even one that raises) — the exact shape
    ``getattr(store, "retrieve", None)`` must degrade on.

    Records every ``get_relevant_principles`` call so a test can assert on
    the exact ``query`` the runner's guarded fallback sent it — required
    test 1 of ai/handoffs/retrieval-request-scoped-state-fix-handoff.md: the
    fallback must use the ENRICHED query, never the raw prompt.
    """

    def __init__(self, principles: tuple[Any, ...] = ()) -> None:
        self._principles = principles
        self.calls: list[dict[str, Any]] = []

    def get_relevant_principles(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
    ) -> list[Any]:
        self.calls.append({"query": query, "top_k": top_k, "domain": domain})
        return list(self._principles)


class RetrieveNoMarkerStore:
    """Store whose ``retrieve()`` returns a ``PrincipleRetrievalResult`` with
    NO ``domain_channel`` key in ``debug_info`` — proves the runner's
    ``setdefault("domain_channel", "retrieve")`` (not a test-supplied value)
    is what produces the persisted marker (required test 2 of
    ai/handoffs/retrieval-request-scoped-state-fix-handoff.md)."""

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = "risk_routing",
    ) -> PrincipleRetrievalResult:
        return PrincipleRetrievalResult(
            principles=(),
            prefiltered_domains=("core", "legal"),
            debug_info={"prefiltered_domains": ["core", "legal"]},
        )
