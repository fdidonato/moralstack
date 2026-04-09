"""
LLMConsequenceSimulator - Simulatore di conseguenze per MoralStack.

Genera scenari futuri plausibili per valutare l'impatto delle risposte.
Usa il Policy LLM con prompt specializzato e parsing JSON rigoroso con retry.

FIX PERFORMANCE: Aggiunto caching per evitare ricalcolo su input identici.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, cast

from moralstack.core.types import PolicyLLMProtocol
from moralstack.models.base import GenerationConfig
from moralstack.models.delib_context import DelibContext
from moralstack.prompts.retry import RETRY_SIMULATOR
from moralstack.prompts.simulator_prompt import (
    HARM_TYPE_MAPPING_RULE,
    SIMULATOR_ENUMS,
    SIMULATOR_SCHEMA_SKELETON,
)
from moralstack.utils.cache import get_global_cache
from moralstack.utils.json_utils import JSONParseError
from moralstack.utils.structured_output import (
    SimulatorOutput,
    parse_and_validate_simulator_output,
)

# =============================================================================
# Data Models
# =============================================================================


class ScenarioType(Enum):
    """Tipi di scenari di conseguenza."""

    IMMEDIATE_HARM = "immediate_harm"
    DOWNSTREAM_MISUSE = "downstream_misuse"
    SOCIAL_IMPACT = "social_impact"
    LEGAL_CONSEQUENCE = "legal_consequence"
    POSITIVE_OUTCOME = "positive_outcome"


@dataclass
class Consequence:
    """
    Rappresenta una singola conseguenza simulata.

    Attributes:
        text: Descrizione narrativa della conseguenza (max 160 chars)
        likelihood: Probabilità/plausibilità [0, 1]
        scenario_id: Identificatore univoco
        scenario_type: Categoria della conseguenza
        outcome_valence: Valenza [-1, 1] (negativa/positiva)
        affected_stakeholders: Parti coinvolte
        harm_type: Tipo di danno semantico
        harm_severity: Gravità danno [0, 1]
        harm_scope: Ambito (individual|group|societal|systemic)
        reversibility: Reversibilità [0, 1]
    """

    text: str
    likelihood: float
    scenario_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    scenario_type: ScenarioType = ScenarioType.SOCIAL_IMPACT
    outcome_valence: float = 0.0
    affected_stakeholders: list[str] = field(default_factory=list)
    harm_type: str = "none"
    harm_severity: float = 0.0
    harm_scope: str = "individual"
    reversibility: float = 0.0

    def __post_init__(self) -> None:
        """Normalizza i valori in range validi."""
        self.likelihood = max(0.0, min(1.0, self.likelihood))
        self.outcome_valence = max(-1.0, min(1.0, self.outcome_valence))


@dataclass
class SimulationResult:
    """
    Risultato completo della simulazione di conseguenze.

    Attributes:
        consequences: Lista di conseguenze simulate
        worst_case_valence: Valenza dello scenario peggiore
        best_case_valence: Valenza dello scenario migliore
        expected_valence: Valenza attesa (media pesata per likelihood)
        semantic_expected_harm: max(likelihood * harm_severity) per harm_type != none
        dominant_harm_types: top 2 harm_type per risk_i
        worst_harm: record argmax(risk_i): {harm_type, harm_scope, risk}
        raw_response: Risposta grezza dell'LLM (per debug)
        parse_attempts: Numero di tentativi di parsing JSON
    """

    consequences: list[Consequence] = field(default_factory=list)
    worst_case_valence: float = 0.0
    best_case_valence: float = 0.0
    expected_valence: float = 0.0
    semantic_expected_harm: float = 0.0
    dominant_harm_types: list[str] = field(default_factory=list)
    worst_harm: dict | None = None
    raw_response: str = ""
    parse_attempts: int = 1
    prompt: str = ""
    system_prompt: str = ""

    @classmethod
    def empty(cls) -> SimulationResult:
        """Crea un risultato vuoto (nessuna conseguenza simulata)."""
        return cls(
            consequences=[],
            worst_case_valence=0.0,
            best_case_valence=0.0,
            expected_valence=0.0,
        )

    @classmethod
    def from_error(cls, error_msg: str) -> SimulationResult:
        """Crea un risultato di fallback in caso di errore critico."""
        return cls(
            consequences=[],
            worst_case_valence=-1.0,  # Assume worst case su errore
            best_case_valence=-1.0,
            expected_valence=-1.0,
            raw_response=error_msg,
        )


# =============================================================================
# Protocols
# =============================================================================

SIMULATOR_SYSTEM_PROMPT = """You are a consequence simulator.
Return ONLY valid JSON matching the required schema.
No explanations. No extra text."""

SCENARIO_SEEDS = [
    "Consider what happens if this advice is followed literally by someone inexperienced",
    "Consider how this information could be misused by a malicious actor",
    "Consider the social/reputational impact if this response became public",
    "Consider the best possible outcome from this response",
    "Consider unintended consequences for vulnerable populations",
]

SEEDED_PROMPT_TEMPLATE = (
    """Generate a consequence based on this perspective:

        PERSPECTIVE: {seed}

        REQUEST: {request}
        RESPONSE: {response}

        Requirements: text <= 15 words. Use only allowed values. harm_type: match REQUEST topic ("""
    + HARM_TYPE_MAPPING_RULE
    + """). JSON only.

"""
    + SIMULATOR_ENUMS
    + """

"""
    + SIMULATOR_SCHEMA_SKELETON
    + """

Output ONLY valid JSON:"""
)

RETRY_PROMPT = RETRY_SIMULATOR


# =============================================================================
# Conversione da output strutturato a Consequence (unica fonte: parser strutturato)
# =============================================================================

_SCENARIO_TYPE_MAP = {
    "immediate_harm": ScenarioType.IMMEDIATE_HARM,
    "downstream_misuse": ScenarioType.DOWNSTREAM_MISUSE,
    "social_impact": ScenarioType.SOCIAL_IMPACT,
    "legal_consequence": ScenarioType.LEGAL_CONSEQUENCE,
    "positive_outcome": ScenarioType.POSITIVE_OUTCOME,
}


def _simulator_output_to_consequences(out: SimulatorOutput) -> list[Consequence]:
    """Converte SimulatorOutput (parser strutturato) in list[Consequence]."""
    consequences: list[Consequence] = []
    for c in out.consequences:
        text = c.text
        if len(text) > 160:
            text = text[:160]
        st = _SCENARIO_TYPE_MAP.get(
            c.scenario_type if isinstance(c.scenario_type, str) else "social_impact",
            ScenarioType.SOCIAL_IMPACT,
        )
        harm_type = str(c.harm_type).strip().lower() if c.harm_type else "none"
        harm_scope = str(c.harm_scope).strip().lower() if c.harm_scope else "individual"
        consequences.append(
            Consequence(
                text=text,
                likelihood=max(0.0, min(1.0, c.likelihood)),
                scenario_type=st,
                outcome_valence=max(-1.0, min(1.0, c.outcome_valence)),
                affected_stakeholders=(list(c.affected_stakeholders)[:3] if c.affected_stakeholders else []),
                harm_type=harm_type,
                harm_severity=max(0.0, min(1.0, c.harm_severity)),
                harm_scope=harm_scope,
                reversibility=max(0.0, min(1.0, c.reversibility)),
            )
        )
    return consequences


def parse_simulator_response(text: str) -> list[Consequence]:
    """
    Parsa la risposta del simulator con parser strutturato obbligatorio.

    Unica fonte: parse_and_validate_simulator_output. Se il parsing fallisce
    si solleva JSONParseError o ValidationError. Nessun fallback.
    """
    out = parse_and_validate_simulator_output(text)
    return _simulator_output_to_consequences(out)


# =============================================================================
# LLM Consequence Simulator
# =============================================================================


@dataclass
class SimulatorConfig:
    """Configurazione per il Consequence Simulator."""

    max_retries: int = 3
    max_tokens: int = 384  # Ultra-lean: narrativa compatta, schema minimale
    temperature: float = 0.8  # Più alta per diversità scenari
    top_p: float = 0.95  # Nucleus sampling; configurable via MORALSTACK_SIMULATOR_TOP_P
    default_num_scenarios: int = 3
    use_seeded_generation: bool = False  # Se True, usa seed separati

    # FIX PERFORMANCE: Caching per evitare ricalcolo su input identici
    enable_caching: bool = True  # Abilita caching risultati


class LLMConsequenceSimulator:
    """
    Simulatore di conseguenze basato su LLM.

    Genera scenari futuri plausibili per valutare l'impatto delle risposte.
    Usa il Policy LLM con prompt specializzato e parsing JSON rigoroso
    con retry automatico.

    Attributes:
        policy: Il Policy LLM per generazione
        config: Configurazione del simulator

    Usage:
        simulator = LLMConsequenceSimulator(policy)
        result = simulator.simulate(request, response, num_scenarios=3)

        for consequence in result.consequences:
            print(f"{consequence.text} (likelihood: {consequence.likelihood})")
    """

    def __init__(
        self,
        policy: PolicyLLMProtocol,
        config: SimulatorConfig | None = None,
    ) -> None:
        """
        Inizializza il Consequence Simulator.

        Args:
            policy: Policy LLM per generazione scenari
            config: Configurazione (opzionale)
        """
        self.policy = policy
        if config is not None:
            self.config = config
        else:
            from moralstack.runtime.modules.simulator_config_loader import (
                load_simulator_config_from_env,
            )

            self.config = load_simulator_config_from_env()

        self._generation_config = GenerationConfig(
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            stop_sequences=[],
            response_format={"type": "json_object"},
        )

    def simulate(
        self,
        request: str,
        response: str,
        num_scenarios: int | None = None,
        delib_context: Any = None,
        context_mode: str = "full",
    ) -> SimulationResult:
        """
        Genera k conseguenze future plausibili.

        FIX PERFORMANCE: Usa caching per evitare ricalcolo su input identici.
        Se la coppia (request, response) è già stata simulata, ritorna il risultato cached.

        Strategia di sampling:
        - 1 scenario worst-case
        - 1 scenario best-case
        - (k-2) scenari probabili

        Args:
            request: Richiesta originale dell'utente
            response: Risposta da valutare
            num_scenarios: Numero di scenari da generare (default: 3)

        Returns:
            SimulationResult con conseguenze e metriche aggregate
        """
        # FIX PERFORMANCE: Check cache prima di simulare
        if self.config.enable_caching:
            cache = get_global_cache()
            cached_result = cache.get_simulation_result(request, response)
            if cached_result is not None:
                return cached_result

        k = num_scenarios or self.config.default_num_scenarios
        k = max(1, min(k, 10))  # Limita range

        if self.config.use_seeded_generation:
            result = self._simulate_with_seeds(request, response, k, delib_context, context_mode)
        else:
            result = self._simulate_batch(request, response, k, delib_context, context_mode)

        # FIX PERFORMANCE: Salva in cache per usi futuri
        if self.config.enable_caching:
            cache = get_global_cache()
            cache.set_simulation_result(request, response, result)

        return result

    def _simulate_batch(
        self,
        request: str,
        response: str,
        num_scenarios: int,
        delib_context: Any = None,
        context_mode: str = "full",
    ) -> SimulationResult:
        """
        Genera tutti gli scenari in un'unica chiamata LLM.

        Più efficiente ma meno controllo sulla diversità.
        """
        ctx = delib_context or DelibContext(user_prompt=request, draft_text_full=response)
        from moralstack.prompts.simulator_prompt import build_simulator_prompt

        prompt = build_simulator_prompt(ctx, num_scenarios=num_scenarios, mode=cast(Literal["full", "thin"], context_mode))

        raw_response = ""
        parse_attempts = 0
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries):
            parse_attempts = attempt + 1

            try:
                if attempt == 0:
                    result = self.policy.generate(
                        prompt=prompt,
                        system=SIMULATOR_SYSTEM_PROMPT,
                        config=self._generation_config,
                    )
                else:
                    retry_prompt = f"{prompt}\n\n{RETRY_PROMPT}"
                    result = self.policy.generate(
                        prompt=retry_prompt,
                        system=SIMULATOR_SYSTEM_PROMPT,
                        config=self._generation_config,
                    )

                raw_response = result.text

                # Parse JSON (parser strutturato obbligatorio; nessun fallback)
                consequences = parse_simulator_response(raw_response)
                effective_prompt = prompt if attempt == 0 else f"{prompt}\n\n{RETRY_PROMPT}"
                return self._build_result(
                    consequences=consequences,
                    raw_response=raw_response,
                    parse_attempts=parse_attempts,
                    prompt=effective_prompt,
                    system_prompt=SIMULATOR_SYSTEM_PROMPT,
                )
            except (JSONParseError, Exception) as e:
                last_error = e
                if attempt > 0:
                    try:
                        from moralstack.persistence.write_queue import async_persist_llm_call

                        async_persist_llm_call(
                            phase="simulator_retry",
                            module="simulator",
                            action=f"retry_attempt_{parse_attempts}",
                            prompt=f"Retry reason: {e!s}",
                            raw_response=raw_response or "",
                            duration_ms=0.0,
                        )
                    except Exception:
                        pass
                continue
        # Tutti i retry falliti: errore esplicito, nessun fallback decisionale
        from moralstack.utils.structured_output import ValidationError as StructuredValidationError

        if last_error is not None and isinstance(last_error, (JSONParseError, StructuredValidationError)):
            raise last_error
        raise RuntimeError(
            f"Simulator structured parsing failed after {parse_attempts} attempts: {last_error}"
        ) from last_error

    def _simulate_with_seeds(
        self,
        request: str,
        response: str,
        num_scenarios: int,
        delib_context: Any = None,
        context_mode: str = "full",
    ) -> SimulationResult:
        """
        Genera scenari usando seed separati per maggiore diversità.

        Ogni seed guida una prospettiva diversa (worst-case, best-case, ecc).
        Più costoso ma produce scenari più vari.
        """
        consequences: list[Consequence] = []
        raw_responses: list[str] = []
        prompts_used: list[str] = []
        total_attempts = 0

        # Seleziona seed appropriati
        selected_seeds = self._select_seeds(num_scenarios)

        for seed in selected_seeds:
            prompt = SEEDED_PROMPT_TEMPLATE.format(
                seed=seed,
                request=request,
                response=response,
            )

            for attempt in range(self.config.max_retries):
                total_attempts += 1

                try:
                    if attempt == 0:
                        result = self.policy.generate(
                            prompt=prompt,
                            system=SIMULATOR_SYSTEM_PROMPT,
                            config=self._generation_config,
                        )
                    else:
                        retry_prompt = f"{prompt}\n\n{RETRY_PROMPT}"
                        result = self.policy.generate(
                            prompt=retry_prompt,
                            system=SIMULATOR_SYSTEM_PROMPT,
                            config=self._generation_config,
                        )

                    raw_responses.append(result.text)
                    prompts_used.append(prompt if attempt == 0 else f"{prompt}\n\n{RETRY_PROMPT}")
                    parsed = parse_simulator_response(result.text)
                    consequences.extend(parsed)
                    break
                except (JSONParseError, Exception):
                    continue

        if not consequences:
            raise RuntimeError(f"Simulator produced no valid structured output after {total_attempts} attempts")
        return self._build_result(
            consequences=consequences,
            raw_response="\n---\n".join(raw_responses),
            parse_attempts=total_attempts,
            prompt="\n---\n".join(prompts_used) if prompts_used else "",
            system_prompt=SIMULATOR_SYSTEM_PROMPT,
        )

    def _select_seeds(self, num_scenarios: int) -> list[str]:
        """
        Seleziona seed appropriati per il numero di scenari.

        Garantisce diversità includendo sempre worst-case e best-case
        se possibile.
        """
        if num_scenarios >= len(SCENARIO_SEEDS):
            return SCENARIO_SEEDS[:num_scenarios]

        # Priorità: worst-case (index 0,1), best-case (3), altri
        priority_order = [0, 1, 3, 2, 4]  # Indices in SCENARIO_SEEDS

        selected: list[str] = []
        for idx in priority_order:
            if len(selected) >= num_scenarios:
                break
            if idx < len(SCENARIO_SEEDS):
                selected.append(SCENARIO_SEEDS[idx])

        return selected

    def _build_result(
        self,
        consequences: list[Consequence],
        raw_response: str,
        parse_attempts: int,
        prompt: str = "",
        system_prompt: str = "",
    ) -> SimulationResult:
        """
        Costruisce SimulationResult con metriche aggregate.
        expected_valence invariato; semantic_expected_harm da risk_i = likelihood * harm_severity.
        """
        if not consequences:
            return SimulationResult.empty()

        # Trova worst e best case
        valences = [c.outcome_valence for c in consequences]
        worst_case = min(valences)
        best_case = max(valences)

        # Calcola expected value (weighted by likelihood) - invariato
        total_weight = sum(c.likelihood for c in consequences)
        if total_weight > 0:
            expected = sum(c.outcome_valence * c.likelihood for c in consequences) / total_weight
        else:
            expected = 0.0

        # Semantic aggregation: risk_i = likelihood * harm_severity, ignora harm_type == "none"
        risk_records: list[tuple[float, str, str]] = []
        for c in consequences:
            if (getattr(c, "harm_type", "none") or "none").strip().lower() == "none":
                continue
            risk_i = c.likelihood * getattr(c, "harm_severity", 0.0)
            risk_records.append((risk_i, getattr(c, "harm_type", "none"), getattr(c, "harm_scope", "individual")))

        semantic_expected_harm = max((r[0] for r in risk_records), default=0.0)
        dominant_harm_types: list[str] = []
        worst_harm: dict | None = None

        if risk_records:
            sorted_by_risk = sorted(risk_records, key=lambda x: -x[0])
            dominant_harm_types = list(dict.fromkeys(r[1] for r in sorted_by_risk[:4]))[:2]
            best = sorted_by_risk[0]
            worst_harm = {"harm_type": best[1], "harm_scope": best[2], "risk": best[0]}

        return SimulationResult(
            consequences=consequences,
            worst_case_valence=worst_case,
            best_case_valence=best_case,
            expected_valence=expected,
            semantic_expected_harm=semantic_expected_harm,
            dominant_harm_types=dominant_harm_types,
            worst_harm=worst_harm,
            raw_response=raw_response,
            parse_attempts=parse_attempts,
            prompt=prompt,
            system_prompt=system_prompt,
        )


# =============================================================================
# Factory Functions
# =============================================================================


def create_simulator(
    policy: PolicyLLMProtocol,
    config: SimulatorConfig | None = None,
) -> LLMConsequenceSimulator:
    """
    Factory function per creare un Consequence Simulator.

    When config is None the constructor loads from env (MORALSTACK_SIMULATOR_*).

    Args:
        policy: Policy LLM da usare
        config: Optional explicit config; when None, loaded from env

    Returns:
        LLMConsequenceSimulator configurato
    """
    return LLMConsequenceSimulator(
        policy=policy,
        config=config,
    )
