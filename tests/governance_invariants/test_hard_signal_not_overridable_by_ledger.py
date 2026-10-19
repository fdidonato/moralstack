"""
Governance invariant: hard-signal supremacy (§5.3, P0) is not overridable by a cached
ledger decision.

A cached non-REFUSE (NORMAL/SAFE) ledger decision must never be reused for a request whose
risk estimation carries hard-signal evidence (any of the 8 ``_HARD_SEMANTIC_SIGNALS`` or the
``clearly_harmful`` category). The predicate is the pure helper
``ledger_hard_signal_block_reason`` (``conversational_fast_path.py``); the controller apply
site must honor it on the ``fast_path`` route, where the cached decision used to skip the
only HARD check of the freshly generated draft (``quick_check``). A cached REFUSE is never
blocked (errs safe). Same shape as
``test_hard_signal_not_overridable_by_retrieval_wave.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moralstack.orchestration.path_router import _HARD_SEMANTIC_SIGNALS
from tests.test_ledger_hard_signal_evidence_guard import (
    NORMAL,
    REFUSE,
    SAFE,
    _decision,
    _evid_signal,
    _no_evid,
    _process,
    _rig,
    _seed,
)


@pytest.mark.parametrize("hard_signal", sorted(_HARD_SEMANTIC_SIGNALS))
def test_every_hard_signal_blocks_cached_non_refuse_reuse(hard_signal: str) -> None:
    from moralstack.orchestration.conversational_fast_path import ledger_hard_signal_block_reason

    est = SimpleNamespace(semantic_signals=[hard_signal], risk_category="benign")
    for cached_action in (NORMAL, SAFE):
        assert (
            ledger_hard_signal_block_reason(
                cached_action=cached_action,
                risk_estimation=est,  # type: ignore[arg-type]
                hard_signal_refuse=False,
            )
            == "hard_signal_evidence"
        )
    assert (
        ledger_hard_signal_block_reason(
            cached_action=REFUSE,
            risk_estimation=est,  # type: ignore[arg-type]
            hard_signal_refuse=False,
        )
        is None
    )


def test_non_p0_signal_does_not_block_reuse() -> None:
    """Negative control: without a hard signal the helper does not block, so the
    parametrized positives above are not vacuously true."""
    from moralstack.orchestration.conversational_fast_path import ledger_hard_signal_block_reason

    est = SimpleNamespace(semantic_signals=["SOME_OTHER_SIGNAL"], risk_category="benign")
    assert (
        ledger_hard_signal_block_reason(
            cached_action=NORMAL,
            risk_estimation=est,  # type: ignore[arg-type]
            hard_signal_refuse=False,
        )
        is None
    )


def test_fast_path_hard_evidence_hit_never_skips_the_hard_check() -> None:
    """Controller level: a cached NORMAL hit on a hard-evidence fast_path request is not
    applied and ``quick_check`` runs; the no-evidence twin IS applied (the seed hits)."""
    twin = _rig(_no_evid())
    _seed(twin.ledger, NORMAL)
    _process(twin, route="fast_path", decision=_decision(), request_id="req-inv-twin", conversation_id="conv-inv-twin")
    assert len(twin.emitter.of_type("LEDGER_FAST_PATH_APPLIED")) == 1
    twin.critic.quick_check.assert_not_called()

    rig = _rig(_evid_signal("Q17:minor_exploitation"))
    _seed(rig.ledger, NORMAL)
    _process(rig, route="fast_path", decision=_decision(), request_id="req-inv", conversation_id="conv-inv")
    assert rig.emitter.of_type("LEDGER_FAST_PATH_APPLIED") == []
    assert len(rig.emitter.of_type("LEDGER_FAST_PATH_NOT_APPLIED")) == 1
    rig.critic.quick_check.assert_called_once()
