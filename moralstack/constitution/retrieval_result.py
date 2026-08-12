"""
PrincipleRetrievalResult - typed return value of one constitution retrieval call.

Leaf module (stdlib imports only): kept free of any MoralStack dependency on
purpose so ``orchestration/types.py``, ``orchestration/deliberation_runner.py``
and ``models/risk/estimator.py`` can import it with no risk of an import cycle
back into ``constitution/retriever.py`` or ``constitution/store.py``.

Mirrors the precedent in ``orchestration/process_context.py`` ("mutable state
that must not live on the controller instance ... created at the start of each
call and passed explicitly"): this dataclass replaces a shared instance
attribute on ``ConstitutionRetriever`` that a concurrent request could
overwrite between another request's write and read (see
``docs/CODEBASE_FACTS.md``).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

_LOG = logging.getLogger(__name__)

_missing_retrieve_warned_types: set[str] = set()
_missing_retrieve_warn_lock = threading.Lock()


def warn_missing_retrieve_once(store: Any) -> None:
    """Rate-limited (once per process, per store class) WARNING when a
    constitution store has no ``retrieve()`` and a reader degrades to the legacy
    ``get_relevant_principles()``-only path (``domain_channel="fallback_no_retrieve"``;
    in the risk estimator also ``runtime_domain=None``).

    Loud by design (plan §6, blocking 3): silently inheriting another request's
    domain via the retired shared-instance-attribute debug channel is exactly the
    P0 bug this fallback avoids, so the degradation must never be silent — never
    ``debug``-level, never a stale domain.

    Lives here, next to the type whose absence it reports, because **both**
    readers need it: ``models/risk/estimator.py`` and
    ``orchestration/deliberation_runner.py``. It previously lived in the
    estimator and was imported across packages as a private symbol.
    """
    store_type = type(store).__name__
    if store_type in _missing_retrieve_warned_types:
        return
    with _missing_retrieve_warn_lock:
        if store_type in _missing_retrieve_warned_types:
            return
        _missing_retrieve_warned_types.add(store_type)
    _LOG.warning(
        "constitution_store type=%s has no retrieve(); domain detection degraded to "
        "runtime_domain=None (domain_channel=fallback_no_retrieve). Implement "
        "retrieve() -> PrincipleRetrievalResult to restore per-request domain detection.",
        store_type,
    )


@dataclass(frozen=True)
class PrincipleRetrievalResult:
    """Everything one retrieval call produced. Never stored on the retriever.

    ``prefiltered_domains`` is the decision channel (§5.1 decision/generation
    separation): the raw prefilter output, which DOES include ``"core"``
    (§5.5 — ``core`` is retrieval-only). The caller owns the ``core`` exclusion;
    this type never filters it out.

    ``debug_info`` is best-effort telemetry (§5.6): its key shape mirrors the
    retired per-retrieval debug dict this type replaces, kept for audit-trail
    compatibility.

    Both fields are built from the same local variable inside
    ``ConstitutionRetriever.retrieve()``, so they cannot diverge.
    """

    principles: tuple[Any, ...] = ()
    prefiltered_domains: tuple[str, ...] = ()
    debug_info: dict[str, Any] = field(default_factory=dict)
