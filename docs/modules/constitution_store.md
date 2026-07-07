# Constitution Store

> **Module**: `moralstack/constitution/store.py` (orchestration) · **Loader**: `moralstack/constitution/loader.py` · **Schema**: `moralstack/constitution/schema.py`

The Constitution Store manages the system's ethical principles, implementing loading, indexing and contextual retrieval.

**Loading engineering**: YAML is read **only** via **ruamel.yaml** (`typ="safe"`) in the `load_yaml_file` function in
`loader.py`. Structural validation is **Pydantic-only** (schema in `schema.py`). **Fail-fast** behavior: invalid YAML or
failed validation → **ConstitutionLoadError** (path, field, reason); no partial constitution. The store exposes **only
typed Pydantic objects** (Principle, Overlay, Constitution), never raw dicts.

**For stakeholders and testers**: Principles and overlays determine *when* a response is considered in violation and
*which* principles apply per domain. For testing, reduced constitutions or custom overlays can be loaded; in production
the files in `moralstack/constitution/data/` define the verifiable ethical behavior.

---

## Overview

The Constitution Store:

- **Loads** ethical principles from YAML files (ruamel.yaml, Pydantic validation)
- **Indexes** principles for efficient retrieval
- **Applies** domain overlays (medical, financial, legal, etc.)
- **Detects** domain automatically from the request
- **Resolves** conflicts between principles

---

## Hierarchical Structure

```
Constitution
├── Core Principles (always active)
│   ├── Hard Constraints (priority 85-100)
│   │   └── Non-negotiable, violation = refusal
│   └── Soft Norms (priority 30-84)
│       └── Flexible, violation = caveat/revision
│
└── Domain Overlays (contextually activated)
    ├── Medical
    ├── Financial
    ├── Legal
    ├── Mental Health
    └── ...
```

---

## Principle Structure

### Principle (Pydantic, in `schema.py`)

```python
# moralstack/constitution/schema.py
class Principle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    level: Literal["hard", "soft"]
    priority: int                # 1-100 (higher = more important)
    title: str
    rule: str
    examples_allow: list[str] = []
    examples_deny: list[str] = []
    remediation: str = ""
    domain: str | None = None
    keywords: list[str] = []
```

### Example Principle (YAML)

```yaml
- id: "CORE.NM.1"
  level: hard
  priority: 100
  title: "Non-Maleficence (Physical Harm)"
  rule: >
    Never provide instructions that could facilitate
    physical harm to people
  examples_allow:
    - "Educational explanation of mechanisms"
    - "Historical information on past events"
  examples_deny:
    - "Instructions for building weapons"
    - "Guides for causing physical harm"
  remediation: "Refuse with explanation and safe alternatives"
  keywords:
    - "bomb"
    - "explosive"
    - "weapon"
```

---

## Domain Overlays

### Overlay (Pydantic, in `schema.py`)

```python
class Overlay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str
    additional_principles: list[Principle] = []
    priority_overrides: dict[str, int] = {}
    description: str = ""
    keywords: list[str] = []
```

### Available Domains

Overlays in `moralstack/constitution/data/overlays/` include: `medical`, `legal`, `financial`, `education`, `mental_health`,
`healthcare`, `children`, `research`, `creative`, `cybersecurity`, `emergency`, `enterprise`, `journalism`, `science`,
`political`, `relationships`, `gaming`, `coding`, `customer_service`, `violent_crime`.

---

## Usage

### Configuration: ConstitutionStoreConfig

To avoid long parameter lists, use the `ConstitutionStoreConfig` dataclass and pass it to
`ConstitutionStore(config=...)`:

```python
from moralstack.constitution import ConstitutionStore, ConstitutionStoreConfig, OpenAIClientConfig

cfg = ConstitutionStoreConfig(
    config_dir=Path("moralstack/constitution/data"),
    policy_llm=policy,
    use_llm_matching=True,
    openai_config=OpenAIClientConfig.with_env_fallback(
        use_openai=True,
        api_key=api_key,
        model="gpt-4o-mini",
    ),
    max_parallel_agents=2,
)
store = ConstitutionStore(config=cfg)
```

All fields have defaults; only override what you need. See `ConstitutionStoreConfig` in `store.py` for the full list (
`config_dir`, `core_file`, `overlays_dir`, `policy_llm`, `use_llm_matching`, `openai_config`, `max_parallel_agents`,
`use_enhanced_retrieval`, `confidence_threshold`, `use_domain_prefilter`, `max_prefilter_domains`).

When `max_parallel_agents` is not set explicitly, SDK/CLI runtime wiring resolves it from
`MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS` (fallback default: `2`).

### Initialization (keyword args, backward compatible)

You can still pass options as keyword arguments (same behavior as before):

```python
from moralstack.constitution import ConstitutionStore, OpenAIClientConfig

# Minimal (no OpenAI; uses policy_llm for domain agents)
constitution_store = ConstitutionStore(
    policy_llm=policy,
    use_llm_matching=True,
    max_parallel_agents=2,
)

# With OpenAI for constitution evaluation
constitution_store = ConstitutionStore(
    policy_llm=policy,
    use_llm_matching=True,
    openai_config=OpenAIClientConfig.with_env_fallback(
        use_openai=True,
        api_key=api_key,
        model="gpt-4o-mini",
    ),
    max_parallel_agents=2,
)
```

### Principle Retrieval

```python
# Retrieve principles relevant to a request
principles = constitution_store.get_relevant_principles(
    prompt="I have had severe headaches for three days",
    top_k=10,
)

for p in principles:
    print(f"{p.id}: {p.title} (priority: {p.priority})")
```

### Overlay Application

```python
# Activate medical overlay
constitution = constitution_store.get_constitution(domain="medical")

# Automatic domain detection
detected_domain = constitution_store.detect_relevant_domains(prompt)
constitution = constitution_store.get_constitution(domain=detected_domain)
```

---

## Architecture: Store as Facade

`ConstitutionStore` acts as a facade: it handles load/overlay and delegates retrieval to
`ConstitutionRetriever` (`moralstack/constitution/retriever.py`). The retriever encapsulates:

- Agent creation and orchestration (DomainAgent, EnhancedDomainAgent)
- DomainPrefilter (two-stage retrieval)
- Parallel execution with configurable batch size
- `get_relevant_principles` internals

`DomainPrefilter`, `DomainAgent`, and `EnhancedDomainAgent` reuse a single OpenAI HTTP client per component instance
when the API key is unchanged; chat completion parameters (`model`, `messages`, `response_format`, etc.) remain per call.
`ConstitutionRetriever.get_debug_info()` includes aggregate `retrieval_openai_client_pooling` counts for diagnostics.

Public API (`get_relevant_principles`, `detect_relevant_domains`, `get_debug_info`) remains unchanged.

**`retrieval_phase` observability qualifier** (unify-constitution-retrieval-
single-pass). `get_relevant_principles(query, top_k=10, domain=None, *,
retrieval_phase="risk_routing")` threads `retrieval_phase` through the
`DomainPrefilter` call, both agent kinds' `evaluate(query, *,
retrieval_phase=...)` → `_call_openai(..., retrieval_phase=...)`, and
`_persist_constitution_llm_call` — so every `llm_calls` row for a given
retrieval wave (prefilter AND per-domain agents, enhanced or legacy) is labeled
consistently: `"risk_routing"` (default) for the single risk-owned wave (see
`docs/modules/risk_estimator.md`), `"deliberation_retrieval"` for the
deliberation runner's fallback wave when the risk-owned retrieval was
unavailable.

## Domain Selection (DomainPrefilter)

Domains are narrowed using **compact keyword maps** backed by YAML overlay metadata to keep token budgets small. When
each overlay declares a human-authored `description` and the provider exposes `get_domain_descriptions()`, DomainPrefilter
prints **`- {domain}: {description}` plus a Keywords line** in the classifier prompt (`moralstack/constitution/retriever.py`).
Missing descriptions cleanly fall back to the historical keywords-only line. Keywords may likewise be derived purely from
deterministic extraction when YAML lacks explicit lists.

Descriptions may embed trailing **`NOT for: …`** sentences (see many overlays beneath `constitution/data/overlays/*.yaml`)
to steer negative scoping—for example signalling that explosives requests should not collapse into a narrowly topical label.

The classifier prompt instructs the model to treat **verbatim embedded segments that use arbitrary encoding or
obfuscation** as inspectable when substantive meaning can be recovered **without fabricating absent material**; domains
should follow that recovered substance. Opaque or non-recoverable material should favour empty-domain conservatism rather
than guessed intent.

**Prefilter cache:** `DomainPrefilter.set_domain_keywords` is idempotent: the in-memory prefilter cache is cleared
only when the effective keyword map changes (canonical fingerprint over sorted domains and sorted de-duplicated
keywords). Identical maps with different key or keyword order reuse the cache across requests. When persistence
context (`run_id` / `request_id`) is active, cache hit / miss / invalidation are recorded as `orchestration_events`
(`DOMAIN_PREFILTER_CACHE_HIT`, `DOMAIN_PREFILTER_CACHE_MISS`, `DOMAIN_PREFILTER_CACHE_INVALIDATED`). The latest
retrieval exposes `prefilter_cache_status` and related fields via `ConstitutionStore.get_debug_info()` and the
`REQUEST_ANALYSIS_CONTEXT` decision trace payload.

**Domain-agent cache:** `EnhancedDomainAgent` and legacy `DomainAgent` cache on the exact OpenAI-relevant request
material: rendered system/user messages plus model, temperature, `json_object`, and completion-token parameter.
The rendered compact principle text includes id, level, and truncated rule. It intentionally does not include
principle titles, so title-only changes do not invalidate the agent cache.

**Structured LLM output:** Domain prefilter and domain agents use OpenAI Chat Completions with
`response_format={"type":"json_object"}` where the model must return a JSON object; legacy `DomainAgent`
uses an object wrapper `{"principle_ids": [...]}` (root arrays are not valid for `json_object` mode).
Tolerant regex / `extract_json` fallbacks remain for observability and compatibility. When persistence
context is active, `llm_calls` rows include `parse_contract` metadata (see `moralstack/utils/llm_parse_contract.py`).

## Principle Retrieval (LLM-based)

Retrieval is delegated to `ConstitutionRetriever` and uses a two-stage LLM flow:

### 1. Domain Selection (DomainPrefilter)

The LLM sees each candidate domain primarily through the description + Keywords bundle when present, selecting up to the
configured cap (see `max_prefilter_domains`). Token usage remains constrained relative to injecting full principle payloads
at this stage.

### 2. Domain Agent Evaluation

For each selected domain, `EnhancedDomainAgent` (or legacy `DomainAgent`) sends the domain's principles (id, title,
rule, level) to the LLM. The LLM returns relevant principle IDs with confidence scores. Principles below
`confidence_threshold` are filtered out.

**Note:** `principle.keywords` from YAML are not used in the agent prompts. They are metadata; the LLM evaluates
relevance from rule and title content.

---

## Conflict Resolution

Precedence order for conflicting principles:

1. **Hard constraint > Soft norm**
2. **Descending numeric priority**
3. **Specificity** (more specific principles prevail)
4. **Alphabetical ID order** (determinism)

```python
# Example: two conflicting principles
# CORE.HELPFUL.1 (soft, priority 65): "Provide useful information"
# MED.DISCLAIMER.1 (soft, priority 80): "Include medical disclaimer"
# → MED.DISCLAIMER.1 prevails (higher priority)
```

---

## Main Hard Constraints

| ID                    | Title                                   | Priority |
|-----------------------|-----------------------------------------|----------|
| `CORE.NM.1`           | Non-Maleficence (Physical Harm)         | 100      |
| `CORE.CSAM.1`         | Child Safety                            | 100      |
| `CORE.NM.2`           | Non-Maleficence (Illegal)               | 95       |
| `CORE.MALWARE.1`      | Malware Prevention                      | 95       |
| `CORE.MANIPULATION.1` | Ban on Manipulation                     | 95       |
| `CORE.PRIV.1`         | Privacy Protection                      | 90       |
| `CORE.DISINFO.1`      | Disinformation Prevention               | 90       |
| `CORE.DUALUSE.1`      | Dual-Use Information Leakage Prevention | 91       |

---

## Main Soft Norms

| ID                  | Title                 | Priority |
|---------------------|-----------------------|----------|
| `SOFT.HONEST.1`     | Honesty and Accuracy  | 70       |
| `SOFT.VULNERABLE.1` | Vulnerable Protection | 70       |
| `SOFT.HELPFUL.1`    | Helpfulness           | 65       |
| `SOFT.BALANCED.1`   | Balanced Perspective  | 60       |
| `SOFT.AUTONOMY.1`   | User Autonomy         | 60       |
| `SOFT.CLARITY.1`    | Clear Communication   | 40       |

---

## Medical Overlay Example

```yaml
# moralstack/constitution/data/overlays/medical.yaml

priority_overrides:
  SOFT.HONEST.1: 85     # High accuracy in medical context
  SOFT.HELPFUL.1: 75   # Informative support important

additional_principles:
  - id: "MED.DISCLAIMER.1"
    level: soft
    priority: 80
    title: "Medical Disclaimer"
    rule: "Include appropriate disclaimers and recommend professional consultation"

  - id: "MED.EMERGENCY.1"
    level: hard
    priority: 100
    title: "Medical Emergency Recognition"
    rule: "In case of medical emergency, immediately recommend emergency services"
```

---

## Configuration Files

```
moralstack/constitution/data/
├── core.yaml                    # Core principles
└── overlays/
    ├── medical.yaml
    ├── legal.yaml
    ├── financial.yaml
    ├── education.yaml
    ├── mental_health.yaml
    ├── healthcare.yaml
    ├── children.yaml
    ├── research.yaml
    ├── creative.yaml
    ├── cybersecurity.yaml
    ├── emergency.yaml
    ├── enterprise.yaml
    ├── journalism.yaml
    ├── science.yaml
    ├── political.yaml
    ├── relationships.yaml
    ├── gaming.yaml
    ├── coding.yaml
    ├── customer_service.yaml
    └── violent_crime.yaml
```

---

## See Also

- [Constitutional Critic](./critic.md) - Uses principles for validation
- [Risk Estimator](./risk_estimator.md) - Integrates principles in estimation
- [Orchestrator](./orchestrator.md) - Overlay coordination
