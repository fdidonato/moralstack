# Documentation-Grounded Dual Adversarial Planning Kit

Questo kit crea un workflow locale per generare piani di lavoro su codice o investigazioni di codebase usando una doppia pianificazione adversarial:

1. Claude produce un piano indipendente.
2. Codex produce un piano indipendente.
3. Codex critica il piano Claude.
4. Claude critica il piano Codex.
5. Claude produce una sintesi finale.
6. Codex fa il final gate e può bloccare il piano.

La differenza rispetto a un normale setup Claude/Codex è che ogni run parte da una **trusted adversarial documentation baseline**: la documentazione già redatta e validata manualmente sulla codebase.

---

## 1. Cosa contiene la cartella

```text
.adversarial/
  README.md
  config.json

  baseline/
    manifest.json
    trust_policy.md

  prompts/
    shared_rules.md
    01_planner_claude.md
    02_planner_codex.md
    03_reviewer_codex.md
    04_reviewer_claude.md
    05_synthesizer.md
    06_final_gate_codex.md
    07_revision_synthesizer.md

  schemas/
    review.schema.json
    final_gate.schema.json
    baseline_manifest.schema.json
    issue_matrix.schema.json

  scripts/
    common.py
    build_baseline_manifest.py
    build_doc_digest.py
    check_doc_code_drift.py
    build_context_pack.py
    adversarial_plan.py
    validate_artifacts.py

  tasks/
    example_task.md

  examples/
    moralstack_multiturn_observability_task.md

  runs/
    .gitkeep

  requirements.txt
  setup.sh
  setup.ps1
  Makefile.snippet
```

---

## 2. Concetto operativo

Il workflow non parte dalla codebase grezza. Parte da tre fonti integrate:

```text
Trusted adversarial documentation baseline
  +
Current codebase snapshot
  +
Task-specific evidence search
```

La documentazione è fonte primaria per:

- architettura intenzionale
- invarianti
- fatti già verificati
- mappe dei moduli
- rischi noti
- trace documentali già validati

Il codice corrente è fonte primaria per:

- comportamento effettivamente implementato
- path reali
- simboli reali
- test disponibili
- eventuale drift rispetto alla documentazione

Se documentazione e codice divergono, il punto deve essere marcato come `[DRIFT]` o `DOC_CODE_CONFLICT`.

---

## 3. Installazione nel repository

Estrai lo zip nella root del repository.

Dopo l’estrazione devi avere:

```text
repo/
  .adversarial/
  CLAUDE.md
  docs/
  ...
```

Su Linux/macOS:

```bash
cd /path/to/repo
bash .adversarial/setup.sh
```

Su Windows PowerShell:

```powershell
cd C:\path\to\repo
.adversarial\setup.ps1
```

La validazione strutturale può essere lanciata anche manualmente:

```bash
python .adversarial/scripts/validate_artifacts.py --root .adversarial
```

Output atteso:

```text
VALIDATION PASSED
```

---

## 4. Prerequisiti

Obbligatori per la generazione completa:

```text
python 3.10+
git
claude CLI
codex CLI
```

Raccomandato:

```text
rg / ripgrep
```

Il kit usa solo la standard library Python. Non richiede pacchetti Python esterni.

---

## 5. Configurare la baseline documentale

Apri:

```text
.adversarial/baseline/manifest.json
```

La configurazione predefinita corretta assume **solo i documenti che sono stati effettivamente generati nella fase di indicizzazione/adversarial documentation**:

```text
CLAUDE.md
docs/MORALSTACK_CODEBASE_INDEX.md
docs/CODEBASE_FACTS.md
```

Questi tre documenti sono marcati come `required: true`. La directory `docs/traces/` è supportata solo come fonte opzionale e discovery-based: se esiste, i file Markdown al suo interno vengono copiati nello snapshot; se non esiste, la run continua senza errore.

Non sono inclusi di default documenti come `docs/ARCHITECTURE_MAP.md`, `docs/INVARIANTS.md`, `docs/KNOWN_RISKS.md` o `docs/TRACE_INDEX.md`, perché non fanno parte della baseline certa se non sono stati realmente creati. Aggiungili al manifest solo dopo averli redatti e validati.

Esempio di documento richiesto:

```json
{
  "path": "CLAUDE.md",
  "role": "agent_operating_rules",
  "authority": "high",
  "required": true,
  "always_include": true
}
```

Se nella tua codebase i nomi sono diversi, modifica il manifest prima di lanciare la pipeline.

Esempio:

```json
{
  "path": "docs/MY_CODEBASE_INDEX.md",
  "role": "codebase_index",
  "authority": "high",
  "required": true,
  "always_include": true
}
```

---

## 6. Creare un task

Crea un file Markdown nella cartella che preferisci, per esempio:

```text
tasks/multiturn_observability.md
```

Esempio:

```markdown
# Task

Analizza la codebase MoralStack e produci un piano per rendere completamente osservabile il multi-turn, includendo DB, file logging, moralstack-ui, export markdown e propagazione di conversation_id/request_id/turn_index.

Non implementare. Produci solo un piano.
```

Puoi anche partire dall’esempio già incluso:

```text
.adversarial/examples/moralstack_multiturn_observability_task.md
```

---

## 7. Primo test: dry run

Prima di chiamare Claude e Codex, esegui una dry run:

```bash
python .adversarial/scripts/adversarial_plan.py \
  --task .adversarial/examples/moralstack_multiturn_observability_task.md \
  --dry-run
```

La dry run genera solo:

```text
00_task.md
00_run_metadata.json
01_baseline_manifest.json
02_baseline_snapshot/
03_baseline_digest.md
04_doc_code_drift_report.md
05_context_pack.md
run_report.md
```

Non chiama Claude e non chiama Codex.

Serve a verificare che:

- la baseline esista
- il digest venga generato
- il drift report funzioni
- il context pack sia coerente

---

## 8. Eseguire la pipeline completa

Quando la dry run è corretta:

```bash
python .adversarial/scripts/adversarial_plan.py \
  --task .adversarial/examples/moralstack_multiturn_observability_task.md \
  --max-rounds 1
```

Oppure, se hai copiato i target nel tuo `Makefile`:

```bash
make adversarial-plan TASK=.adversarial/examples/moralstack_multiturn_observability_task.md
```

---

## 9. Output della run

Ogni run crea una cartella:

```text
.adversarial/runs/<timestamp>-<task-name>/
```

Dentro troverai:

```text
00_task.md
00_run_metadata.json
01_baseline_manifest.json
02_baseline_snapshot/
03_baseline_digest.md
04_doc_code_drift_report.md
05_context_pack.md
06_claude_plan.md
07_codex_plan.md
08_codex_reviews_claude.json
09_claude_reviews_codex.md
10_synthesis_input.md
11_final_plan_candidate.md
12_codex_final_gate.json
final_plan.md
run_report.md
raw_prompts/
```

`final_plan.md` viene creato solo se il final gate accetta il piano.

Se la run non converge, troverai:

```text
run_report.md
12_codex_final_gate.json
```

ma non devi trattare il piano come approvato.

---

## 10. Criteri di accettazione

Il piano finale è accettato solo se Codex final gate restituisce:

```json
{
  "verdict": "ACCEPT",
  "baseline_consistency": true,
  "used_required_baseline_documents": true,
  "drift_report_handled": true,
  "unresolved_doc_code_conflicts": [],
  "blocking_issues": [],
  "has_sufficient_tests": true,
  "has_rollback_strategy": true,
  "is_implementable_by_fresh_agent": true
}
```

La soglia minima di confidence è configurata in:

```text
.adversarial/config.json
```

Default:

```json
"min_final_confidence": 0.82
```

---

## 11. Regole anti-allucinazione

Ogni claim importante nei piani deve usare uno di questi tag:

```text
[DOC]        Preso dalla baseline documentale.
[CODE]       Verificato nel codice corrente.
[TEST]       Basato su test esistenti o comandi di validazione.
[DRIFT]      Divergenza tra documentazione e codice.
[ASSUMPTION] Ipotesi da verificare prima di implementare.
```

Un piano che afferma cose architetturali senza tag dovrebbe essere bloccato dal final gate.

---

## 12. Come usare il final plan

Quando ottieni:

```text
.adversarial/runs/<run_id>/final_plan.md
```

apri una branch separata:

```bash
git switch -c implement/<task-name>
```

Poi dai `final_plan.md` a un agente implementatore in una sessione nuova.

Principio fondamentale:

```text
Planning adversarial in read-only.
Implementation in branch separata.
Validation con test reali.
```

Non fare implementare allo stesso agente che ha prodotto il piano senza ripartire da una sessione pulita.

---

## 13. Aggiornamento della documentazione dopo implementazione

Ogni `final_plan.md` deve contenere una sezione:

```text
## 15. Documentation Maintenance Plan
```

Dopo l’implementazione, aggiorna i documenti indicati dal piano. Nella baseline predefinita i documenti da mantenere sono:

```text
CLAUDE.md
docs/MORALSTACK_CODEBASE_INDEX.md
docs/CODEBASE_FACTS.md
```

Se in futuro aggiungi documenti opzionali validati, come mappe architetturali, invarianti separati, rischi noti o trace documents, inseriscili nel manifest solo quando esistono davvero e aggiorna anche quelli. Questo evita che la baseline contenga riferimenti a documenti non prodotti.

---

## 14. Personalizzazione dei comandi Claude/Codex

Apri:

```text
.adversarial/config.json
```

Se il tuo Codex non accetta prompt lunghi come argomento CLI, cambia:

```json
"codex_prompt_mode": "arg"
```

in:

```json
"codex_prompt_mode": "stdin"
```

Se la tua CLI Codex usa flag diversi per output o schema, modifica:

```json
"codex_output_flag": "-o",
"codex_schema_flag": "--output-schema"
```

Per Claude puoi modificare:

```json
"claude_prompt_mode": "stdin"
```

in:

```json
"claude_prompt_mode": "arg"
```

oppure:

```json
"claude_prompt_mode": "file_arg"
```

se il tuo wrapper locale accetta un path al file di prompt.

---

## 15. Flusso completo interno

La pipeline esegue questi passi:

```text
Step 0  Create run directory
Step 1  Copy task
Step 2  Snapshot trusted baseline with SHA256 hashes
Step 3  Build baseline digest
Step 4  Run documentation/code drift check
Step 5  Build task-specific context pack
Step 6  Claude independent plan
Step 7  Codex independent plan
Step 8  Codex reviews Claude
Step 9  Claude reviews Codex
Step 10 Claude synthesizes final plan
Step 11 Codex final gate
Step 12 Optional one revision round
Step 13 Accept final_plan.md or fail explicitly
```

---

## 16. Debug

Tutti i prompt effettivi inviati agli agenti sono salvati in:

```text
.adversarial/runs/<run_id>/raw_prompts/
```

Per ogni chiamata trovi anche:

```text
*.stdout.log
*.stderr.log
```

Questo permette di capire se un errore deriva da:

- CLI non configurata
- prompt troppo lungo
- schema non accettato
- output non JSON
- fallimento del final gate

---

## 17. Politica consigliata per Git

Consiglio di committare:

```text
.adversarial/config.json
.adversarial/baseline/
.adversarial/prompts/
.adversarial/schemas/
.adversarial/scripts/
.adversarial/tasks/
.adversarial/examples/
.adversarial/README.md
```

E di ignorare le run:

```gitignore
.adversarial/runs/*
!.adversarial/runs/.gitkeep
```

---

## 18. Comando minimo raccomandato

Dry run:

```bash
python .adversarial/scripts/adversarial_plan.py \
  --task .adversarial/examples/moralstack_multiturn_observability_task.md \
  --dry-run
```

Run completa:

```bash
python .adversarial/scripts/adversarial_plan.py \
  --task .adversarial/examples/moralstack_multiturn_observability_task.md \
  --max-rounds 1
```

---

## 19. Risultato atteso

Il risultato utile è:

```text
.adversarial/runs/<run_id>/final_plan.md
```

Tutto il resto serve per audit, riproducibilità e debugging.
