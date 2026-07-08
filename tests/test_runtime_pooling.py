"""Runtime pooling: OpenAI client reuse (retrieval) and policy reuse (risk mini-estimators)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.retriever import DomainPrefilter
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.observability.context import set_current_cycle, set_current_request_id, set_current_run_id


def _fake_completion_json_obj(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    ch = MagicMock()
    ch.message = msg
    resp = MagicMock()
    resp.choices = [ch]
    resp.usage = None
    return resp


def test_domain_prefilter_reuses_openai_client_across_calls():
    """Same DomainPrefilter instance: one OpenAI() construct; multiple completion calls."""
    cfg = OpenAIClientConfig(api_key="sk-test", model="gpt-4o-mini")
    pre = DomainPrefilter(openai_config=cfg)
    payload = json.dumps({"domains": ["core"], "confidence": 0.9})
    fake = _fake_completion_json_obj(payload)

    with patch("openai.OpenAI") as ctor:
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=fake)
        ctor.return_value = mock_client

        r1 = pre._call_openai("q1", system_prompt="s")
        r2 = pre._call_openai("q2", system_prompt="s")

        assert ctor.call_count == 1
        assert mock_client.chat.completions.create.call_count == 2
        assert r1.get("domains") == ["core"]
        assert r2.get("domains") == ["core"]
        assert pre._openai_client_creates == 1
        assert pre._openai_client_reuses_after_cache == 1
        assert mock_client.chat.completions.create.call_args_list[0].kwargs["messages"][0] == {
            "role": "system",
            "content": "s",
        }


def test_domain_prefilter_new_client_when_api_key_changes():
    pre = DomainPrefilter(openai_config=OpenAIClientConfig(api_key="sk-a", model="gpt-4o-mini"))
    fake = _fake_completion_json_obj('{"domains":[],"confidence":0}')

    with patch("openai.OpenAI") as ctor:
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=fake)
        ctor.return_value = mock_client

        pre._call_openai("x", system_prompt="s")
        pre.openai_config = OpenAIClientConfig(api_key="sk-b", model="gpt-4o-mini")
        pre._call_openai("y", system_prompt="s")

        assert ctor.call_count == 2


def test_risk_estimator_pools_mini_policies_per_model():
    """Same target model -> one OpenAIPolicy construct per estimator instance."""
    main = MagicMock()
    main.model = "gpt-4o-main"
    main.tracker = None

    cfg = RiskEstimatorConfig(
        intent_model="gpt-mini-x",
        signals_model="gpt-mini-y",
        operational_model="gpt-mini-x",
    )
    est = LLMBasedRiskEstimator(policy=main, config=cfg)

    with patch("moralstack.models.policy.OpenAIPolicy") as mock_policy_cls:
        mock_policy_cls.side_effect = lambda *a, **k: MagicMock()
        p1 = est._policy_for_mini_estimator_model("gpt-mini-x")
        p2 = est._policy_for_mini_estimator_model("gpt-mini-x")
        p3 = est._policy_for_mini_estimator_model("gpt-mini-y")

        assert p1 is p2
        assert p3 is not p1
        assert mock_policy_cls.call_count == 2

    d = est.get_pooling_diagnostics()
    assert set(d["risk_mini_policy_pool_models"]) == {"gpt-mini-x", "gpt-mini-y"}
    assert d["risk_policy_pool_misses"] == 2
    assert d["risk_policy_pool_hits"] >= 1


def test_get_pooling_diagnostics_stable_keys():
    main = MagicMock()
    main.model = "m"
    main.tracker = None
    est = LLMBasedRiskEstimator(policy=main, config=RiskEstimatorConfig())
    d = est.get_pooling_diagnostics()
    assert "risk_mini_policy_pool_models" in d
    assert "risk_policy_pool_hits" in d
    assert "risk_policy_pool_misses" in d


def test_persist_mini_llm_call_records_effective_model_not_main_policy():
    """Parallel mini-estimators may use pooled policies; observability must store that model."""
    main = MagicMock()
    main.model = "gpt-4o-mini"
    main.tracker = None
    est = LLMBasedRiskEstimator(policy=main, config=RiskEstimatorConfig())
    set_current_run_id("run-runtime-pooling")
    set_current_request_id("req-runtime-pooling")
    set_current_cycle(0)

    pooled_policy_env = est._build_mini_llm_call_envelope(
        system_prompt="s",
        prompt="p",
        raw_response="{}",
        action="estimate_intent",
        duration_ms=1.0,
        attempts=1,
        llm_model="gpt-4o",
        parse_contract={"ok": True},
        token_usage_json='{"total_tokens":3}',
        message_sections={"final_user_message": "p"},
    )
    main_policy_env = est._build_mini_llm_call_envelope(
        system_prompt="s",
        prompt="p",
        raw_response="{}",
        action="estimate_signals",
        duration_ms=1.0,
        attempts=1,
    )

    assert pooled_policy_env is not None
    assert main_policy_env is not None
    assert pooled_policy_env.payload["model"] == "gpt-4o"
    assert main_policy_env.payload["model"] == "gpt-4o-mini"
    assert pooled_policy_env.payload["token_usage_json"] == '{"total_tokens":3}'

    summary = json.loads(pooled_policy_env.payload["parsed_summary_json"])
    assert summary["mini_estimator"] == "estimate_intent"
    assert summary["parse_contract"] == {"ok": True}
    assert summary["message_sections"] == {"final_user_message": "p"}
    assert pooled_policy_env.payload["sequence_in_cycle"] == -9
