"""
[Gap 7, A1] `RiskEstimation` gained defaulted constitution-retrieval carrier fields
(`relevant_principles`, `retrieval_metadata`, `retrieval_count`,
`retrieval_duration_ms`, `retrieval_started_at_ms`, `retrieval_top_k`,
`retrieval_attempted`, `retrieval_succeeded`, `retrieval_error`). All must default
so the `benign`/`clearly_harmful`/`from_error` factories (and any positional-free
construction) still work unchanged, and so older/fallback traces are unaffected.
"""

from __future__ import annotations

from moralstack.models.risk.categories import RiskCategory
from moralstack.models.risk.schema import RiskEstimation


def _assert_defaulted(est: RiskEstimation) -> None:
    assert est.relevant_principles == ()
    assert est.retrieval_metadata == {}
    assert est.retrieval_count == 0
    assert est.retrieval_duration_ms == 0.0
    assert est.retrieval_started_at_ms == 0
    assert est.retrieval_top_k == 0
    assert est.retrieval_attempted is False
    assert est.retrieval_succeeded is False
    assert est.retrieval_error is None


def test_benign_factory_defaults_carrier_fields():
    _assert_defaulted(RiskEstimation.benign())


def test_clearly_harmful_factory_defaults_carrier_fields():
    _assert_defaulted(RiskEstimation.clearly_harmful(semantic_signals=["Q10:weapons_explosives_toxins"]))


def test_from_error_factory_defaults_carrier_fields():
    _assert_defaulted(RiskEstimation.from_error("boom"))


def test_plain_construction_defaults_carrier_fields():
    est = RiskEstimation(score=0.2, confidence=0.5, risk_category=RiskCategory.BENIGN)
    _assert_defaulted(est)


def test_carrier_fields_are_settable_and_frozen():
    est = RiskEstimation(
        score=0.2,
        confidence=0.5,
        risk_category=RiskCategory.BENIGN,
        relevant_principles=("P1", "P2"),
        retrieval_metadata={"prefilter_cache_status": "hit"},
        retrieval_count=2,
        retrieval_duration_ms=12.5,
        retrieval_started_at_ms=1000,
        retrieval_top_k=20,
        retrieval_attempted=True,
        retrieval_succeeded=True,
        retrieval_error=None,
    )
    assert est.relevant_principles == ("P1", "P2")
    assert est.retrieval_metadata == {"prefilter_cache_status": "hit"}
    assert est.retrieval_count == 2
    assert est.retrieval_top_k == 20
    assert est.retrieval_attempted is True
    assert est.retrieval_succeeded is True
