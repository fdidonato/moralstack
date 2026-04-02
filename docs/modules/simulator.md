# Consequence Simulator

> **Module**: `moralstack/runtime/modules/simulator_module.py`

The Consequence Simulator generates plausible future scenarios to evaluate the impact of responses.

**For testers**: The output (`expected_valence`, `worst_case_valence`, `semantic_expected_harm`) contributes to the
Orchestrator's decision and revision guidance. Tests can verify that scenarios with strongly negative `expected_valence`
or high `semantic_expected_harm` generate revision recommendations (REVISE).

---

## Overview

The Consequence Simulator:

- **Generates scenarios** (best/worst/likely case)
- **Evaluates the potential impact** of responses
- **Identifies risks** not evident from the response itself
- **Provides feedback** to guide revisions
- **Semantic layer**: damage taxonomy (`harm_type`, `harm_scope`) and `semantic_expected_harm` influence deliberation

---

## Ultra-Lean Design

To reduce token usage:

- **Narrative**: each `consequence.text` ≤ 15 words, short nominal phrases
- **max_tokens**: 384 (configurable in `SimulatorConfig`)
- **Minimal schema**: prompt without verbose examples, JSON skeleton only
- **Post-parse truncation**: `text` > 160 characters is truncated

---

## Scenario Types

### ScenarioType

| Type                | Description                | Purpose                   |
|---------------------|----------------------------|---------------------------|
| `IMMEDIATE_HARM`    | Foreseeable immediate harm | Identify urgent risks     |
| `DOWNSTREAM_MISUSE` | Subsequent misuse          | Anticipate abuse          |
| `SOCIAL_IMPACT`     | Social/community impact    | Assess broad effects      |
| `LEGAL_CONSEQUENCE` | Legal consequences         | Identify regulatory risks |
| `POSITIVE_OUTCOME`  | Positive outcome           | Balance the evaluation    |

---

## Semantic Harm Layer

### Damage taxonomy (harm_type)

| harm_type                | Description                 | Constitutional principle             |
|--------------------------|-----------------------------|--------------------------------------|
| `financial_loss`         | Economic loss               | -                                    |
| `physical_harm`          | Physical harm               | CORE.NM.1                            |
| `psychological_harm`     | Psychological harm          | -                                    |
| `privacy_breach`         | Privacy violation           | CORE.PRIV.1                          |
| `legal_risk`             | Legal risk                  | CORE.NM.2                            |
| `reputational_harm`      | Reputational harm           | -                                    |
| `security_breach`        | Security breach             | -                                    |
| `discrimination`         | Discrimination              | -                                    |
| `misinformation`         | Misinformation              | CORE.DECEPTION.1                     |
| `exploitation`           | Exploitation                | CORE.NONEXPLOITATION.1               |
| `self_harm`              | Self-harm                   | MH.CRISIS.1                          |
| `emotional_manipulation` | Hidden emotional influence  | CORE.MANIPULATION.1, CORE.AUTONOMY.1 |
| `financial_deception`    | Financial deception / fraud | FIN.SCAM.1, CORE.DECEPTION.1         |
| `none`                   | No semantic harm            | -                                    |

### Scope (harm_scope)

| harm_scope   | Description        |
|--------------|--------------------|
| `individual` | Single person      |
| `group`      | Group              |
| `societal`   | Society            |
| `systemic`   | System/institution |

### semantic_expected_harm calculation

```python
risk_i = likelihood * harm_severity  # for harm_type != "none"
semantic_expected_harm = max(risk_i)
dominant_harm_types = top 2 harm_type by risk_i
worst_harm = argmax(risk_i)  # {harm_type, harm_scope, risk}
```

### Influence on deliberation

`semantic_expected_harm` and `dominant_harm_types` influence votes in `_determine_decision`:

- `semantic_expected_harm >= 0.4` → +1 vote REVISE
- `semantic_expected_harm >= 0.6` → +2 votes REVISE
- `physical_harm` or `self_harm` in `dominant_harm_types` and `semantic_expected_harm >= 0.4` → +2 votes REVISE

**The simulator can never produce REFUSE**; REFUSE comes only from hard violations, op_risk HIGH, or policy bounds.

---

## Output Structure

### Consequence

```python
@dataclass
class Consequence:
    text: str  # Narrative description (max 160 chars)
    likelihood: float  # Probability [0, 1]
    scenario_id: str  # Unique identifier
    scenario_type: ScenarioType  # Category
    outcome_valence: float  # Valence [-1, 1]
    affected_stakeholders: list[str]  # Affected parties (max 3)
    harm_type: str  # Semantic harm type
    harm_severity: float  # Severity [0, 1]
    harm_scope: str  # Scope
    reversibility: float  # Reversibility [0, 1]
```

### SimulationResult

```python
@dataclass
class SimulationResult:
    consequences: list[Consequence]  # Generated scenarios
    worst_case_valence: float  # min(valence)
    best_case_valence: float  # max(valence)
    expected_valence: float  # Likelihood-weighted average
    semantic_expected_harm: float  # max(likelihood * harm_severity)
    dominant_harm_types: list[str]  # top 2 harm_type
    worst_harm: dict | None  # {harm_type, harm_scope, risk}
    raw_response: str  # LLM response (debug)
```

---

## Expected Valence Calculation

```python
expected_valence = Σ(valence_i × likelihood_i) / Σ(likelihood_i)
```

| Expected Valence | Interpretation                                      |
|------------------|-----------------------------------------------------|
| > 0.5            | Predominantly positive outcome                      |
| 0.0 - 0.5        | Mixed outcome                                       |
| < 0.0            | Predominantly negative outcome → generates guidance |

---

## Usage

### Initialization

```python
from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator

simulator = LLMConsequenceSimulator(policy_llm=policy)
```

### Simulation

```python
result = simulator.simulate(
    prompt="User request",
    response="Response to evaluate",
)

print(f"Expected valence: {result.expected_valence}")
print(f"Worst case: {result.worst_case_valence}")
print(f"Best case: {result.best_case_valence}")

for consequence in result.consequences:
    print(f"- [{consequence.scenario_type.value}] {consequence.text}")
    print(f"  Valence: {consequence.outcome_valence}, Likelihood: {consequence.likelihood}")
```

---

## Example Output

```python
SimulationResult(
    consequences=[
        Consequence(
            text="User uses the information as a starting point for deeper research",
            likelihood=0.6,
            scenario_type=ScenarioType.POSITIVE_OUTCOME,
            outcome_valence=0.8,
            affected_stakeholders=["user"]
        ),
        Consequence(
            text="User might interpret the advice as a substitute for professional consultation",
            likelihood=0.25,
            scenario_type=ScenarioType.DOWNSTREAM_MISUSE,
            outcome_valence=-0.5,
            affected_stakeholders=["user", "healthcare_system"]
        ),
        Consequence(
            text="In case of unrecognized emergency, delay in appropriate care",
            likelihood=0.15,
            scenario_type=ScenarioType.IMMEDIATE_HARM,
            outcome_valence=-0.7,
            affected_stakeholders=["user"]
        )
    ],
    expected_valence=0.35,
    worst_case_valence=-0.7,
    best_case_valence=0.8,
)
```

---

## Caching

The simulator implements caching to avoid recomputation:

```python
# Automatic cache based on hash(prompt + response)
# Avoids duplicate LLM calls for identical inputs
```

---

## Orchestrator Integration

The Simulator contributes to aggregated guidance:

```python
if simulation.expected_valence < 0:
    # Generate specific guidance
    guidance_parts.append(
        f"[SIMULATOR] Negative consequences predicted: {worst_consequence.text}"
    )
```

### Impact on Decisions

| Expected Valence | Impact                      |
|------------------|-----------------------------|
| ≥ 0.5            | No negative impact          |
| 0 - 0.5          | Generates warning           |
| < 0              | Generates revision guidance |

**Semantic harm** (independent of valence): `semantic_expected_harm >= 0.4` adds REVISE votes; `physical_harm`/
`self_harm` with high harm add further REVISE votes.

---

## Factory Methods

### SimulationResult.empty()

```python
# No relevant consequences
result = SimulationResult.empty()
```

### SimulationResult.from_error()

```python
# Fallback on error
result = SimulationResult.from_error("Simulation failed")
# Assumes neutral valence (0.0)
```

---

## Environment Variables

All simulator tuning can be overridden via `.env`. Variables are read at simulator construction; empty or missing values
use the defaults below. See `.env.template` for the full list. **In application runs (CLI and benchmark), simulator
configuration is the single source of configuration — no CLI or code path overrides these variables.**

### Model (simulator LLM)

#### MORALSTACK_SIMULATOR_MODEL

- **Default**: *(none — uses the same model as the rest of the stack, e.g. `OPENAI_MODEL` or `gpt-4o`)*
- **Type**: string (OpenAI model id)
- **Description**: OpenAI model used **only** for the consequence simulator. When set and non-empty, the CLI and
  benchmark create a dedicated `OpenAIPolicy` with this model for the simulator; the rest of the stack keeps using
  `OPENAI_MODEL`.
- **Effect**:
    - **Set to a model id** (e.g. `gpt-4o-mini`, `gpt-4.1-nano` as in `.env.template` / `.env.minimal`): The simulator uses that model. Lets you use a smaller/cheaper model
      for simulation and a larger one for generation.
    - **Unset or empty**: The simulator uses the same policy (and model) as the rest of the pipeline.

In the recommended configuration (`.env.template`), the simulator uses `gpt-4.1-nano`.
Benchmark testing shows this reduces simulator-step cost versus heavier defaults; combined
with other stack optimizations, **benchmark run 11** reports overall mean wall-clock **~44s**
(median **~39s**) with **98.8%** compliance maintained and overall judge score **~9.35/10**
(vs **7.73/10** baseline), indicating minimal quality regression versus heavier simulator
configurations on the same suite.

### LLM and retry behaviour

#### MORALSTACK_SIMULATOR_MAX_RETRIES

- **Default**: `3`
- **Type**: int (>= 1)
- **Description**: Number of parse attempts for the simulator JSON response before raising an error.

Simulator generation uses OpenAI's `json_object` response format (`response_format={"type": "json_object"}` on `GenerationConfig`), which guarantees valid JSON and greatly reduces retries caused by malformed JSON.

#### MORALSTACK_SIMULATOR_MAX_TOKENS

- **Default**: `384`
- **Type**: int (>= 1)
- **Description**: Maximum tokens for the simulator LLM response. 384 keeps narratives compact (ultra-lean design).

#### MORALSTACK_SIMULATOR_TEMPERATURE

- **Default**: `0.8`
- **Type**: float (0–2)
- **Description**: LLM temperature for scenario generation. Higher values produce more diverse scenarios.

#### MORALSTACK_SIMULATOR_TOP_P

- **Default**: `0.95`
- **Type**: float (0–1)
- **Description**: Nucleus sampling (top-p) for simulator LLM generation. Controls diversity of token sampling.

### Scenario generation

#### MORALSTACK_SIMULATOR_DEFAULT_NUM_SCENARIOS

- **Default**: `3`
- **Type**: int (>= 1)
- **Description**: Default number of consequence scenarios to generate per simulation call.

#### MORALSTACK_SIMULATOR_USE_SEEDED_GENERATION

- **Default**: `false`
- **Type**: bool (1/true/yes or 0/false/no)
- **Description**: When true, each scenario is generated with a separate seed prompt for greater diversity. More costly
  but produces more varied scenarios.

### Caching

#### MORALSTACK_SIMULATOR_ENABLE_CACHING

- **Default**: `true`
- **Type**: bool (1/true/yes or 0/false/no)
- **Description**: Enable caching of simulation results to avoid recomputation on identical inputs.

---

## See Also

- [Hindsight Evaluator](./hindsight.md) - Retrospective scenario evaluation
- [Orchestrator](./orchestrator.md) - Feedback aggregation
- [Policy LLM](./policy.md) - Scenario generation
