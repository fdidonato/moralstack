# EU AI Act — regulatory landscape, evaluation tooling, and a proposed compliance-evidence pipeline

**Status: PARKED research note. Nothing here is implemented.**
Captured 2026-08-03. Author: research session, no code changed.
**Updated 2026-08-05** — §3 rewritten (recognition status, selected track, over-refusal
requirement, operational integration details) and §7 extended. §1, §2, §4, §5, §6 untouched.

This note exists so a future session can resume the AI Act compliance track without
re-doing the search. It is deliberately kept out of `docs/CODEBASE_FACTS.md`: per
`.claude/rules/memory-maintenance.md`, claims verifiable only against external systems
(regulatory texts, third-party benchmarks) never enter the Verified facts table.

## 0. Shelf life — read this first

Regulatory facts decay. Everything in §1 was retrieved on **2026-08-03** from the
sources listed in §7 and was already reshaped once by the Digital Omnibus. Before
relying on any date or article number below:

- re-check the deferral status (the Omnibus was agreed, not the end of the story);
- re-check whether any CEN-CENELEC deliverable has been cited in the Official Journal
  (that single event changes the whole strategy — see §1.3);
- treat all article numbers as **to be re-verified against the consolidated text**.
  They were gathered from secondary legal commentary, not read from the Regulation.

## 1. Regulatory state as of 2026-08-03 (EXTERNAL — sourced, not code-verifiable)

### 1.1 What the Digital Omnibus deferred

| Obligation set | Original date | New date |
| --- | --- | --- |
| Standalone high-risk systems (Annex III) | 2026-08-02 | **2027-12-02** |
| High-risk AI embedded in regulated products (Annex I) | 2026-08-02 | **2028-08-02** |

Annex III covers recruitment and worker management, creditworthiness, biometric
identification and categorisation, education access, migration and border control,
law enforcement, administration of justice.

### 1.2 What actually applies now (from 2026-08-02)

- **Article 50 transparency duties**, all of them:
  1. AI-interaction disclosure — users must know they are interacting with an AI,
     unless obvious to a reasonably well-informed, observant person;
  2. **machine-readable marking** of artificially generated or manipulated audio,
     image, video or text, "to the extent technically feasible";
  3. emotion-recognition / biometric-categorisation notice (deployer duty);
  4. deepfake disclosure (deployer duty), with an artistic/satirical/fictional carve-out;
  5. public-interest text disclosure, except where a person holds editorial responsibility.
- Narrow transition: systems placed on the market before 2026-08-02 get until
  **2026-12-02** to implement the technical marking solution only — not the other
  Article 50 duties.
- **AI Office enforcement powers over GPAI models**: requests for information, model
  access, corrective measures and recalls become exercisable from 2026-08-02.
- New prohibitions added by the Omnibus (non-consensual intimate imagery, CSAM),
  effective **2026-12-02**.
- Penalties for Article 50 breaches: up to **€15 M or 3% of worldwide annual turnover**,
  whichever is higher.

### 1.3 Harmonised standards — the strategic blocker

As of June 2026 **zero CEN-CENELEC JTC21 deliverables are published or cited in the
Official Journal**, so nothing yet grants the Article 40 presumption of conformity.
`EN 18286` (AI management system) reached Approval / Formal Vote first; `prEN 18228`,
`prEN 18229-1`, `prEN 18282` were at Enquiry. CEN-CENELEC adopted an exceptional
acceleration package in October 2025 targeting availability of prioritised deliverables
by **Q4 2026**; the Commission standardisation request expires **2027-02-28**.

Consequence: any claim of "AI Act conformity" made today rests on no citable standard.
Aim at *evidence production*, not at conformity.

### 1.4 Article 25(4) — the value-chain hook

> The provider of a high-risk AI system and the third party that supplies AI systems,
> tools, services, components or processes used or integrated in it shall, **by written
> agreement**, specify the necessary information, capabilities, technical access and
> other assistance based on the generally acknowledged state of the art, to enable the
> provider of the high-risk AI system to fully comply with the obligations of the Regulation.

**Carve-out:** the obligation does not apply to third parties making tools, services,
processes or components (other than GPAI models) publicly accessible under a **free and
open-source licence**. The AI Office may publish voluntary model contract terms.

## 2. MoralStack's position (HYPOTHESIS — not legal advice, not verified by counsel)

MoralStack is neither a GPAI model nor, on its own, a high-risk AI system. The working
qualification is **third-party component supplier** under §1.4: it is integrated into a
downstream system whose provider carries the high-risk obligations.

That reframes the deliverable from "MoralStack is compliant" (not a well-formed claim)
to:

> **MoralStack exposes decision evidence sufficient for the downstream provider to
> discharge its own obligations.**

This is a specification that can be implemented and tested. Whether §1.4 binds
MoralStack as a contractual obligation depends on the licence it ships under (see the
open decision in §6).

## 3. Evaluation tooling — alternatives to COMPL-AI (EXTERNAL)

COMPL-AI (ETH Zurich / INSAIT / LatticeFlow) has already been used against MoralStack
(`scripts/complai_probe/`, `docs/traces/complai_llm_rules_flow.md`). It self-declares as
*not* official auditing software. Surveyed 2026-08-03, re-surveyed and decided 2026-08-05.

### 3.0 Recognition status — no tool is EU-recognised

Do not look for "an officially recognised AI Act benchmark": as of 2026-08-05 none exists,
and the reason is structural.

- No CEN-CENELEC deliverable is cited in the Official Journal (§1.3) → no tool can confer
  a presumption of conformity.
- The AI Office was still **gathering expert opinion** on independence and qualification
  requirements for external evaluators of systemic-risk GPAI models (online workshop
  announced for 2026-07-15). The accreditation pathway does not exist yet.
- The strongest endorsement any tool holds is COMPL-AI's: European Commission spokesperson
  Thomas Regnier called it "a first step in translating the EU AI Act into technical
  requirements". That is a press statement, not recognition.

Recognition is therefore a **gradient**, not a gate. Ranked by institutional weight:
COMPL-AI (EC spokesperson statement) > Inspect (promoted by the EC's Interoperable Europe
portal) > AIR-Bench 2024 (ICLR 2025, taxonomy derived from the AI Act among 8 regulations)
> AILuminate (industry consortium + ISO/IEC 42001 bridge) > everything else.

### 3.1 The criterion that actually discriminates

MoralStack is a runtime governance layer, not a model. Two questions separate the field:

1. Does the framework accept a *system under test* including guardrails, or only a model?
2. Does it measure **over-refusal**? Without it, the delta of any governance layer is an
   artefact — a layer that refuses everything scores perfectly on safety. Documented
   magnitude of the trade-off: LlamaGuard blocks >98% of harmful queries at a **6.4% false
   positive rate**; ShieldGemma reaches 1.3% with better precision.

Question 1 is answered only by AILuminate. Question 2 is answered by XSTest and OR-Bench —
**both now present in the Inspect Evals registry**, which was not recorded on 2026-08-03.

### 3.2 Selected track (decided 2026-08-05): AIR-Bench 2024 via Inspect

Chosen over the AILuminate-first alternative. Trade-off accepted knowingly: Inspect
evaluates the governed system *as if it were a model*, so the architectural claim ("system
with guardrails") is weaker than AILuminate's — bought in exchange for a faster, fully
reproducible setup, a public HELM baseline, and one harness covering safety **and**
over-refusal.

- **AIR-Bench 2024 — Stanford CRFM.** Taxonomy unifying **8 government regulations (AI Act
  included) and 16 corporate policies** → 314 level-4 risks under 45 / 16 / 4 coarser
  levels; 5,694 prompts. Paper includes case studies mapping model performance onto the
  AI Act. Published at ICLR 2025. Public HELM leaderboard → a citable comparison baseline.
- **Harness — Inspect / Inspect Evals (UK AISI).** MIT-licensed, promoted by the European
  Commission's Interoperable Europe portal. Registry also carries StrongREJECT, SOSBench,
  WMDP, AgentHarm, Do-Not-Answer, BBQ / BOLD / StereoSet, **XSTest** (250 safe prompts a
  well-calibrated model must not refuse) and **OR-Bench Hard-1K**. No eval is labelled
  "EU AI Act" — that mapping is our contribution.

Integration (**UNVERIFIED — from Inspect docs, not run here**): Inspect's `openai-api`
provider addresses an arbitrary OpenAI-compatible endpoint as
`openai-api/<provider>/<model>`, reading `<PROVIDER>_API_KEY` / `<PROVIDER>_BASE_URL`
(hyphens → underscores). Two providers can therefore be declared — one at the MoralStack
proxy, one at the upstream vendor — giving a clean paired A/B **without** mutating a global
`OPENAI_BASE_URL`. Run form: `inspect eval inspect_evals/air_bench --model openai-api/<p>/<m>`.

**Experimental design fixed 2026-08-10 (user decisions).** The run is parity-matched to the
June COMPL-AI comparison — same harness (Inspect), same arms (`openai/gpt-4o` vs the proxy via
`openai-api/<provider>/<model>`), same paired per-item reporting; MoralStack's matrix pinned to
`gpt-4o` + `gpt-4o-mini` and `MORALSTACK_GENERATION_MODE=internal` (entailed by parity:
`upstream_then_verify` landed 2026-07-20, after the June run). One deliberate departure: **the
judge is always a third model, different from the baseline arm and from every model in
MoralStack's matrix** — chosen `openai/gpt-5.4`, identical on both arms. COMPL-AI's own
`strong_reject` judge was `gpt-4o-mini`, which sits inside MoralStack's matrix, so parity on the
judge would have compromised judge independence. Consequence: AIR-Bench absolute scores are not
comparable to the COMPL-AI ones — only paired deltas within a benchmark.

Reproducibility warning to carry into any write-up: the Inspect port's own `results.csv`
reports a **~10% difference in refusal rate** against the paper's baseline for gpt-4o —
partly a model-version difference (`gpt-4o-2024-08-06` vs `2024-05-13`). Absolute numbers
are not comparable across harnesses; only the paired delta is.

Bucket mapping onto the existing pre-registration (`scripts/complai_probe/prereg.md`) — the
three-bucket structure does not change, it gets *instrumented*:

| Bucket | Prediction | New instrument |
| --- | --- | --- |
| 1 — governance | Δ > 0 | AIR-Bench 2024 (regulation-derived hazard categories) |
| 2 — capability / do-no-harm | Δ ≈ 0 | **XSTest + OR-Bench Hard-1K** — direct over-refusal measurement, replacing the indirect proxy (`ifbench`, `include`, contrast sets) |
| 3 — double-edged | ambiguous | unchanged: estimator precision/recall |

### 3.3 Not selected — AILuminate (MLCommons)

The only mainstream framework that **explicitly** evaluates both bare models and AI systems
*with* moderation filters and guardrails; SUT is a primitive of its design, not an
adaptation. 12 hazard categories, 5-point grade (Poor→Excellent) via a safety-evaluator
ensemble. Tooling: `modelgauge` (runs tests against SUTs, annotates responses) +
`modelbench` (aggregates into hazards/benchmarks, writes reports); a custom SUT subclasses
`PromptResponseSUT` under `modelgauge/suts/`, and a `modelgauge-openai` plugin already
exists → the proxy attaches exactly as it does for COMPL-AI.

Blockers that decided against it for now, all to be re-checked if the track is revived:

- Official grading needs the member-only evaluator ensemble; the public path scores with
  LlamaGuard via Together AI → a local run yields an *AILuminate-like* number, **not an
  official AILuminate grade**.
- Prompt tiers: demo 1,200 prompts **CC-BY-4.0** on GitHub; practice 12k and official 12k
  are MLCommons-member-only, official held out → reproducibility stops at the practice set.
- Locales `en_US` and `fr_FR`; ZH/HI in development. **Italian absent** (2026-08-03 note
  said "IT/DE unconfirmed" — now confirmed absent for v1.0/v1.1).
- **AILuminate Global Assurance Program** (2026-02, with KPMG, Google, Microsoft,
  Qualcomm) bridges ISO/IEC 42001 *procedural* requirements to empirical metrics. Strongest
  available credential, but not an EU one.

Complements worth citing rather than running:

- **"Can We Trust AI Benchmarks?"** (arXiv 2502.06559, AIES 2025) — interdisciplinary
  meta-review of ~100–110 studies on benchmarking shortcomings, authored by **European
  Commission JRC** researchers (Seville, Ispra, Brussels), with the standard "not an
  official EC position" disclaimer. **The strongest EU-institutional citation available to
  justify evaluation methodology** — including the refusal to report a single aggregate
  score. Concludes that assessing *which benchmarks can be trusted* is itself a policymaker
  task. EC summary published on Knowledge4Policy as "AI benchmarking: nine challenges and
  a way forward".
- **JRC143259**, *The Role of AI Safety Benchmarks in Evaluating Systemic Risks in GPAI
  Models* (Vanschoren, Fernandez Llorca, Eriksson, Gomez — JRC, 2025-10-10). The EU
  institutional reference: proposes a dual-trigger framework (capability trigger +
  safety benchmark) and a tiered, proportionate evaluation strategy. One of six JRC
  external scientific reports on GPAI. **Contents not re-verified on 2026-08-05** — the
  PDF fetch returned unreadable binary; still second-hand.
- **Bench-2-CoP** (arXiv 2508.05464, Prandi et al.) — LLM-as-judge mapping of 194,955
  benchmark questions onto the GPAI Code of Practice taxonomy. Headline numbers worth
  quoting: **61.6%** of regulation-relevant questions target "tendency to hallucinate" and
  **31.2%** "lack of performance reliability", while evading human oversight,
  self-replication and autonomous AI development get **zero coverage**. Use as critical
  framing for why public benchmarks alone cannot evidence compliance.
- **capAI** (Floridi, Holweg, Taddeo et al. — Oxford + Bologna, SSRN 4064091) — not a
  benchmark: a *conformity assessment procedure* with an Internal Review Protocol following
  the AI system lifecycle. Procedural complement to the empirical track, not a substitute.
- **Phare** (Giskard, EU/Bpifrance/DeepMind funded) — EN/FR/ES, hallucination, bias,
  harmful content, jailbreak. European provenance.
- **Project Moonshot** (AI Verify Foundation) — benchmark + red-teaming; AI Verify maps
  to AI Act technical documentation, NIST AI RMF, OECD, ISO/IEC 42001.
- **DEMM-Bench** (arXiv 2606.20634) — measures whether emitted traces, ledgers, policy
  logs and provenance are *sufficient to reconstruct decision properties*, not merely
  present; reports trace-present baselines overclaiming on 75% of cases. Uniquely aligned
  with MoralStack's value proposition, but a June 2026 preprint with near-zero
  recognition: an original contribution, not a credential.

Integration note: all of these speak (or can speak) OpenAI `chat.completions`, so they
point at the MoralStack proxy exactly as COMPL-AI does. What they do *not* exercise is
the `llm_rules` pattern of a deployer rule in the system prompt — the DCCL /
compliance-fast-path route stays uncovered by them and needs our own probe
(`docs/traces/complai_llm_rules_flow.md` §2).

## 4. Proposed verifiable pipeline (PROPOSED — none of this exists yet)

Six stages. Each stage: **one claim → one artefact → one CI gate.** No artefact, no claim.

0. **Claim register** (`docs/compliance/claims.yaml`). One row per requirement:
   requirement id → testable claim → evidence artefact → producing command → status →
   **what would falsify it**. The last column is mandatory: a claim nothing could
   falsify gets deleted, not documented (the A4 census lesson).
1. **Determinism & replay.** The governance verdict is itself produced by LLM calls
   (risk mini-estimators, DCCL, safety override, deliberation, rewrite) — so it is
   stochastic, and that is the first thing an auditor probes. Build a replay harness
   that re-runs a stored `requests` row N times and reports a *decision stability rate*
   per bucket over `final_action` / `path_taken` / risk score. All the inputs are
   already persisted (§5).
2. **Evidence sufficiency, not evidence presence.** Build `reconstruct_decision(run_id,
   request_id)` answering a fixed property questionnaire (actor, authority, action,
   policy applied, decision basis, verification strength) **from the DB alone**, and gate
   CI on it. Write the questionnaire *before* the implementation.
3. **Audit-trail integrity.** See the open decision in §6 — this is an architecture
   choice, not a task.
4. **Behavioural evidence.** Run the §3 benchmarks **always paired A/B**: bare model vs
   governed system through the proxy, same prompts, same set. The delta is the result;
   the absolute score measures the upstream vendor. Reuse the pre-registered bucket
   discipline from `scripts/complai_probe/prereg.md` verbatim (bucket 1 governance Δ>0,
   bucket 2 capability Δ≈0 do-no-harm, bucket 3 double-edged) and pre-register before running.
5. **Article 50 conformity** — the only binding target today, and MoralStack sits in the
   right place for it: every user-visible answer is produced inside the governed pipeline
   and the upstream client never delivers directly, including the synthetic SSE replay
   for `stream=True` (`.claude/rules/governed-delivery.md`). That single choke point makes
   transparency marking enforceable by construction rather than by convention. Needs: a
   provenance marker on every delivered answer + a test proving no delivery path
   (NORMAL / SAFE / REFUSE / stream) can emit without it.
6. **CI gate and release attestation.** Every stage writes to
   `artifacts/compliance/<git-sha>/`; a release ships a manifest (claim register status,
   stability rate, reconstruction rate, benchmark deltas, log completeness) regenerable
   from a single sha.

Suggested order: 0 → 1 → 2 → 5, then 4 (most expensive in API spend, least original).

## 5. Internal facts the pipeline depends on

Read this session. **Sourced from the trace docs, which cite the code — the cited
modules themselves were not re-read here.** Re-verify against the implementation before
building on them (PROJECT_SPEC §3).

| Fact | Source read |
| --- | --- |
| `llm_calls` persists module, action, model, prompt, system_prompt, raw_response, parsed/summary JSON, tokens, cache status, `billable_provider_call` → replay inputs already exist | `docs/traces/observability_db_to_ui.md:67` |
| `decision_traces` stores `trace_json` as an untyped blob: new fields are additive with no migration, but **old rows silently lack the key** → the "present but insufficient" failure mode DEMM-Bench targets | `docs/traces/observability_db_to_ui.md:70` |
| Observability is async fire-and-forget; the **proxy does not flush per request** (removed: under burst the bounded flush timed out, ~5s overhead, no visibility gained) | `docs/traces/observability_db_to_ui.md:22-29` |
| Offline token reconstruction from `llm_calls` is "still not a completeness guarantee because the async queue may drop envelopes before they reach the DB" → **"every decision is logged" is currently not a defensible claim** | `docs/traces/observability_db_to_ui.md:108-115` |
| A synchronous audit spine already exists: `finalize_audit_sync` writes `request.meta_updated` and exactly-one `proxy.request_finalized` via `route_audit_sync` | `docs/traces/observability_db_to_ui.md:35-38` |
| Governed delivery: every user-visible answer is produced inside the pipeline; upstream never delivers; failure fails closed to a governed refusal | `.claude/rules/governed-delivery.md` (invariant §5.7) |
| The proxy is the COMPL-AI-style integration point; `turn_index` derived statelessly, conversation correlation for clients without a stable id | `docs/traces/complai_llm_rules_flow.md:15-59`, `moralstack/server/fingerprint.py:36-53` |

## 6. Open decisions — resume here

1. **Audit-trail integrity: A or B?**
   - **A (recommended):** keep async, keep the synchronous audit spine, add a
     reconciliation job that detects missing children and *measures* the loss rate, and
     hash-chain the spine (each `request_finalized` carries the previous hash) so gaps
     and tampering are detectable. Claim becomes: "spine complete and tamper-evident;
     detail tier best-effort with measured loss rate X%". Cheap, honest, weaker claim.
   - **B:** make the audit-critical subset synchronous and fail-closed. Claim becomes
     "100% logged", at the cost of latency (~5s under burst, empirically measured) and a
     new production failure mode. **B conflicts with invariant §5.6 "observability never
     breaks the request"** — it must not be implemented without an explicit decision to
     rewrite that invariant.
2. **Licence.** Does MoralStack ship under a free/open-source licence? Determines whether
   Article 25(4) binds it as an obligation or is only a voluntary template (§1.4).
3. **Thesis target.** "Component supplier for a downstream high-risk provider"
   (ambitious, deadline 2027-12-02, no standards base) vs "Article 50 transparency
   enforceable by construction" (binding today, fully demonstrable). Both can coexist;
   the priority order decides what gets built first.

## 7. Sources (retrieved 2026-08-03, extended 2026-08-05)

Added 2026-08-05:

- AI Office workshop, qualification requirements for external evaluators of systemic-risk
  GPAI models — <https://digital-strategy.ec.europa.eu/en/events/call-participants-workshop-qualification-requirements-external-evaluators-gpai-models-systemic-risk>
- EC spokesperson statement on COMPL-AI (Thomas Regnier) — <https://www.cio.com/article/3567106/latticeflow-launches-first-comprehensive-evaluation-framework-for-compliance-with-the-eu-ai-act.html>
- AI Act Service Desk / Single Information Platform (Compliance Checker, AI Act Explorer;
  EN/FR/DE, all 24 languages announced for early 2026) — <https://ai-act-service-desk.ec.europa.eu/en>,
  <https://digital-strategy.ec.europa.eu/en/news/commission-launches-ai-act-service-desk-and-single-information-platform-support-ai-act>
- "Can We Trust AI Benchmarks?" (JRC) — <https://arxiv.org/abs/2502.06559>; EC summary
  <https://knowledge4policy.ec.europa.eu/news/ai-benchmarking-nine-challenges-way-forward_en>
- AIR-Bench 2024 in Inspect Evals — <https://ukgovernmentbeis.github.io/inspect_evals/evals/knowledge/air_bench/>,
  source <https://github.com/UKGovernmentBEIS/inspect_evals/tree/main/src/inspect_evals/air_bench>
- Inspect providers, `openai-api` OpenAI-compatible endpoints — <https://inspect.aisi.org.uk/providers.html>
- AILuminate tooling — modelbench <https://github.com/mlcommons/modelbench>, modelgauge SUT
  tutorial <https://github.com/mlcommons/modelgauge/blob/main/docs/tutorial_suts.md>
- capAI — <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4064091>
- GPAI Code of Practice, Safety & Security chapter (safety mitigations incl. input/output
  filtering) — <https://digital-strategy.ec.europa.eu/en/policies/contents-code-gpai>

Retrieved 2026-08-03:

- Jones Walker — <https://www.joneswalker.com/en/insights/blogs/ai-law-blog/yes-august-2-still-matters-the-eu-approved-a-high-risk-ai-delay-but-most-trans.html>
- Gibson Dunn, EU AI Act Omnibus Agreement — <https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/>
- Article 25 AI Act — <https://ai-act-law.eu/article/25/> and <https://www.activemind.legal/legislation/ai-act/article-25/>
- CEN-CENELEC acceleration — <https://www.cencenelec.eu/news-events/news/2025/brief-news/2025-10-23-ai-standardization/>
- JTC 21 standards tracker — <https://kla.digital/blog/jtc-21-standards-tracker>
- AILuminate — <https://mlcommons.org/benchmarks/ailuminate/>, Global Assurance Program <https://mlcommons.org/2026/02/ailuminate-global-assurance/>, v1.1 FR <https://mlcommons.org/2025/02/ailumiate-v1-1-fr/>
- Inspect — <https://github.com/UKGovernmentBEIS/inspect_ai>, Inspect Evals <https://ukgovernmentbeis.github.io/inspect_evals/>, EC Interoperable Europe <https://interoperable-europe.ec.europa.eu/collection/open-source-observatory-osor/news/inspect-uk-government-backed-open-source-ai-testing-platform>
- AIR-Bench 2024 — <https://arxiv.org/abs/2407.17436>, <https://github.com/stanford-crfm/air-bench-2024>; HELM <https://github.com/stanford-crfm/helm>
- JRC143259 — <https://publications.jrc.ec.europa.eu/repository/handle/JRC143259>; collection <https://ai-watch.ec.europa.eu/news/new-jrc-collection-external-scientific-reports-inform-implementation-eu-ai-act-general-purpose-ai-2025-10-14_en>
- Bench-2-CoP — <https://arxiv.org/pdf/2508.05464>
- Phare — <https://github.com/Giskard-AI/phare>
- Project Moonshot — <https://aiverify-foundation.github.io/moonshot/>
- DEMM-Bench — <https://arxiv.org/pdf/2606.20634>
- COMPL-AI — <https://github.com/compl-ai/compl-ai>
