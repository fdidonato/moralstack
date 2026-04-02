# Risk Estimator

> **Module**: `moralstack/models/risk/` (subpackage)

The Risk Estimator performs semantic classification of the ethical risk associated with user requests. The module is
organized as a subpackage: `schema.py`, `categories.py`, `estimator.py`, `calibration.py`, `parse_result.py`, `utils.py`;
the public API is exposed via `__init__.py`.

**For testers and stakeholders**: The output includes `risk_category`, `score` and semantic signals (`intent_clarity`,
`misuse_plausibility`, `actionability_risk`, `operational_risk`) used by the Orchestrator for routing (Fast Path vs
Deliberative) and for the **SAFE_COMPLETE** vs **NORMAL_COMPLETE** decision. Tests can verify that benign requests
receive `BENIGN` and that harmful requests receive `CLEARLY_HARMFUL` or `POTENTIALLY_HARMFUL`.

---

## Overview

The Risk Estimator is a "semantic judge" that:

- Analyzes the **meaning** and **intent** of requests
- **Does not** rely on keyword matching or superficial patterns
- Determines the processing path (Fast Path vs Deliberative Path)
- Provides semantic signals to guide deliberation

### Philosophy

```
"How does a nuclear explosion work?" → BENIGN (educational)
"How to build a nuclear bomb?"      → CLEARLY_HARMFUL (harmful intent)
```

Context and semantic nuance are fundamental.

---

## Risk Categories

| Category              | Score     | Description                     | Action                  |
|-----------------------|-----------|---------------------------------|-------------------------|
| `BENIGN`              | 0.0 - 0.3 | Standard informational requests | Fast Path               |
| `MORALLY_NUANCED`     | 0.3 - 0.5 | Interpersonal ethical dilemmas  | Deliberative (default 2 cycles) |
| `SENSITIVE`           | 0.5 - 0.7 | Delicate but legitimate topics  | Deliberative (default 2 cycles) |
| `POTENTIALLY_HARMFUL` | 0.7 - 0.9 | Requires careful scrutiny       | Full deliberative       |
| `CLEARLY_HARMFUL`     | 0.9 - 1.0 | Evident violation               | Immediate refusal       |

### RiskCategory Enum

```python
from moralstack.models.risk import RiskCategory

class RiskCategory(Enum):
    BENIGN = "benign"
    MORALLY_NUANCED = "morally_nuanced"
    SENSITIVE = "sensitive"
    POTENTIALLY_HARMFUL = "potentially_harmful"
    CLEARLY_HARMFUL = "clearly_harmful"
```

---

## Output Structure

### RiskParseResult (parsing)

`parse_risk_response(text)` returns an immutable `RiskParseResult` (from `parse_result.py`) with all parsed and
calibrated fields (score, confidence, category, signals, rationale, risk_policy_action, domain_sensitivity,
operational_risk, intent_clarity, misuse_plausibility, actionability_risk, harm_type, self_harm_language,
requested_instructions, intent_to_harm, request_type, intent_operational, detected_language). The estimator uses this
result to build the public `RiskEstimation` and applies crisis post-processing when needed.

### RiskEstimation

```python
@dataclass
class RiskEstimation:
    score: float                     # [0, 1] - higher = riskier
    confidence: float                # [0, 1] - estimate confidence
    risk_category: RiskCategory     # Assigned category
    semantic_signals: list[str]      # Identified semantic signals
    rationale: str                   # Judge explanation
    raw_response: str                # Raw LLM response (debug)
```

### Example Output

```python
RiskEstimation(
    score=0.35,
    confidence=0.85,
    risk_category=RiskCategory.MORALLY_NUANCED,
    semantic_signals=["ethical_dilemma", "interpersonal_conflict"],
    rationale="The request presents an ethical dilemma that requires moral deliberation"
)
```

---

## Methodology

### Classification Process

1. **LLM Semantic Analysis**: The prompt is analyzed for meaning and intent
2. **Signal Extraction**: Identification of semantic risk indicators
3. **Classification**: Category and score assignment
4. **Constitution Consultation**: Identification of relevant principles

### Internal flow (semantic analysis)

Semantic analysis in `LLMBasedRiskEstimator._semantic_analysis` is split into:

1. **Prompt building** — `_build_generation_config()`, `_build_full_prompt(prompt)` (base template + optional principles from constitution store). `GenerationConfig` requests OpenAI **`response_format={"type":"json_object"}`** for monolithic and parallel mini-estimator calls (structured output); tolerant recovery via `extract_json` remains for parse classification and edge cases. In parallel mini-estimator mode, `OpenAIPolicy` objects for per-mini model overrides are **pooled per model id** on the `LLMBasedRiskEstimator` instance (optional diagnostics: `get_pooling_diagnostics()`).
2. **LLM call with retry** — `_call_llm_with_retry(full_prompt, gen_config)` runs the policy LLM, persists the call via `_persist_risk_llm_call` (when persistence is available), and returns `(raw_response, RiskParseResult)` on success; `parsed_summary_json` includes a **`parse_contract`** object (`response_contract`, `strict_json_requested`, `parse_status`, `fallback_used`, `retry_count`, etc.). On parse/generation failure it retries up to `max_retries` (unchanged policy), then raises `RiskEstimationError`.
3. **Parsing** — `parse_risk_dict` after `parse_dict_with_contract` (direct `json.loads` vs `extract_json` fallback) produces a `RiskParseResult` (same governance semantics as the former `parse_risk_response` pipeline).
4. **Crisis post-processing** — `_post_process_crisis(parsed)` applies the crisis/help-seeking clamp (self-harm language without requested instructions or intent to harm → score clamp, category/signals overrides).
5. **Mapping** — `_to_risk_estimation(...)` builds the public `RiskEstimation` from the parsed result and post-processed values (including `intent_type` from request type).

Persistence of LLM calls is best-effort: if `moralstack.persistence.sink.persist_llm_call` is unavailable (e.g. import error), a debug log is emitted and execution continues.

### Prompt Template

The Risk Estimator uses a structured prompt that asks the LLM to:

- Analyze the intent of the request
- Identify potential ethical risks
- Evaluate context and nuance
- **Identify request language** (`detected_language`, ISO 639-1, required) — used for response language matching (
  refusal, safe_complete)
- Produce a structured JSON judgment

### Configuration

`RiskEstimatorConfig` (in `moralstack/models/risk/schema.py`) controls LLM settings. When no explicit config is passed,
the estimator loads values from environment variables (see [Environment Variables](#environment-variables)).

- **max_tokens**: 512 (configurable) — response budget for the semantic judge; 512 avoids JSON truncation with Q1–Q12,
  rationale, and all fields. The API parameter name (`max_tokens` vs `max_completion_tokens`) is chosen automatically
  based on the model; see [OpenAI Params](./openai_params.md).
- **max_retries**: 2 — parse retries before fallback
- **temperature**: 0.1 — low for consistent decisions

---

## Environment Variables

All risk estimator tuning can be overridden via `.env`. Variables are read at estimator construction; empty or missing
values use the defaults below. See `.env.template` for the full list. **In application runs (CLI and benchmark), risk
configuration is the single source of configuration — no CLI or code path overrides these variables.**

### Model (semantic judge LLM)

#### MORALSTACK_RISK_MODEL

- **Default**: *(none — uses the same model as the rest of the stack, e.g. `OPENAI_MODEL` or `gpt-4o`)*
- **Tipo**: string (OpenAI model id)
- **Significato**: OpenAI model used **only** for the risk estimator (semantic judge). When set and non-empty, the CLI
  and benchmark create a dedicated `OpenAIPolicy` with this model for the risk estimator; the rest of the stack (critic,
  simulator, generation) keeps using `OPENAI_MODEL`.
- **Effetto della modifica**:
    - **Set to a model id** (e.g. `gpt-4o`, `gpt-4o-mini`): The semantic judge uses that model. Lets you use a
      smaller/cheaper model for risk classification and a larger one for generation, or vice versa.
    - **Unset or empty**: The risk estimator uses the same policy (and model) as the rest of the pipeline — current
      behaviour.
- **Esempio**: `MORALSTACK_RISK_MODEL=gpt-4o-mini` uses a smaller model for risk classification to reduce cost/latency
  while keeping `OPENAI_MODEL=gpt-4o` for generation.

### Thresholds and path routing

#### MORALSTACK_RISK_LOW_THRESHOLD

- **Default**: `0.3`
- **Tipo**: float (0–1)
- **Significato**: Score below this is considered "low" risk. Used by `get_risk_level()` and `should_deliberate()`.
  Requests with score &lt; low_threshold take the Fast Path (minimal deliberation, fewer cycles).
- **Effetto della modifica**:
    - **Valore più alto**: More requests are classified as medium/high → more deliberative processing, fewer Fast Paths.
      More conservative.
    - **Valore più basso**: More requests are classified as low → more Fast Paths, less deliberation. More permissive.
- **Esempio**: Setting `0.4` sends more borderline requests into deliberation.

#### MORALSTACK_RISK_MEDIUM_THRESHOLD

- **Default**: `0.7`
- **Tipo**: float (0–1)
- **Significato**: Boundary between "medium" and "high" risk. Scores between low_threshold and medium_threshold are
  medium; scores ≥ medium_threshold are high and get full deliberation.
- **Effetto della modifica**:
    - **Valore più alto**: More requests stay "medium" (fewer "high") → slightly less intensive deliberation at the top.
    - **Valore più basso**: More requests become "high" → more requests get full deliberation. More conservative.

### LLM and retry behaviour

#### MORALSTACK_RISK_MAX_RETRIES

- **Default**: `2`
- **Tipo**: int (≥ 1)
- **Significato**: Number of parse attempts for the semantic judge JSON response before using the conservative
  fallback (e.g. `RiskEstimation.from_error`).
- **Effetto della modifica**:
    - **Valore più alto**: More resilient to transient parse failures; slightly higher latency on repeated attempts.
    - **Valore più basso**: Fallback to conservative estimate sooner; faster failure path, less resilient.

#### MORALSTACK_RISK_MAX_TOKENS

- **Default**: `512`
- **Tipo**: int (≥ 1)
- **Significato**: Maximum tokens for the semantic judge LLM response. 512 is tuned to avoid truncating the JSON (
  Q1–Q12, rationale, etc.).
- **Effetto della modifica**:
    - **Valore più alto**: Longer responses possible; higher token cost and risk of truncation if the model outputs more
      than expected.
    - **Valore più basso**: Shorter responses; risk of truncated JSON and parse failures.

#### MORALSTACK_RISK_TEMPERATURE

- **Default**: `0.1`
- **Tipo**: float (0–2)
- **Significato**: LLM temperature for the semantic judge. Low values favour consistent, deterministic classifications.
- **Effetto della modifica**:
    - **Valore più alto**: More variability in risk scores and categories across similar requests; less predictable.
    - **Valore più basso**: More stable, repeatable judgements; may be less sensitive to nuance.

### Fallback when LLM is unavailable

#### MORALSTACK_RISK_FALLBACK_SCORE

- **Default**: `0.5`
- **Tipo**: float (0–1)
- **Significato**: Risk score used when no LLM is available (e.g. policy=None or all retries failed). Drives path and
  deliberation requirements.
- **Effetto della modifica**:
    - **Valore più alto**: Fallback is treated as higher risk → more deliberation or refusal.
    - **Valore più basso**: Fallback is treated as lower risk → less deliberation; more permissive when LLM fails.

#### MORALSTACK_RISK_FALLBACK_CONFIDENCE

- **Default**: `0.3`
- **Tipo**: float (0–1)
- **Significato**: Confidence assigned to the fallback estimation when LLM is unavailable. Low value signals
  uncertainty.
- **Effetto della modifica**: Affects downstream logic that uses confidence; lowering it further stresses uncertainty;
  raising it is not recommended for fallback.

#### MORALSTACK_RISK_REQUIRE_DELIBERATION_ON_FALLBACK

- **Default**: `true`
- **Tipo**: bool (`true`/`false`, `1`/`0`, `yes`/`no`)
- **Significato**: When true, the system always requires deliberation when the estimator uses the fallback (no LLM).
  Ensures safe behaviour on failure.
- **Effetto della modifica**:
    - **true**: Safe default; every fallback triggers deliberation.
    - **false**: Fallback may skip deliberation; only change for special deployments where safety is handled elsewhere.

### LLM generation and constitution context

#### MORALSTACK_RISK_TOP_P

- **Default**: `0.9`
- **Tipo**: float (0–1)
- **Significato**: Nucleus sampling (top_p) for the semantic judge LLM call. Controls diversity of token sampling.
- **Effetto della modifica**:
    - **Valore più alto**: Slightly more variety in outputs.
    - **Valore più basso**: More deterministic, focused outputs.

#### MORALSTACK_RISK_TOP_K

- **Default**: `10`
- **Tipo**: int (≥ 1)
- **Significato**: Number of relevant constitution principles injected into the risk prompt. More principles give more
  context but increase prompt size and cost.
- **Effetto della modifica**:
    - **Valore più alto**: Richer principle context; longer prompts, higher token usage.
    - **Valore più basso**: Shorter prompts; less constitutional context in the judge.

#### MORALSTACK_RISK_RULE_PREVIEW_LEN

- **Default**: `200`
- **Tipo**: int (≥ 1)
- **Significato**: Maximum characters of each principle rule shown in the prompt (longer rules are truncated
  with "...").
- **Effetto della modifica**:
    - **Valore più alto**: More rule text in the prompt; better context, larger prompts.
    - **Valore più basso**: Shorter rule previews; smaller prompts, possible loss of nuance.

### Crisis / help-seeking post-processing

#### MORALSTACK_RISK_CRISIS_CLAMP_LOW

- **Default**: `0.35`
- **Tipo**: float (0–1)
- **Significato**: Lower bound of the score clamp applied to crisis_support requests (self-harm language without
  requested instructions or intent to harm). Keeps score in a range that triggers deliberate, supportive handling
  without over-penalising.
- **Effetto della modifica**:
    - **Valore più alto**: Crisis requests get a higher minimum score → more consistently treated as
      sensitive/deliberative.
    - **Valore più basso**: Crisis requests can have a lower score → may approach Fast Path if combined with other
      logic.

#### MORALSTACK_RISK_CRISIS_CLAMP_HIGH

- **Default**: `0.65`
- **Tipo**: float (0–1)
- **Significato**: Upper bound of the score clamp for crisis_support requests. Prevents such requests from being
  classified as clearly harmful when they are help-seeking.
- **Effetto della modifica**:
    - **Valore più alto**: Crisis requests can reach higher scores → closer to potentially harmful band.
    - **Valore più basso**: Crisis requests capped lower → stay in sensitive/deliberate band; more protective.

### Score-to-category mapping (categorize_from_score)

#### MORALSTACK_RISK_CATEGORIZE_BENIGN_THRESHOLD

- **Default**: `0.2`
- **Tipo**: float (0–1)
- **Significato**: Score below this is mapped to BENIGN in `categorize_from_score()`. Together with low_threshold,
  defines the benign band.
- **Effetto della modifica**:
    - **Valore più alto**: Fewer requests classified as BENIGN; more become MORALLY_NUANCED/SENSITIVE. More
      conservative.
    - **Valore più basso**: More requests classified as BENIGN. More permissive.

#### MORALSTACK_RISK_CATEGORIZE_SENSITIVE_THRESHOLD

- **Default**: `0.5`
- **Tipo**: float (0–1)
- **Significato**: In `categorize_from_score()`, score between low_threshold and this (and below medium_threshold)
  influences MORALLY_NUANCED vs SENSITIVE. Boundary between nuanced ethical dilemmas and sensitive topics.
- **Effetto della modifica**:
    - **Valore più alto**: More scores in the middle range map to SENSITIVE; fewer to MORALLY_NUANCED.
    - **Valore più basso**: More scores map to MORALLY_NUANCED; fewer to SENSITIVE.

#### MORALSTACK_RISK_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD

- **Default**: `0.85`
- **Tipo**: float (0–1)
- **Significato**: Score ≥ this is mapped to CLEARLY_HARMFUL in `categorize_from_score()`; below it (and above
  medium_threshold) maps to POTENTIALLY_HARMFUL.
- **Effetto della modifica**:
    - **Valore più alto**: Fewer requests classified as CLEARLY_HARMFUL; more stay POTENTIALLY_HARMFUL. More permissive
      at the top.
    - **Valore più basso**: More requests classified as CLEARLY_HARMFUL; stricter refusal behaviour.

---

## Usage

### Initialization

```python
from moralstack.models.risk import LLMBasedRiskEstimator
from moralstack.constitution.store import ConstitutionStore

risk_estimator = LLMBasedRiskEstimator(
    policy=policy_llm,
    constitution_store=constitution_store,
)
```

### Risk Estimation

```python
estimation = risk_estimator.estimate("How can I manage a family conflict?")

print(f"Score: {estimation.score}")
print(f"Category: {estimation.risk_category.value}")
print(f"Signals: {estimation.semantic_signals}")
print(f"Rationale: {estimation.rationale}")
```

---

## Factory Methods

### RiskEstimation.benign()

```python
# For clearly benign requests
estimation = RiskEstimation.benign(
    confidence=0.95,
    rationale="No harmful intent detected"
)
```

### RiskEstimation.clearly_harmful()

```python
# For clearly harmful requests
estimation = RiskEstimation.clearly_harmful(
    semantic_signals=["violence", "harm_intent"],
    rationale="Request for harmful instructions",
    confidence=0.95
)
```

### RiskEstimation.from_error()

```python
# Fallback on error (assumes moderate risk)
estimation = RiskEstimation.from_error("LLM parsing failed")
# Returns score=0.5, requires deliberation for safety
```

---

## Orchestrator Integration

The Risk Estimator determines the flow in the Orchestrator:

```python
if estimation.score < risk_thresholds.low:  # < 0.3
    path = "fast"
    max_cycles = 1
elif estimation.risk_category == RiskCategory.MORALLY_NUANCED:
    path = "deliberative"
    max_cycles = 2  # Ethical dilemmas require deliberation
else:
    path = "deliberative"
    max_cycles = config.max_deliberation_cycles
```

---

## Common Semantic Signals

| Signal               | Description                       | Impact                      |
|----------------------|-----------------------------------|-----------------------------|
| `educational_intent` | Informational/educational request | Reduces risk                |
| `ethical_dilemma`    | Moral dilemma present             | MORALLY_NUANCED             |
| `vulnerable_context` | Potential vulnerable user         | Increases scrutiny          |
| `harm_potential`     | Possibility of harm               | Increases risk              |
| `illegal_activity`   | Reference to illegal activity     | CLEARLY_HARMFUL             |
| `medical_context`    | Medical/health context            | Activates medical overlay   |
| `financial_advice`   | Financial advice request          | Activates financial overlay |

---

## Error Handling

The Risk Estimator is designed to be resilient:

```python
try:
    estimation = risk_estimator.estimate(prompt)
except Exception as e:
    # Fallback: assume moderate risk, requires deliberation
    estimation = RiskEstimation.from_error(str(e))
```

---

## Risk Estimation Flow

High-level flow of risk estimation:

1. **Input prompt** → `RiskEstimator` (implementation: `LLMBasedRiskEstimator`)
2. **Raw score calculation** — LLM semantic analysis and signal extraction
3. **Category classification** — `RiskCategory` assignment based on thresholds
4. **Calibration** → score mapping to risk band (schema in `calibration.py`)
5. **Output** → `RiskEstimation` with score, category, signals and rationale

---

## See Also

- [Orchestrator](./orchestrator.md) - Flow coordination
- [Constitutional Critic](./critic.md) - Principle validation
- [Constitution Store](./constitution_store.md) - Ethical principle management
