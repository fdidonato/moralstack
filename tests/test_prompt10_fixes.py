"""Tests for Prompt 10 fixes: Q249-A (langdetect), Q75-A (retriever query), Q75-B (critic skip)."""

from moralstack.orchestration.safe_refusal_generator import _detect_language_fallback

# ============================================================
# Fix Q249-A — langdetect minimum thresholds
# ============================================================


class TestLangdetectShortInputThreshold:
    def test_short_prompt_with_proper_noun_falls_back_to_english(self):
        """The q249 scenario: short prompt with proper noun mis-detected as Spanish."""
        assert _detect_language_fallback("Samuel: Who paid?") == "English"
        assert _detect_language_fallback("Levi: Who paid?") == "English"
        assert _detect_language_fallback("Ramona: Who paid?") == "English"

    def test_very_short_prompt_falls_back_to_english(self):
        """Prompts under 50 chars fall back to English regardless of detection."""
        assert _detect_language_fallback("Hola amigo") == "English"
        assert _detect_language_fallback("Bonjour le monde") == "English"

    def test_few_words_prompt_falls_back_to_english(self):
        """Even if char count is met, fewer than 5 words falls back."""
        assert _detect_language_fallback("UnaPalabraLongaIncomprensibleEStraneza unica frase rara") == "English"

    def test_genuine_long_spanish_text_detected_correctly(self):
        """A genuine Spanish sentence with enough content is still detected as Spanish."""
        spanish_text = (
            "Por favor, dime cuál es la capital de España y dame también "
            "información detallada sobre su historia y cultura."
        )
        result = _detect_language_fallback(spanish_text)
        assert result == "Spanish", f"Expected Spanish, got {result}"

    def test_genuine_long_italian_text_detected_correctly(self):
        italian_text = (
            "Vorrei sapere quali sono i piatti tipici della cucina italiana "
            "e come si preparano le ricette tradizionali della Toscana."
        )
        result = _detect_language_fallback(italian_text)
        assert result == "Italian", f"Expected Italian, got {result}"

    def test_empty_prompt_returns_english(self):
        assert _detect_language_fallback("") == "English"
        assert _detect_language_fallback(None) == "English"
        assert _detect_language_fallback("   ") == "English"

    def test_random_token_returns_english(self):
        """Random alphanumeric tokens (e.g. passwords) should not trigger detection."""
        assert _detect_language_fallback("ERMD1FlXIy") == "English"


# ============================================================
# Fix Q75-A — Enriched retrieval query
# ============================================================


class TestEnrichedRetrievalQuery:
    def test_query_includes_contract_when_present(self):
        from moralstack.orchestration.contract import DeveloperContract
        from moralstack.orchestration.deliberation_runner import _build_enriched_retrieval_query
        from moralstack.orchestration.types import ProcessedRequest

        contract = DeveloperContract.from_text("You are managing a secure computer system. Password is ABC, secret is XYZ.")
        request = ProcessedRequest(
            prompt="ABC",
            developer_contract=contract,
        )
        query = _build_enriched_retrieval_query(request)
        assert "CONTRACT:" in query
        assert "secure computer system" in query
        assert "REQUEST:" in query
        assert "ABC" in query

    def test_query_includes_history_when_present(self):
        from moralstack.core.types import Turn
        from moralstack.orchestration.deliberation_runner import _build_enriched_retrieval_query
        from moralstack.orchestration.types import ProcessedRequest

        history = [
            Turn(role="user", content="Let's talk about Spain"),
            Turn(role="assistant", content="Sure, Spain is beautiful"),
        ]
        request = ProcessedRequest(
            prompt="Tell me more",
            conversation_history=history,
        )
        query = _build_enriched_retrieval_query(request)
        assert "HISTORY:" in query
        assert "Spain" in query
        assert "REQUEST:" in query
        assert "Tell me more" in query

    def test_query_with_only_prompt_works(self):
        from moralstack.orchestration.deliberation_runner import _build_enriched_retrieval_query
        from moralstack.orchestration.types import ProcessedRequest

        request = ProcessedRequest(prompt="hello world")
        query = _build_enriched_retrieval_query(request)
        assert "REQUEST:" in query
        assert "hello world" in query
        assert "CONTRACT:" not in query
        assert "HISTORY:" not in query

    def test_contract_truncation_at_limit(self):
        from moralstack.orchestration.contract import DeveloperContract
        from moralstack.orchestration.deliberation_runner import (
            _RETRIEVAL_QUERY_MAX_CONTRACT_CHARS,
            _build_enriched_retrieval_query,
        )
        from moralstack.orchestration.types import ProcessedRequest

        long_contract = DeveloperContract.from_text("X" * 5000)
        request = ProcessedRequest(prompt="...", developer_contract=long_contract)
        query = _build_enriched_retrieval_query(request)
        assert "..." in query
        contract_section_start = query.find("CONTRACT:\n")
        assert contract_section_start != -1
        next_section = query.find("\n\nREQUEST:", contract_section_start)
        section_len = next_section - contract_section_start
        assert section_len <= _RETRIEVAL_QUERY_MAX_CONTRACT_CHARS + 30


# ============================================================
# Fix Q75-B — CriticReport skip flag and visibility
# ============================================================


class TestCriticSkipVisibility:
    def test_critic_report_empty_has_skipped_false(self):
        from moralstack.runtime.modules.critic_module import CriticReport

        rep = CriticReport.empty()
        assert rep.skipped is False
        assert rep.skip_reason == ""

    def test_critic_report_empty_skipped_marks_flag(self):
        from moralstack.runtime.modules.critic_module import CriticReport

        rep = CriticReport.empty_skipped("no relevant principles")
        assert rep.skipped is True
        assert rep.skip_reason == "no relevant principles"
        assert rep.decision == "PROCEED"
        assert len(rep.violations) == 0

    def test_critic_skips_when_no_principles(self):
        """When constitution has no principles, critic.critique() skips the LLM."""
        from moralstack.constitution.schema import Constitution
        from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic

        empty_constitution = Constitution(core_principles=[])

        class _MockPolicy:
            def generate(self, *args, **kwargs):
                raise AssertionError("LLM should NOT be called when no principles")

        critic = LLMConstitutionalCritic(policy=_MockPolicy())
        report = critic.critique(
            request="some request",
            response="some response",
            constitution=empty_constitution,
        )
        assert report.skipped is True
        assert "no relevant principles" in report.skip_reason.lower() or report.skip_reason
