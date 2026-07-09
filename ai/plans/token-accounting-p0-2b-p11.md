# Plan — token-accounting-p0-2b-p11

> Corrisponde a: `codex_upgrade_plan.md` punto **P0-2b** ("Token accounting incompleto",
> priorità Alta, area Token/costo/audit) e `claude_upgrade_plan.md` punto **P11**
> ("Token accounting completo, preciso e aggregabile", priorità Media, area audit/costo).
> Le due review indipendenti convergono sullo stesso problema con evidenze quasi
> identiche; questo piano le unifica dopo verifica diretta del codice a `main` (0.6.1).
>
> Stato: piano tecnico, nessun codice applicativo modificato in questa fase
> (PROJECT_SPEC §6 — Claude non implementa in questo comando).

## Revision log

- **v1** (prima review): design iniziale su 5 punti (TokenUsage canonico,
  colonne SQLite + tabella `request_token_usage`, embedder, proxy `usage`,
  SDK). Sottoposto a `/ai-review-plan-with-codex` → **verdetto BLOCK**, 5
  finding bloccanti (report:
  `ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260701-144844.md`).
- **v2** (questa versione): risolve i 5 blocking. Decisioni di prodotto prese
  dall'utente per sbloccare il design (vedi "Product decisions" sotto).
  Scoperto un **bug pre-esistente e indipendente** durante la revisione
  (sezione "Design — Decisione 4 estesa"): `SpeculativeOverlapHandle.abandon()`
  perde `run_id`/`request_id` nel thread daemon perché non propaga il contesto
  — l'evento della speculativa scartata non è mai persistito oggi. Corretto
  come prerequisito dello stesso fix (stesso file, stesso PR).
- **v3** (questa versione): la seconda review Codex
  (`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260701-155739.md`)
  ha dato **BLOCK di nuovo**, con 5 finding: 3 dei 5 blocking originali
  restavano sostanzialmente aperti (refusal generation puntava al file
  sbagliato, `billable_provider_call` mancava un sito, la race speculative
  non era davvero chiusa dal meccanismo v2), un finding era solo parziale
  (retry falliti nei moduli runtime non contati), e v2 aveva introdotto
  un'incoerenza interna nuova (semantica zero-token contraddittoria tra
  Decisione 1 e Product decision 4). Risolti con: 3 nuove decisioni di
  prodotto (vedi sotto) + una mappatura mirata che ha corretto lo scope del
  fix refusal generation, completato l'elenco dei siti non-billable, e
  progettato l'accumulo dei retry falliti nei 4 moduli runtime. Il
  meccanismo di contatore/finalizzazione differita introdotto in v2 per
  BLOCKING 1 è stato **rimosso** (non chiudeva davvero la race, come
  confermato da Codex) e sostituito da un solo fix mirato (propagazione
  contesto) + una Product decision che accetta esplicitamente `usage`
  parziale in quel caso specifico.
- **v4** (questa versione): la terza review Codex
  (`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260701-163343.md`)
  ha dato **BLOCK di nuovo**: 3 dei 5 blocking originali risultavano chiusi,
  ma 3 nuovi finding bloccanti sono emersi: (D) `quick_check()` del critic
  non contabilizzato in nessun branch, (E) tre sotto-percorsi con copertura
  token dubbia (simulator "seeded" retry, hindsight "individual evaluation"
  a zero token, granularità perspective per-call vs aggregata), (F) la
  tabella `request_token_usage` descritta come "totale autoritativo" quando
  in realtà è un riepilogo sincrono best-effort e potenzialmente parziale.
  Decisioni di prodotto prese dall'utente: **non restringere lo scope**
  (coprire ogni sotto-percorso, non solo i principali) e **rinominare
  esplicitamente** il riepilogo per riflettere la sua vera natura best-effort
  (vedi "Product decisions v4" sotto). Risolti con: fix di persistenza in
  `critic_module.py` per `quick_check()` (BLOCKING D); estensione del
  pattern retry-falliti già usato per gli altri moduli al percorso simulator
  "seeded" (BLOCKING E-a); un **bug numerico pre-esistente e indipendente
  scoperto in questa sessione** — `HindsightResult.tokens_used` resta
  sistematicamente `0` sul percorso "individual evaluation" (default quando
  `len(consequences) == 1`) nonostante chiamate LLM reali, perché
  `HindsightEvaluation` non ha mai propagato i token (BLOCKING E-b);
  arricchimento di `parsed_summary_json` con un breakdown per-prospettiva
  per Perspectives, senza nuove righe di audit (BLOCKING E-c, design
  aggregato confermato corretto); rinomina di `request_token_usage` a
  "riepilogo sincrono best-effort" con nuovo flag
  `usage_may_be_incomplete`/`incomplete_reason` calcolato in modo sincrono
  (unica condizione nota: `abandon()` invocato per la richiesta corrente),
  e promozione della query di ricostruzione SQL su `llm_calls` a fonte più
  completa disponibile per l'audit tardivo/offline (BLOCKING F; terminologia
  "canonica"/"garanzia di completezza" della v4 corretta in v6, BLOCKING
  v6-2 — stesso design, solo linguaggio più accurato).
- **v5**: la quarta review Codex
  (`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260702-092750.md`)
  ha dato **BLOCK di nuovo**: i 3 blocking chiusi in v4 (D/E/F) restavano
  chiusi, ma sono emersi **6 nuovi finding bloccanti**, causati dai fix v4
  stessi o da lacune non ancora coperte: (v5-1) la speculative call
  eseguita in `_speculative_generate` non passava mai `token_usage_json` a
  `persist_kwargs`, quindi restava priva di token sia sull'esito used sia
  discarded, anche dopo il fix di propagazione contesto di BLOCKING 1;
  (v5-2) `TokenUsage.combine()` poteva azzerare conteggi reali già sommati
  numericamente quando un solo componente aggregato era `"missing"`, per
  via della regola di nullabilità di `to_json()` legata al solo `source`;
  (v5-3) il Goal/Target behavior del piano contraddiceva testualmente il
  design aggregato già accettato in E-c (correzione solo terminologica,
  nessun cambio di design — **decisione di prodotto dell'utente**:
  mantenere 1 riga per invocazione di modulo); (v5-4) diversi payload
  pianificati per retry falliti e refusal generation non includevano
  `model`, con l'aggravante che `write_queue.py` distingue tra chiave
  assente (default `""`) e chiave presente con valore `None` (che
  sovrascrive il default con `None`); (v5-5) `usage_may_be_incomplete` non
  copriva il path implicito `shutdown_executor()` → `abandon()`, verificato
  come **limite residuo accettato** (non chiudibile senza refactoring
  sproporzionato o un `UPDATE` posteriore, entrambi fuori scope), non un
  bug risolvibile nel design corrente; (v5-6) la sequenza PR lasciava le
  due colonne v4 di `request_token_usage` fuori dalla `CREATE TABLE`
  originale nonostante il testo dicesse il contrario. Risolti con: fix di
  cattura token nella speculative call (v5-1, solo `controller.py`);
  riformulazione della condizione di nullabilità di `to_json()` in termini
  di `total_tokens==0 AND source=="missing"` invece di solo `source`
  (v5-2, nessuna regressione su Product decision 4 per il caso
  single-result); correzione mirata di Goal/Target behavior/Acceptance
  criteria, design invariato (v5-3); normalizzazione di `model` su tutti i
  payload pianificati mancanti + pattern `str(getattr(self.policy,
  "model", "") or "")` uniforme, mai `None` esplicito (v5-4);
  documentazione esplicita del limite residuo in R21 + nuovo acceptance
  criterion, nessun codice nuovo richiesto — `mark_request_usage_partial()`
  già pianificata in BLOCKING F gira comunque nel path implicito,
  semplicemente senza effetto osservabile per la richiesta già
  finalizzata (v5-5); consolidamento delle 2 colonne v4 direttamente
  nella `CREATE TABLE` della Decisione 2, rimozione della migrazione
  separata di BLOCKING F (v5-6).
- **v6** (questa versione): la quinta review Codex
  (`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260702-101838.md`)
  ha dato **BLOCK di nuovo**: tutti e 6 i BLOCKING v5 risultavano chiusi, ma
  sono emersi **2 nuovi finding bloccanti**: (v6-1) le righe `llm_calls` di
  refusal generation emesse quando `self.policy`/`policy` è `None`
  (`RefusalGenerationResult.attempts == 0`, nessuna chiamata al provider mai
  avvenuta) restavano `billable_provider_call=True` per default in
  `refusal_handler.py`/`final_revalidation.py`, gonfiando il totale
  fatturabile; (v6-2) il piano dichiarava la query di ricostruzione
  `SUM(...) FROM llm_calls` "fonte canonica/garanzia di completezza per
  billing/compliance", in tensione con l'invariante §5.6 (observability
  best-effort) dato che `llm_calls` condivide la stessa coda lossy di
  `request_token_usage` (già correttamente riconosciuta come non garantita,
  R1). Risolti con: `billable_provider_call=refusal_result.attempts > 0`
  sui due call site già toccati da BLOCKING 4/v5-4 (v6-1, verificato che
  `response_assembler.py` non è affetto — il blocco è già interamente
  condizionato a `self.policy is not None`); declassamento del linguaggio
  da "canonica/garanzia di completezza" a "fonte più completa disponibile
  tra le righe effettivamente persistite" in ogni punto del piano che
  descrive quella query — **decisione di prodotto dell'utente**: nessun
  cambio di design/persistenza, nessuna nuova PR per rendere la coda
  non-lossy (P2 resta esplicitamente fuori scope, R1 esteso per coprire
  anche questa query) (v6-2).
- **v7** (questa versione): la sesta review Codex
  (`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260702-110902.md`)
  ha chiuso i 2 BLOCKING v6 ma dato **BLOCK di nuovo**, con **3 nuovi
  finding bloccanti**, tutti fix di coerenza interna del piano (nessuna
  nuova decisione di prodotto tranne una): (v7-1) la derivazione delle
  colonne numeriche `llm_calls` (Decisione 2) usava una regola di
  nullabilità diversa da quella già corretta per `to_json()` (BLOCKING
  v5-2), sottocontando esattamente gli aggregati misti che v5-2 doveva
  preservare; (v7-2) la query di breakdown per modulo/modello (Query
  target) mancava del predicato billable già richiesto altrove nel piano,
  e lo pseudocodice dell'hook accumulatore (`ObservabilityService.emit()`,
  Decisione 4) non era mai stato allineato alla prosa di BLOCKING 2 che ne
  descriveva già il filtro; (v7-3) il fix `model` di BLOCKING v5-4 copriva
  solo i payload nuovi (retry/refusal), lasciando 5 righe billable
  pre-esistenti (benign fast-path, safe-complete, fast-path,
  compliance-regenerate, draft revalidation) senza `model`, più un bug
  pre-esistente indipendente in `_speculative_generate` (stessa insidia
  chiave-presente-vs-`None` già vista in v5-4, mai chiusa in quel punto
  specifico). Risolti con: derivazione delle colonne numeriche resa
  provabilmente equivalente a `to_json()` (v7-1); predicato billable
  aggiunto alla query di breakdown e allo pseudocodice `emit()`/
  `emit_batch()`, con definizione esplicita che
  `llm_call_count`/`missing_usage_count`/`estimated_usage_count` contano
  solo righe billable (v7-2); pattern uniforme `str(getattr(self.policy,
  "model", "") or "")` applicato ai 4 siti generici + un'eccezione motivata
  per `_revalidate_draft` (usa `get_dccl_llm_model()`, il modello
  effettivamente invocato via `model_override`, non `self.policy.model`) +
  fix del bug pre-esistente in `_speculative_generate` nello stesso punto
  già toccato da BLOCKING v5-1 (v7-3). Durante v7-3 è emerso un bug più
  ampio, fuori dal perimetro dei 6 call site delegati:
  `_policy_llm_model_for_action`/`_module_model`
  (`deliberation_runner.py:200-215`), condivisi da ~13 call site billable
  pre-esistenti (inclusi i core critic/simulator/hindsight/perspectives),
  ritornano `None` esplicito con la stessa insidia — **decisione di
  prodotto dell'utente**: includerlo nello scope di v7 (fix a costo minimo,
  2 funzioni ritornano `""` invece di `None`, un solo punto tocca tutti i
  13 siti — coerente con Product decision 1/8, non restringere lo scope).
  Chiusa anche una QUESTION residua di Codex sul default di
  `RefusalGenerationResult.token_usage` (vincolo di design esplicitato in
  BLOCKING 4, nessuna modifica alla sezione Tests).
- **v8** (questa versione, PROJECT_SPEC §9 — correzione, non nuova
  decisione di prodotto): la settima review Codex
  (`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260702-120013.md`)
  ha dato **BLOCK**, trovando **un errore di implementazione mio nello
  snippet v7** per l'estensione sistemica `_policy_llm_model_for_action`/
  `_module_model` (Product decision v7/12): lo snippet proposto sostituiva
  interamente il corpo delle 2 funzioni invece di normalizzare solo il
  confine "nessun modello disponibile", perdendo logica di business reale
  già esistente — il branch `rewrite_model` per `action=="rewrite"`
  (`_policy_llm_model_for_action`) e il lookup a due livelli
  `module.policy.model` → `module.model` (`_module_model`), verificato
  contro `models/policy.py:143,162,489` e i 4 moduli runtime core.
  Corretto leggendo per la prima volta il corpo reale delle 2 funzioni
  (`deliberation_runner.py:200-226`) e producendo un fix chirurgico che
  cambia **solo** i 2 `return None` finali per funzione in `return ""`,
  preservando firma/parametro/branch/lookup esistenti. Aggiunti 2
  acceptance criteria dedicati (protezione del Goal "per modello
  effettivo"). La Product decision v7/12 (includere il fix in questo
  scope) resta valida — cambia solo l'implementazione.
- **Prossimo passo**: ri-sottoporre a `/ai-review-plan-with-codex
  ai/plans/token-accounting-p0-2b-p11.md` (ottava review).

### Product decisions v7 (prese dall'utente dopo il sesto verdetto BLOCK)

12. **Estensione del fix `model` a `_policy_llm_model_for_action`/
    `_module_model` (BLOCKING v7-3)**: incluso nello scope di v7 — le 2
    funzioni condivise da ~13 call site billable pre-esistenti (inclusi i
    moduli core critic/simulator/hindsight/perspectives) vengono corrette a
    ritornare `""` invece di `None`, chiudendo un gap sistemico sulla
    superficie billable con un fix a costo minimo (un solo punto di
    modifica).

### Product decisions v6 (prese dall'utente dopo il quinto verdetto BLOCK)

11. **Linguaggio di completezza per la query di ricostruzione (BLOCKING
    v6-2)**: declassato da "fonte canonica/garanzia di completezza per
    billing/compliance" a "fonte più completa disponibile tra le righe
    effettivamente persistite" — nessun cambio di design/persistenza,
    nessuna nuova PR per portare in scope la persistenza durevole P2
    (rimane esplicitamente fuori perimetro di questo piano).

### Product decisions v5 (prese dall'utente dopo il quarto verdetto BLOCK)

10. **Terminologia dell'unità di audit (BLOCKING v5-3)**: confermato il
    design aggregato già accettato in BLOCKING E-c (1 riga `llm_calls` per
    invocazione di modulo/tentativo, non per round-trip HTTP al provider).
    Corretta **solo** la formulazione di Goal/Target behavior/Acceptance
    criteria per eliminare la contraddizione testuale segnalata da Codex —
    nessun nuovo comportamento di persistenza, nessuna nuova riga per
    round-trip provider.

### Product decisions v4 (prese dall'utente dopo il terzo verdetto BLOCK)

8. **Coprire ogni sotto-percorso di chiamata LLM, non restringere lo scope**:
   anche i percorsi opt-in o meno frequenti (simulator "seeded",
   `quick_check()` fast-path, hindsight "individual evaluation") vanno
   corretti o esplicitamente giustificati come non-bug, non lasciati
   fuori perimetro solo perché meno comuni del percorso principale.
9. **Rinominare esplicitamente `request_token_usage`/"totale autoritativo"**:
   il riepilogo per-richiesta va descritto come sincrono, best-effort e
   potenzialmente parziale, non come dato autoritativo — con un flag
   esplicito di incompletezza nota, non un meccanismo di attesa/contatore.

### Product decisions v3 (prese dall'utente dopo il secondo verdetto BLOCK)

5. **Usage parziale accettato per le speculative call scartate**: quando la
   contabilizzazione della speculativa scartata si risolve dopo che
   `_finalize_token_accounting` è già girato, il campo `usage` della
   risposta HTTP può non includerla — nessuna attesa bloccante, nessuna
   latenza aggiunta ai path refuse/safe_complete. Il dato resta comunque
   persistito correttamente in `llm_calls` per l'audit/la ricostruzione SQL.
   Eccezione esplicitamente documentata alla regola generale (Product
   decision 1), non un difetto residuo. Ha permesso di **rimuovere** il
   meccanismo di contatore/finalizzazione differita/timer introdotto in v2
   per BLOCKING 1 (si è rivelato comunque insufficiente a chiudere la race,
   secondo Codex — la soluzione più semplice era accettare il limite,
   non costruire un meccanismo più complesso).
6. **I retry falliti nei moduli runtime (critic/simulator/hindsight/
   perspective) vanno inclusi nel conteggio token**: un tentativo che fallisce
   il parsing/la validazione ha comunque consumato token reali dal provider
   e va sommato, non scartato — coerente con la Product decision 1 (onestà
   di costo reale).
7. **`GovernedResponse.usage` (SDK legacy) resta invariato/`None`** per
   risposte governate; `GovernanceMetadata` è l'unica superficie SDK
   supportata per il token accounting. Nessuna azione di codice richiesta
   su questo punto — è il comportamento già esistente e coerente con
   l'invariante che il client wrappato non genera mai la risposta finale.

### Product decisions (prese dall'utente dopo il verdetto BLOCK)

1. **Scope di `usage` nel proxy**: include TUTTE le chiamate LLM interne
   effettuate per servire la richiesta (speculative scartate, embedding,
   retry di refusal, revalidation), non solo quelle che hanno contribuito al
   testo consegnato. Motivazione: onestà di audit/costo reale sostenuto dal
   sistema.
2. **Righe di audit non-provider** (dedup output-protection, riuso
   speculativo senza nuova chiamata LLM): restano in `llm_calls` con un
   discriminatore esplicito `billable_provider_call: bool`, non spostate in
   `orchestration_events`.
3. **Metadata a livello di richiesta con source misti**: bastano i conteggi
   (`missing_usage_count`, `estimated_usage_count`); nessun campo aggiuntivo
   di stato aggregato tipo `overall_source`.
4. **`total_tokens==0` vs usage mancante**: se il provider ha restituito un
   oggetto `usage` (anche con valori a 0), ci si fida — marcato `exact` (se
   c'è lo split) o `estimated` (se manca lo split ma total=0). `"missing"` è
   riservato SOLO al caso in cui l'intero oggetto `usage` è assente/`None`.
   Questo principio si applica in modo uniforme anche a
   `TokenUsage.from_generation_result`/`_token_usage_json_from_result`
   (vedi Decisione 8): `to_json()` è `None` **se e solo se** `source ==
   "missing"`, indipendentemente dal fatto che i conteggi numerici siano
   zero — un `source` esplicito non-`missing` non collassa mai a `None`. È
   un affinamento intenzionale rispetto al comportamento legacy di
   `GenerationResult.token_usage_json()` (che guardava solo i conteggi
   grezzi); nessun test esistente pin quel comportamento su
   `_token_usage_json_from_result` (il test-locking esistente,
   `tests/test_orchestrator.py:1630-1642`, verifica solo la propagazione dei
   conteggi con un oggetto privo dell'attributo `source`, non il caso
   zero-con-source-esplicito) — quindi nessuna regressione, solo un
   comportamento nuovo da lockare esplicitamente con un test dedicato (vedi
   sezione Tests).

   > **Correzione v5 (BLOCKING v5-2)**: la regola sopra resta invariata per
   > il caso `GenerationResult` singolo (comportamento identico, nessuna
   > regressione — se `source=="missing"` per un singolo risultato i
   > conteggi sono per costruzione sempre zero). Per `TokenUsage.combine()`
   > (Decisione 8, BLOCKING 3), la condizione di nullabilità di `to_json()`
   > è riformulata come `total_tokens==0 AND source=="missing"` (non
   > `source` da solo) — questa è una **correzione**, non un nuovo
   > comportamento arbitrario: la formulazione originale del combine()
   > causava perdita silenziosa di conteggi reali già sommati ogni volta
   > che anche un solo componente combinato era `"missing"` (bug scoperto
   > dalla quarta review Codex, verificato riproducibile su
   > `EnsembleResult`/perspectives, `combine()` di refusal, simulator
   > seeded, hindsight individual — ogni sito che usa `TokenUsage.combine()`
   > in questo piano).

---

## Goal

Rendere il token accounting completo, corretto ed esplicito (esatto / stimato / mancante)
a livello di singola chiamata LLM, modulo, modello e richiesta, ed eliminare il campo
`usage` hardcoded a zero nel proxy OpenAI-compatible. Per ogni domanda/risposta deve
essere calcolabile in modo affidabile:

- token input/output/total per **riga di audit `llm_calls`** (una per
  tentativo/invocazione di modulo distinta — inclusi i retry falliti — non
  necessariamente una per round-trip HTTP al provider: quando un modulo
  esegue più chiamate interne in una singola invocazione, es. perspectives
  per-prospettiva o refusal con retry anti-leak, la riga aggregata riporta
  la somma reale di tutte le chiamate sottostanti, vedi Decisione 8,
  BLOCKING E-c);
- token per modulo (`risk_intent`, `risk_signal`, `risk_operational`, `dccl`,
  `policy_generate`, `policy_rewrite`, `critic`, `simulator`, `perspectives`,
  `hindsight`, `embedder`, `constitution_retriever`, ecc.);
- token per modello effettivo;
- token totali della singola request (`run_id` + `request_id`), distinguendo
  valori esatti, stimati e mancanti;
- il proxy OpenAI-compatible deve restituire un campo `usage` reale, non zero.

## Current behavior (verificato in sessione, file:riga)

- `GenerationResult.token_usage_json()` (`moralstack/models/base.py:123-134`) serializza
  `{prompt_tokens, completion_tokens, total_tokens}` o `None` — nessun campo che
  distingua "stimato" da "esatto".
- `OpenAIPolicy._complete()` (`moralstack/models/policy.py:239-249`) legge
  `response.usage`; se `prompt_tokens`/`completion_tokens` mancano fa fallback
  stimato 70/30 (righe 243-245) **senza propagare alcun flag**. Il 5-tuple di
  ritorno è consumato in 2 call site (`policy.py:~330-349`, `~385-405`).
- `moralstack/constitution/retriever.py` ha **3 implementazioni duplicate e
  indipendenti** dello stesso fallback 70/30 (righe ~599-608, ~858-867, ~1037-1046),
  usate solo per alimentare `TokenCostTracker`. **Bug scoperto in questa sessione,
  non presente nei due documenti di partenza**: il valore calcolato non viene mai
  passato a `_persist_constitution_llm_call` (firma `retriever.py:56-69`, call site
  righe 654, 909, 1062) — il modulo `constitution_retriever` non ha **mai**
  `token_usage_json` in `llm_calls`.
- `OpenAIEmbedder.embed()` (`moralstack/orchestration/embedder.py:238-266`) chiama
  `embeddings.create()` ma legge solo `.data[0].embedding`, mai `response.usage`.
  `LocalEmbedder`/`HashingEmbedder` (stesso file, righe 109-188) sono il **default
  attuale** (commit "feat(embedder): add local embedder as default provider") e
  non consumano token fatturabili di alcun provider esterno.
- `moralstack/orchestration/ledger.py` chiama `_embedder.embed()` due volte per
  turno (lookup `:267`, store `:367`) senza mai emettere `EVENT_LLM_CALL`.
- Esistono **3 funzioni duplicate** che costruiscono l'envelope `EVENT_LLM_CALL`:
  `async_persist_llm_call` (`persistence/write_queue.py:36-73`), `persist_llm_call`
  (`persistence/sink.py:50-116`), e l'analogo in `models/risk/estimator.py`
  (`_obs_route`/`_obs_route_batch`, righe 64-75). Tutte confluiscono in
  `ObservabilityService.emit()`/`.emit_batch()` (`observability/service.py:43-57`)
  — l'unico vero collo di bottiglia comune a ogni chiamata LLM del sistema.
- `observability/sinks/sqlite_sink.py`: tabella `llm_calls` (righe 138-217) ha
  solo `token_usage_json TEXT`, popolata da `_write_llm_call_single` (righe
  1769-1810) e `_write_llm_call_batch` (righe 1938-1990) via `_LLM_CALLS_INSERT`
  (righe 579-586) — **nessuna colonna numerica interrogabile via SQL**. Esiste
  già un pattern di migrazione additiva idempotente in `init_db()` (righe
  643-713, `ALTER TABLE ... ADD COLUMN` in try/except).
- `observability/read_store.py` (Protocol righe 34-100) non ha alcun metodo di
  aggregazione sui token.
- `orchestration/types.py::ResponseMetadata` (righe 235-284, dataclass non
  frozen) non ha campi token.
- `orchestration/controller.py::_attach_trace_and_return` (righe 312-320) è il
  **funnel unico di uscita** di `process()`: 11 call site (righe 1429, 1577,
  1628, 1666, 1702, 1748, 2018, 2614, 2616, 2633, 2662) passano tutti da qui
  prima del `return`.
- `server/proxy.py::_build_synthetic_chat_completion` (righe 161-193) ritorna
  `usage` hardcoded `{0,0,0}` (righe 188-192) sia nel path di successo (righe
  396-401) sia nel path di fallimento fail-closed (righe 359-364) — **mai**
  legge `result.response.metadata`.
- `sdk/response.py::GovernanceMetadata` (righe 24-141) non ha campi token.
- `utils/cost_tracker.py::TokenCostTracker` è wirato solo nella CLI con
  `--verbose` (`cli/shell.py:159-166`), non nel proxy.
- Rischio strutturale collegato (P2, persistenza — **fuori scope**, solo
  documentato): `observability/write_queue.py:175-181` scarta eventi su
  `queue.Full` con un drop-marker parziale (`:190-260`) che non identifica
  quali eventi specifici sono persi.

## Target behavior

1. Ogni invocazione di modulo che consuma token (policy, mini-estimatori di
   rischio, critic, simulator, perspectives, hindsight, DCCL,
   constitution_retriever, embedder OpenAI) produce almeno una riga di
   audit `llm_calls` con provenienza esplicita: `exact` (dal provider),
   `estimated` (fallback 70/30, ora marcato), `missing` (nessun dato),
   `unknown` (righe storiche pre-migrazione senza il campo). Quando
   un'invocazione di modulo esegue internamente più round-trip reali verso
   il provider (perspectives: 1 per prospettiva; simulator "seeded": 1 per
   seed; refusal generation: fino a 2 con retry anti-leak), la riga
   aggregata riporta la **somma numerica reale** di tutte le chiamate
   sottostanti, non un record separato per round-trip (design confermato,
   Decisione 8/BLOCKING E-c e Product decision v5/10) — `llm_call_count`
   nella tabella `request_token_usage` conta **righe di audit billable**
   (`billable_provider_call=True`, default se assente), non round-trip HTTP
   né righe diagnostiche non-billable (vedi nota su quel campo, Decisione 2,
   estesa in v7 BLOCKING v7-2).
2. `llm_calls` ha colonne numeriche interrogabili (`input_tokens`,
   `output_tokens`, `total_tokens`, `token_usage_missing`,
   `token_usage_estimated`) oltre al JSON esistente (retro-compatibile).
3. Una nuova tabella `request_token_usage` fornisce un **riepilogo sincrono,
   best-effort e potenzialmente parziale** per singola richiesta
   (`run_id`+`request_id`), scritto una sola volta a fine richiesta da un
   accumulatore in-process, con un flag esplicito `usage_may_be_incomplete`
   quando una condizione nota e verificabile in modo sincrono lo rende
   incompleto per costruzione (BLOCKING F, v4). Per l'audit tardivo/offline,
   la **fonte più completa disponibile tra le righe effettivamente
   persistite** resta la query di ricostruzione `SUM(...) FROM llm_calls
   WHERE run_id=? AND request_id=? AND COALESCE(billable_provider_call,1)=1`
   — non questa tabella (vedi Decisione 4 rivista). **Non è una garanzia
   assoluta di completezza** (BLOCKING v6-2): le righe `llm_calls`
   condividono la stessa coda best-effort lossy di ogni altro evento
   osservabilità (`observability/write_queue.py`, §5.6, rischio P2/R1) — è
   semplicemente più granulare e quindi statisticamente più resiliente alla
   perdita di una singola riga rispetto al riepilogo a riga singola.
4. Il proxy OpenAI-compatible restituisce un campo `usage` reale (letto da
   `result.response.metadata`), non più hardcoded.
5. `GovernanceMetadata` (SDK) espone gli stessi totali.
6. `constitution_retriever` ed `embedder` (solo `OpenAIEmbedder`) smettono di
   essere buchi invisibili nel breakdown per modulo.

## Assumptions

- A1: `moralstack.orchestration.*` è sotto `mypy --strict` (`pyproject.toml:140-141`)
  — ogni file toccato in quel package deve restare strict-clean. **Confermata
  da Codex.**
- A2 (**corretta dopo il verdetto BLOCK — era falsa come scritta**):
  `ProcessedRequest.request_id` (`orchestration/types.py:194,198`) è un
  campo mutabile con un default generato (`uuid.uuid4()`), ma **non
  garantito univoco da solo** — un chiamante può fornire un `request_id`
  esplicito, e lo schema SQLite usa `(run_id, request_id)` come chiave
  composita (`sqlite_sink.py:99-136, 207-216`), non `request_id` da solo. Per
  questo l'accumulatore usa la tupla `(run_id, request_id)` come chiave
  (BLOCKING 5, Decisione 8), non `request_id` isolato.
- A3: `set_current_run_id()`/`set_current_request_id()` sono già impostati prima
  che qualunque `record_llm_call`/`persist_llm_call` produca un evento
  persistito (precondizione preesistente, non nuova).
- A4 (**corretta dopo il verdetto BLOCK — vera solo come garanzia
  best-effort, non assoluta**): nel path proxy il contesto di persistenza
  non è *sempre* attivo: `_initialize_observability_run()`
  (`server/proxy.py:617-654`) ritorna stringa vuota quando l'osservabilità
  non è configurata (nessun `db_path` e modalità diversa da
  `file_only`/`db_only`/`dual`, righe 647-649) — in quel caso
  `set_current_run_id()` (riga 650) non viene mai chiamato, e
  `_ensure_request_row` (righe 657-681) ritorna immediatamente senza
  persistere nulla quando `proxy_run_id` è vuoto (righe 680-681). Il design
  resta corretto in questa configurazione (nessun crash, `usage` a `{0,0,0}`
  di default) grazie ai guardrail `if not run_id or not request_id: return`
  già presenti in ogni funzione dell'accumulatore — ma va trattata come
  garanzia best-effort, non assoluta.
- A5: `OpenAIEmbedder` è opt-in, non il default (default = `LocalEmbedder`,
  verificato in `embedder.py:158-188`); `LocalEmbedder`/`HashingEmbedder` non
  consumano token fatturabili e sono intenzionalmente esclusi
  dall'instrumentazione.

## Constraints

- Invariante §5.6 (Observability never breaks the request): ogni nuovo hook
  (accumulatore, parsing JSON, emit) deve essere avvolto in try/except che non
  propaga, senza I/O bloccante sincrono nel path di generazione/proxy.
- Nessun'altra invariante P0 è toccata: `final_action` (§5.1), system prompt
  (§5.2), hard-signal (§5.3), `core` overlay (§5.5), Governed-delivery (§5.7) —
  il proxy continua a non generare mai la risposta consegnata dal client
  wrapped, cambia solo il valore del campo `usage` nel payload sintetico già
  esistente.
- Fuori scope esplicito: P0-1 (DCCL bypass hard-signal), P0-2/P2 (persistenza
  audit-grade generale — solo documentata la dipendenza, non risolta), P0-3
  (correlation store), P0-4 (multi-turn safety).
- Minimal diff (PROJECT_SPEC §6): niente rinominazioni di campi esistenti su
  `GenerationResult`/`ResponseMetadata`; solo aggiunte additive con default
  retro-compatibili.
- mypy strict su `moralstack.orchestration.*`: `controller.py`, `types.py`,
  `embedder.py`, `deliberation_runner.py`, `persistence_helpers.py` restano
  pienamente tipizzati.
- Documentazione (§8): `docs/MORALSTACK_CODEBASE_INDEX.md`,
  `docs/CODEBASE_FACTS.md`, `docs/modules/observability.md`,
  `docs/modules/persistence.md`, aggiornati nello stesso PR del comportamento
  che descrivono.

## Design

### Decisione 1 — Rappresentazione canonica: `TokenUsage`

Nuovo file **`moralstack/observability/token_usage.py`** (modulo leaf, zero
dipendenze interne oltre `json`/`dataclasses`/`typing`; `moralstack.observability`
non importa nulla da `moralstack.models`/`moralstack.constitution` → nessun ciclo):

```python
TokenUsageSource = Literal["exact", "estimated", "missing", "unknown"]

@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    source: TokenUsageSource

    def to_json(self) -> str | None:
        """None sse (total_tokens == 0 AND source == 'missing') — corretto
        in v5 (BLOCKING v5-2). Per un GenerationResult singolo questo è
        comportamentalmente identico a v1-v4 (source=='missing' implica per
        costruzione total_tokens==0 in from_generation_result/
        from_openai_usage — nessuna regressione su Product decision 4). Per
        combine() (Decisione 8, BLOCKING 3) questo evita che un solo
        componente aggregato 'missing' azzeri conteggi reali già sommati da
        altri componenti: il totale aggregato viene emesso finché
        total_tokens > 0, anche se source resta 'missing' come etichetta di
        bassa confidenza (segnala che il totale potrebbe essere una
        sottostima)."""

    @classmethod
    def from_openai_usage(cls, usage: Any | None, *, is_embedding: bool = False) -> "TokenUsage":
        """Centralizza il fallback 70/30, ora sempre marcato 'estimated'.
        is_embedding=True: output_tokens forzato a 0 (embeddings API non hanno completion).
        usage is None -> source='missing' (nessun oggetto usage dal provider).
        usage presente con total_tokens==0 e nessuno split -> source='estimated'
        (NON 'missing': l'oggetto usage esiste, ci si fida — Product decision 4,
        vedi Revision log). 'missing' è riservato esclusivamente al caso
        usage is None."""

    @classmethod
    def from_json(cls, s: str | None) -> "TokenUsage":
        """Tollerante a JSON legacy senza 'source' -> source='unknown'.
        s is None -> TokenUsage(0, 0, 0, 'missing') (round-trip esatto con
        to_json(): sse total_tokens==0 AND source=='missing' -> to_json()
        ritorna None, quindi from_json(None) deve ricostruire esattamente
        quello stato, non un default arbitrario — BLOCKING v7-1)."""
```

Motivazione: oggi la stessa logica di fallback 70/30 è duplicata 4 volte
(`policy.py` + 3× `retriever.py`) con la stessa lacuna (nessun flag) — sintomo
diretto della mancanza di questo tipo. `TokenUsage` diventa l'unico punto di
verità per lo shape JSON; il campo `source` è additivo e retro-compatibile
(consumer esistenti che leggono solo le prime 3 chiavi non se ne accorgono).

### Decisione 2 — Persistenza per-call: colonne numeriche + migrazione idempotente

In `observability/sinks/sqlite_sink.py`:

- Aggiungere al loop di migrazione esistente (dopo riga ~702, stesso pattern
  try/except):
  ```python
  ("input_tokens", "ALTER TABLE llm_calls ADD COLUMN input_tokens INTEGER"),
  ("output_tokens", "ALTER TABLE llm_calls ADD COLUMN output_tokens INTEGER"),
  ("total_tokens", "ALTER TABLE llm_calls ADD COLUMN total_tokens INTEGER"),
  ("token_usage_missing", "ALTER TABLE llm_calls ADD COLUMN token_usage_missing INTEGER"),
  ("token_usage_estimated", "ALTER TABLE llm_calls ADD COLUMN token_usage_estimated INTEGER"),
  ```
- Nuovo indice: `CREATE INDEX IF NOT EXISTS idx_llm_calls_module_model ON
  llm_calls(run_id, request_id, module, phase, action, model)`.
- `_LLM_CALLS_INSERT` (righe 579-586): append delle 5 colonne.
- `_write_llm_call_single`/`_write_llm_call_batch`: calcolare
  `usage = TokenUsage.from_json(payload.get("token_usage_json"))` e derivare le
  colonne con la **stessa** condizione di `to_json()` (BLOCKING v5-2), corretto
  in v7 (BLOCKING v7-1) — non una regola indipendente basata solo su `source`:
  ```python
  usage = TokenUsage.from_json(payload.get("token_usage_json"))
  if usage.total_tokens == 0 and usage.source == "missing":
      input_tokens = output_tokens = total_tokens = None
  else:
      input_tokens, output_tokens, total_tokens = (
          usage.input_tokens, usage.output_tokens, usage.total_tokens,
      )
  token_usage_missing = 1 if usage.source == "missing" else 0
  token_usage_estimated = 1 if usage.source == "estimated" else 0
  ```
  Le colonne numeriche sono quindi `NULL` **esattamente** quando
  `token_usage_json` è `NULL` — le due rappresentazioni (JSON e colonne) sono
  ora provabilmente equivalenti (BLOCKING v7-1), chiudendo il gap tra
  "preservazione JSON" (v5-2) e "preservazione SQL numerica" (v7-1).

  > **Nota di interpretazione — `token_usage_missing` su aggregati misti**:
  > per una riga non aggregata, `token_usage_missing=1` implica sempre
  > `total_tokens==0` (nessuna ambiguità, Product decision 4). Per una riga
  > aggregata (ogni sito che usa `TokenUsage.combine()`, Decisione 8/BLOCKING
  > 3/E), `token_usage_missing=1` può coesistere con `total_tokens>0`:
  > significa che almeno una delle chiamate sottostanti non ha riportato
  > alcun `usage` dal provider, mentre le altre sì — il totale numerico
  > riportato è la somma reale delle componenti note, non un totale
  > sconosciuto. Interpretazione per un consumer/dashboard: "sottostima
  > potenziale rispetto al consumo reale, non valore inaffidabile" —
  > `total_tokens` resta un lower bound verificato, mai un valore inventato.
  > Nessun campo aggiuntivo introdotto (coerente con Product decision 3).
- Nuova tabella (stile identico a `llm_calls`; **schema consolidato in v5,
  BLOCKING v5-6** — le 2 colonne `usage_may_be_incomplete`/
  `incomplete_reason`, originariamente introdotte da BLOCKING F/v4 come
  aggiunta "successiva", sono incluse fin da subito nella definizione
  perché la tabella è nuova in questo stesso piano e non esiste ancora in
  nessun DB di produzione — nessuna migrazione `ALTER TABLE` separata è
  necessaria per queste 2 colonne):
  ```sql
  CREATE TABLE IF NOT EXISTS request_token_usage (
      run_id TEXT NOT NULL,
      request_id TEXT NOT NULL,
      input_tokens INTEGER NOT NULL DEFAULT 0,
      output_tokens INTEGER NOT NULL DEFAULT 0,
      total_tokens INTEGER NOT NULL DEFAULT 0,
      llm_call_count INTEGER NOT NULL DEFAULT 0,  -- righe di audit, non round-trip HTTP (v5, BLOCKING v5-3, vedi nota sotto)
      missing_usage_count INTEGER NOT NULL DEFAULT 0,
      estimated_usage_count INTEGER NOT NULL DEFAULT 0,
      usage_may_be_incomplete INTEGER NOT NULL DEFAULT 0,  -- v4/BLOCKING F, consolidato qui in v5 (BLOCKING v5-6)
      incomplete_reason TEXT,                              -- v4/BLOCKING F, consolidato qui in v5 (BLOCKING v5-6)
      finalized_at INTEGER NOT NULL,
      PRIMARY KEY (run_id, request_id),
      FOREIGN KEY (run_id, request_id) REFERENCES requests (run_id, request_id) ON DELETE CASCADE
  );
  ```
  (`CREATE TABLE IF NOT EXISTS` gira già incondizionatamente a ogni avvio via
  `conn.executescript(_SCHEMA)` — nessuna migrazione aggiuntiva per tabella nuova).

  > **Nota terminologica (BLOCKING v5-3, estesa in v7-2)**: `llm_call_count`
  > conta il numero di righe di audit `llm_calls` **billable**
  > (`billable_provider_call=True`, default se assente — BLOCKING v7-2)
  > accumulate per la richiesta (una per tentativo/invocazione di modulo),
  > **non** il numero di round-trip HTTP al provider — un'invocazione di
  > modulo con più chiamate interne (perspectives, simulator seeded) conta
  > come 1 verso questo totale nonostante N round-trip reali sottostanti
  > (design confermato, Product decision v5/10). Una riga diagnostica
  > non-billable (speculative-reuse, leakage detection, skipped rewrite,
  > critic-skip) **non** incrementa questo contatore né i due contatori
  > `*_usage_count` — stessa coerenza applicata all'hook dell'accumulatore
  > (Decisione 4) e alla query di ricostruzione offline. Nome mantenuto
  > invariato per diff minimo (il campo non è ancora implementato in alcun
  > codice — introdotto da questo stesso piano — quindi comparirebbe già
  > rinominato in 6 punti se si scegliesse di cambiarlo: schema SQL, 2
  > dataclass, `read_store`, query target, SDK).
- Nuovo evento `EVENT_REQUEST_TOKEN_USAGE_FINALIZED = "request.token_usage_finalized"`
  in `observability/events.py` (aggiunto a `ALL_EVENT_TYPES` e a `_FK_ORDER` in
  `sqlite_sink.py`, dopo `EVENT_PROXY_REQUEST_FINALIZED`).
- Nuova funzione scrittore `insert_request_token_usage(payload, *, conn=None) -> bool`
  (stile `insert_proxy_request_event`), `INSERT OR REPLACE` (idempotente).
- Branch aggiunto nei dispatcher esistenti (`_dispatch`, dispatch batch).
- `read_store.py`: nuovi metodi `get_token_usage_totals(run_id, request_id)` e
  `get_token_usage_breakdown(run_id, request_id)` (query `GROUP BY module,
  phase, action, model` — vedi query target sotto), sia nel `Protocol` sia
  nell'impl SQLite.

`token_usage_json` **non viene rimosso** (retro-compatibilità).

### Decisione 3 — Embedder: solo `OpenAIEmbedder`, self-contained

In `orchestration/embedder.py::OpenAIEmbedder.embed()`: dopo aver ottenuto il
vettore con successo, in un blocco try/except separato (mai far fallire
`embed()` per un problema di telemetria):

```python
try:
    usage = TokenUsage.from_openai_usage(getattr(response, "usage", None), is_embedding=True)
    from moralstack.orchestration.persistence_helpers import record_llm_call
    record_llm_call(None, None, {
        "phase": "ledger", "module": "embedder", "action": "embed",
        "model": self.model, "started_at": started_at, "duration_ms": elapsed_ms,
        "token_usage_json": usage.to_json(), "call_kind": "embedding",
    })
except Exception:
    logger.debug("embedder token accounting failed", exc_info=True)
```

Nessuna modifica a `ledger.py` (righe 267, 367) né al `Protocol` `EmbedderProtocol`
(firma `embed(self, text: str) -> list[float]` invariata) — l'instrumentazione
è interamente incapsulata dentro `OpenAIEmbedder`. `LocalEmbedder`/`HashingEmbedder`
restano intenzionalmente non istrumentati (documentato in
`docs/modules/observability.md`).

### Decisione 4 — Riepilogo sincrono best-effort per-richiesta (BLOCKING F,
v4: non più "totale autoritativo")

**Rinominato in v4.** La v3 descriveva questa tabella/riepilogo come "il
totale autoritativo per singola richiesta" — impreciso: `request_token_usage`
è scritta da un accumulatore in-process **prima** che alcune chiamate LLM
billabili possano ancora risolversi (BLOCKING 1, speculative call scartate),
e viaggia sulla stessa coda best-effort di ogni altro evento (rischio P2,
R1). "Autoritativo" implicava una garanzia di completezza che il design non
offre e non può offrire senza reintrodurre il meccanismo di
contatore/timer già rimosso in v3 (Revision log v3, Decisione 8/BLOCKING 1)
— che Codex ha verificato non chiudere comunque la race. **Nuova
definizione esplicita**: `request_token_usage` è un riepilogo **sincrono**
(scritto in-process, non richiede I/O bloccante), **best-effort** (soggetto
alla stessa coda lossy di ogni evento osservabilità, §5.6), **potenzialmente
parziale per costruzione** in un caso noto e circoscritto (speculative
discard non ancora risolto, sotto). Non è più definito né documentato come
"il totale" della richiesta — il ruolo di riferimento più completo
disponibile passa alla query di ricostruzione SQL su `llm_calls` (vedi in
fondo a questa sezione), che resta comunque essa stessa best-effort
(BLOCKING v6-2, sotto) e non una garanzia assoluta di completezza.

Nuovo file **`moralstack/observability/request_token_accumulator.py`** (nel
package `observability`, non sotto `orchestration.*` — resta comunque pienamente
tipizzato):

```python
@dataclass
class RequestTokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    missing_usage_count: int = 0
    estimated_usage_count: int = 0
    usage_may_be_incomplete: bool = False   # NEW v4 — BLOCKING F
    incomplete_reason: str | None = None    # NEW v4 — BLOCKING F

# dict thread-safe keyato dalla TUPLA (run_id, request_id) — non da request_id
# isolato: vedi Decisione 8 / BLOCKING 5, request_id da solo non è garantito
# univoco (A2, corretta). Cap difensivo FIFO configurabile (default ~5000
# entry, esposto come parametro di modulo/costruttore per testabilità — non
# hardcoded), rete di sicurezza contro leak di memoria se
# _attach_trace_and_return non viene mai raggiunto.
def record_llm_call_usage(run_id: str, request_id: str, token_usage_json: str | None) -> None: ...
def pop_request_token_usage(run_id: str, request_id: str) -> RequestTokenTotals | None: ...
def finalize_and_persist(run_id: str, request_id: str) -> RequestTokenTotals | None:
    """pop + emit EVENT_REQUEST_TOKEN_USAGE_FINALIZED. Best-effort, mai raise."""

def mark_request_usage_partial(run_id: str, request_id: str, *, reason: str) -> None:
    """NEW v4 — BLOCKING F. Sincrona, idempotente, mai bloccante: segna
    (run_id, request_id) come potenzialmente incompleto perché una chiamata
    billabile il cui esito si risolve in modo asincrono (es. una speculativa
    scartata) è stata avviata per questa richiesta. NON attende la
    risoluzione, NON reintroduce un contatore/timer pendente (rimosso in
    v3) — un solo flag booleano sticky, letto una volta sola da
    finalize_and_persist(). Riusa lo stesso lock/dict di
    record_llm_call_usage."""
```

**Punto di incremento — unico, in `ObservabilityService.emit()`/`emit_batch()`**
(`observability/service.py:43-57`), perché tutte le funzioni produttrici di
`EVENT_LLM_CALL` convergono qui per costruzione (verificato — nessuna via
alternativa esiste):

```python
def emit(self, envelope: EventEnvelope) -> None:
    if envelope.event_type == EVENT_LLM_CALL and envelope.run_id and envelope.request_id:
        if envelope.payload.get("billable_provider_call", True):  # NEW v7-2
            try:
                record_llm_call_usage(envelope.run_id, envelope.request_id, envelope.payload.get("token_usage_json"))
            except Exception:
                logger.debug("token usage accumulation failed", exc_info=True)
    try:
        self._queue.submit_envelope(envelope)
    ...

def emit_batch(self, envelopes: Sequence[EventEnvelope]) -> None:
    for envelope in envelopes:
        if envelope.event_type == EVENT_LLM_CALL and envelope.run_id and envelope.request_id:
            if envelope.payload.get("billable_provider_call", True):  # NEW v7-2
                try:
                    record_llm_call_usage(envelope.run_id, envelope.request_id, envelope.payload.get("token_usage_json"))
                except Exception:
                    logger.debug("token usage accumulation failed", exc_info=True)
    try:
        ...
        self._queue.submit_batch(list(envelopes))
    ...
```

Guardia sul branch: il parsing JSON avviene solo per eventi `EVENT_LLM_CALL`
(costo O(1) su stringhe corte, non impatta l'hot path degli altri eventi).
`envelope.payload.get("billable_provider_call", True)`: default `True`
quando la chiave è assente (righe pre-esistenti/non ancora toccate da
BLOCKING 2), coerente con `write_queue.py::async_persist_llm_call`. Fix
allineato in v7 (**BLOCKING v7-2**): lo pseudocodice qui sopra non
applicava ancora il filtro billable, nonostante la prosa di BLOCKING 2 lo
dichiarasse già — `llm_call_count`/`missing_usage_count`/
`estimated_usage_count` in `request_token_usage` contano quindi **solo**
righe `billable_provider_call=True` (default se assente), non tutte le
righe di audit: coerenza a 3 punti con la query di ricostruzione offline e
con `get_token_usage_breakdown` (vedi Decisione 2).

**Punto di lettura/finalizzazione — unico, in
`OrchestrationController._attach_trace_and_return`** (`controller.py:312-320`,
funnel di uscita comune agli 11 return path di `process()`):

```python
def _attach_trace_and_return(self, result, request, call_ctx) -> OrchestratorResult:
    out = self._diagnostics.attach_trace_and_return(result, request, self.execution_trace)
    self._apply_conversation_metadata_to_result(out, request, call_ctx)
    self._finalize_token_accounting(out, request)  # NEW
    return out

def _finalize_token_accounting(self, result: OrchestratorResult, request: ProcessedRequest) -> None:
    try:
        run_id = get_current_run_id()
        request_id = request.request_id
        if not run_id or not request_id:
            return
        totals = finalize_and_persist(run_id, request_id)
        if totals is None:
            return
        m = result.response.metadata
        m.input_tokens, m.output_tokens, m.total_tokens = totals.input_tokens, totals.output_tokens, totals.total_tokens
        m.llm_call_count = totals.llm_call_count
        m.token_usage_missing_count = totals.missing_usage_count
        m.token_usage_estimated_count = totals.estimated_usage_count
    except Exception:
        _LOG.debug("token accounting finalize failed", exc_info=True)
```

Richiede aggiungere `get_current_run_id` all'import esistente di
`moralstack.observability.context` in `controller.py`.

**Relazione esplicita con P2 (lossy queue, non risolto qui):** la riga di
riepilogo `request_token_usage` viaggia sulla stessa coda best-effort
(`observability/write_queue.py:175-181`) di ogni altro evento — nessun canale
privilegiato/bloccante, coerente con §5.6. Mitigazioni nel design senza toccare P2:

1. È un'unica riga per richiesta (non N righe per modulo) → superficie di
   perdita ridotta rispetto a sommare `llm_calls`.
2. Se la riga `request_token_usage` va persa, il totale è ricostruibile con
   `SELECT SUM(input_tokens), SUM(output_tokens), SUM(total_tokens), COUNT(*),
   SUM(token_usage_missing), SUM(token_usage_estimated) FROM llm_calls WHERE
   run_id=? AND request_id=?` (le colonne numeriche della Decisione 2 rendono
   questa query possibile — oggi non lo sarebbe).
3. Il valore esposto al proxy `usage` e a `GovernanceMetadata` è popolato in
   `_finalize_token_accounting` **in-process, sincrono, prima di qualsiasi
   scrittura su coda** — non è mai affetto da un drop della coda. Solo la riga
   durevole su DB per analytics successive è a rischio.

Un fix strutturale di P2 resta un follow-up separato, esplicitamente fuori scope.

### Decisione 5 — Proxy: `usage` reale

`server/proxy.py::_build_synthetic_chat_completion` (righe 161-193): aggiungere
parametro `usage: dict[str, int] | None = None`, default
`{"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}` se `None`
(comportamento invariato quando non c'è nulla da riportare).

- Call site di successo (righe ~396-401): passare i valori reali da
  `result.response.metadata` (`input_tokens`/`output_tokens`/`total_tokens`).
- Call site di fallimento fail-closed (righe ~359-364): **nessun `result`
  esiste** in questo ramo (l'eccezione è avvenuta prima che
  `_attach_trace_and_return` fosse raggiunto) → resta `{0,0,0}`, comportamento
  corretto/onesto (nessuna chiamata LLM completata con successo).
- Stesso trattamento per l'eventuale variante SSE, solo se il formato
  OpenAI-compatible già usato prevede un chunk `usage` — non introdurlo se non
  è già previsto, per restare a diff minimo.

### Decisione 6 — Esposizione SDK

`sdk/response.py::GovernanceMetadata` (righe 24-141): aggiungere 6 campi con
default `0`, mirror di `ResponseMetadata`:

```python
input_tokens: int = 0
output_tokens: int = 0
total_tokens: int = 0
llm_call_count: int = 0
token_usage_missing_count: int = 0
token_usage_estimated_count: int = 0
```

`from_result()` li popola da `meta.input_tokens`, ecc. Additivo, non rompe
firme esistenti (uso keyword nei call site osservati).

### Decisione 8 — Fix ai 5 BLOCKING della review Codex (v2)

Questa decisione estende/corregge le Decisioni 1 e 4 sopra. Ogni sottosezione
risolve un finding BLOCKING del report Codex
(`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260701-144844.md`).

#### Prerequisito — bug indipendente scoperto in revisione

`SpeculativeOverlapHandle.abandon()` (`orchestration/speculative_overlap.py:127-170`)
avvia un thread daemon grezzo (`threading.Thread(target=_bg, daemon=True)`,
riga 170) **senza** `contextvars.copy_context()`. Il dict `merged` passato a
`self._events.emit_llm_call(**merged)` dentro `_bg` non contiene mai
`run_id`/`request_id` (verificato: `persist_kwargs` costruito in
`_speculative_generate`, `controller.py:973-999`, non include quelle chiavi).
`get_current_run_id()`/`get_current_request_id()` leggono `ContextVar`
(`observability/context.py:12-13`, default `None`) — un thread senza
`copy_context()` non eredita il contesto del chiamante. Risultato verificato:
nel thread `_bg`, `run_id`/`request_id` sono sempre `None`, quindi
`async_persist_llm_call` ritorna subito (`write_queue.py:40-41`) e **l'evento
`EVENT_LLM_CALL` della speculativa scartata non viene mai emesso oggi** —
bug pre-esistente e indipendente dal token accounting, ma prerequisito
bloccante per il fix di BLOCKING 1 sotto (senza correggerlo, non c'è nulla da
riconciliare perché la riga non arriva mai al DB). Va corretto nello stesso
PR/file di BLOCKING 1.

#### BLOCKING 1 — Race di finalizzazione con le speculative call

**Risolto in v3 con una Product decision esplicita, non con più meccanismo.**
Nella v2, questo fix introduceva un contatore di outcome pendenti +
finalizzazione differita + timer di sicurezza per garantire che `usage` del
proxy includesse SEMPRE anche le speculative scartate. La seconda review
Codex ha segnalato che anche quel meccanismo non chiudeva davvero la race
(`peek_request_token_usage()` poteva comunque essere letto prima della
risoluzione). **La v3 non prova più a chiudere la race lato risposta HTTP**:
la Product decision (Revision log v3) accetta esplicitamente `usage`
parziale in questo caso specifico, con riconciliazione solo lato audit/DB —
questo elimina la necessità dell'intero meccanismo di Parte B della v2.

**Fix — una sola parte, stesso file `orchestration/speculative_overlap.py`:**
correggere la perdita di contesto (il bug prerequisito indipendente, sezione
0), così la riga `llm_calls` della speculativa scartata sia almeno
**persistita correttamente** per l'audit/la ricostruzione SQL, anche se non
sincrona con la risposta HTTP. In `abandon()`, catturare `run_id`/
`request_id`/`session_id`/`turn_number`/`cycle` **nel thread chiamante**
(dove il contesto è corretto), prima di avviare `_bg`, e iniettarli in
`merged` via `setdefault`:

```python
def abandon(self, discard_reason: str, final_route: str) -> None:
    if self._joined or self._abandoned:
        return
    self._abandoned = True
    from moralstack.observability.context import (
        get_current_run_id, get_current_request_id,
        get_current_session_id, get_current_turn_number, get_current_cycle,
    )
    captured_run_id = get_current_run_id()
    captured_request_id = get_current_request_id()
    captured_session_id = get_current_session_id()
    captured_turn_number = get_current_turn_number()
    captured_cycle = get_current_cycle()

    def _bg() -> None:
        try:
            draft, meta = self._spec_future.result()
            if meta:
                try:
                    merged = dict(meta)
                    merged["call_outcome"] = "discarded"
                    merged.setdefault("run_id", captured_run_id)
                    merged.setdefault("request_id", captured_request_id)
                    merged.setdefault("session_id", captured_session_id)
                    merged.setdefault("turn_number", captured_turn_number)
                    merged.setdefault("cycle", captured_cycle)
                    self._events.emit_llm_call(**merged)
                except Exception:
                    _LOG.debug("persist speculative discarded failed", exc_info=True)
            _ = draft
        except Exception as e:
            _LOG.debug("abandon speculative future: %s", e)

    threading.Thread(target=_bg, daemon=True, name="speculative-abandon").start()
```

Nessun contatore, nessun `peek`/differimento, nessun timer: `controller.py::
_finalize_token_accounting` resta esattamente come nella Decisione 4
originale (`finalize_and_persist(run_id, request_id)` incondizionato, subito,
sincrono). Se la speculativa scartata si risolve dopo la finalizzazione,
l'evento `llm.call` che arriva più tardi via `emit()` crea semplicemente una
nuova entry `(run_id, request_id)` nell'accumulatore (mai popolata/letta di
nuovo da nessuno) — innocua, evinta dal cap FIFO difensivo (R2) come
qualunque altra entry orfana. La riga `llm_calls` sottostante resta
correttamente scritta con `billable_provider_call=True` (token realmente
consumati) e il proprio `run_id`/`request_id` corretti (grazie al fix sopra)
— quindi la query di ricostruzione `SUM(...) FROM llm_calls WHERE run_id=?
AND request_id=?` la include comunque, anche se il riepilogo sincrono/
`request_token_usage` no.

**Cosa NON è più garantito, esplicitamente accettato (Product decision,
Revision log v3)**: il campo `usage` restituito nella risposta HTTP del
proxy può non includere una speculativa scartata risolta dopo la
finalizzazione — nei path refuse/safe_complete/deliberativo dove
`abandon()` viene chiamato (`controller.py:2528, 2553, 2594`, righe
ri-verificate). Nessuna latenza aggiuntiva introdotta su questi path.

**Alternative scartate**: attendere sincronamente `_spec_future` dentro
`abandon()` con timeout breve (reintrodurrebbe la latenza che la speculative
overlap doveva eliminare sui path più sensibili — esplicitamente rifiutata
dalla Product decision); il meccanismo a contatore/differimento della v2
(rimosso: aggiungeva complessità senza chiudere davvero la race, come
confermato dalla seconda review Codex).

#### BLOCKING 2 — `llm.call` non è un proxy affidabile di "chiamata al provider"

**v3 — audit sistematico dei 33 call site `record_llm_call(...)` in
`deliberation_runner.py`.** La maggioranza (`pre_cycle_check`,
`deliberation_cycle_*_start/complete`, rami `SKIPPED`/`GATED`/`DISABLED`,
tutti i rami `*(ERROR)`/`timeout_warning`) passano `persist_kwargs=None` a
`record_llm_call(logger, diagnostics_payload, persist_kwargs)`
(`persistence_helpers.py:25-47`) — quando `persist_kwargs is None`,
`async_persist_llm_call` **non viene mai chiamato**: sono solo log locali
(CLI verbose), non arrivano mai a `llm_calls`. Questi restano fuori scope
per costruzione (non generano righe di audit).

Nuovo campo esplicito **`billable_provider_call: bool`** (dimensione
separata da `TokenUsageSource`). Default `True` ovunque (nessuna riga
esistente cambia comportamento); `False` esplicito (o condizionale) sui
seguenti siti — **inventario completo e verificato**, non solo i 3 del v2:

| # | File:riga | module/action | Condizione | Nel v2? |
|---|---|---|---|---|
| 1 | `deliberation_runner.py:866-887` (`run_fast_path`) | `policy` / `"generate (speculative-reuse, fast_path)"` | sempre `False` | **NO — mancante** |
| 2 | `deliberation_runner.py:909-937` (`run_fast_path`) | `output_protection` / `"leakage_detected (fast_path)"` | sempre `False` | **NO — mancante** (è il sito esatto citato nella seconda review Codex; il v2 copriva solo il gemello deliberativo a riga 2718) |
| 3 | `deliberation_runner.py:2616-2639` | `policy` / `"generate (speculative-reuse)"` | sempre `False` | Sì |
| 4 | `deliberation_runner.py:2651-2674` | `policy` / `"rewrite (SKIPPED_EMPTY_GUIDANCE)"` | sempre `False` | Sì |
| 5 | `deliberation_runner.py:2718-2742` | `output_protection` / `"leakage_detected"` | sempre `False` | Sì |
| 6 | `deliberation_runner.py:2907-2939` (`_critique`) | `critic` / `"critique"` | **condizionale**: `not is_skipped`, dove `is_skipped = bool(getattr(critique, "skipped", False))` (vero quando `CriticReport.empty_skipped()`, `critic_module.py:391`, nessun principio rilevante → nessuna chiamata LLM) | **NO — mancante**, non citato nemmeno da Codex |

Il sito #6 richiede un booleano condizionale, non una costante:
```python
"billable_provider_call": not is_skipped,
```

**Difetto sistemico scoperto in v3, gravità default-on (non un edge case):
righe cache-hit ri-fatturate come nuove chiamate.**
`simulator_module.py::LLMSimulator.simulate()` (righe 342-355) e
`hindsight_module.py::LLMHindsightEvaluator.evaluate()` (righe 655-667)
ritornano un risultato cache-hit (`ModuleResultCache`, `utils/cache.py:167-297`)
**senza mai invocare l'LLM** — l'oggetto ritornato è la stessa istanza
mutabile salvata in cache, con `tokens_used`/`prompt_tokens`/
`completion_tokens` calcolati la prima volta. `deliberation_runner.py::
_simulate`/`_evaluate_hindsight` non hanno modo di sapere che il risultato
viene dalla cache: **ogni cache-hit ri-riporta gli stessi token come
consumo nuovo**. `enable_caching: bool = True` è il default sia su
`SimulatorConfig` (`simulator_module.py:260`) sia su `HindsightConfig`
(`hindsight_module.py:470`) — non un opt-in raro. Stesso pattern su
`perspective_module.py::LLMPerspectiveEnsemble.evaluate()` (righe 482-495),
ma lì `enable_caching` default è `False` (`perspective_module.py:397`) —
stesso difetto, dormiente salvo opt-in esplicito dell'operatore. Incluso
nello scope obbligatorio (non un nice-to-have): senza questo fix,
`billable_provider_call` resta sistematicamente sbagliato ogni volta che la
cache è attiva (default per simulator/hindsight). Vedi Rischio R12.

**Fix — campo additivo `from_cache: bool = False`** su 3 dataclass (nessuna
frozen):
- `simulator_module.py::SimulationResult` — `cached_result.from_cache = True`
  prima del `return` cache-hit (riga 355).
- `hindsight_module.py::HindsightResult` — stesso pattern, riga 667.
- `perspective_module.py::EnsembleResult` — stesso pattern, riga 495.

Punto di lettura in `deliberation_runner.py` (stesso schema in `_simulate`
~riga 3018, `_evaluate_hindsight` ~riga 3129, `_evaluate_perspectives` ~riga
3242):
```python
from_cache = bool(getattr(simulation, "from_cache", False))
...
record_llm_call(self.logger, {...}, {
    ...,
    "billable_provider_call": not from_cache,
    "cache_status": "hit" if from_cache else None,
})
```

**Fuori scope esplicito, non bloccante**: `HindsightResult.empty()`
(`hindsight_module.py:648`) ed `EnsembleResult.empty()`
(`perspective_module.py:500`) sono "nessuna chiamata LLM" ma producono
token=0 — nessun double-counting numerico, solo chiarezza diagnostica
futura, non richiesto in questo PR (minimal diff).

**Perché un booleano esplicito e non una derivazione da `call_kind`/
`call_outcome`**: scartato — sono testo libero con valori eterogenei usati
anche per scopi diagnostici non legati alla fatturabilità; derivare
`billable_provider_call` da una whitelist/blacklist di stringhe è fragile.

**Plumbing (invariato dal v2):**
- `persistence/write_queue.py::async_persist_llm_call` (righe 51-71):
  `"billable_provider_call": kwargs.get("billable_provider_call", True)` nel
  `payload`.
- `observability/sinks/sqlite_sink.py`: nuova colonna
  `llm_calls.billable_provider_call INTEGER` (nullable, righe 698-702).
  Righe storiche restano `NULL`; ogni query di aggregazione legge
  `COALESCE(billable_provider_call, 1) = 1`.
- `observability/service.py::emit()`/`emit_batch()`: l'hook
  dell'accumulatore salta `record_llm_call_usage(...)` quando
  `envelope.payload.get("billable_provider_call") is False`, per singolo
  envelope dentro il ciclo di `emit_batch()` (R9). Ora copre anche i nuovi
  siti sopra, non solo i 3 originali.

**Fuori scope esplicito**: il payload locale costruito da
`models/risk/estimator.py` (non passa da `async_persist_llm_call`) non deve
guadagnare questo campo. Un test esistente
(`tests/test_risk_persist_batch.py:168-169`) si romperebbe correttamente se
questo confine venisse violato — è il guardrail naturale, non va
indebolito.

#### BLOCKING 3 — Provenienza token assente nei dataclass runtime

**Verificato**: `moralstack.runtime.modules.*` non è sotto l'override `mypy
--strict` di `pyproject.toml:139-141` (solo `moralstack.orchestration.*` lo
è) — un `Protocol` condiviso non è necessario per la tipizzazione; è
sufficiente un campo additivo per dataclass (diff minimo, PROJECT_SPEC §6).

**Nuovi helper in `observability/token_usage.py`:**

```python
@classmethod
def from_generation_result(cls, result: Any) -> "TokenUsage":
    """Costruisce TokenUsage da un oggetto GenerationResult-like via getattr
    (tollerante a mock/chiamanti legacy privi di token_usage_source)."""
    tokens_used = int(getattr(result, "tokens_used", 0) or 0)
    prompt_tokens = getattr(result, "prompt_tokens", None)
    completion_tokens = getattr(result, "completion_tokens", None)
    source = getattr(result, "token_usage_source", None)
    if source not in ("exact", "estimated", "missing", "unknown"):
        source = "missing" if (tokens_used == 0 and prompt_tokens is None and completion_tokens is None) else "unknown"
    return cls(int(prompt_tokens or 0), int(completion_tokens or 0), tokens_used, source)

@classmethod
def combine(cls, usages: Sequence["TokenUsage"]) -> "TokenUsage":
    """Somma i conteggi; la source combinata è la 'meno certa' tra gli input
    (missing < unknown < estimated < exact). Lista vuota -> TokenUsage(0,0,0,'missing')."""
    if not usages:
        return cls(0, 0, 0, "missing")
    priority = {"missing": 0, "unknown": 1, "estimated": 2, "exact": 3}
    worst = min(usages, key=lambda u: priority[u.source])
    return cls(
        sum(u.input_tokens for u in usages),
        sum(u.output_tokens for u in usages),
        sum(u.total_tokens for u in usages),
        worst.source,
    )
```

**Campo additivo `token_usage_source: TokenUsageSource = "unknown"` su 5
dataclass:**

- `runtime/modules/critic_module.py::CriticReport` — popolato righe ~500-515.
- `runtime/modules/simulator_module.py::SimulationResult` — popolato via
  `_build_result` (esteso), righe ~448-459.
- `runtime/modules/hindsight_module.py::HindsightResult` — popolato righe
  ~780-789.
- `runtime/modules/perspective_module.py::PerspectiveResult` — popolato
  righe ~731-737 (dataclass NON frozen, mutazione post-costruzione, stesso
  stile già usato per `tokens_used`/`prompt_tokens` in quel blocco).
- `runtime/modules/perspective_module.py::EnsembleResult` (aggregato, 2 siti
  di costruzione: righe ~624-634 e ~674-684, entrambi già sommano
  `tokens_used`/`prompt_tokens`/`completion_tokens` su `results:
  list[PerspectiveResult]`): aggiungere
  `token_usage_source=TokenUsage.combine([TokenUsage(r.prompt_tokens or 0, r.completion_tokens or 0, r.tokens_used, r.token_usage_source) for r in results]).source`
  in **entrambi** i siti (parallelo e sequenziale — vedi Rischio R10).

**`deliberation_runner.py::_token_usage_json_from_result`** (righe 379-392)
riscritta:

```python
def _token_usage_json_from_result(result: Any) -> str | None:
    usage = TokenUsage.from_generation_result(result)
    return usage.to_json()  # None solo quando usage.source == "missing"
```

Mantiene il comportamento-locking del test esistente
(`tests/test_orchestrator.py:1630-1642`): l'oggetto mock senza `source` non
solleva `AttributeError` — `from_generation_result` gestisce l'assenza con
un default esplicito. **Comportamento nuovo, non una regressione** (nessun
test esistente pin il caso opposto): con `source` esplicito e valido
diverso da `"missing"`, `to_json()` non collassa più a `None` anche se i
conteggi numerici grezzi sono zero — coerente con la Product decision 4
(vedi Revision log in cima al documento).

**Alternativa scartata**: `Protocol HasTokenUsage` comune ai 4 moduli —
nessun beneficio di tipizzazione (moduli non sotto mypy strict), richiede
rifattorizzare 4 dataclass non correlate, va contro il diff minimo.

**v3 — retry falliti (Product decision 6): 4 retry loop verificati**
(righe leggermente diverse da quelle citate da Codex): `critic_module.py::
critique()` (righe 435-537, due rami `except`: uno specifico
`JSONParseError/StructuredValidationError/PydanticValidationError` righe
517-527, uno generico `Exception` righe 528-537);
`simulator_module.py::_simulate_batch()` (righe 419-489, ha già un
precedente **parziale**: righe 466-481 persistono via
`async_persist_llm_call(phase="simulator_retry", ...)` ma **senza
`token_usage_json`**, e con un bug di copertura — la condizione `if attempt
> 0:` esclude il primo tentativo fallito); `hindsight_module.py::
_evaluate_batch()` (righe 723-793, nessuna persistenza sui falliti);
`perspective_module.py::_evaluate_single_perspective()` (righe 707-740,
nessuna persistenza; su esaurimento retry la prospettiva finisce
silenziosamente in `failed_perspectives`, `perspective_module.py:664`).

Confermato in tutti e 4: `result = self.policy.generate(...)`/
`generate_messages(...)` avviene **prima** del parsing in ogni branch —
ogni tentativo (fallito o riuscito) ha già consumato token reali dal
provider prima che il parsing/la validazione decidano se accettarlo.

**Decisione di design: NON rendere cumulativi i campi numerici esistenti
del dataclass.** Scartato sia l'approccio v2 (solo `token_usage_source` dal
risultato finale) sia l'alternativa "sommare tutti i tentativi nei campi
esistenti", perché quest'ultima introdurrebbe **doppio conteggio** nella
ricostruzione SQL: se il dataclass finale riportasse già la somma di tutti
i tentativi E si persistesse anche una riga di audit separata per ogni
tentativo fallito, `SUM(...) FROM llm_calls` conterebbe due volte i token
dei falliti. **Design scelto**: i campi esistenti (`tokens_used`,
`prompt_tokens`, `completion_tokens`, `token_usage_source`) restano
semanticamente "solo l'ultimo tentativo riuscito" — **nessun cambio di
comportamento numerico** (verificato: nessun test esistente pinna un
valore numerico atteso per questi campi in scenario di retry; l'unico test
di retry esistente, `tests/test_perspective_module.py::
TestLLMPerspectiveEnsembleRetry`, righe 547-580, asserisce solo
`evaluation_count`/`failed_perspectives`, mai i conteggi token). La somma
richiesta dalla Product decision 6 avviene **a livello di sistema/DB**: ogni
tentativo fallito genera la propria riga `llm_calls` con `call_outcome=
"retry_failed"`, `billable_provider_call=True`, i propri token reali; il
tentativo riuscito genera la propria riga separata. `SUM(...) FROM
llm_calls WHERE run_id=? AND request_id=? AND
COALESCE(billable_provider_call,1)=1` somma correttamente tutto una sola
volta — coerente con Decisione 2/4. Vantaggio pratico: queste righe passano
per lo stesso funnel sincrono `ObservabilityService.emit()` (Decisione 4)
**dentro** il ciclo deliberativo (non in un thread daemon come BLOCKING 1)
— entrano nell'accumulatore prima che la richiesta ritorni, quindi `usage`
del proxy e `GovernanceMetadata` le riflettono correttamente senza bisogno
di logiche differite.

**Fix — pattern uniforme sui 4 moduli**, variabile locale resettata a ogni
iterazione:

```python
for attempt in range(self.config.max_retries):
    parse_attempts = attempt + 1
    attempt_token_usage: TokenUsage | None = None  # reset ogni iterazione
    try:
        result = self.policy.generate(...)
        attempt_token_usage = TokenUsage.from_generation_result(result)  # NEW, prima del parsing
        raw_response = result.text
        ...  # parsing/validazione esistente, può sollevare
        return CriticReport(...)  # invariato: usa SOLO l'ultimo `result`
    except (JSONParseError, StructuredValidationError, PydanticValidationError) as e:
        last_error = e
        if attempt_token_usage is not None:  # il provider ha risposto, solo IL NOSTRO parsing è fallito
            try:
                from moralstack.persistence.write_queue import async_persist_llm_call
                async_persist_llm_call(
                    phase=f"{module_name}_retry", module=module_name,
                    action=f"retry_failed_attempt_{parse_attempts}",
                    prompt=f"Retry reason: {str(e)[:200]}", raw_response=raw_response or "",
                    duration_ms=0.0, attempts=parse_attempts, call_outcome="retry_failed",
                    billable_provider_call=True, token_usage_json=attempt_token_usage.to_json(),
                )
            except Exception:
                logger.debug("persist %s retry-failed llm call failed", module_name, exc_info=True)
        continue
    except Exception as e:
        # invariato: qui `generate()` stessa può aver fallito (errore infra/rete,
        # non parsing) — result potrebbe non essere mai stato assegnato. Nessuna
        # persistenza di token qui, fuori scope (non è un "retry di parsing").
        last_error = e
        continue
```

Punti di applicazione: `critic_module.py:435-537` (blocco `except
(JSONParseError, StructuredValidationError, PydanticValidationError)`,
righe 517-527, `module_name="critic"`); `simulator_module.py:419-489`
(**correggere** il precedente esistente righe 466-481: rimuovere `if
attempt > 0:`, aggiungere `token_usage_json` mancante; il loop cattura
`except (JSONParseError, Exception)` — il guardrail
`attempt_token_usage is not None` evita di fatturare errori di rete come
retry di parsing, `module_name="simulator"`); `hindsight_module.py:723-793`
(nessun precedente, aggiungere ex novo nel blocco `except (JSONParseError,
Exception) as e`, righe 791-793, `module_name="hindsight"`);
`perspective_module.py:707-740` (nessun precedente; il blocco `except
(JSONParseError, Exception):` righe 738-739 oggi non lega nemmeno
l'eccezione a un nome — cambiare in `except (JSONParseError, Exception) as
e:` e aggiungere la persistenza, `module_name="perspectives"`, includere
`perspective.id`/`perspective.name` nel campo `prompt` per distinguere
quale prospettiva ha fallito).

Il campo `token_usage_source` (mantenuto dal v2) riflette la provenienza
**del solo tentativo finale riuscito**, non una combinazione dei tentativi
— coerente con la scelta di non rendere cumulativi i campi numerici.
`TokenUsage.combine([...])` resta usata **solo** per `EnsembleResult`
attraverso prospettive diverse (asse ortogonale ai retry, invariato dal v2).

Layering: tutti e 4 i moduli sono in `moralstack.runtime.modules.*`, fuori
dall'override mypy strict — l'import locale dentro il blocco `except`
(stesso stile già usato oggi in `simulator_module.py:470`) non introduce
cicli né richiede tipizzazione più stringente. Nessun invariante P0
toccato: la logica di parsing/decisione resta bit-per-bit identica —
l'unica aggiunta è una persistenza best-effort in try/except non
propagante (§5.6), simmetrica al pattern già esistente in
`simulator_module.py`.

#### BLOCKING 4 — Refusal generation fuori scope

**v2 (causa radice, confermata)**: `_llm_refusal_call`
(`safe_refusal_generator.py:540-556`) chiama `llm_client.generate(...)`,
ottiene un `GenerationResult` reale, estrae solo `.text` e scarta il resto.
`RefusalGenerationResult` (righe 19-43, frozen) non ha campi token.

**v3 — correzione del targeting.** La seconda review Codex ha segnalato che
il v2 correggeva solo `response_assembler.py`/`final_revalidation.py`, ma
il path REFUSE **principale** passa da un punto diverso. Verifica di
sessione: `_route_refuse` (`controller.py:1551-1578`) è l'**unico** call
site che invoca `RefusalHandler.handle()`, raggiunto da un solo punto
(`controller.py:2529`, ramo `route == "refuse"`). `RefusalHandler.handle()`
(`refusal_handler.py:49-181`) chiama `generate_llm_safe_refusal_detailed(...)`
(riga 94) e persiste con `self._events.emit_llm_call(...)` (righe 147-158)
— payload **senza** `token_usage_json`. `DefaultEventEmitter.emit_llm_call`
(`default_event_emitter.py:17-18`) → `record_llm_call(None, None, kwargs)`
→ `async_persist_llm_call(**kwargs)` (`write_queue.py:36-73`, mappa
`token_usage_json` a riga 63) — basta aggiungere la chiave al dict passato
a `emit_llm_call`.

**I 3 call site sono tutti legittimi e restano nello scope** — non un
errore di targeting del v2, sono 3 percorsi REFUSE distinti:

| Call site | Quando scatta | Persistenza |
|---|---|---|
| `refusal_handler.py:94` (via `_route_refuse`) | REFUSE early/hard-signal, prima del ciclo deliberativo (`controller.py:2526-2528`) | `self._events.emit_llm_call(...)` |
| `response_assembler.py:286` | REFUSE deciso **durante** il ciclo deliberativo | `record_llm_call(...)`, righe 304-322 |
| `final_revalidation.py:557` | REFUSE post-hoc dopo fallimento della validazione finale | `record_llm_call(...)`, righe 577-597 |

**Fix — 4 file totali (3 del v2 + 1 nuovo), stesso pattern di cattura
usato per `policy.py` (Decisione 2):**

1. `safe_refusal_generator.py::_llm_refusal_call` — ritorno esteso a
   `tuple[str, TokenUsage]` (invariato dal v2):
   ```python
   def _llm_refusal_call(*, llm_client, system, user_msg) -> tuple[str, TokenUsage]:
       try:
           result = llm_client.generate(prompt=user_msg, system=system)
           text = getattr(result, "text", None) or (str(result) if result else "")
           return (text or "").strip(), TokenUsage.from_generation_result(result)
       except Exception as e:
           logger.warning("generate_llm_safe_refusal: LLM fallito, uso fallback: %s", str(e)[:100])
           return "", TokenUsage(0, 0, 0, "missing")
   ```
2. `RefusalGenerationResult` (righe 19-43): +campo `token_usage: TokenUsage`
   (invariato dal v2, vedi dettaglio sotto).
   `generate_llm_safe_refusal_detailed` (righe 559-662) accumula un
   `TokenUsage` per ogni chiamata effettivamente emessa (1 senza retry, 2
   con anti-leak retry) e imposta `token_usage=TokenUsage.combine([...])`
   su tutti e 3 i punti di ritorno. **Un solo audit row per generazione di
   rifiuto** (non uno per attempt).
3. `response_assembler.py:304-322` e `final_revalidation.py:577-597`:
   `"token_usage_json": refusal_result.token_usage.to_json()` al
   `persist_kwargs` (invariato dal v2).
4. **NUOVO in v3** — `refusal_handler.py:147-158`:
   ```python
   self._events.emit_llm_call(
       cycle=0, phase="refusal", module="orchestration",
       action="refuse (fast_path)", duration_ms=_refusal_duration_ms,
       prompt=refusal_result.user_prompt, system_prompt=refusal_result.system_prompt,
       raw_response=refusal_content, attempts=refusal_result.attempts,
       sequence_in_cycle=6,
       token_usage_json=refusal_result.token_usage.to_json(),  # NEW
   )
   ```

**Nota**: nessun invariante di governance toccato — `_llm_refusal_call`
genera solo la forma linguistica del rifiuto dopo che REFUSE è già stato
deciso da segnali strutturati (commento esplicito in cima al file, righe
1-6); il fix aggiunge solo contabilità dei token.

#### BLOCKING 5 — Chiave dell'accumulatore non sicura

**Verificato**: `ProcessedRequest` (`orchestration/types.py:194-207`) è un
`@dataclass` non frozen, `request_id: str = field(default_factory=lambda:
str(uuid.uuid4()))` — default generato, ma campo mutabile assegnabile dal
chiamante (nessuna garanzia di unicità a livello di processo). Lo schema
SQLite usa `(run_id, request_id)` come chiave composita (`requests`:
`PRIMARY KEY (run_id, request_id)`, `sqlite_sink.py:123-128`; `llm_calls`:
`FOREIGN KEY (run_id, request_id) REFERENCES requests (...)`,
`sqlite_sink.py:207-216`).

**Fix**: il dict interno di `request_token_accumulator.py` è keyato dalla
tupla `(run_id, request_id)`, non da `request_id` isolato:

```python
_KeyT = tuple[str, str]  # (run_id, request_id)
_entries: dict[_KeyT, RequestTokenTotals] = {}
_lock = threading.Lock()
```

Le firme pubbliche restano identiche a quelle della Decisione 4 (già
prendevano entrambi i parametri) — cambia solo l'implementazione interna
della chiave del dict. Il cap FIFO difensivo (R2) evince per tupla, non per
`request_id` isolato.

---

#### BLOCKING D — `quick_check()` non contabilizzato (v4)

**Causa radice**: `LLMConstitutionalCritic.quick_check()`
(`critic_module.py:589-694`) fa una vera chiamata `self.policy.generate(...)`
(righe 650-654) per il fast path, ma non persiste mai nulla — né sul
successo né sul fallimento. Unico call site:
`deliberation_runner.py:976` (`run_fast_path`, dentro `if self.critic is
not None and constitution is not None:`, riga 974). Wiring di produzione
confermato (non solo test): `LLMConstitutionalCritic(...)` è costruito in
`pipeline/deliberation_stack.py:138` ed è il critic realmente iniettato in
`deps.critic` (`deliberation_runner.py:416`). Early-return senza chiamata
LLM (`critic_module.py:629-630`, `if not hard_constraints: return
QuickCheckResult(passed=True)`) è corretto e non deve generare alcuna riga
(nessun consumo).

**Fix — self-persistenza interamente dentro `critic_module.py`** (stesso
pattern già usato per `OpenAIEmbedder.embed()`, Decisione 3, e per il fix
dei retry falliti nello stesso file, BLOCKING 3): nessuna modifica a
`deliberation_runner.py`, nessuna modifica alla firma di `QuickCheckResult`
o al call site esistente — il risultato (`passed`/`critical_violation`) non
cambia.

```python
def quick_check(
    self, request: str, response: str, constitution: Constitution,
) -> QuickCheckResult:
    ...
    if not hard_constraints:
        return QuickCheckResult(passed=True)  # nessuna chiamata LLM: nessuna riga

    ...
    quick_check_token_usage: TokenUsage | None = None  # reset, guardrail R15-style

    try:
        result = self.policy.generate(
            prompt=prompt, system=CRITIC_SYSTEM_PROMPT, config=quick_config,
        )
        from moralstack.observability.token_usage import TokenUsage
        quick_check_token_usage = TokenUsage.from_generation_result(result)

        data = extract_json(result.text)
        violated = data.get("violated", False)
        elapsed_ms = (time.perf_counter() - start) * 1000

        self._persist_quick_check(
            duration_ms=elapsed_ms, prompt=prompt, raw_response=result.text,
            token_usage=quick_check_token_usage, call_outcome=None,
        )

        if violated:
            ...
            return QuickCheckResult(passed=False, critical_violation=violation, check_time_ms=elapsed_ms)
        return QuickCheckResult(passed=True, check_time_ms=elapsed_ms)

    except (JSONParseError, Exception):
        elapsed_ms = (time.perf_counter() - start) * 1000
        if quick_check_token_usage is not None:
            # generate() è tornato con successo: solo l'estrazione/parsing JSON
            # è fallita, i token sono stati comunque consumati dal provider.
            self._persist_quick_check(
                duration_ms=elapsed_ms, prompt=prompt, raw_response="",
                token_usage=quick_check_token_usage, call_outcome="quick_check_failed",
            )
        # generate() stessa ha fallito (result mai assegnato): nessuna
        # persistenza, nessun token fantasma (stesso guardrail già
        # applicato ai 4 retry loop di BLOCKING 3).
        return QuickCheckResult(passed=True, check_time_ms=elapsed_ms)

def _persist_quick_check(
    self, *, duration_ms: float, prompt: str, raw_response: str,
    token_usage: "TokenUsage", call_outcome: str | None,
) -> None:
    try:
        from moralstack.persistence.write_queue import async_persist_llm_call
        async_persist_llm_call(
            phase="critic", module="critic", action="quick_check (fast_path)",
            model=getattr(self.policy, "model", None),
            prompt=prompt, raw_response=raw_response, duration_ms=duration_ms,
            billable_provider_call=True, call_outcome=call_outcome,
            token_usage_json=token_usage.to_json(),
        )
    except Exception:
        logger.debug("persist critic quick_check llm call failed", exc_info=True)
```

Note di design:
- `run_id`/`request_id` non passati esplicitamente: `async_persist_llm_call`
  li legge da `get_current_run_id()`/`get_current_request_id()` se assenti
  (`write_queue.py:38-39`) — stesso comportamento già usato da tutti gli
  altri call site self-persistenti in questo file.
- `cycle`/`sequence_in_cycle` omessi deliberatamente: `critic_module.py` è
  sotto `moralstack.runtime.modules.*`, layer sottostante a
  `orchestration.*` (nessun import da `deliberation_runner.py`, verificato
  — nessun ciclo introdotto); `cycle` viene letto dal ContextVar corrente
  (`write_queue.py:42`), come già avviene per il fix dei retry loop nello
  stesso file (BLOCKING 3).
- Dipende dal plumbing di BLOCKING 2 già pianificato
  (`write_queue.py::async_persist_llm_call` accetta `billable_provider_call`
  nel payload).
- `call_outcome="quick_check_failed"` è un nuovo valore, distinto da
  `"retry_failed"` (non è un retry — `quick_check()` non ha loop di retry,
  è single-shot con fallback fail-open) — nessuna collisione semantica con i
  valori già usati altrove.

#### BLOCKING E — Tre sotto-percorsi con copertura token dubbia (v4)

**E(a) — Simulator: retry falliti nel percorso "seeded" — CONFERMATO, loop
indipendente e scoperto.** `_simulate_with_seeds()` (`simulator_module.py:
492-586`) ha un proprio doppio loop (`for seed in selected_seeds: for
attempt in range(self.config.max_retries):`, righe 521-573),
**indipendente** da `_simulate_batch()` (righe 391-490) — non lo riusa in
alcun modo. `except (JSONParseError, Exception): continue` a righe 572-573:
zero persistenza, anche per i tentativi falliti — il fix di BLOCKING 3
(che copre solo `_simulate_batch`, righe 466-481) non copre questo
percorso. `use_seeded_generation: bool = False` (`simulator_module.py:257`)
è opt-in, non default — ma per Product decision 8 (non restringere lo
scope) va comunque corretto, non solo documentato, stesso principio già
applicato al difetto cache-hit dormiente di Perspectives (BLOCKING 2/B). I
tentativi **riusciti** (uno per seed) sommano già correttamente in
`total_tokens_used` (righe 512, 560, 583-585) — nessuna perdita numerica
sul totale, solo sui tentativi falliti.

Fix, stesso pattern del fix già pianificato per `_simulate_batch`, applicato
al loop interno di `_simulate_with_seeds`:

```python
for seed in selected_seeds:
    prompt = SEEDED_PROMPT_TEMPLATE.format(seed=seed, request=request, response=response)
    legacy_prompt = prompt + _build_context_block(developer_contract, conversation_history)

    for attempt in range(self.config.max_retries):
        total_attempts += 1
        attempt_token_usage: TokenUsage | None = None  # reset ogni iterazione
        try:
            result = self.policy.generate_messages(...) if hasattr(...) else self.policy.generate(...)
            from moralstack.observability.token_usage import TokenUsage
            attempt_token_usage = TokenUsage.from_generation_result(result)

            raw_responses.append(result.text)
            prompts_used.append(...)
            total_tokens_used += int(getattr(result, "tokens_used", 0) or 0)
            ...
            seed_token_usages.append(attempt_token_usage)  # NEW: per token_usage_source combinato
            parsed = parse_simulator_response(result.text)
            consequences.extend(parsed)
            break
        except (JSONParseError, Exception) as e:
            if attempt_token_usage is not None:  # provider ha risposto, solo il parsing è fallito
                try:
                    from moralstack.persistence.write_queue import async_persist_llm_call
                    async_persist_llm_call(
                        phase="simulator_retry", module="simulator",
                        action=f"retry_failed_seeded_attempt_{total_attempts}",
                        prompt=f"Retry reason (seed={seed}): {e!s}"[:200],
                        raw_response="", duration_ms=0.0,
                        billable_provider_call=True, call_outcome="retry_failed",
                        token_usage_json=attempt_token_usage.to_json(),
                    )
                except Exception:
                    pass
            continue

...
return self._build_result(
    consequences=consequences, raw_response="\n---\n".join(raw_responses),
    parse_attempts=total_attempts, prompt="\n---\n".join(prompts_used),
    system_prompt=SIMULATOR_SYSTEM_PROMPT, tokens_used=total_tokens_used,
    prompt_tokens=(total_prompt_tokens if has_prompt_tokens else None),
    completion_tokens=(total_completion_tokens if has_completion_tokens else None),
    token_usage_source=TokenUsage.combine(seed_token_usages).source if seed_token_usages else "missing",  # NEW
)
```

`_build_result()` (già esteso da BLOCKING 3 con `token_usage_source`)
riceve qui il **combine** di tutti i seed riusciti (stesso helper
`TokenUsage.combine`, stessa semantica "meno certa vince" già usata per
`EnsembleResult`), non solo dell'ultimo — perché a differenza di
`_simulate_batch` (retry sullo stesso contenuto, solo l'ultimo tentativo
riuscito conta), qui **ogni seed è un contenuto/scenario diverso**, tutti
riflessi nel totale numerico finale.

**Limite esplicitamente accettato, non un gap**: il breakdown per-seed
(quale seed ha consumato quanti token) non è tracciato in
`SimulationResult`/`Consequence` (nessun campo "seed" su `Consequence`,
verificato) — aggiungerlo richiederebbe propagare l'identità del seed
attraverso `parse_simulator_response` fino a `Consequence`, una modifica di
forma più ampia e sproporzionata per questo task (PROJECT_SPEC §6, no
astrazioni speculative). Il totale numerico resta comunque **completo**
(nessun token perso), solo la granularità per-riga-di-audit resta a livello
di modulo, non di singola chiamata provider.

**E(b) — Hindsight: "individual evaluation" a zero token — CONFERMATO, bug
numerico, non solo gap di audit.** `evaluate()` (`hindsight_module.py:
622-693`) instrada su `_evaluate_individual()` quando `not
(self.config.use_batch_evaluation and len(consequences) > 1)` (riga 669) —
con `use_batch_evaluation: bool = True` di default (riga 467), **ogni
richiesta con una sola conseguenza simulata prende questo percorso di
default**, non è un edge case raro. `_evaluate_individual()` (righe
803-843) chiama `evaluate_scenario()` (righe 536-620) per ogni conseguenza,
che fa una vera chiamata `self.policy.generate(...)` (righe 568-580) con
proprio retry loop (righe 563-605). **Bug numerico**: `HindsightEvaluation`
(dataclass, righe 118-137) non ha mai avuto campi token —
`raw_response = result.text` (riga 582) e i token vengono scartati subito
dopo; `_evaluate_individual()` costruisce l'`HindsightResult` finale (righe
837-843) senza mai passare `tokens_used`/`prompt_tokens`/
`completion_tokens` — il default dataclass (`HindsightResult.tokens_used:
int = 0`, riga 282) resta `0` anche se N chiamate LLM reali sono state
fatte. Non è un fallback euristico: è una regressione di contabilità su
chiamate LLM reali, da correggere con lo stesso schema `TokenUsage`, non
solo da documentare. Anche il retry loop di `evaluate_scenario()` (righe
600-605, oggi solo `continue`) non persiste nulla sui tentativi falliti —
gap analogo (ma distinto) a quello già coperto da BLOCKING 3 per
`_evaluate_batch()` (righe 723-793).

Fix, 3 parti nello stesso file:

1. Nuovi campi additivi su `HindsightEvaluation`:
   ```python
   @dataclass
   class HindsightEvaluation:
       ...
       tokens_used: int = 0
       prompt_tokens: int | None = None
       completion_tokens: int | None = None
       token_usage_source: TokenUsageSource = "unknown"
   ```
2. `evaluate_scenario()` (righe 536-620): catturare i token subito dopo
   `generate()`, valorizzare i nuovi campi sul `return evaluation` di
   successo (riga 598), e persistere i tentativi falliti (righe 600-605)
   con lo stesso pattern guardrail `attempt_token_usage is not None` già
   usato negli altri 3 moduli (BLOCKING 3):
   ```python
   for attempt in range(self.config.max_retries):
       parse_attempts = attempt + 1
       attempt_token_usage: TokenUsage | None = None
       try:
           result = self.policy.generate(...)  # invariato
           from moralstack.observability.token_usage import TokenUsage
           attempt_token_usage = TokenUsage.from_generation_result(result)
           raw_response = result.text
           evaluation = parse_hindsight_response(raw_response, scenario_id=consequence.scenario_id)
           evaluation.scores = HindsightScores.compute_total(...)
           evaluation.tokens_used = attempt_token_usage.total_tokens
           evaluation.prompt_tokens = attempt_token_usage.input_tokens
           evaluation.completion_tokens = attempt_token_usage.output_tokens
           evaluation.token_usage_source = attempt_token_usage.source
           return evaluation
       except (JSONParseError, Exception) as e:
           last_error = str(e)
           if attempt_token_usage is not None:
               try:
                   from moralstack.persistence.write_queue import async_persist_llm_call
                   async_persist_llm_call(
                       phase="hindsight_retry", module="hindsight",
                       action=f"retry_failed_scenario_attempt_{parse_attempts}",
                       prompt=f"Retry reason (scenario={consequence.scenario_id}): {e!s}"[:200],
                       raw_response="", duration_ms=0.0,
                       billable_provider_call=True, call_outcome="retry_failed",
                       token_usage_json=attempt_token_usage.to_json(),
                   )
               except Exception:
                   pass
           continue
   # fallback pessimistico invariato (righe 607-620): nessun tentativo
   # riuscito, tokens_used resta 0 sul dataclass — corretto (i falliti sono
   # già persistiti singolarmente sopra, coerente con la scelta di design
   # di BLOCKING 3 di non rendere cumulativi i campi del risultato finale).
   ```
3. `_evaluate_individual()` (righe 803-843): aggregare i `TokenUsage` di
   tutte le `evaluations` riuscite nell'`HindsightResult` finale, stesso
   pattern già usato per `EnsembleResult` (BLOCKING 3):
   ```python
   from moralstack.observability.token_usage import TokenUsage
   usages = [TokenUsage(e.prompt_tokens or 0, e.completion_tokens or 0, e.tokens_used, e.token_usage_source) for e in evaluations]
   combined = TokenUsage.combine(usages)
   return HindsightResult(
       aggregated=aggregated, raw_response="", parse_attempts=total_attempts,
       prompt="\n---\n".join(prompts_used) if prompts_used else "",
       system_prompt=HINDSIGHT_SYSTEM_PROMPT,
       tokens_used=combined.total_tokens,
       prompt_tokens=combined.input_tokens,
       completion_tokens=combined.output_tokens,
       token_usage_source=combined.source,  # già previsto da BLOCKING 3
   )
   ```

**E(c) — Perspective: N chiamate reali, 1 riga aggregata — CONFERMATO,
design aggregato corretto, arricchimento minimo richiesto.**
`_evaluate_single_perspective()` (`perspective_module.py:687-740`) fa **una
vera chiamata `generate`/`generate_messages` per prospettiva** (righe
715-730), invocata una volta per ciascuna `perspective` in
`_evaluate_parallel()`/`_evaluate_sequential()` — con `max_perspectives=2`
(default, riga 390) sono **2 chiamate reali distinte al provider**, non 1
chiamata che produce N output strutturati. `EnsembleResult` **somma
correttamente** i token di tutte le prospettive (righe 632, 682) — nessuna
perdita numerica. `deliberation_runner.py::_evaluate_perspectives` (righe
3197-3286) persiste **una sola riga** `llm_calls` per l'intera valutazione
(riga 3242), concatenando già `prompt`/`raw_response` di tutte le
prospettive (righe 3238, 3259-3261) — il contenuto testuale non è perso,
solo non è in righe separate.

**Decisione di design (esplicita, non un errore di targeting)**: mantenere
**1 riga per invocazione di modulo**, coerente con la convenzione già
adottata ovunque in questo piano per il tentativo riuscito finale di ogni
modulo — non 1 riga per singola chiamata provider. Il totale
`usage`/audit resta numericamente completo (Product decision 1 soddisfatta:
costo reale sostenuto, non provenienza granulare per-round-trip); passare a
N righe per N prospettive richiederebbe estendere lo schema/i punti di
persistenza per un guadagno di granularità già ricostruibile diversamente
(sotto) — sproporzionato rispetto al diff minimo richiesto (PROJECT_SPEC
§6).

**Arricchimento richiesto (piccolo, quasi gratuito)**: `PerspectiveResult`
ha già `perspective_id`/`tokens_used`/`prompt_tokens`/`completion_tokens`
(`perspective_module.py:77, 86-88`) — arricchire `parsed_summary_json` del
call site esistente (`deliberation_runner.py:3262`) con il breakdown
per-prospettiva già disponibile, senza nuovi campi sui dataclass:

```python
# deliberation_runner.py:3262, sostituire:
"parsed_summary_json": json.dumps({
    "context_shape": _context_shape_payload(request, "perspectives"),
    "per_perspective_tokens": {  # NEW
        r.perspective_id: {
            "total_tokens": r.tokens_used,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
        }
        for r in (getattr(result, "results", None) or [])
    },
}),
```

Nessuna modifica a `perspective_module.py` richiesta (i dati esistono già).
**Fuori scope esplicito**: lo stesso breakdown per-seed per il simulator
seeded (E-a) non è aggiunto perché quel dato non esiste ancora da nessuna
parte (nessun campo "seed" su `Consequence`) — asimmetria intenzionale tra
E(a) ed E(c), non un'incoerenza.

#### BLOCKING F — Flag di completezza sincrono per `request_token_usage` (v4)

Vedi Decisione 4 rivista (rinomina "totale autoritativo" → "riepilogo
sincrono best-effort") per il contesto completo. Qui il design del flag
`usage_may_be_incomplete`/`incomplete_reason`.

**Vincolo verificato e rispettato**: il meccanismo di contatore/
finalizzazione differita/timer rimosso in v3
(`Revision log v3, Decisione 8/BLOCKING 1`) **non viene reintrodotto**. Il
flag sotto è una singola scrittura booleana sincrona, mai una lettura
d'attesa (`peek`) né un contatore incrementale.

**Unica condizione oggi nota e verificabile in modo sincrono al momento
della finalizzazione**: una speculative call è stata scartata (`abandon()`
invocato) per questo `(run_id, request_id)` durante il processing della
richiesta corrente. `abandon()` è sincrono fino all'avvio del thread `_bg`
(`speculative_overlap.py:127-170` — fix di contesto già progettato in
BLOCKING 1) — quindi il flag può essere impostato **nel thread chiamante**,
prima che qualunque `return` raggiunga `_attach_trace_and_return`, senza
alcuna attesa. Verificati **6 call site** di `abandon()`, non solo i 3 già
citati in BLOCKING 1: `controller.py:1378, 2213, 2258, 2528, 2553, 2594` —
tutti sincroni, tutti precedenti al funnel di uscita. Poiché il fix vive
dentro `abandon()` stesso (non duplicato per call site), tutti e 6 sono
coperti automaticamente.

**Nessun'altra fonte nota è candidabile a questo flag in modo sincrono**:
- Le righe di retry fallito (BLOCKING 3/D/E) sono sincrone, dentro il ciclo
  deliberativo, già presenti nell'accumulatore **prima** della
  finalizzazione — non generano incompletezza.
- `quick_check()` (BLOCKING D) è sincrona nello stesso modo.
- Il drop della coda write_queue (rischio P2, R1) **non è rilevabile in
  modo sincrono per costruzione** — resta un limite distinto, non coperto
  da questo flag (nessuna falsa sicurezza: il flag protegge solo dal caso
  "nota ma non ancora risolta", non dal caso "persa silenziosamente").

**Call site — `speculative_overlap.py::abandon()`**, subito dopo la
cattura del contesto già prevista dal fix di BLOCKING 1, **prima** di
avviare `_bg`:

```python
if captured_run_id and captured_request_id:
    try:
        from moralstack.observability.request_token_accumulator import mark_request_usage_partial
        mark_request_usage_partial(captured_run_id, captured_request_id, reason="speculative_discard_pending")
    except Exception:
        _LOG.debug("mark_request_usage_partial failed", exc_info=True)
```

**Propagazione**: `finalize_and_persist()` include i 2 nuovi campi nel
`RequestTokenTotals` restituito e nel payload dell'evento
`EVENT_REQUEST_TOKEN_USAGE_FINALIZED`; `controller.py::
_finalize_token_accounting` li copia su `ResponseMetadata`
(`m.usage_may_be_incomplete`, `m.incomplete_reason`); `GovernanceMetadata`
(Decisione 6) li specchia con gli stessi nomi, default `False`/`None`.

**Schema**: le 2 colonne `usage_may_be_incomplete`/`incomplete_reason` sono
parte della `CREATE TABLE IF NOT EXISTS request_token_usage` fin dalla
definizione originale (Decisione 2) — **consolidato in v5, BLOCKING
v5-6**: non più una "estensione additiva" descritta separatamente qui,
vedi Decisione 2 per lo schema completo e la motivazione.
`get_token_usage_totals()` (`read_store.py`, Decisione 2) le include nel
dict restituito.

**Effetto su R8 (vedi Risks)**: il limite resta lo stesso (usage HTTP può
non includere una speculativa scartata risolta tardi), ma ora è
**segnalato esplicitamente** al chiamante via `usage_may_be_incomplete=True`,
non più silenzioso.

---

#### BLOCKING v5-1 — Speculative call senza token usage nell'evento emesso (v5)

**Causa radice, verificata**: `_speculative_generate` (`controller.py:907-1016`)
esegue la vera chiamata a `controller.py:941-957` ma `persist_kwargs`
(righe 973-1000) non include mai `token_usage_json`. Il dict fluisce
invariato sia a `join_for_consumer()` (`speculative_overlap.py:103-109`,
`call_outcome="used"`) sia al thread `_bg` di `abandon()`
(`speculative_overlap.py:159-165`, `call_outcome="discarded"`) — stesso
oggetto restituito dalla stessa `Future`, quindi un solo punto di fix copre
entrambi gli esiti.

**Fix — solo `controller.py::_speculative_generate`**, nessuna modifica a
`speculative_overlap.py` per questo bug:

```python
            except TypeError:
                result = self.policy.generate(prompt_text)
            elapsed = (time.time() - start) * 1000
            from moralstack.observability.token_usage import TokenUsage  # NEW v5
            speculative_token_usage = TokenUsage.from_generation_result(result)  # NEW v5
            response_text = getattr(result, "text", None) or str(result)
            ...
            persist_kwargs: dict[str, Any] = {
                "cycle": 0,
                "phase": "speculative_generate",
                "module": "policy",
                "action": "generate (speculative)",
                "model": policy_model_str,
                "started_at": int(start * 1000),
                "duration_ms": elapsed,
                "prompt": prompt_used,
                "system_prompt": system_used or "",
                "raw_response": response_text,
                "parsed_summary_json": json.dumps({...}, ensure_ascii=False),
                "sequence_in_cycle": 0,
                "call_kind": "speculative",
                "token_usage_json": speculative_token_usage.to_json(),  # NEW v5
            }
```

`TokenUsage` viene importata a livello di modulo in `controller.py` (stesso
stile già usato per gli import da `moralstack.observability.*`), non
localmente dentro la funzione — coerente con l'import di
`get_current_run_id` già pianificato dalla Decisione 4.

**Perché basta questo fix**: `billable_provider_call` per questa riga resta
`True` di default (Decisione 8/BLOCKING 2, riga già emessa come chiamata
reale al provider, sia essa poi usata o scartata) — nessun cambiamento
necessario lì. Se `self.policy.generate(...)` fallisce del tutto (eccezione
catturata dal blocco `except Exception` più esterno), la funzione ritorna
`(None, None)` come già oggi — nessuna riga viene emessa, comportamento
corretto e invariato (nessun token fantasma).

#### BLOCKING v5-2 — `TokenUsage.combine()` può azzerare conteggi noti (v5)

Vedi correzione a Decisione 1 (`to_json()`) e Product decision 4 sopra —
non ripetuta qui per evitare duplicazione. Riassunto: `to_json()` ritorna
`None` sse `total_tokens==0 AND source=="missing"`, non `source` da solo.
Nessuna modifica a `combine()` stesso (la logica di sum + worst-source
resta corretta); il fix è interamente nella condizione di `to_json()`,
quindi in un solo metodo di un solo file (`observability/token_usage.py`,
non ancora creato — il fix è incorporato direttamente nella prima
implementazione, PR1, nessuna PR aggiuntiva).

**Verificato — nessuna regressione**: `tests/test_orchestrator.py:1630-1641`
(unico test esistente che tocca la funzione consumer,
`_token_usage_json_from_result`) usa un `GenerationResult`-like con
`tokens_used=120` (>0) e nessun attributo `source` — `from_generation_result`
assegna `source="unknown"` (non `"missing"`, perché `tokens_used != 0`),
quindi `to_json()` non è `None` con la regola vecchia **né** con quella
nuova. Nessun test esistente pinna il caso `source=="missing"` con
`total_tokens>0` (il caso che il fix corregge) perché quel caso non era
raggiungibile prima dell'introduzione di `combine()` in questo stesso piano.

#### BLOCKING v5-3 — Contraddizione Goal vs design aggregato (v5, solo testo)

**Decisione di prodotto già presa dall'utente**: mantenere il design
aggregato esistente (Decisione 8/BLOCKING E-c: 1 riga `llm_calls` per
invocazione di modulo, non per round-trip provider) — vedi Product decision
v5/10. Correzioni testuali applicate: Goal, Target behavior punto 1, nota
su `llm_call_count` (Decisione 2). **Nessuna riga di codice cambia per
questo punto** — è puramente una correzione di formulazione del piano, non
introduce PR né tocca Files to modify.

Verificato che il design aggregato è già corretto/completo dove esiste
oggi: `perspective_module.py:621-634,671-684` somma correttamente i token
di tutte le prospettive; `deliberation_runner.py:3242-3265` persiste 1 riga
aggregata con breakdown per-prospettiva già pianificato (BLOCKING E-c);
`safe_refusal_generator.py` (BLOCKING 4) già usa `TokenUsage.combine()` per
sommare fino a 2 chiamate (originale + retry anti-leak) in un solo
`RefusalGenerationResult.token_usage`, persistito come 1 sola riga.

#### BLOCKING v5-4 — Model mancante sulle righe retry-failed/refusal (v5)

**Payload da correggere, tutti verificati mancanti di `model`** (aggiungere
`"model": str(getattr(self.policy, "model", "") or ""),` — mai `model=None`
esplicito, per evitare che `write_queue.py::async_persist_llm_call`
(`kwargs.get("model", "")`) riceva una chiave presente con valore `None`,
che sovrascriverebbe il default `""`):

1. Pattern generico retry-falliti (Decisione 8/BLOCKING 3), applicato in
   tutti e 4 i moduli (`critic_module.py`, `simulator_module.py`,
   `hindsight_module.py`, `perspective_module.py`).
2. **Precedente reale già esistente** in `simulator_module.py:472-479`
   (`async_persist_llm_call(phase="simulator_retry", ...)`) — già in scope
   di correzione per la rimozione di `if attempt > 0:` (BLOCKING 3): stesso
   PR, aggiungere anche `model` qui.
3. BLOCKING E-a (simulator seeded retry) e BLOCKING E-b (hindsight
   individual retry): stesso pattern, nessuna chiave `model`.
4. `refusal_handler.py:147-158` (BLOCKING 4):
   ```python
   self._events.emit_llm_call(
       cycle=0, phase="refusal", module="orchestration",
       action="refuse (fast_path)", duration_ms=_refusal_duration_ms,
       model=str(getattr(self.policy, "model", "") or ""),  # NEW v5
       prompt=refusal_result.user_prompt, system_prompt=refusal_result.system_prompt,
       raw_response=refusal_content, attempts=refusal_result.attempts,
       sequence_in_cycle=6,
       token_usage_json=refusal_result.token_usage.to_json(),
   )
   ```
5. `response_assembler.py:303-322` (BLOCKING 4): aggiungere
   `"model": str(getattr(self.policy, "model", "") or ""),` al dict passato
   a `record_llm_call(None, None, {...})`, insieme a `token_usage_json`
   (già pianificato).

**Normalizzazione (non un nuovo bug, precisione aggiuntiva)**:
`_persist_quick_check` (BLOCKING D) già include `model` ma con
`getattr(self.policy, "model", None)` — può passare `None` esplicito.
Correggere in `model=str(getattr(self.policy, "model", "") or "")` per
coerenza con il pattern sopra e per evitare la stessa insidia
chiave-presente-con-None.

**Nessuna modifica necessaria**: `final_revalidation.py:577-597` include
già `"model": str(getattr(policy, "model", "") or "")` — pattern di
riferimento per tutti i fix sopra, non toccare.

**Nessuna PR aggiuntiva**: le correzioni sopra si applicano dentro le PR
già pianificate (PR14 per i retry-failed dei 4 moduli + il precedente
simulator, PR15 per refusal_handler.py/response_assembler.py, PR17 per la
normalizzazione di quick_check, PR18 per E-a/E-b) — solo estensione dello
scope descritto in Decisione 7, nessun nuovo numero di PR.

#### BLOCKING v5-5 — `usage_may_be_incomplete` non copre `shutdown_executor()` (v5, limite accettato)

**Verificato**: `shutdown_executor()` (`speculative_overlap.py:51-58`)
chiama `abandon()` implicitamente quando né `join_for_consumer()` né
`abandon()` sono già stati invocati. Tutti i 5 percorsi di routing normali
(refuse/benign/safe_complete/fast_path/deliberative, `controller.py:2527-2599`)
chiamano esplicitamente uno dei due **prima** di ritornare — per questi,
`shutdown_executor()` in `finally` (`controller.py:2675-2678`) è un no-op.
Il path implicito scatta **solo** su eccezione sollevata tra la creazione
di `spec_handle` (`controller.py:2066`) e la determinazione del `route`
(`controller.py:2503-2526`), catturata dai 4 blocchi `except` a
`controller.py:2613-2674` — tutti con pattern `return
self._attach_trace_and_return(...)` **diretto, senza variabile locale**.

**Risultato dell'analisi**: per costruzione del linguaggio, `finally`
esegue prima del ritorno effettivo al chiamante, ma non ha alcun
riferimento all'oggetto `OrchestratorResult`/`ResponseMetadata` già
costruito (nessuna variabile intermedia lo cattura in nessuno dei 4 rami
`except` verificati) — quindi anche se `mark_request_usage_partial()` gira
dentro `abandon()` chiamato implicitamente da `shutdown_executor()` in
questo punto (**gira comunque**, perché è lo stesso metodo `abandon()` già
pianificato da BLOCKING F, nessun codice nuovo da scrivere), non può più
raggiungere né l'oggetto già restituito né la entry dell'accumulatore (già
rimossa dal `pop` interno a `finalize_and_persist`, eseguito **prima** che
`finally` giri). Riaprire questo caso richiederebbe o (i) refactoring di
tutti gli `except`/route-return per catturare il risultato in una variabile
visibile a `finally` (~11 siti, PROJECT_SPEC §6 lo esclude come
sproporzionato per un edge case), o (ii) un `UPDATE` posteriore sulla riga
già persistita (esplicitamente rifiutato in precedenza dall'utente per lo
stesso genere di meccanismo, v3 Revision log).

**Fix: solo documentazione, nessun file di codice aggiuntivo.** Aggiornato
R21 (vedi Risks) e aggiunto un acceptance criterion esplicito che descrive
questo limite come noto e accettato, non silenzioso — distinto dal caso già
coperto (i 6 call site espliciti di `abandon()`, BLOCKING F/v4, che restano
correttamente coperti).

#### BLOCKING v5-6 — Migrazione schema incoerente per le 2 colonne v4 (v5)

Vedi Decisione 2 (schema consolidato). Le 2 colonne `usage_may_be_incomplete`/
`incomplete_reason` sono ora parte della `CREATE TABLE` originale di PR5,
non di una PR/migrazione successiva — vedi anche la nota aggiornata su
PR20 in Decisione 7 sotto.

---

#### BLOCKING v6-1 — Righe di refusal "no-provider" contate come billable provider call (v6)

**Causa radice, verificata**: `RefusalHandler.handle()` riceve `self.policy`
dal costruttore di `OrchestrationController` (`controller.py:145`, `policy:
PolicyLLMProtocol | None`, propagato a `RefusalHandler(policy=policy, ...)`
a `controller.py:201-205`) — `self.policy` **può essere `None`** (nessuna
guardia nel costruttore). `RefusalHandler.handle()` (`refusal_handler.py:
93-104`) passa quel valore, invariato, come `llm_client` a
`generate_llm_safe_refusal_detailed(...)`. Quando `llm_client is None`,
`generate_llm_safe_refusal_detailed` (`safe_refusal_generator.py:599-605`)
ritorna immediatamente `RefusalGenerationResult(text=_fallback_refusal(),
system_prompt=system, user_prompt=user_msg, attempts=0)` — **nessuna
chiamata al provider è mai avvenuta**. Nonostante questo,
`RefusalHandler.handle()` (`refusal_handler.py:147-158`) emette
**incondizionatamente** una riga `llm_calls` via
`self._events.emit_llm_call(...)`, senza guardia su `attempts`. Stessa forma
in `final_revalidation.py::_generate_post_revalidation_refusal`: `llm_client
= policy if callable(generate) else None` (righe 538-540) può essere
`None`; la chiamata (righe 557-567) e la persistenza incondizionata via
`record_llm_call(...)` (righe 576-597) seguono lo stesso schema — nessuna
guardia su `refusal_result.attempts`. Questi sono esattamente i call site
già toccati dal fix di BLOCKING 4/v5-4 per `token_usage_json`/`model` — il
fix sotto si integra in quei payload, non li duplica.

**Verificato — `response_assembler.py` NON è affetto**: il blocco che
chiama `generate_llm_safe_refusal_detailed` (righe 286-296) e persiste
(righe 304-322) è interamente **dentro** `if self.policy is not None:`
(riga 261) — quando `self.policy` è `None`, l'intero blocco (inclusa la
persistenza) è saltato, non solo la chiamata LLM (il branch alternativo,
righe 353-380, è solo formattazione strutturale, nessuna chiamata
LLM/nessuna persistenza). Per costruzione, `llm_client=self.policy` passato
qui **non è mai `None`**, quindi `refusal_result.attempts` è sempre `>= 1`
— nessuna riga "no-provider" può essere emessa da questo file. Nessuna
modifica richiesta a `response_assembler.py` per questo BLOCKING.

**Fix — integrato nei payload già pianificati da BLOCKING 4/v5-4 per gli
stessi due call site, non un fix separato**:

1. `refusal_handler.py:147-158`:
   ```python
   self._events.emit_llm_call(
       cycle=0, phase="refusal", module="orchestration",
       action="refuse (fast_path)", duration_ms=_refusal_duration_ms,
       model=str(getattr(self.policy, "model", "") or ""),
       prompt=refusal_result.user_prompt, system_prompt=refusal_result.system_prompt,
       raw_response=refusal_content, attempts=refusal_result.attempts,
       sequence_in_cycle=6,
       token_usage_json=refusal_result.token_usage.to_json(),
       billable_provider_call=refusal_result.attempts > 0,  # NEW v6-1
   )
   ```
2. `final_revalidation.py:576-597`, stesso principio nel dict passato a
   `record_llm_call(None, None, {...})`: aggiungere
   `"billable_provider_call": refusal_result.attempts > 0,`.

Nessuna modifica a `safe_refusal_generator.py` oltre a quanto già
pianificato in BLOCKING 4: il campo `attempts` esiste già su
`RefusalGenerationResult` (righe 19-43), popolato correttamente a `0` sul
ramo `llm_client is None` (riga 604) — solo lettura del campo esistente.

**Chiusura della QUESTION di Codex — "billable ma missing" non si applica
qui**: a differenza dei 4 retry loop runtime (BLOCKING 3/D/E), dove il
guardrail `attempt_token_usage is not None` impedisce di persistere
qualunque riga quando `generate(...)` solleva un'eccezione prima di
produrre un risultato, `refusal_handler.py`/`final_revalidation.py` non
persistono una riga per tentativo ma **una riga per l'intera generazione**
(Decisione 8/BLOCKING E-c, Product decision v5/10). All'interno di quella
riga aggregata sono raggiungibili solo due stati: `attempts == 0`
(`llm_client` era `None`, nessuna chiamata mai tentata →
`billable_provider_call=False`, questo fix) oppure `attempts >= 1`
(`llm_client` non era `None`, almeno una chiamata reale è stata emessa via
`_llm_refusal_call`, `safe_refusal_generator.py:540-556` — anche se quella
chiamata fallisce internamente, la funzione cattura l'eccezione e ritorna
`("", TokenUsage(0,0,0,"missing"))` invece di propagarla, comportamento
pre-esistente invariato da questo piano — la riga resta correttamente
`billable_provider_call=True` con `token_usage_json` eventualmente `None`,
`source="missing"`: una chiamata reale è stata comunque tentata verso il
provider, esattamente la semantica di `billable_provider_call` stabilita
da BLOCKING 2). Il pattern "generate() fallito prima di un risultato,
nessuna riga" (BLOCKING 3) non è strutturalmente applicabile a questa
granularità a-riga-singola: o `llm_client` era `None` dall'inizio
(`attempts=0`), o è stato invocato con esito noto (successo o fallimento
catturato, comunque billable) — nessun terzo stato/campo nuovo richiesto.

#### BLOCKING v6-2 — Linguaggio "canonico/completo" per la query di ricostruzione va declassato (v6)

**Decisione di prodotto già presa dall'utente**: declassare il linguaggio
del piano da "fonte canonica/completa per billing/compliance" a "fonte più
completa disponibile tra le righe effettivamente persistite" — nessun
cambio di design/persistenza, nessuna nuova PR per rendere la coda
non-lossy (P2 resta fuori scope, R1 continua a documentare il limite come
accettato non eliminato).

**Causa radice, verificata**: dalla v4 in poi (BLOCKING F) il piano
promuoveva la query di ricostruzione `SUM(...) FROM llm_calls` a "fonte
CANONICA per l'audit completo/tardivo... quando serve garanzia di
completezza (offline, billing, compliance)". Affermazione inaccurata:
`ObservabilityWriteQueue._enqueue()` (`observability/write_queue.py:
175-181`) chiama `self._queue.put_nowait(item)` dentro un `try`/`except
queue.Full: self._record_drop(...)` **generico**, senza distinzione per
tipo di evento. `submit_envelope()` (`write_queue.py:91-96`) — il punto
d'ingresso usato da `ObservabilityService.emit()`, già citato in Decisione
4 — instrada **ogni** `EventEnvelope`, incluso `EVENT_LLM_CALL`, per lo
stesso `_enqueue()` usato da `EVENT_REQUEST_TOKEN_USAGE_FINALIZED`. Non
esiste una coda separata o prioritaria per `EVENT_LLM_CALL`: entrambe le
fonti condividono esattamente la stessa coda FIFO best-effort lossy. La
query di ricostruzione non è quindi immune al limite che
`request_token_usage` già riconosce di avere — è semplicemente **più
granulare** (la perdita di una singola riga `llm_calls` su N produce una
sottostima silenziosa parziale, senza segnale di incompletezza per questo
caso specifico), e resta comunque preferibile per l'audit tardivo/offline
perché copre anche i casi noti già esclusi da `request_token_usage` per
costruzione (speculative discard non ancora risolto, BLOCKING 1/F) — da
qui "la fonte più completa disponibile tra le righe effettivamente
persistite", non "canonica"/"garanzia di completezza".

**Nessun cambio di design**: puramente testuale — non introduce nuovi
meccanismi (nessuna coda prioritaria, nessun retry di scrittura, nessun
canale non-lossy per `EVENT_LLM_CALL`), coerente con la decisione di
prodotto dell'utente e con P2 già accettato come fuori perimetro. Nessuna
riga di codice applicativo cambia — solo testo del piano/docs (§8), stesso
trattamento già usato per BLOCKING v5-3. Correzioni applicate a: Target
behavior punto 3, Decisione 4, Query target, Files to modify (bullet docs
v4), Risks (R1 esteso, R21), Acceptance criteria (nuovo criterio).

---

#### BLOCKING v7-1 — Colonne numeriche SQLite ancora NULL su aggregati misti (v7)

Vedi correzione a Decisione 1 (`from_json`) e Decisione 2 (derivazione
colonne numeriche) sopra — non ripetuta qui per evitare duplicazione.
Riassunto causa radice: BLOCKING v5-2 ha corretto la nullabilità di
`to_json()` a `total_tokens==0 AND source=="missing"`, ma la derivazione
delle colonne numeriche in `sqlite_sink.py` (Decisione 2) usava ancora la
regola vecchia (`NULL` se `source=="missing"` da sola) — le due
rappresentazioni (JSON e colonne SQL) erano scollegate, e la query di
ricostruzione somma le **colonne**, non il JSON, sottocontando esattamente
gli aggregati misti che v5-2 doveva preservare. Fix: la derivazione delle
colonne usa ora la stessa condizione di `to_json()`, resa provabilmente
equivalente per costruzione — nessuna riga di codice diversa da quanto già
pianificato nella prima implementazione della Decisione 2 (il fix è
incorporato nel design prima che il codice esista, come già per v5-2/PR1).

#### BLOCKING v7-2 — Query di breakdown senza filtro `billable_provider_call`; accumulatore non allineato (v7)

Vedi correzioni a Query target (blocco SQL breakdown), Decisione 2 (bullet
`read_store.py`) e Decisione 4 (`emit()`/`emit_batch()`) sopra. Causa
radice: la query "Breakdown per modulo e modello" non aveva il predicato
`AND COALESCE(billable_provider_call, 1) = 1` che invece la query di
ricostruzione già aveva; lo pseudocodice `emit()` della Decisione 4 non
implementava mai il filtro billable che la prosa di BLOCKING 2 dichiarava
già — un'incoerenza testuale interna al piano (nessun gap nel codice
reale, dato che l'hook non esiste ancora). Fix: predicato billable
applicato in modo esplicito e coerente a 3 punti — accumulatore
(`emit()`/`emit_batch()`), riepilogo `request_token_usage`
(`llm_call_count`/`missing_usage_count`/`estimated_usage_count` contano
solo righe billable) e query di breakdown/ricostruzione offline. Chiude
anche una QUESTION di Codex sulla definizione di `llm_call_count`: conta
solo righe `billable_provider_call=True` (default se assente), non tutte
le righe di audit — le righe diagnostiche non-billable (speculative-reuse,
leakage detection, skipped rewrite, critic-skip; siti verificati
`deliberation_runner.py:866-887, 909-937` più i 2 gemelli deliberativi)
alimentano `llm_calls` per l'audit qualitativo ma non contribuiscono al
riepilogo sincrono né alla query offline.

#### BLOCKING v7-3 — `model` mancante su righe billable pre-esistenti oltre a retry/refusal (v7)

**Causa radice, verificata su 6 call site**: BLOCKING v5-4 ha normalizzato
`model` solo sui payload NUOVI/pianificati da questo piano (retry-failed,
refusal). Verificati sul codice reale come billable e privi di `model`:

| # | Call site | Metodo | `model` oggi |
|---|---|---|---|
| 1 | `deliberation_runner.py:667-683` | `run_benign_fast_path` | assente |
| 2 | `deliberation_runner.py:772-788` | `run_safe_complete_path` | assente |
| 3 | `deliberation_runner.py:947-968` | `run_fast_path` | assente |
| 4 | `controller.py:1227-1243` | `_regenerate_for_contract` | assente |
| 5 | `controller.py:1309-1326` | `_revalidate_draft` | assente, **caso speciale** |
| 6 | `controller.py:971-978` | `_speculative_generate` | presente ma può essere `None` esplicito |

**Fix 1-4, siti 1-4** — pattern uniforme già stabilito dal piano (identico
a `final_revalidation.py:587`): aggiungere
`"model": str(getattr(self.policy, "model", "") or ""),` ai
`persist_kwargs`/dict passati a `record_llm_call`. `self.policy` è
l'attributo già usato in tutti i call site adiacenti in entrambi i file
(verificato).

**Fix 5, sito 5 (`_revalidate_draft`), caso speciale, NON il pattern
generico**: questo call site invoca `self.policy.generate(...,
model_override=get_dccl_llm_model())` (riga 1294) — il modello
**effettivamente usato** è `get_dccl_llm_model()`, non `self.policy.model`.
`GenerationResult` (`models/base.py:107-134`) non ha alcun campo che
rifletta l'override — applicare qui il pattern generico registrerebbe
silenziosamente il modello **sbagliato** nel breakdown per-modello (uno
degli obiettivi dichiarati del Goal). Fix corretto:
`"model": get_dccl_llm_model(),` (già importato in questo file per la
chiamata alla riga precedente — nessun nuovo import). Questa è l'unica
eccezione motivata al pattern uniforme in tutto il piano: ogni altro sito
che tocca `model` usa `self.policy.model` perché non applica mai un
`model_override` diverso dal default della policy.

**Fix 6, sito 6 (`_speculative_generate`), bug pre-esistente e indipendente
da questo piano, corretto nello stesso punto già toccato da BLOCKING
v5-1** (stesso file, stesso blocco `persist_kwargs`, minimo overhead):
sostituire
```python
policy_model = getattr(self.policy, "model", None)
policy_model_str = str(policy_model) if policy_model is not None else None
```
con
```python
policy_model_str = str(getattr(self.policy, "model", "") or "")
```
(`"model": policy_model_str` nel dict resta invariato). Causa: `str(None)
if policy_model is not None else None` produce `None` esplicito quando
`self.policy.model` è assente — `write_queue.py:55`
(`kwargs.get("model", "")`) applica il default `""` solo a chiave
**assente**, non a chiave presente con valore `None` — stessa insidia già
corretta altrove da BLOCKING v5-4, mai chiusa in questo punto specifico.

**Chiusura QUESTION di Codex sul fallback `model`**: quando `policy`/
`self.policy` non espone `.model`, il fallback è sempre stringa vuota `""`
via `str(getattr(obj, "model", "") or "")` — nessun fallback più elaborato.
Unica eccezione motivata: il sito 5 sopra (modello effettivo = override
esplicito, non `policy.model`). Pattern di riferimento invariato:
`final_revalidation.py:587`.

**Vincolo di design per il NON_BLOCKING di Codex** (non tocca la sezione
Tests): `RefusalGenerationResult` (`safe_refusal_generator.py:19-43`) deve
dichiarare il nuovo campo `token_usage` con un default sicuro fin dalla sua
introduzione (Decisione 8/BLOCKING 4, punto 2):
```python
token_usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0, 0, "missing"))
```
coerente con lo stile già usato nello stesso dataclass per `leaked_terms`.
Necessario perché `tests/test_controller_speculative_lazy.py:101` e
`tests/test_refusal_handler_duration.py:111` costruiscono
`RefusalGenerationResult(...)` senza `token_usage` — con un default
esplicito questi test restano verdi senza modifiche.

**Estensione decisa dall'utente (Product decision v7/12) — fix sistemico su
`_policy_llm_model_for_action`/`_module_model`, implementazione corretta in
v8 (PROJECT_SPEC §9 — correzione di un errore di implementazione introdotto
in v7, non una nuova decisione di prodotto: la decisione di includere questo
fix nello scope del piano resta quella presa in v7/12)**: durante la
verifica del sito 6 è emerso che la stessa insidia "chiave `model` presente
con valore `None`" è condivisa da `_policy_llm_model_for_action`/
`_module_model` (`deliberation_runner.py:200-226`), usate da **~13 call site
billable pre-esistenti** oltre ai 6 sopra — inclusi i moduli **core** del
breakdown per-modello (`critic_model`/`sim_model`/`hindsight_model`/
`persp_model` a `deliberation_runner.py:2906, 3017, 3128, 3241`, più
`soft_model`/`reuse_model`/`rw_model`/`policy_model_label` nel path
deliberativo/rewrite, righe 2571, 2615, 2650, 2749). L'utente ha deciso di
includere il fix in questa revisione invece di rimandarlo.

Corpo reale delle 2 funzioni, verificato su `deliberation_runner.py:200-226`
(la settima review Codex ha bloccato una v7 che sostituiva interamente
questi corpi, perdendo logica di business esistente — la v8 corregge
normalizzando **solo** il confine "nessun modello disponibile"):

```python
def _policy_llm_model_for_action(policy: Any, action: str) -> str | None:
    """Effective OpenAI model name for policy generate vs rewrite (rewrite may use MORALSTACK_POLICY_REWRITE_MODEL)."""
    if policy is None:
        return None
    if action == "rewrite":
        rw = getattr(policy, "rewrite_model", None)
        if rw is not None:
            return str(rw)
    m = getattr(policy, "model", None)
    return str(m) if m is not None else None


def _module_model(module: Any) -> str | None:
    """Return the OpenAI model name used by a cognitive module (critic, simulator, …).

    Each module stores its inner ``OpenAIPolicy`` as ``self.policy``; fall back
    to ``module.model`` if present.
    """
    if module is None:
        return None
    inner = getattr(module, "policy", None)
    if inner is not None:
        m = getattr(inner, "model", None)
        if m is not None:
            return str(m)
    m = getattr(module, "model", None)
    return str(m) if m is not None else None
```

Fix corretto — diff minimo, **solo** sui 2 punti per funzione che oggi
ritornano `None` esplicito; firma, parametro `action`, branch
`rewrite_model` e lookup a due livelli (`module.policy.model` poi
`module.model`) restano **invariati**:

```python
def _policy_llm_model_for_action(policy: Any, action: str) -> str:
    """Effective OpenAI model name for policy generate vs rewrite (rewrite may use MORALSTACK_POLICY_REWRITE_MODEL)."""
    if policy is None:
        return ""  # era: return None
    if action == "rewrite":
        rw = getattr(policy, "rewrite_model", None)
        if rw is not None:
            return str(rw)
    m = getattr(policy, "model", None)
    return str(m) if m is not None else ""  # era: else None


def _module_model(module: Any) -> str:
    """Return the OpenAI model name used by a cognitive module (critic, simulator, …).

    Each module stores its inner ``OpenAIPolicy`` as ``self.policy``; fall back
    to ``module.model`` if present.
    """
    if module is None:
        return ""  # era: return None
    inner = getattr(module, "policy", None)
    if inner is not None:
        m = getattr(inner, "model", None)
        if m is not None:
            return str(m)
    m = getattr(module, "model", None)
    return str(m) if m is not None else ""  # era: else None
```

Nessun call site dei ~13 elencati richiede modifica propria — tutti leggono
già il valore di ritorno di queste 2 funzioni e lo passano come `model` nel
payload, quindi il fix a monte si propaga automaticamente. Verificare con
`mypy --strict` (`moralstack.orchestration.*` è sotto strict scope) che il
cambio dell'annotazione di ritorno da `str | None` a `str` non richieda
allineare a `str` eventuali variabili a valle annotate `str | None`.

---

### Decisione 7 — Sequenza PR consigliata

1. **PR1** — `observability/token_usage.py` (nuovo, isolato, testabile in
   unità, nessun consumer ancora).
2. **PR2** — `models/base.py` (+`token_usage_source`, riscrittura
   `token_usage_json()`), `models/policy.py` (`_complete()` a 6-tuple, 2 call
   site), `deliberation_runner.py::_token_usage_json_from_result` (propaga
   `source`, con default esplicito quando l'attributo manca sull'oggetto —
   vedi non-regressione test esistente).
3. **PR3** — `constitution/retriever.py`: sostituire i 3 blocchi inline con
   `TokenUsage.from_openai_usage()`, aggiungere `token_usage_json` a
   `_persist_constitution_llm_call` e ai 3 call site. Fixa il bug scoperto in
   sessione.
4. **PR4** — `orchestration/embedder.py::OpenAIEmbedder.embed()`.
5. **PR5** — schema SQLite: colonne numeriche su `llm_calls` + tabella
   `request_token_usage` + indice + `read_store` nuovi metodi. Nessun
   comportamento runtime cambia ancora (solo schema + lettura).
6. **PR6** — `observability/request_token_accumulator.py` + hook in
   `ObservabilityService.emit()`/`emit_batch()`.
7. **PR7** — `orchestration/types.py::ResponseMetadata` (+6 campi) e
   `orchestration/controller.py::_attach_trace_and_return`/
   `_finalize_token_accounting`.
8. **PR8** — `server/proxy.py::_build_synthetic_chat_completion` + 2 call
   site. Chiude il requisito esplicito del task.
9. **PR9** — `sdk/response.py::GovernanceMetadata`.
10. **PR10** — BLOCKING 5: chiave composita `(run_id, request_id)`
    nell'accumulatore (va prima di PR11, è un prerequisito strutturale).
11. **PR11** — BLOCKING 2/B: discriminatore `billable_provider_call`,
    inventario completo (schema + plumbing + 6 call site
    `deliberation_runner.py`: 2616-2639, 2651-2674, 2718-2742 + i 2 nuovi
    siti fast-path 866-887/909-937 + il sito condizionale critic-skip
    2907-2939 + filtro nell'hook dell'accumulatore, tutto nello stesso PR
    per evitare la divergenza R9).
12. **PR12** — BLOCKING 2/B (difetto cache-hit, R12): campo `from_cache` su
    `SimulationResult`/`HindsightResult`/`EnsembleResult` + lettura in
    `_simulate`/`_evaluate_hindsight`/`_evaluate_perspectives`. Va dopo
    PR11 perché usa lo stesso discriminatore `billable_provider_call`.
13. **PR13** — BLOCKING 3/C: `TokenUsage.from_generation_result`/`combine` +
    propagazione `token_usage_source` nei 5 dataclass runtime + riscrittura
    `_token_usage_json_from_result` (comportamento sul risultato finale
    riuscito, invariato numericamente).
14. **PR14** — BLOCKING 3/C (retry falliti, Product decision 6): accumulo
    per-tentativo nei 4 retry loop (critic/simulator/hindsight/
    perspective), correzione del precedente esistente in
    `simulator_module.py` (righe 466-481). Va dopo PR11 (usa
    `billable_provider_call=True` esplicito) e PR13 (stesso helper
    `TokenUsage`).
15. **PR15** — BLOCKING 4: `safe_refusal_generator.py` +
    `response_assembler.py` + `final_revalidation.py` (invariati dal v2) +
    **`refusal_handler.py`** (nuovo in v3, il vero path REFUSE principale).
16. **PR16** — BLOCKING 1 (+ fix prerequisito, semplificato in v3):
    `speculative_overlap.py` (sola propagazione contesto in `abandon()` —
    nessun contatore/timer, `controller.py::_finalize_token_accounting`
    resta invariato dalla Decisione 4 base).
17. **PR17** — BLOCKING D (v4): `critic_module.py::quick_check()` +
    `_persist_quick_check`. Indipendente dagli altri PR, va dopo PR11
    (usa `billable_provider_call`).
18. **PR18** — BLOCKING E/a+E/b (v4): retry falliti nel percorso simulator
    "seeded" (`_simulate_with_seeds`) e nel percorso hindsight "individual
    evaluation" (`evaluate_scenario`/`_evaluate_individual`), inclusi i
    nuovi campi token su `HindsightEvaluation`. Va dopo PR13/PR14 (stesso
    helper `TokenUsage`, stesso pattern retry-falliti).
19. **PR19** — BLOCKING E/c (v4): arricchimento `parsed_summary_json` con
    `per_perspective_tokens` in `deliberation_runner.py::
    _evaluate_perspectives`. Indipendente, nessuna dipendenza da altri PR
    v4.
20. **PR20** — BLOCKING F (v4): rinomina "totale autoritativo" → "riepilogo
    sincrono best-effort" (Decisione 4, Target behavior), nuovo
    `mark_request_usage_partial()` + 2 campi `RequestTokenTotals`, call
    site in `speculative_overlap.py::abandon()`, propagazione a
    `ResponseMetadata`/`GovernanceMetadata`. **v5: non crea più schema** —
    le 2 colonne `usage_may_be_incomplete`/`incomplete_reason` sono ora
    parte della `CREATE TABLE` originale di PR5 (BLOCKING v5-6). Va dopo
    PR16 (BLOCKING 1, stesso file `speculative_overlap.py`) e PR10 (chiave
    composita già in essere).
21. **PR21 (NEW v5)** — BLOCKING v5-1: `controller.py::_speculative_generate`
    cattura `TokenUsage.from_generation_result(result)` e la aggiunge a
    `persist_kwargs["token_usage_json"]`. Dipende da PR1 (`token_usage.py`)
    e PR13 (`from_generation_result`); nessuna modifica a
    `speculative_overlap.py` (il dict fluisce invariato a
    `join_for_consumer`/`abandon`). Va dopo PR16/PR20 (stesso dominio
    funzionale).

    Nota: PR14, PR15, PR17, PR18 estendono il proprio scope con l'aggiunta
    di `model` ai payload pianificati (BLOCKING v5-4) — nessun nuovo numero
    di PR per questo, solo estensione dello scope già assegnato.
22. Docs (§8) aggiornati nello stesso PR del comportamento corrispondente, non
    accumulati alla fine.

### Query target che il design deve rendere possibili

```sql
-- Riepilogo sincrono best-effort per singola domanda/risposta (v4: non più
-- "il totale" — vedi usage_may_be_incomplete; per il riepilogo più completo
-- disponibile usare la query di ricostruzione su llm_calls sotto)
SELECT input_tokens, output_tokens, total_tokens, llm_call_count,
       missing_usage_count, estimated_usage_count,
       usage_may_be_incomplete, incomplete_reason
FROM request_token_usage WHERE run_id = ? AND request_id = ?;

-- Breakdown per modulo e modello (solo righe billable — v7-2, stesso
-- predicato dell'accumulatore in-process e della query di ricostruzione
-- sotto: coerenza esplicita, non duplicazione accidentale)
SELECT module, phase, action, model,
       SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
       SUM(total_tokens) AS total_tokens, COUNT(*) AS calls,
       SUM(token_usage_missing) AS missing_usage
FROM llm_calls WHERE run_id = ? AND request_id = ?
  AND COALESCE(billable_provider_call, 1) = 1
GROUP BY module, phase, action, model ORDER BY module, phase, action, model;

-- Fonte PIÙ COMPLETA DISPONIBILE per l'audit tardivo/offline (v4, BLOCKING F;
-- terminologia corretta in v6, BLOCKING v6-2): usare questa, non
-- request_token_usage, quando serve il riepilogo più affidabile possibile
-- (offline, billing, compliance) — copre anche il caso
-- usage_may_be_incomplete=1 e il caso in cui la riga request_token_usage
-- manca del tutto (mitigazione P2). NON è una garanzia assoluta di
-- completezza: le righe llm_calls condividono la stessa coda best-effort
-- lossy di ogni altro evento osservabilità (observability/write_queue.py,
-- §5.6) e possono anch'esse essere scartate su queue.Full (rischio P2, R1,
-- pre-esistente, fuori scope di questo piano) — è "la fonte più completa
-- tra le righe effettivamente persistite", non "il totale garantito".
SELECT SUM(input_tokens), SUM(output_tokens), SUM(total_tokens), COUNT(*),
       SUM(token_usage_missing), SUM(token_usage_estimated)
FROM llm_calls WHERE run_id = ? AND request_id = ?
  AND COALESCE(billable_provider_call, 1) = 1;
```

## Alternatives considered (rejected)

- **A. Hook dell'accumulatore in `record_llm_call()` (`persistence_helpers.py`)**
  invece che in `ObservabilityService.emit()`. Scartata: `record_llm_call` è
  solo una delle 3 funzioni produttrici di `EVENT_LLM_CALL` — mancherebbero
  `persistence/sink.py::persist_llm_call` (usato da `constitution/retriever.py`
  / `compliance/dccl.py`) e `models/risk/estimator.py::persist_llm_call` (usato
  dai mini-estimatori); coprirebbe solo una parte dei moduli richiesti nel
  breakdown.
- **B. `ContextVar` invece di dict keyato per `request_id`** per l'accumulatore.
  Scartata: dipende dalla corretta propagazione del contesto asyncio/thread
  (rischio silente in path che usano thread pool senza `copy_context()`); un
  dict globale thread-safe keyato per `request_id` (già univoco) è più robusto
  e non richiede reset espliciti.
- **C. Sommare `llm_calls` via SQL invece di un accumulatore in-process** per
  popolare `usage` nel proxy in tempo reale. Scartata come unica fonte:
  richiederebbe un `flush()` sincrono della coda scrivente prima di ogni
  risposta proxy (bloccante, viola §5.6 in termini di latenza) — usata invece
  come fallback di riconciliazione offline, non come path primario.
- **D. Ogni produttore di JSON costruisce da sé il campo `source`** (nessuna
  dataclass condivisa). Scartata per DRY: è esattamente il pattern che ha
  causato il bug del `constitution_retriever` (3 copie della stessa logica,
  una delle quali non persiste affatto).
- **E. Estendere `GenerationResult` con un campo `TokenUsage` unico** invece di
  mantenere `prompt_tokens`/`completion_tokens`/`tokens_used` separati.
  Scartata per questo task: più invasiva (richiede toccare ogni lettore
  esistente, incluso `TokenCostTracker.add_call`); l'aggiunta di un solo campo
  `token_usage_source` con default retro-compatibile è il diff minimo.

## Files to modify

- `moralstack/observability/token_usage.py` — **NEW**: dataclass `TokenUsage` canonica.
- `moralstack/observability/request_token_accumulator.py` — **NEW**: accumulatore
  in-process + finalize/persist.
- `moralstack/observability/service.py` — hook accumulo in
  `emit()`/`emit_batch()` (righe 43-57).
- `moralstack/observability/events.py` — nuovo
  `EVENT_REQUEST_TOKEN_USAGE_FINALIZED`.
- `moralstack/observability/sinks/sqlite_sink.py` — nuove colonne `llm_calls`,
  nuova tabella `request_token_usage`, nuovo indice, nuovo writer, dispatch,
  `_FK_ORDER`.
- `moralstack/observability/read_store.py` — nuovi metodi di aggregazione
  (Protocol + impl).
- `moralstack/models/base.py` — `GenerationResult.token_usage_source` +
  `token_usage_json()` riscritto.
- `moralstack/models/policy.py` — `_complete()` a 6-tuple, 2 call site.
- `moralstack/orchestration/deliberation_runner.py` —
  `_token_usage_json_from_result`.
- `moralstack/constitution/retriever.py` — 3 blocchi inline + firma
  `_persist_constitution_llm_call`.
- `moralstack/orchestration/embedder.py` — `OpenAIEmbedder.embed()`.
- `moralstack/orchestration/types.py` — `ResponseMetadata` +6 campi.
- `moralstack/orchestration/controller.py` — import `get_current_run_id`,
  `_attach_trace_and_return` + nuovo `_finalize_token_accounting`.
- `moralstack/server/proxy.py` — `_build_synthetic_chat_completion` + 2 call site.
- `moralstack/sdk/response.py` — `GovernanceMetadata` +6 campi.
- `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/CODEBASE_FACTS.md`,
  `docs/modules/observability.md`, `docs/modules/persistence.md` — aggiornati
  per riflettere schema/comportamento nuovo.

### Aggiunte dopo il primo verdetto BLOCK (v2, Decisione 8)

- `moralstack/orchestration/speculative_overlap.py` — BLOCKING 1 (+ fix
  prerequisito, semplificato in v3): `abandon()` cattura esplicitamente
  `run_id`/`request_id`/`session_id`/`turn_number`/`cycle` prima di avviare
  `_bg`, li inietta in `merged` via `setdefault`. Nessun contatore/timer
  (rimossi in v3, vedi Decisione 8/BLOCKING 1).
- `moralstack/persistence/write_queue.py` — BLOCKING 2:
  `async_persist_llm_call` propaga `billable_provider_call` nel payload
  (righe 51-71).
- `moralstack/orchestration/safe_refusal_generator.py` — BLOCKING 4:
  `_llm_refusal_call` a `tuple[str, TokenUsage]`, `RefusalGenerationResult`
  +campo `token_usage`, propagazione nei 3 punti di ritorno di
  `generate_llm_safe_refusal_detailed`.
- `moralstack/orchestration/response_assembler.py` — BLOCKING 4: call site
  righe 304-322, +`token_usage_json` al payload. **Confermato in v3** come
  call site legittimo (REFUSE deliberativo), non un errore di targeting.
- `moralstack/orchestration/final_revalidation.py` — BLOCKING 4: call site
  righe 577-597, +`token_usage_json` al payload. **Confermato in v3** come
  call site legittimo (REFUSE post-revalidation).
- `moralstack/observability/read_store.py` — invariato nello scope, ma le
  query di `get_token_usage_totals`/`get_token_usage_breakdown` devono ora
  filtrare `WHERE COALESCE(billable_provider_call, 1) = 1` — stesso
  predicato della query di ricostruzione (Decisione 4) e dell'hook
  dell'accumulatore (Decisione 4, `emit()`/`emit_batch()`): un solo
  predicato billable condiviso da 3 punti del design, non 3 copie
  indipendenti (allineato in v7, BLOCKING v7-2, dopo che la query di
  esempio nella Query target ne era rimasta priva).
- `moralstack/persistence/db.py` — suggerimento non bloccante di Codex, a
  basso costo: `get_llm_calls_for_request` è già specchiato qui (righe
  76-77, delega a `_rs`); specchiare anche `get_token_usage_totals`/
  `get_token_usage_breakdown` con lo stesso pattern per coerenza con l'API
  di lettura esistente.

### Aggiunte dopo il secondo verdetto BLOCK (v3)

- `moralstack/orchestration/refusal_handler.py` — **NUOVO nello scope**
  (BLOCKING 4/A): `+"token_usage_json": refusal_result.token_usage.to_json()`
  al dict passato a `self._events.emit_llm_call(...)` (righe 147-158). È il
  vero call site del path REFUSE principale (`_route_refuse` →
  `RefusalHandler.handle()`), mancante nel v2.
- `moralstack/orchestration/deliberation_runner.py` — elenco
  `billable_provider_call` **corretto e ampliato** (BLOCKING 2/B): oltre ai
  3 siti v2 (righe 2616-2639, 2651-2674, 2718-2742), aggiunti righe 866-887
  (speculative-reuse fast_path), righe 909-937 (leakage_detected fast_path
  — il sito esatto citato dalla seconda review Codex), e righe 2907-2939
  (`_critique`, condizionale `not is_skipped`); più le 3 letture
  `from_cache` in `_simulate`/`_evaluate_hindsight`/`_evaluate_perspectives`
  (~righe 3018, 3129, 3242).
- `moralstack/runtime/modules/critic_module.py` — BLOCKING 3/C (oltre al
  campo `token_usage_source` già previsto dal v2): nuova persistenza
  per-tentativo-fallito nel loop 435-537 (solo ramo
  `JSONParseError`/`StructuredValidationError`/`PydanticValidationError`).
- `moralstack/runtime/modules/simulator_module.py` — BLOCKING 3/C:
  **correzione** del precedente di persistenza esistente (righe 466-481:
  rimossa condizione `if attempt > 0`, aggiunto `token_usage_json`
  mancante); BLOCKING 2/B: nuovo campo `from_cache: bool = False` su
  `SimulationResult`, impostato a `True` prima del `return` cache-hit
  (riga 355).
- `moralstack/runtime/modules/hindsight_module.py` — BLOCKING 3/C: nuova
  persistenza per-tentativo-fallito nel loop 723-793 (nessun precedente
  esistente); BLOCKING 2/B: nuovo campo `from_cache: bool = False` su
  `HindsightResult`, impostato a `True` prima del `return` cache-hit
  (riga 667).
- `moralstack/runtime/modules/perspective_module.py` — BLOCKING 3/C: nuova
  persistenza per-tentativo-fallito nel loop 707-740 (con binding esplicito
  dell'eccezione, oggi assente); BLOCKING 2/B: nuovo campo `from_cache:
  bool = False` su `EnsembleResult`, impostato a `True` prima del `return`
  cache-hit (riga 495).
- **Rimosso dal v2**: l'idea di rendere `tokens_used`/`prompt_tokens`/
  `completion_tokens` cumulativi sui 4 dataclass — sostituita dal design a
  righe di audit separate per tentativo (vedi BLOCKING 3, decisione di
  design).

### Aggiunte dopo il terzo verdetto BLOCK (v4)

- `moralstack/runtime/modules/critic_module.py` — BLOCKING D: nuovo metodo
  privato `_persist_quick_check`, chiamato da `quick_check()` (righe
  589-694) sia sul successo sia sul fallimento-dopo-risposta-provider
  (guardrail `quick_check_token_usage is not None`). Nessuna modifica a
  `QuickCheckResult` né al call site in `deliberation_runner.py:976`.
- `moralstack/runtime/modules/simulator_module.py` — BLOCKING E/a:
  `_simulate_with_seeds()` (righe 492-586) — persistenza dei tentativi
  falliti per-seed (stesso pattern del fix già pianificato per
  `_simulate_batch`), accumulo `seed_token_usages: list[TokenUsage]`,
  `token_usage_source` passato a `_build_result()` (già esteso da
  BLOCKING 3) via `TokenUsage.combine(...)`.
- `moralstack/runtime/modules/hindsight_module.py` — BLOCKING E/b: nuovi
  campi `tokens_used`/`prompt_tokens`/`completion_tokens`/
  `token_usage_source` su `HindsightEvaluation` (righe 118-137);
  `evaluate_scenario()` (righe 536-620) valorizza i nuovi campi sul
  successo e persiste i tentativi falliti (righe 600-605, oggi solo
  `continue`); `_evaluate_individual()` (righe 803-843) aggrega via
  `TokenUsage.combine(...)` nell'`HindsightResult` finale (oggi sempre a
  zero — bug numerico corretto, non solo gap di audit).
- `moralstack/orchestration/deliberation_runner.py` — BLOCKING E/c:
  `_evaluate_perspectives()` (`parsed_summary_json` a riga 3262) arricchito
  con `per_perspective_tokens` da `result.results` (dati già disponibili,
  nessuna modifica a `perspective_module.py`).
- `moralstack/observability/request_token_accumulator.py` — BLOCKING F:
  nuova funzione `mark_request_usage_partial(run_id, request_id, *,
  reason)`; `RequestTokenTotals` +2 campi (`usage_may_be_incomplete`,
  `incomplete_reason`); `finalize_and_persist()` li propaga.
- `moralstack/orchestration/speculative_overlap.py` — BLOCKING F: `abandon()`
  chiama `mark_request_usage_partial(...)` nel thread chiamante, subito dopo
  la cattura di contesto già prevista da BLOCKING 1 — stesso file, stesso
  punto, nessun nuovo call site introdotto altrove (copre tutti e 6 i punti
  di invocazione di `abandon()` verificati: `controller.py:1378, 2213, 2258,
  2528, 2553, 2594`).
- `moralstack/observability/sinks/sqlite_sink.py` — BLOCKING F: 2 nuove
  colonne su `request_token_usage` (`usage_may_be_incomplete INTEGER NOT
  NULL DEFAULT 0`, `incomplete_reason TEXT`), aggiunte alla definizione
  `CREATE TABLE IF NOT EXISTS` della Decisione 2 (non serve `ALTER TABLE`:
  la tabella è nuova in questo stesso piano).
- `moralstack/observability/read_store.py` — BLOCKING F:
  `get_token_usage_totals()` include i 2 nuovi campi nel dict restituito
  (Protocol + impl).
- `moralstack/orchestration/types.py` — BLOCKING F: `ResponseMetadata` +2
  campi (`usage_may_be_incomplete: bool = False`, `incomplete_reason: str |
  None = None`), oltre ai 6 già previsti dalla Decisione 4/6.
- `moralstack/sdk/response.py` — BLOCKING F: `GovernanceMetadata` specchia
  gli stessi 2 campi.
- `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/CODEBASE_FACTS.md`,
  `docs/modules/observability.md`, `docs/TRACES/observability_db_to_ui.md`
  — aggiornati con: (a) il reframing "riepilogo sincrono best-effort" per
  `request_token_usage` invece di "totale autoritativo"; (b) il flag
  `usage_may_be_incomplete`/`incomplete_reason` e la sua unica condizione di
  innesco nota; (c) la query di ricostruzione `SUM(...) FROM llm_calls` come
  **fonte più completa disponibile tra le righe effettivamente persistite**
  per l'audit tardivo/offline — non come garanzia assoluta di completezza
  (BLOCKING v6-2): esplicitare che anche `llm_calls` condivide la stessa
  coda best-effort lossy (§5.6, rischio P2/R1 pre-esistente).

### Aggiunte dopo il quarto verdetto BLOCK (v5)

- `moralstack/orchestration/controller.py` — BLOCKING v5-1:
  `_speculative_generate` cattura `TokenUsage.from_generation_result(result)`
  subito dopo la risoluzione di `result` e la aggiunge a
  `persist_kwargs["token_usage_json"]`; nuovo import a livello di modulo
  `from moralstack.observability.token_usage import TokenUsage`.
- `moralstack/observability/token_usage.py` (**NEW**, file già pianificato
  da PR1/Decisione 1) — BLOCKING v5-2: `to_json()` implementato con la
  condizione corretta `total_tokens==0 AND source=="missing"` fin dalla
  prima versione (nessun secondo commit di correzione, il fix è incorporato
  nel design prima dell'implementazione).
- `moralstack/runtime/modules/simulator_module.py` — BLOCKING v5-4: oltre
  al fix già pianificato (BLOCKING 3, rimozione `if attempt > 0:`), aggiunta
  di `model` al payload retry-failed sia in `_simulate_batch` (precedente
  esistente) sia in `_simulate_with_seeds` (BLOCKING E-a).
- `moralstack/runtime/modules/critic_module.py` — BLOCKING v5-4: aggiunta di
  `model` al payload retry-failed (BLOCKING 3) e normalizzazione di
  `_persist_quick_check` (BLOCKING D) da `getattr(self.policy, "model", None)`
  a `str(getattr(self.policy, "model", "") or "")`.
- `moralstack/runtime/modules/hindsight_module.py` — BLOCKING v5-4: aggiunta
  di `model` al payload retry-failed (BLOCKING 3, generico, e BLOCKING E-b,
  `evaluate_scenario()`).
- `moralstack/runtime/modules/perspective_module.py` — BLOCKING v5-4:
  aggiunta di `model` al payload retry-failed (BLOCKING 3).
- `moralstack/orchestration/refusal_handler.py` — BLOCKING v5-4: aggiunta di
  `model=str(getattr(self.policy, "model", "") or ""),` a
  `self._events.emit_llm_call(...)`, oltre a `token_usage_json` già
  pianificato in BLOCKING 4.
- `moralstack/orchestration/response_assembler.py` — BLOCKING v5-4: aggiunta
  di `"model": str(getattr(self.policy, "model", "") or ""),` al dict
  passato a `record_llm_call(...)`, oltre a `token_usage_json` già
  pianificato in BLOCKING 4.
- `moralstack/observability/sinks/sqlite_sink.py` — BLOCKING v5-6: le 2
  colonne `usage_may_be_incomplete`/`incomplete_reason` sono ora parte
  della `CREATE TABLE IF NOT EXISTS request_token_usage` originale
  (Decisione 2), non di una migrazione separata — nessuna riga di
  migrazione `ALTER TABLE` aggiuntiva richiesta per queste 2 colonne (a
  differenza delle colonne su `llm_calls`, tabella preesistente, che
  restano nel loop `ALTER TABLE` idempotente).
- **Nessuna modifica** a `moralstack/orchestration/speculative_overlap.py`
  oltre a quanto già pianificato in BLOCKING 1/BLOCKING F — verificato che
  BLOCKING v5-1 non lo richiede (il payload fluisce invariato) e che
  BLOCKING v5-5 non lo richiede (`mark_request_usage_partial()` dentro
  `abandon()` già gira nel path implicito, semplicemente senza effetto
  osservabile per la richiesta già finalizzata — solo documentazione in
  Risks/Acceptance criteria).
- `ai/plans/token-accounting-p0-2b-p11.md` — Goal, Target behavior punto 1,
  nota `llm_call_count`, Product decision 4 + R7, Decisione 2, Decisione 8,
  Decisione 7 — solo testo del piano, nessun file applicativo.

### Aggiunte dopo il quinto verdetto BLOCK (v6)

- `moralstack/orchestration/refusal_handler.py` — BLOCKING v6-1: il payload
  già esteso da BLOCKING 4/v5-4 (`token_usage_json`, `model`) passato a
  `self._events.emit_llm_call(...)` (righe 147-158) guadagna
  `billable_provider_call=refusal_result.attempts > 0` — nessuna riga
  fatturabile quando `self.policy` era `None` all'ingresso di
  `generate_llm_safe_refusal_detailed` (`attempts == 0`,
  `safe_refusal_generator.py:599-605`).
- `moralstack/orchestration/final_revalidation.py` — BLOCKING v6-1: stesso
  principio nel dict passato a `record_llm_call(None, None, {...})` (righe
  576-597, già esteso da BLOCKING 4/v5-4 con `token_usage_json`/`model`):
  `"billable_provider_call": refusal_result.attempts > 0`.
- **Nessuna modifica** a `moralstack/orchestration/response_assembler.py` per
  BLOCKING v6-1 — verificato che il blocco di generazione/persistenza
  (righe 261-327) è già interamente condizionato a `self.policy is not
  None` (riga 261): `attempts == 0` non può mai raggiungere il call site di
  persistenza in questo file.
- **Nessuna modifica** a `moralstack/orchestration/safe_refusal_generator.py`
  oltre a quanto già pianificato in BLOCKING 4 — `RefusalGenerationResult.
  attempts` esiste già ed è già correttamente `0` sul ramo `llm_client is
  None` (riga 604); BLOCKING v6-1 si limita a leggere quel campo già
  pianificato nei due call site sopra.
- `ai/plans/token-accounting-p0-2b-p11.md` — BLOCKING v6-2 (solo testo,
  nessun file applicativo): Target behavior punto 3, Decisione 4 (frase
  finale prima del blocco `RequestTokenTotals`), Query target (blocco SQL,
  riformulazione del commento sulla query di ricostruzione), Files to
  modify (bullet docs v4), Risks (R1 esteso, R21), Acceptance criteria
  (nuovo criterio) — declassamento del linguaggio "canonico/completo" a
  "fonte più completa disponibile tra le righe effettivamente persistite",
  coerente con la decisione di prodotto dell'utente. Nessuna sezione Tests
  toccata (fuori scope di questo comando).
- `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/CODEBASE_FACTS.md`,
  `docs/modules/observability.md`, `docs/TRACES/observability_db_to_ui.md`
  — BLOCKING v6-2: stessa riformulazione applicata dove questi documenti
  descrivono la query di ricostruzione `SUM(...) FROM llm_calls`
  (aggiornati nello stesso PR/commit del comportamento, §8 — nessun
  comportamento nuovo in questo v6, solo precisione terminologica).

### Aggiunte dopo il sesto verdetto BLOCK (v7)

- `moralstack/observability/sinks/sqlite_sink.py` — BLOCKING v7-1: la
  derivazione delle colonne numeriche in `_write_llm_call_single`/
  `_write_llm_call_batch` (Decisione 2) usa la stessa condizione di
  nullabilità di `to_json()` (`total_tokens==0 AND source=="missing"`), non
  una regola indipendente basata solo su `source`.
- `moralstack/observability/token_usage.py` (**NEW**, file già pianificato
  da PR1) — BLOCKING v7-1: `from_json(None)` ritorna
  `TokenUsage(0, 0, 0, "missing")` esplicitamente (round-trip con
  `to_json()`), documentato nel docstring fin dalla prima implementazione.
- `moralstack/observability/service.py` — BLOCKING v7-2: l'hook accumulo in
  `emit()`/`emit_batch()` (Decisione 4) filtra su
  `envelope.payload.get("billable_provider_call", True)` prima di chiamare
  `record_llm_call_usage(...)`, per singolo envelope anche dentro il ciclo
  di `emit_batch()`.
- `moralstack/observability/read_store.py` — BLOCKING v7-2: la query di
  `get_token_usage_breakdown` include `AND COALESCE(billable_provider_call,
  1) = 1`, stesso predicato di `get_token_usage_totals`/query di
  ricostruzione.
- `moralstack/orchestration/deliberation_runner.py` — BLOCKING v7-3:
  aggiunta di `"model": str(getattr(self.policy, "model", "") or ""),` ai
  `persist_kwargs`/dict passati a `record_llm_call` in `run_benign_fast_path`
  (righe 667-683), `run_safe_complete_path` (righe 772-788), `run_fast_path`
  (righe 947-968); **estensione decisa dall'utente (Product decision v7/12),
  implementazione corretta in v8 — PROJECT_SPEC §9**:
  `_policy_llm_model_for_action`/`_module_model` (righe 200-226) restano
  invariate nella firma, nel parametro `action`, nel branch `rewrite_model` e
  nel lookup a due livelli `module.policy.model` → `module.model`; l'unico
  cambio è sui 2 punti per funzione dove oggi ritornano `None` esplicito,
  sostituiti con `""` (tipo di ritorno annotato `str` invece di
  `str | None`) — fix chirurgico che copre ~13 call site billable
  pre-esistenti, inclusi i moduli core critic/simulator/hindsight/
  perspectives (righe 2906, 3017, 3128, 3241) e il path deliberativo/rewrite
  (righe 2571, 2615, 2650, 2749).
- `moralstack/orchestration/controller.py` — BLOCKING v7-3:
  - `_regenerate_for_contract` (righe 1227-1243): aggiunta di `"model":
    str(getattr(self.policy, "model", "") or ""),`.
  - `_revalidate_draft` (righe 1309-1326): aggiunta di `"model":
    get_dccl_llm_model(),` (**non** il pattern generico — il modello
    effettivo è l'override DCCL, non `self.policy.model`).
  - `_speculative_generate` (righe 971-978): bug pre-esistente e
    indipendente da questo piano, corretto nello stesso punto già toccato
    da BLOCKING v5-1: `policy_model_str = str(getattr(self.policy,
    "model", "") or "")` sostituisce il calcolo attuale che può produrre
    `None` esplicito.
- `moralstack/orchestration/safe_refusal_generator.py` — BLOCKING v7-3
  (vincolo di design, chiude un NON_BLOCKING Codex): `RefusalGenerationResult.
  token_usage` dichiarato con `field(default_factory=lambda: TokenUsage(0,
  0, 0, "missing"))` fin dalla sua introduzione (Decisione 8/BLOCKING 4,
  punto 2) — nessuna modifica alla sezione Tests del piano; i due test
  esistenti (`tests/test_controller_speculative_lazy.py:101`,
  `tests/test_refusal_handler_duration.py:111`) restano verdi senza
  modifiche.
- `ai/plans/token-accounting-p0-2b-p11.md` — BLOCKING v7-1/v7-2/v7-3 (solo
  testo del piano): Decisione 1, Decisione 2, Decisione 4, Decisione 8,
  Target behavior punto 1, Query target, Risks, Acceptance criteria.
  Nessun file applicativo toccato da questo comando.

Nessun file tra quelli già elencati nello scope originale viene rimosso.

## Risks

- **R1 — falso senso di completezza del totale per-richiesta sotto P2.**
  Mitigato (non eliminato) come da Decisione 4; documentato esplicitamente come
  limite noto. **Esteso in v6 (BLOCKING v6-2)**: lo stesso limite si applica
  anche alla query di ricostruzione `SUM(...) FROM llm_calls` (Query target,
  Decisione 4) — è la fonte più completa disponibile tra le righe
  effettivamente persistite, non un totale garantito immune a P2; le righe
  `llm_calls` condividono la stessa coda lossy best-effort di
  `request_token_usage` (`observability/write_queue.py`, verificato).
- **R2 — crescita di memoria dell'accumulatore** se `_attach_trace_and_return`
  non viene mai raggiunto (crash raro). Mitigato con cap FIFO configurabile.
- **R3 — regressione mypy strict** in `controller.py`/`types.py`/`embedder.py`.
  Mitigazione: tipizzazione esplicita, `Literal` per `TokenUsageSource`, nessun
  `Any` implicito nei nuovi metodi.
- **R4 — doppio conteggio** se in futuro un chiamante invocasse sia
  `record_llm_call` sia direttamente `persist_llm_call`/`async_persist_llm_call`
  per lo stesso evento logico. Non osservato nei call site verificati; area di
  attenzione futura in review.
- **R5 — costo aggiuntivo di parsing JSON in `emit()`** (hot path di ogni
  evento). Mitigato: parsing solo se `event_type == EVENT_LLM_CALL`, costo
  O(1) su stringhe corte.
- **R6 — migrazione schema su DB esistenti di grandi dimensioni.**
  `ALTER TABLE ADD COLUMN` su SQLite è O(1); righe storiche avranno le nuove
  colonne `NULL` finché non riscritte — accettabile, nessun backfill richiesto.
- **R7 — RISOLTO da Product decision 4, esteso in v5 (BLOCKING v5-2)**:
  `TokenUsage.from_openai_usage` con `total_tokens == 0` e oggetto `usage`
  presente è marcato `exact`/`estimated` (a seconda che lo split sia
  presente), **non** `missing`. `"missing"` è riservato solo all'assenza
  totale dell'oggetto `usage`. Applicato in modo uniforme anche a
  `TokenUsage.from_generation_result`/`_token_usage_json_from_result`
  (Decisione 8, BLOCKING 3). La nullabilità di `to_json()` è ora
  `total_tokens==0 AND source=="missing"` (non `source` da solo) — evita
  che `combine()` azzeri conteggi reali quando un solo componente è
  `missing` (bug v4→v5, vedi Decisione 8, BLOCKING v5-2).
- **R8 — usage HTTP parziale sulle speculative scartate (BLOCKING 1,
  ridefinito in v3 come limite accettato, non più come bug da chiudere).**
  Per costruzione (Product decision v3), `usage` nella risposta HTTP può non
  includere una speculativa scartata la cui contabilizzazione si risolve
  dopo `_finalize_token_accounting`. Blast radius: solo i path
  refuse/safe_complete/deliberativo dove `abandon()` viene invocato
  (`controller.py:2528, 2553, 2594`); nessun impatto su correttezza
  dell'audit (la riga `llm_calls` è comunque persistita correttamente,
  ricostruibile via `SUM(...)`), nessuna latenza aggiuntiva, nessun crash.
  Documentato esplicitamente nell'Acceptance criteria corrispondente perché
  un reviewer futuro non lo scambi per un difetto.
- **R9 — discriminatore billable/non-billable disallineato tra accumulatore
  in-process e query SQL (BLOCKING 2), chiuso in v7 (BLOCKING v7-2).** Se il
  filtro `billable_provider_call` viene applicato in un solo posto (es.
  solo nella query SQL ma non nell'hook di `emit()`), il totale sincrono
  esposto al proxy diverge silenziosamente dal totale ricostruibile
  offline. Mitigazione: stesso predicato (`COALESCE(billable_provider_call,
  1) = 1` lato SQL, controllo equivalente lato accumulatore) applicato nei
  due punti nello stesso PR (PR11), con un test di parità esplicito (vedi
  acceptance criteria e Tests). **Nota v7**: questo rischio era già
  documentato e la mitigazione già dichiarata qui, ma lo pseudocodice di
  `emit()` nella Decisione 4 non la implementava — R9 era quindi previsto
  ma non ancora chiuso nel design. BLOCKING v7-2 allinea il codice-esempio
  alla mitigazione già promessa da questo rischio.
- **R10 — propagazione incompleta di `token_usage_source` nei moduli runtime
  (BLOCKING 3).** Rischio che un quinto sito di costruzione (oggi non
  individuato) di uno dei 5 dataclass rimanga scoperto — in particolare i 2
  siti di `EnsembleResult` (parallelo/sequenziale), il più esposto a questo
  rischio. Mitigazione: `TokenUsage.from_generation_result` centralizzato in
  un solo helper (stesso principio DRY del fallback 70/30); un audit grep su
  `getattr(result, "tokens_used"` nei 4 file individua ogni sito residuo
  prima del merge; test dedicato che esercita esplicitamente entrambi i
  metodi di valutazione di `EnsembleResult`.
- **R11 — performance dell'accumulatore sotto carico dei mini-estimatori di
  rischio (non bloccante da Codex, incorporato).**
  `models/risk/estimator.py::_persist_mini_llm_calls_batch` (righe 703-710)
  invia batch multi-envelope via `_obs_route_batch` (righe 71-75) →
  `ObservabilityService.emit_batch()` (`service.py:50-57`). L'hook
  dell'accumulatore deve iterare ogni envelope del batch e fare un parsing
  JSON breve per ciascuno sotto lock — costo O(N) per batch, non O(1) come
  nel caso `emit()` singolo. Mitigazione: lock a grana fine (solo increment
  di contatori, mai I/O dentro la sezione critica), validare con un test di
  carico sintetico (N=50-100 envelope per batch) prima del merge.
- **Correzione al framing del rischio proxy/governed-delivery (suggerimento
  Codex, incorporato).** Il rischio reale di questo piano è **audit
  correctness** (il campo `usage` riportato al client/agli export di
  analytics può essere impreciso), non "delivered-answer provenance": il
  proxy non chiama mai l'upstream per generare la risposta consegnata
  (`.claude/rules/governed-delivery.md`, `orchestration/delivery.py`) —
  cambia solo il valore del campo `usage` nel payload sintetico già
  esistente. Nessun conflitto con l'invariante Governed-delivery-only in
  nessuno dei fix.

### Rischi introdotti/scoperti in v3 (seconda revisione post-BLOCK)

- **R12 — cache-hit ri-fatturata come nuova chiamata (BLOCKING 2/B).**
  Difetto **attivo per default** su simulator/hindsight
  (`enable_caching=True` di default). Se non corretto, ogni cache-hit
  gonfia `SUM(llm_calls)` e il totale sincrono del proxy con token già
  contati in precedenza. Mitigazione: campo `from_cache` additivo +
  `billable_provider_call=False`/`cache_status="hit"` sulle righe
  cache-hit; test dedicato con cache popolata che verifica che una seconda
  chiamata identica non aumenti il totale token della richiesta.
- **R13 — sito condizionale critic-skip disallineato (BLOCKING 2/B).** A
  differenza degli altri siti (sempre `False`), il sito critic
  (`deliberation_runner.py:2907`) è condizionale su `is_skipped`. Rischio:
  se applicato per errore come `False` incondizionato, le vere chiamate
  critic (`is_skipped=False`, la maggioranza) sparirebbero erroneamente dal
  conteggio fatturabile. Mitigazione: test esplicito su entrambi i rami.
- **R14 — doppio conteggio se un implementatore rende cumulativi i campi
  del dataclass (BLOCKING 3/C).** Il design v3 sceglie esplicitamente di
  non farlo; se in implementazione si sommassero comunque i tentativi nel
  dataclass finale, le righe di audit per-tentativo-fallito diventerebbero
  un doppio conteggio. Mitigazione: test di parità esplicito (`SUM(llm_calls)`
  per un `(run_id, request_id)` con un retry fallito deve corrispondere
  esattamente a: token del tentativo fallito + token del tentativo
  riuscito, non di più).
- **R15 — divergenza tra i 4 moduli sul ramo `except` che cattura il
  fallimento (BLOCKING 3/C).** `critic_module.py` distingue eccezioni di
  parsing da `Exception` generico; gli altri 3 moduli usano
  `except (JSONParseError, Exception)` (cattura-tutto). Il guardrail
  `attempt_token_usage is not None` (impostato solo dopo che `generate()` è
  tornato con successo) evita di persistere una riga "retry_failed" con
  token fantasma quando il fallimento è in realtà un errore di
  rete/provider — va preservato in tutti e 4 i moduli, non semplificato
  per uniformità sintattica.
- **R16 — copertura test non esaustiva verificata in sessione (BLOCKING
  3/C).** Nessun test esistente individuato (grep mirato) che pinni i
  valori numerici di `tokens_used`/`prompt_tokens`/`completion_tokens` su
  critic/simulator/hindsight/perspective in scenario di retry; non esistono
  `tests/test_critic_module.py`/`test_simulator_module.py`/
  `test_hindsight_module.py` dedicati. Il design v3 non tocca questi campi
  numerici, quindi il rischio è basso, ma non è una verifica esaustiva di
  tutta la suite — l'implementatore deve far girare i test completi prima
  del merge.
- **R2/R9 del v2 estesi**: l'accumulatore/parità billable ora coprono anche
  i 6+3 siti aggiunti in v3, non solo i 3 originali — nessuna modifica al
  meccanismo, solo estensione della superficie di applicazione.

### Rischi introdotti/scoperti in v4 (terza revisione post-BLOCK)

- **R17 — `quick_check()` non contabilizzato prima del fix (BLOCKING D).**
  Bug di audit, non di correttezza decisionale: `quick_check()` non decide
  mai `final_action` da sola (solo `passed`/`not passed` per instradare al
  path deliberativo), quindi nessun invariante P0 è toccato. Blast radius:
  ogni richiesta fast-path con critic configurato (wiring di produzione
  confermato). Mitigato dal fix self-contenuto in `critic_module.py`.
- **R18 — retry falliti del simulator seeded silenziosamente scartati
  (BLOCKING E/a).** Prima del fix, ogni tentativo fallito nel loop per-seed
  consumava token reali mai contabilizzati in alcuna riga di audit (diverso
  da `_simulate_batch`, già corretto in v3). Dormiente per default
  (`use_seeded_generation=False`), attivo su opt-in esplicito
  dell'operatore. Mitigato dal fix simmetrico a quello già in scope per
  `_simulate_batch`.
- **R19 — Hindsight individual evaluation a zero token per default
  (BLOCKING E/b), gravità più alta di R17/R18: bug numerico, non solo di
  audit.** Con `use_batch_evaluation=True` di default, ogni richiesta con
  una sola conseguenza simulata (caso comune) produceva un
  `HindsightResult.tokens_used == 0` nonostante N chiamate LLM reali —
  sottostima sistematica del costo per il breakdown modulo "hindsight" e
  per il totale per-richiesta. Mitigato dal fix che valorizza i campi token
  su `HindsightEvaluation` e li aggrega in `_evaluate_individual()`. Bug
  pre-esistente e indipendente da questo piano, scoperto in sessione.
- **R20 — granularità per-provider-call vs per-modulo su Perspective/
  Simulator-seeded (BLOCKING E/c), design accettato non un difetto.**
  `EnsembleResult`/`SimulationResult` (seeded) sommano correttamente più
  chiamate reali in un unico risultato di modulo, persistito come 1 sola
  riga `llm_calls` — nessuna perdita numerica, ma un reviewer futuro non
  può distinguere da `llm_calls` da solo quante chiamate provider reali
  sono avvenute per una singola riga "perspectives"/"simulator". Mitigato
  parzialmente per Perspectives (breakdown per-prospettiva in
  `parsed_summary_json`, dati già disponibili); non mitigato per
  Simulator-seeded (dato non tracciato, fix sproporzionato per questo
  task — limite esplicitamente accettato, non un gap residuo).
- **R21 — `usage_may_be_incomplete` copre solo i call site sincroni ed
  espliciti di `abandon()`, non è una garanzia generale di completezza
  (BLOCKING F, esteso in v5 da BLOCKING v5-5).** Il flag si attiva quando
  uno dei 6 call site espliciti verificati (`controller.py:1378, 2213,
  2258, 2528, 2553, 2594`) invoca `abandon()` — copertura confermata e
  invariata. **Non copre** (a) perdite dovute a `queue.Full` (P2, R1, non
  rilevabile in modo sincrono per costruzione); (b) il path **implicito**
  `SpeculativeOverlapHandle.shutdown_executor()` → `abandon()`
  (`speculative_overlap.py:51-58`), invocato da `controller.py`'s `finally`
  quando un'eccezione viene sollevata tra la creazione di `spec_handle` e
  la determinazione del `route` — verificato in v5 (BLOCKING v5-5): per
  costruzione del linguaggio (nessuna variabile cattura il risultato già
  computato prima che `finally` esegua), il flag non può più raggiungere
  né `ResponseMetadata` né la riga `request_token_usage` già finalizzata in
  questo caso specifico. Blast radius circoscritto: solo richieste il cui
  processing fallisce con eccezione prima della determinazione del route —
  in questi casi l'intera risposta è già un percorso di errore, quindi la
  priorità primaria (l'errore stesso) è già surfacata al chiamante; la
  sola conseguenza è che `usage_may_be_incomplete` resta `False` per questa
  riga anche se una speculative call scartata è ancora in volo. Sempre
  ricostruibile via `SUM(...) FROM llm_calls` (la fonte più completa
  disponibile tra le righe effettivamente persistite, BLOCKING F — non una
  garanzia assoluta di completezza, BLOCKING v6-2, vedi R1 esteso).
  Documentato esplicitamente per evitare che un reviewer futuro lo scambi
  per una garanzia più ampia di quella offerta, o per un bug risolvibile
  senza refactoring sproporzionato.
- **Aggiornamento a R8 (v2/v3, non riscritto per intero)**: il limite
  resta lo stesso (usage HTTP può non includere una speculativa scartata
  risolta dopo la finalizzazione), ma ora è **segnalato esplicitamente**
  al chiamante tramite `usage_may_be_incomplete=True`/
  `incomplete_reason="speculative_discard_pending"` sia in
  `ResponseMetadata`/`GovernanceMetadata` sia nella riga durevole
  `request_token_usage` — non più un limite silenzioso.

### Rischi introdotti/scoperti in v5 (quarta revisione post-BLOCK)

- **R22 — speculative call priva di token fino al fix (BLOCKING v5-1).**
  Prima del fix, ogni riga `llm_calls` `call_outcome="used"`/`"discarded"`
  per una speculative call aveva `token_usage_json` sempre assente,
  vanificando la contabilizzazione di un consumo reale del provider anche
  dopo il fix di propagazione contesto di BLOCKING 1. Blast radius: ogni
  richiesta con speculative overlap attivo. Mitigato dal fix in
  `_speculative_generate`.
- **R23 — `combine()` azzerava conteggi reali su aggregati misti prima del
  fix (BLOCKING v5-2).** Ogni riga aggregata `llm_calls` (perspectives,
  refusal con retry, simulator seeded, hindsight individual) con anche un
  solo componente sottostante `"missing"` avrebbe avuto
  `token_usage_json=NULL` nonostante conteggi reali >0 già sommati —
  perdita silenziosa di dati di audit/costo su esattamente i moduli
  multi-round-trip che questo piano introduce. Gravità alta: avrebbe
  reintrodotto lo stesso tipo di bug (perdita silenziosa di token noti) che
  l'intero piano esiste per correggere. Mitigato dalla riformulazione della
  condizione di nullabilità.
- **R24 — model mancante su righe retry-failed/refusal, con l'aggravante
  chiave-presente-vs-assente (BLOCKING v5-4).** Oltre alla perdita del
  breakdown per-modello su queste righe, il pattern `model=None` esplicito
  (es. `_persist_quick_check` prima della normalizzazione) sovrascrive
  silenziosamente il default `""` di `write_queue.py` con `None` — rischio
  di valori `NULL`/`None` inconsistenti in colonna `model` a seconda di
  quale call site ha scritto la riga, anche dopo un fix superficiale che
  aggiunga la chiave senza normalizzare il valore.
- **R25 — `usage_may_be_incomplete` non copre il path implicito
  `shutdown_executor()` (BLOCKING v5-5), limite accettato non un difetto
  del design.** Vedi R21 aggiornato sopra per il dettaglio completo — non
  mitigato ulteriormente in questo piano (fix strutturale esplicitamente
  rifiutato per sproporzione rispetto al beneficio, PROJECT_SPEC §6).
- **R26 — schema `request_token_usage` senza le 2 colonne v4 se la
  sequenza PR non viene consolidata (BLOCKING v5-6).** Se PR5 e PR20 fossero
  implementate come PR separate senza il fix di consolidamento, un
  ambiente che deploya solo fino a PR5-PR19 avrebbe una tabella priva delle
  2 colonne, e ogni query verso `usage_may_be_incomplete` fallirebbe con
  `no such column`. Mitigato eliminando la separazione: le 2 colonne sono
  ora parte della `CREATE TABLE` di PR5 stessa.

### Rischi introdotti/scoperti in v6 (quinta revisione post-BLOCK)

- **R27 — righe di refusal "no-provider" fatturate come chiamata reale al
  provider prima del fix (BLOCKING v6-1).** Prima del fix, ogni riga
  `llm_calls` `action="refuse (fast_path)"` (`refusal_handler.py`) o
  `action="refuse (final_revalidation)"` (`final_revalidation.py`) generata
  quando `self.policy`/`policy` era `None` restava `billable_provider_call=
  True` per default, gonfiando silenziosamente `SUM(...) FROM llm_calls
  WHERE COALESCE(billable_provider_call,1)=1` con righe che non hanno mai
  raggiunto alcun provider (`RefusalGenerationResult.attempts == 0`,
  `safe_refusal_generator.py:599-605`). Blast radius: ogni
  deployment/test/percorso che invoca REFUSE senza un `policy` LLM
  disponibile. Mitigato dal fix (BLOCKING v6-1):
  `billable_provider_call=refusal_result.attempts > 0` sui due call site.
- **R28 — tensione testuale tra la promozione della query di ricostruzione a
  "fonte canonica" (v4, BLOCKING F) e il limite P2/R1 già riconosciuto per
  `request_token_usage` (BLOCKING v6-2).** Prima della correzione, il piano
  affermava implicitamente due standard diversi per lo stesso rischio
  strutturale (coda `write_queue.py` lossy, §5.6): "best-effort, non
  garantito" per `request_token_usage`, ma "canonico, garanzia di
  completezza" per la query di ricostruzione su `llm_calls` — nonostante le
  due fonti condividano esattamente la stessa coda di scrittura
  (`ObservabilityWriteQueue.submit_envelope()`/`_enqueue()`,
  `write_queue.py:91-181`, verificato). Mitigato con la riformulazione
  testuale (BLOCKING v6-2) in "fonte più completa disponibile tra le righe
  effettivamente persistite" — nessun cambio di design/persistenza, R1
  esteso per coprire esplicitamente anche questa query.

### Rischi introdotti/scoperti in v7 (sesta revisione post-BLOCK)

- **R29 — equivalenza JSON/SQL numerica rotta prima del fix (BLOCKING
  v7-1).** Prima del fix, ogni riga aggregata `llm_calls` con
  `token_usage_json` non-NULL (per costruzione, v5-2) ma
  `source=="missing"` avrebbe avuto le 3 colonne numeriche `NULL` —
  invisibile a `SUM(...) FROM llm_calls`, la query di ricostruzione
  promossa a "fonte più completa disponibile" (BLOCKING F/v6-2). Avrebbe
  vanificato silenziosamente proprio la classe di bug che v5-2 esisteva per
  correggere, ma solo lato SQL, non lato JSON — gravità alta per
  audit/costo. Mitigato rendendo le due regole di nullabilità identiche per
  costruzione.
- **R30 — breakdown per modulo/modello include righe diagnostiche
  non-billable prima del fix (BLOCKING v7-2).** Senza il predicato billable
  nella query "Breakdown per modulo e modello", righe come
  speculative-reuse/leakage_detected/skipped-rewrite/critic-skip (Decisione
  8/BLOCKING 2, siti verificati: `deliberation_runner.py:866-887,
  909-937`, più i 2 gemelli deliberativi) sarebbero apparse nel breakdown
  per-modulo con conteggi/token che non riflettono consumo reale dal
  provider. Mitigato con lo stesso predicato usato ovunque altrove nel
  design (query di ricostruzione, accumulatore).
- **R31 — `model` mancante o `None` esplicito su righe billable
  pre-esistenti, superficie più ampia di BLOCKING v5-4 (BLOCKING v7-3).**
  Oltre ai payload nuovi già coperti da v5-4 (retry/refusal), 5 righe
  billable pre-esistenti (benign fast-path, safe-complete, fast-path,
  compliance-regenerate, draft-revalidate) mancavano di `model` del tutto,
  e `_speculative_generate` (bug pre-esistente, indipendente da questo
  piano) passava `model=None` esplicito con la stessa insidia
  chiave-presente-vs-assente già vista in v5-4. Blast radius: ogni
  richiesta sui path fast-path/benign/safe_complete/compliance-regenerate/
  draft-revalidate/speculative — cioè la maggioranza del traffico reale,
  gravità più alta di v5-4 (che copriva solo retry/refusal, percorsi meno
  frequenti). Mitigato dal pattern uniforme sui 4 siti generici + il caso
  speciale di `_revalidate_draft` (modello effettivo = override DCCL, non
  `self.policy.model`) + il fix del bug pre-esistente in
  `_speculative_generate` nello stesso punto già toccato da v5-1.
- **R32 — stessa insidia `model=None` su ~13 call site billable
  pre-esistenti condivisi da `_policy_llm_model_for_action`/`_module_model`
  (BLOCKING v7-3, estensione decisa dall'utente).** Include i moduli core
  del breakdown per-modello (critic/simulator/hindsight/perspectives,
  `deliberation_runner.py:2906, 3017, 3128, 3241`) e il path
  deliberativo/rewrite (righe 2571, 2615, 2650, 2749) — gravità più alta di
  R31 per blast radius (copre la maggioranza dei moduli di deliberazione,
  non solo i path di routing). Mitigato con un fix a un solo punto (le 2
  funzioni condivise ritornano sempre `str`, mai `None`), che si propaga
  automaticamente a tutti i call site senza modificarli singolarmente.

## Acceptance criteria

- [ ] `llm_calls` ha le colonne `input_tokens`, `output_tokens`, `total_tokens`,
      `token_usage_missing`, `token_usage_estimated`, popolate per ogni nuova
      riga scritta dopo il deploy.
- [ ] Esiste la tabella `request_token_usage` con una riga per
      `(run_id, request_id)` dopo ogni `process()` che ha effettuato almeno una
      chiamata LLM.
- [ ] Le query di totale-per-richiesta e breakdown-per-modulo/modello
      restituiscono dati corretti su un run di prova end-to-end.
- [ ] `policy.py` marca `source="estimated"` quando il provider non riporta
      `prompt_tokens`/`completion_tokens`.
- [ ] `constitution_retriever` compare nel breakdown `llm_calls` con
      `token_usage_json` non-NULL per le sue 3 azioni.
- [ ] `OpenAIEmbedder.embed()` produce una riga `module="embedder"` in
      `llm_calls`; `LocalEmbedder`/`HashingEmbedder` non ne producono
      (comportamento documentato, non un bug).
- [ ] Il proxy OpenAI-compatible restituisce `usage` con valori reali > 0
      quando la pipeline ha generato testo; resta `{0,0,0}` solo quando nessuna
      generazione LLM è avvenuta o la pipeline è fallita prima di produrre un
      risultato.
- [ ] `GovernanceMetadata` espone i 6 nuovi campi popolati.
- [ ] Nessuna regressione sui test esistenti di decision policy, byte-equality
      del prompt, observability best-effort, ledger fast-path.
- [ ] mypy `--strict` pulito su `moralstack.orchestration.*`.
- [ ] Documentazione §8 aggiornata nello stesso commit del comportamento
      corrispondente.

### Aggiunte dopo il verdetto BLOCK (Decisione 8)

- [ ] Una richiesta che imbocca il path refuse/safe_complete/deliberativo con
      `constrained_generation=True` e che ha una speculative draft ancora in
      corso al momento del routing produce, dopo il completamento in
      background, una riga `llm_calls` con `call_outcome="discarded"`,
      `run_id`/`request_id` corretti e `billable_provider_call=True`
      (verificato con `threading.Event`/`Future` controllato nel test, non
      `time.sleep`). **Non è richiesto** che questa riga sia inclusa nel
      campo `usage` della risposta HTTP né nel riepilogo sincrono
      `request_token_usage` per quella stessa richiesta — è un limite
      accettato esplicitamente (R8, Product decision v3); è però sempre
      ricostruibile via `SUM(...) FROM llm_calls WHERE run_id=? AND
      request_id=?`.
- [ ] Le righe sintetiche/diagnostiche (speculative-reuse,
      SKIPPED_EMPTY_GUIDANCE, output_protection/leakage_detected, sia nel
      fast-path che nel path deliberativo) compaiono in `llm_calls` con
      `billable_provider_call=0` e sono escluse sia dal totale sincrono
      (`ResponseMetadata`/`usage` del proxy) sia dalla query di breakdown
      per modulo/modello.
- [ ] Il breakdown per modulo (`get_token_usage_breakdown`) riporta
      `token_usage_source` corretta e non-default per critic, simulator,
      perspectives, hindsight su un run di prova con mock che restituiscono
      esplicitamente `token_usage_source="exact"`/`"estimated"`.
- [ ] Una generazione di rifiuto (REFUSE) — dal path fast/hard-signal
      (`refusal_handler.py`), dal path deliberativo (`response_assembler.py`)
      e dal path di final revalidation (`final_revalidation.py`) — produce
      una riga `llm_calls` con `token_usage_json` non-NULL quando il policy
      LLM è disponibile; con retry anti-leak (2 chiamate), il totale
      riportato è la somma delle due chiamate, non solo dell'ultima.
- [ ] Test di parità esplicito: per una richiesta con un mix di righe
      billable e non-billable, `SUM(...) FROM llm_calls WHERE
      COALESCE(billable_provider_call,1)=1` è uguale al totale sincrono
      esposto in `ResponseMetadata`/`usage` del proxy per la stessa
      richiesta.
- [ ] Nessuna riga storica (pre-migrazione, `billable_provider_call IS
      NULL`) viene esclusa per errore dai totali ricostruiti (verificato con
      `COALESCE(...,1)`).
- [ ] Due `run_id` diversi con lo stesso `request_id` fornito esplicitamente
      dal chiamante non mescolano mai i propri totali nell'accumulatore.

### Aggiunte dopo il secondo verdetto BLOCK (v3)

- [ ] Il sito critic-skip (`deliberation_runner.py:2907-2939`) produce
      `billable_provider_call=1` quando `critique.skipped is False`
      (chiamata reale) e `=0` quando `True` (nessuna chiamata,
      `critic_module.py:391`) — testato su entrambi i rami.
- [ ] Un secondo `simulate()`/`evaluate()` (simulator/hindsight/perspectives)
      con input identico che risulta in cache-hit produce una riga
      `llm_calls` con `billable_provider_call=0`, `cache_status="hit"`, e
      **non altera** il totale token sincrono della richiesta rispetto alla
      prima chiamata (cache-miss).
- [ ] Un tentativo fallito per errore di parsing/validazione in
      critic/simulator/hindsight/perspective produce una riga `llm_calls`
      separata con `billable_provider_call=1`, `call_outcome=
      "retry_failed"`, `token_usage_json` non-NULL riportante i token
      realmente consumati da quel tentativo.
- [ ] Test di parità: per una richiesta con esattamente un tentativo
      fallito seguito da uno riuscito su uno dei 4 moduli,
      `SUM(total_tokens) FROM llm_calls WHERE run_id=? AND request_id=? AND
      module=<modulo> AND COALESCE(billable_provider_call,1)=1` è uguale a
      `tentativo_fallito.total_tokens + tentativo_riuscito.total_tokens`,
      mai di più (non doppio conteggio) né di meno (non scartato).
- [ ] Un fallimento totale di tutti i retry (perspective: prospettiva in
      `failed_perspectives`; critic/simulator/hindsight: eccezione
      propagata) produce comunque N righe `llm_calls` (una per tentativo
      fallito, tutte `billable_provider_call=1`), anche se nessun risultato
      finale viene mai costruito con successo.
- [ ] `CriticReport.tokens_used`/`SimulationResult.tokens_used`/
      `HindsightResult.tokens_used`/`PerspectiveResult.tokens_used` restano
      numericamente identici al comportamento pre-esistente (solo l'ultimo
      tentativo riuscito) — nessuna regressione sui test esistenti.
- [ ] Una richiesta il cui routing passa per `_route_refuse` (REFUSE
      fast/hard-signal) produce una riga `llm_calls` con
      `module="orchestration"`, `action="refuse (fast_path)"` e
      `token_usage_json` non-NULL quando `self.policy` è disponibile.

### Aggiunte dopo il terzo verdetto BLOCK (v4)

- [ ] Una richiesta fast-path che invoca `quick_check()` con successo
      produce una riga `llm_calls` con `module="critic"`,
      `action="quick_check (fast_path)"`, `token_usage_json` non-NULL,
      `billable_provider_call=1`. Se `hard_constraints` è vuoto, **nessuna**
      riga viene prodotta (nessuna chiamata LLM avvenuta).
- [ ] Un fallimento di parsing JSON dopo una risposta provider valida in
      `quick_check()` produce una riga `llm_calls` con
      `call_outcome="quick_check_failed"`, `billable_provider_call=1`,
      `token_usage_json` non-NULL; un fallimento di `generate()` stessa
      (nessuna risposta ottenuta) non produce alcuna riga.
- [ ] `_simulate_with_seeds()` con almeno un tentativo per-seed fallito
      produce una riga `llm_calls` separata per ciascun fallimento
      (`call_outcome="retry_failed"`, `billable_provider_call=1`,
      `token_usage_json` non-NULL) e il `token_usage_source` del
      `SimulationResult` finale riflette il `combine()` di tutti i seed
      riusciti, non solo dell'ultimo.
- [ ] `HindsightResult.tokens_used` per una richiesta con una singola
      conseguenza simulata (percorso `_evaluate_individual`, default con
      `use_batch_evaluation=True`) è **> 0** quando la valutazione LLM ha
      successo — non più sistematicamente `0`.
- [ ] Un tentativo fallito dentro `evaluate_scenario()` (percorso
      individual) produce una riga `llm_calls` separata con
      `call_outcome="retry_failed"`, `billable_provider_call=1`,
      `token_usage_json` non-NULL.
- [ ] La riga `llm_calls` aggregata per "perspectives" contiene in
      `parsed_summary_json` un breakdown `per_perspective_tokens` con una
      entry per ogni `perspective_id` valutato con successo, i cui totali
      sommano esattamente a `token_usage_json.total_tokens` della stessa
      riga.
- [ ] Una richiesta il cui routing invoca `abandon()` (in uno qualunque dei
      6 call site verificati: `controller.py:1378, 2213, 2258, 2528, 2553,
      2594`) produce una riga `request_token_usage` con
      `usage_may_be_incomplete=1` e `incomplete_reason=
      "speculative_discard_pending"`; una richiesta che non invoca mai
      `abandon()` produce `usage_may_be_incomplete=0`,
      `incomplete_reason IS NULL`.
- [ ] `ResponseMetadata.usage_may_be_incomplete`/`GovernanceMetadata.
      usage_may_be_incomplete` riflettono lo stesso valore persistito nella
      riga `request_token_usage` per la stessa richiesta.
- [ ] Nessun testo residuo "autoritativo"/"authoritative" riferito a
      `request_token_usage` in questo piano né nei documenti `docs/`
      aggiornati nello stesso PR (§8).

### Aggiunte dopo il quarto verdetto BLOCK (v5)

- [ ] Una speculative call il cui esito è `used` produce una riga
      `llm_calls` con `token_usage_json` non-NULL riflettente i token reali
      della chiamata; una speculative call il cui esito è `discarded`
      produce anch'essa una riga `llm_calls` con `token_usage_json`
      non-NULL (stesso valore catturato al momento della generazione, non
      ricostruito al momento del discard).
- [ ] Una riga aggregata `llm_calls` con almeno un componente sottostante
      `source=="missing"` e almeno un componente `source in ("exact",
      "estimated")` con `total_tokens>0` ha `token_usage_json` **non-NULL**,
      con `total_tokens` pari alla somma reale dei componenti non-missing.
      Solo un aggregato con **tutti** i componenti `missing` (quindi
      `total_tokens==0`) produce `token_usage_json IS NULL`.
- [ ] Nessun testo residuo nel piano né nei documenti `docs/` aggiornati
      nello stesso PR (§8) afferma o implica "una riga `llm_calls` per ogni
      round-trip HTTP al provider" — l'unità di audit dichiarata è
      esplicitamente "riga per invocazione di modulo/tentativo".
- [ ] Ogni riga `llm_calls` con `call_outcome="retry_failed"` (nei 4 moduli
      critic/simulator/hindsight/perspective, inclusi i percorsi seeded/
      individual) ha `model` non-vuoto quando `self.policy.model` è
      disponibile — mai `NULL`/`None` per una chiave `model` esplicitamente
      passata come `None`.
- [ ] La riga `llm_calls` `action="refuse (fast_path)"` (via
      `refusal_handler.py`) e la riga `action="refuse (deliberative...)"`
      (via `response_assembler.py`) hanno `model` non-vuoto quando
      `self.policy.model` è disponibile.
- [ ] Una richiesta il cui processing solleva un'eccezione **prima** che il
      `route` sia determinato, con `spec_handle` creato e mai esplicitamente
      joinato/abbandonato, produce comunque una riga `llm_calls`
      `call_outcome="discarded"` per la speculativa (via
      `shutdown_executor()` → `abandon()` implicito) — **ma non è
      richiesto** che `usage_may_be_incomplete=1` sia riflesso né in
      `ResponseMetadata`/`GovernanceMetadata` né nella riga
      `request_token_usage` di quella stessa richiesta: limite accettato
      esplicitamente (R21/R25), documentato, non silenzioso.
- [ ] La tabella `request_token_usage` creata da un deploy pulito (nessuna
      migrazione pregressa) ha già le colonne `usage_may_be_incomplete`/
      `incomplete_reason` fin dalla prima `CREATE TABLE` — nessun errore
      `no such column` in nessuna query che le referenzia, a qualunque
      punto della sequenza PR (PR5 in poi).

### Aggiunte dopo il quinto verdetto BLOCK (v6)

- [ ] Una generazione di rifiuto il cui `self.policy`/`policy` è `None` al
      momento della chiamata (`refusal_handler.py` via `_route_refuse`, o
      `final_revalidation.py` via `_generate_post_revalidation_refusal`)
      produce comunque una riga `llm_calls` (con `token_usage_json IS NULL`,
      `raw_response` pari al fallback), ma con `billable_provider_call=0`
      (`RefusalGenerationResult.attempts == 0`) — esclusa dal totale
      fatturabile ricostruito (`SUM(...) FROM llm_calls WHERE
      COALESCE(billable_provider_call,1)=1`).
- [ ] Una generazione di rifiuto con `self.policy`/`policy` disponibile
      produce `billable_provider_call=1` (`attempts >= 1`), indipendentemente
      dal fatto che il testo generato sia quello reale o il fallback interno
      per output troppo corto/leak (il provider è stato comunque invocato
      almeno una volta).
- [ ] `response_assembler.py` non produce mai una riga `llm_calls` di refusal
      con `self.policy is None` (l'intero blocco è condizionato alla riga
      261) — verificato con un test che esercita `self.policy = None`.
- [ ] Nessun testo residuo nel piano né nei documenti `docs/` aggiornati
      nello stesso PR (§8) descrive la query di ricostruzione `SUM(...) FROM
      llm_calls` come fonte "canonica"/"completa"/con "garanzia di
      completezza" in senso assoluto — il testo corretto è "la fonte più
      completa disponibile tra le righe effettivamente persistite",
      esplicitamente soggetta allo stesso limite P2/R1 (coda
      `write_queue.py` lossy) di `request_token_usage`.

### Aggiunte dopo il sesto verdetto BLOCK (v7)

- [ ] Per una riga aggregata `llm_calls` con almeno un componente `missing`
      e almeno un componente non-missing con `total_tokens>0`, anche le
      colonne numeriche `input_tokens`/`output_tokens`/`total_tokens` sono
      **non-NULL** e uguali ai valori decodificati da `token_usage_json`
      per la stessa riga — non solo la colonna JSON. Solo un aggregato con
      **tutti** i componenti `missing` (`total_tokens==0`) produce sia
      `token_usage_json IS NULL` sia le 3 colonne numeriche `NULL`.
- [ ] `SELECT SUM(total_tokens) FROM llm_calls WHERE run_id=? AND
      request_id=? AND COALESCE(billable_provider_call,1)=1` su un run con
      almeno una riga aggregata mista produce lo stesso totale che si
      otterrebbe sommando manualmente `json.loads(token_usage_json)
      ["total_tokens"]` per le stesse righe — nessuna sottostima introdotta
      dalla derivazione delle colonne numeriche.
- [ ] La query "Breakdown per modulo e modello" (`get_token_usage_breakdown`)
      esclude le righe `billable_provider_call=0` con lo stesso predicato
      della query di ricostruzione offline — verificato con un test che
      include almeno 1 riga diagnostica non-billable (es.
      speculative-reuse) e ne verifica l'assenza dal breakdown.
- [ ] Per una richiesta con un mix di 1 riga billable e 1 riga diagnostica
      non-billable, `request_token_usage.llm_call_count` (e
      `missing_usage_count`/`estimated_usage_count`) valgono **1**, non 2 —
      l'hook dell'accumulatore in `ObservabilityService.emit()`/
      `emit_batch()` applica lo stesso filtro billable della query SQL.
- [ ] `run_benign_fast_path`, `run_safe_complete_path`, `run_fast_path`
      (`deliberation_runner.py`) e `_regenerate_for_contract`
      (`controller.py`) producono righe `llm_calls` con `model` non-vuoto
      quando `self.policy.model` è disponibile — mai `NULL`/`None` per una
      chiave `model` esplicitamente passata come `None`.
- [ ] `_revalidate_draft` (`controller.py`) produce una riga `llm_calls`
      con `model` uguale al valore di `get_dccl_llm_model()` usato per la
      chiamata (`model_override`), **non** a `self.policy.model` quando i
      due differiscono.
- [ ] Una speculative call (`_speculative_generate`) con
      `self.policy.model` assente/`None` produce `model=""` in `llm_calls`,
      mai `NULL`/`None`.
- [ ] `RefusalGenerationResult` costruito senza l'argomento `token_usage`
      (come nei test esistenti `tests/test_controller_speculative_lazy.py:101`,
      `tests/test_refusal_handler_duration.py:111`) usa il default
      `TokenUsage(0, 0, 0, "missing")` senza sollevare `TypeError` — nessuna
      modifica richiesta a questi due test.
- [ ] Righe `llm_calls` prodotte da critic/simulator/hindsight/perspectives
      (via `_policy_llm_model_for_action`/`_module_model`) hanno `model`
      non-vuoto quando l'oggetto sorgente espone `.model`, e `model=""`
      (mai `NULL`/`None`) quando non lo espone.

### Correzione dopo il settimo verdetto BLOCK (v8, PROJECT_SPEC §9)

- [ ] `_module_model()` (`deliberation_runner.py:212-226`) ritorna
      `module.policy.model` — non `module.model` — per critic, simulator,
      hindsight e perspectives quando `module.policy.model` è presente;
      verificato con un test dedicato che imposta `module.policy.model` e
      `module.model` a valori diversi e verifica che venga ritornato il
      primo (protegge il Goal "per modello effettivo" — la lettura errata
      azzererebbe l'attribuzione di modello dei 4 moduli core).
- [ ] `_policy_llm_model_for_action(policy, "rewrite")`
      (`deliberation_runner.py:200-209`) ritorna `policy.rewrite_model` —
      non `policy.model` — quando i due differiscono
      (`models/policy.py:143`, `models/policy.py:162`,
      `models/policy.py:489` — `OpenAIPolicy.rewrite()` chiama
      `generate(..., model_override=self._rewrite_model)`); verificato con
      un test dedicato che imposta `rewrite_model` diverso da `model` e
      confronta il valore ritornato per `action="rewrite"` vs
      `action="generate"` (protegge il Goal "per modello effettivo" — la
      lettura errata misattribuirebbe ogni rewrite al modello di
      generazione primario).

## Tests to add / modify

### Copertura esistente rilevante (da controllare per non-regressione)

Nessun file `tests/test_token_*.py` esiste oggi. La copertura del token
accounting è indiretta, sparsa in test di altre funzionalità:

- `tests/test_generation_overrides.py:37-46` — `_FakeUsage` sempre con split
  completo; non copre fallback 70/30 né usage assente.
- `tests/test_orchestrator.py:1630-1642`
  (`test_token_usage_json_builder_from_deliberative_result`) —
  **comportamento-locking** su `_token_usage_json_from_result`: l'oggetto mock
  (righe 1633-1637) non ha un attributo `source` → la funzione modificata deve
  gestirne l'assenza con un default esplicito, senza `AttributeError`.
- `tests/test_risk_persist_batch.py:40,94,107,120,174` e
  `tests/test_runtime_pooling.py:124,140` — comportamento-locking sul formato
  stringa di `token_usage_json` già passato pre-formattato: non deve cambiare
  per le chiamate esistenti.
- `tests/test_phase0_baseline_script.py:34,49` — schema/colonna storica.
- `tests/test_observability_sqlite_sink.py:39-67` — dispatch minimale
  `EVENT_LLM_CALL`, non asserisce colonne token; da leggere insieme al pattern
  di migrazione idempotente esistente (righe 643-713).
- `tests/test_observability_read_store.py:155-182,321` —
  comportamento-locking sulle colonne di `get_llm_calls_for_request` e sul
  Protocol; da estendere, non sostituire.
- `tests/test_observability_service.py:64-160` —
  comportamento-locking sulla non-propagazione degli errori in
  `emit`/`emit_batch` (riga 118).
- `tests/test_embedder.py:191-234` — happy path `OpenAIEmbedder.embed()`,
  oggi senza asserzioni di persistenza.
- `tests/test_local_embedder.py` — conferma che `LocalEmbedder`/`HashingEmbedder`
  restano fuori scope.
- `tests/test_constitution_retrieval_persistence.py:15-67` —
  comportamento-locking su `_persist_constitution_llm_call`; verificato che i 3
  call site non passano oggi `token_usage_json` (bug reale).
- `tests/test_server_proxy.py:54-72` — `usage` fisso nell'upstream mock, ma
  nessun test asserisce il campo `usage` della risposta sintetica del proxy —
  gap puro.
- `tests/test_sdk_response.py:15-62` — `_make_metadata` usa `MagicMock()`:
  auto-vivifica attributi non settati come `MagicMock`, non `0` — rischio di
  falso positivo se `from_result` non fa `getattr(..., 0)` esplicito.
- Da non toccare, verificare verdi senza modifiche:
  `tests/test_system_prompt_byte_equality.py`,
  `tests/governance_invariants/test_q17_hard_signal_invariant.py`,
  `tests/test_decide_action.py`, `tests/test_safe_complete_policy.py`,
  `tests/test_governed_delivery.py`, `tests/test_observability_write_queue.py`.

### Gap analysis

1. Nessun test unitario per `TokenUsage` (modulo non esiste).
2. Nessun test per l'accumulatore per-`request_id` (concorrenza, cap FIFO,
   idempotenza `finalize_and_persist`).
3. Nessun test distingue `estimated` da `missing` da `exact` in
   `OpenAIPolicy._complete`.
4. Nessun test copre il bug di persistenza mancante in
   `constitution/retriever.py`.
5. Nessun test copre l'instrumentazione nuova di `OpenAIEmbedder.embed()` né
   la sua natura best-effort.
6. Nessun test verifica che `LocalEmbedder`/`HashingEmbedder` restino
   **non istrumentati** (rischio di regressione "per simmetria").
7. Nessun test di migrazione idempotente per le nuove colonne/tabella su DB
   pre-esistente.
8. Nessun test per `get_token_usage_totals`/`get_token_usage_breakdown`.
9. Nessun test che il campo `usage` del proxy rifletta i totali reali su
   NORMAL_COMPLETE/SAFE_COMPLETE e resti `{0,0,0}` sul path di fallimento.
10. Nessun test end-to-end che copra l'intera catena `process()` → DB →
    breakdown query → `usage` nel proxy.
11. Nessun test verifica che l'hook di `_finalize_token_accounting` non alteri
    la byte-equality del prompt in assenza di `developer_contract`/history.
12. Nessun test copre la mutazione post-costruzione dei 6 nuovi campi su
    `ResponseMetadata` (non frozen) sotto mypy strict.

### Nuovi test da aggiungere

**`tests/test_token_usage.py`** (NUOVO)
- `test_from_openai_usage_exact_when_split_present`
- `test_from_openai_usage_estimated_when_split_missing_but_total_present` —
  blocca la regressione dello split 70/30 storico.
- `test_from_openai_usage_missing_when_usage_is_none`
- `test_from_openai_usage_embedding_forces_output_zero`
- `test_from_openai_usage_zero_total_with_usage_object_present_is_estimated_not_missing`
  — **nome e comportamento corretti rispetto alla v1 di questo piano**
  (Product decision 4/R7): `usage` non-`None` con `total_tokens==0` e nessuno
  split → `source="estimated"`, MAI `"missing"`. `"missing"` è riservato solo
  a `usage is None`. (La v1 di questo test, chiamato
  `test_from_openai_usage_zero_total_is_missing_not_estimated`, lockava il
  comportamento opposto — errore segnalato da Codex nella seconda review,
  corretto qui.)
- `test_from_json_legacy_without_source_defaults_unknown`
- `test_from_json_roundtrip_with_source`
- `test_to_json_missing_returns_none`
- `test_to_json_zero_but_exact_is_not_none`
- `test_token_usage_is_frozen`
- Fixture: nessun mock esterno; `types.SimpleNamespace`/`MagicMock(spec=[...])`
  per controllare precisamente quali attributi esistono (assente vs None vs zero).

**`tests/test_request_token_accumulator.py`** (NUOVO)
- `test_record_llm_call_usage_accumulates_totals`
- `test_record_llm_call_usage_counts_missing_and_estimated_separately`
- `test_pop_request_token_usage_removes_entry` (no double-accounting)
- `test_different_request_ids_do_not_mix`
- `test_concurrent_requests_isolated_under_thread_pool` — `ThreadPoolExecutor(max_workers=3)`
  (stesso pattern dei risk mini-estimator), N=50 `request_id` distinti × 10
  chiamate concorrenti ciascuno; solo invarianti di stato finale, niente
  asserzioni di ordine/timing.
- `test_fifo_cap_evicts_oldest_entry_deterministically` — richiede un cap
  configurabile iniettabile nel design (parametro/costante di modulo
  patchabile via `monkeypatch`), non il default 5000 hardcoded.
- `test_finalize_and_persist_never_raises_on_persistence_failure`
- `test_finalize_and_persist_emits_event_with_correct_payload`
- `test_finalize_and_persist_pops_even_if_no_calls_recorded` — comportamento
  da decidere esplicitamente e lockare.
- Fixture: nessun I/O reale; mock/patch del punto di persistenza/emit. Nessun
  clock reale (contatore di inserimento, non timestamp, per il test del cap).

**`tests/test_models_base_token_usage.py`** (nuovo)
- `test_generation_result_token_usage_json_includes_source_exact`
- `test_generation_result_token_usage_json_none_when_missing_preserves_legacy_null`
  — comportamento-locking diretto su `models/base.py:126-127`.
- `test_generation_result_default_source_when_field_omitted` — retrocompatibilità
  con costruttori esistenti che non passano il nuovo campo (es.
  `test_llm_parse_contract.py:138`).

**`tests/test_models_policy_token_usage.py`** (nuovo, o estendere
`test_generation_overrides.py`)
- `test_complete_returns_exact_source_when_usage_has_split`
- `test_complete_returns_estimated_source_on_70_30_fallback` — blocca la
  regressione dello split storico.
- `test_complete_returns_missing_source_when_usage_is_none`
- `test_complete_6_tuple_call_sites_updated` — sui due call site di
  `policy.py` (~330-349, ~385-405), verifica che il `GenerationResult`
  risultante porti `token_usage_source` coerente.

**`tests/test_deliberation_runner_token_usage.py`** (o estendere
`test_orchestrator.py` vicino a riga 1630)
- `test_token_usage_json_builder_propagates_source`
- `test_token_usage_json_builder_defaults_source_when_attribute_absent` — usa
  l'oggetto mock esistente esattamente come nel test attuale, **senza**
  aggiungere l'attributo, per verificare che non sollevi e produca un default
  esplicito. Test di non-regressione diretto e bloccante.

**`tests/test_observability_sqlite_sink_token_usage.py`** (o estendere
`test_observability_sqlite_sink.py`)
- `test_sqlite_sink_write_llm_call_with_token_columns`
- `test_sqlite_sink_write_llm_call_without_usage_leaves_columns_null` —
  distinzione NULL/0 esplicita.
- `test_init_db_migration_idempotent_adds_new_llm_calls_columns`
- `test_init_db_migration_on_preexisting_db_without_new_columns` — simula DB
  storico con schema minimale creato manualmente.
- `test_new_index_idx_llm_calls_module_model_created`
- `test_request_token_usage_table_created_with_fk_cascade` — verificare che
  `PRAGMA foreign_keys=ON` sia attivo sulla connessione di test, altrimenti
  impostarlo esplicitamente.
- `test_insert_request_token_usage_is_idempotent`
- `test_dispatch_routes_request_token_usage_finalized_event` — sia via sink
  singolo sia via batch dispatch.
- `test_fk_order_includes_request_token_usage` — `request_token_usage` deve
  seguire `requests` nell'ordine FK.
- Fixture: pattern esistente `tmp_path` + `monkeypatch.setenv` +
  `init_db(dbp)` (`test_observability_sqlite_sink.py:31-36`).

**`tests/test_observability_read_store_token_usage.py`** (o estendere
`test_observability_read_store.py`)
- `test_get_token_usage_totals_returns_aggregated_values`
- `test_get_token_usage_totals_missing_request_returns_none_or_zero` —
  comportamento esplicito da lockare.
- `test_get_token_usage_breakdown_groups_by_module_phase_action_model`
- `test_read_store_protocol_declares_new_methods` — estende
  `test_read_store_protocol_via_obs` (riga 321).

**`tests/test_orchestrator_embedder_token_usage.py`** (o estendere
`test_embedder.py`)
- `test_embed_records_llm_call_with_usage_on_success`
- `test_embed_records_missing_usage_when_response_has_no_usage_attr`
- `test_embed_instrumentation_failure_does_not_break_embedding` —
  invariante osservabilità applicata qui per la prima volta.
- `test_embed_still_calls_client_exactly_once_with_instrumentation` — nessuna
  doppia fatturazione.
- `test_local_embedder_embed_does_not_emit_llm_call` — blocca la regressione
  "instrumentazione per simmetria" su `LocalEmbedder`/`HashingEmbedder`.

**`tests/test_sdk_response_token_usage.py`** (o estendere `test_sdk_response.py`)
- `test_from_result_copies_token_fields_when_present`
- `test_from_result_defaults_token_fields_to_zero_when_metadata_lacks_attrs` —
  usare `types.SimpleNamespace`, **non** `MagicMock`, per evitare
  auto-vivificazione mascherante.
- `test_metadata_is_frozen` — estende il test omonimo esistente (riga 89) ai
  nuovi campi.

**`tests/test_server_proxy_token_usage.py`** (o estendere `test_server_proxy.py`)
- `test_synthetic_completion_usage_reflects_result_metadata`
- `test_synthetic_completion_usage_defaults_to_zero_without_metadata` —
  comportamento-locking del default attuale.
- `test_pipeline_failure_usage_is_zero_no_result_available` — estende
  `test_pipeline_failure_fails_closed_without_upstream` (riga 429).
- `test_refuse_usage_zero_or_absent_no_generation_occurred` — lockare
  esplicitamente la decisione presa.
- Fixture: riusa `client_factory`/`_make_result`/`_make_upstream_chat_completion`
  già presenti (righe 36-94).

### Test di integrazione end-to-end

**`tests/test_token_accounting_e2e.py`** (NUOVO)
- `test_process_to_proxy_usage_end_to_end` — `Orchestrator` reale minimale
  (`create_minimal_orchestrator`, pattern già usato in `test_server_proxy.py:972-975`)
  con `MockPolicyLLM`/`MockRiskEstimator` estesi per produrre un mix
  `exact`+`estimated`/`missing` nello stesso turno; `MORALSTACK_OBSERVABILITY_MODE=db_only`;
  verifica in sequenza: (1) `response.json()["usage"]` coerente con la somma
  reale; (2) query diretta `SUM(...)` su `llm_calls` coerente con `usage` del
  proxy (verifica la ricostruibilità, mitigazione P2); (3)
  `read_store.get_token_usage_totals(...)` coerente con le due fonti
  precedenti; (4) `get_token_usage_breakdown(...)` con un gruppo distinto per
  policy e per risk_estimator; (5) evento
  `EVENT_REQUEST_TOKEN_USAGE_FINALIZED` presente dopo `obs.flush()`.
- `test_process_to_proxy_usage_survives_write_queue_drop` — forza
  `queue.Full` (size piccola iniettata esplicitamente, non affidata al
  timing) → verifica che `usage` nella risposta HTTP resti corretto (popolato
  sincrono prima della coda) anche se la riga durevole
  `request_token_usage` può mancare.
- `test_multiturn_usage_accumulates_per_turn_not_across_turns` — due richieste
  sequenziali stessa `conversation_id` (pattern `TestMultiTurnConversation`,
  riga 640) → il secondo turno riporta solo i propri token, non cumulativi
  (accumulatore keyato per `request_id`, non `conversation_id`).
- `test_concurrent_conversations_usage_not_mixed` — estende
  `TestAsyncConcurrency` (riga 856) con `httpx.AsyncClient`+`ASGITransport`, N
  richieste concorrenti su `conversation_id` distinti con valori di token
  deterministici per indice → nessun mix nella risposta HTTP.

Fixture/mock comuni: `MockPolicyLLM`/`MockRiskEstimator` da
`test_orchestrator.py` (già importati in `test_server_proxy.py:33`), `tmp_path`
per DB isolato, nessuna rete reale, nessun `time.sleep` (contatori/eventi).

### Test di non-regressione critici (gate rapido prima della full suite)

```bash
python -m pytest tests/test_system_prompt_byte_equality.py -q
python -m pytest tests/governance_invariants/ -q
python -m pytest tests/test_decide_action.py tests/test_safe_complete_policy.py tests/test_safe_complete_gating.py tests/test_safe_complete_user_turn.py -q
python -m pytest tests/test_observability_write_queue.py tests/test_observability_service.py tests/test_observability_contract.py -q
python -m pytest tests/test_server_proxy.py tests/test_conversation_correlation.py -q
python -m pytest tests/test_ledger.py tests/test_ledger_storage.py tests/test_ledger_fast_path_events.py tests/test_ledger_fast_path_gate_rejected_e2e.py tests/test_ledger_posture_symmetry.py -q
python -m pytest tests/test_orchestrator.py -k "token_usage" -q
python -m pytest tests/test_constitution_retrieval_persistence.py tests/test_constitution_retrieval.py -q
python -m pytest tests/test_generation_overrides.py tests/test_llm_parse_contract.py -q
python -m pytest tests/test_governed_delivery.py -q
```

Se uno qualsiasi fallisce dopo l'implementazione, il difetto è nella modifica,
non nel test — non modificare l'asserzione esistente senza giustificazione
esplicita (PROJECT_SPEC §7/§9).

### Comandi esatti da eseguire (in ordine)

```bash
# 1. Unit isolati sui nuovi moduli
python -m pytest tests/test_token_usage.py tests/test_request_token_accumulator.py -q

# 2. Unit sui moduli modificati
python -m pytest tests/test_models_base_token_usage.py tests/test_models_policy_token_usage.py tests/test_generation_overrides.py -q
python -m pytest tests/test_deliberation_runner_token_usage.py tests/test_orchestrator.py -k "token_usage" -q
python -m pytest tests/test_constitution_retrieval_persistence.py -q
python -m pytest tests/test_embedder.py tests/test_local_embedder.py tests/test_orchestrator_embedder_token_usage.py -q
python -m pytest tests/test_observability_sqlite_sink.py tests/test_observability_sqlite_sink_token_usage.py tests/test_observability_read_store.py tests/test_observability_read_store_token_usage.py -q
python -m pytest tests/test_sdk_response.py tests/test_sdk_response_token_usage.py -q
python -m pytest tests/test_server_proxy.py tests/test_server_proxy_token_usage.py -q

# 3. Integrazione end-to-end
python -m pytest tests/test_token_accounting_e2e.py -q

# 4. Gate di non-regressione mirato
python -m pytest tests/test_system_prompt_byte_equality.py tests/governance_invariants/ tests/test_decide_action.py tests/test_safe_complete_policy.py tests/test_observability_write_queue.py tests/test_ledger.py -q

# 5. Full suite
python -m pytest

# 6. Type checking strict sui moduli orchestration toccati
python -m mypy moralstack.orchestration.controller moralstack.orchestration.types moralstack.orchestration.embedder moralstack.orchestration.persistence_helpers --strict
python -m mypy moralstack --ignore-missing-imports

# 7. pre-commit scoped sui file toccati (HEAD non è pre-commit-clean: usare --files, non -a)
python -m pre_commit run --files moralstack/observability/token_usage.py moralstack/observability/request_token_accumulator.py moralstack/models/base.py moralstack/models/policy.py moralstack/orchestration/deliberation_runner.py moralstack/constitution/retriever.py moralstack/orchestration/embedder.py moralstack/observability/sinks/sqlite_sink.py moralstack/observability/read_store.py moralstack/observability/service.py moralstack/orchestration/types.py moralstack/orchestration/controller.py moralstack/server/proxy.py moralstack/sdk/response.py tests/test_token_usage.py tests/test_request_token_accumulator.py
```

### Parti difficili da testare deterministicamente

- **Cap FIFO dell'accumulatore** (~5000 entry di default): esporre un
  parametro/costante patchabile per i test, non affidarsi a creare 5001 entry
  reali nel test.
- **Concorrenza reale con `ThreadPoolExecutor`**: solo invarianti di stato
  finale, mai assert su ordine/timing; `threading.Barrier` se serve forzare
  sovrapposizione reale, non `time.sleep`.
- **`queue.Full` drop nel test e2e**: dimensione coda piccola iniettata
  esplicitamente prima di avviare il servizio, non affidata al carico/timing.
- **Distinzione `estimated` vs `missing` su `total_tokens=0`** (R7) — **risolta**:
  `usage` presente con `total_tokens==0` → `"estimated"`; `usage is None` →
  `"missing"`. Nessuna ambiguità residua (vedi correzione v3 al nome/comportamento
  del test in questa sezione).
- **`GovernanceMetadata.from_result` con mock esistenti**: i test attuali
  usano `MagicMock()` puro (auto-vivificante); i nuovi test sui campi token
  devono usare `SimpleNamespace`/`spec` esplicito per evitare falsi positivi.

### DELTA v3 — test aggiuntivi dopo il secondo verdetto BLOCK

Copre le novità introdotte nella revisione v3: refusal path corretto
(`refusal_handler.py`), inventario completo dei siti non-billable
(incluso il difetto cache-hit scoperto in v3), e accumulo dei retry
falliti nei 4 moduli runtime. Sostituisce/estende i test già previsti per
BLOCKING 2/3/4 nel DELTA v2 sotto (quelli restano validi dove non in
conflitto: `test_speculative_reuse_call_marked_non_billable`,
`test_skipped_empty_guidance_rewrite_marked_non_billable`,
`test_leakage_detected_output_protection_marked_non_billable` — sezione H
— restano validi as-is).

**L. `tests/test_refusal_handler.py` (nuovo o estendere se esiste)** —
BLOCKING 4/A:
- `test_route_refuse_persists_token_usage_json_via_emit_llm_call` — target
  `refusal_handler.py:147-158`.
- `test_route_refuse_zero_token_usage_when_llm_client_unavailable`.

**M. `tests/test_deliberation_runner_billable_provider_call.py` (estendere,
sezione H del DELTA v2)** — BLOCKING 2/B, inventario completo:
- `test_fast_path_speculative_reuse_marked_non_billable` — target righe
  866-887.
- `test_fast_path_leakage_detected_marked_non_billable` — target righe
  909-937 (il sito esatto della seconda review Codex).
- `test_critique_real_call_marked_billable_when_not_skipped` — target
  righe 2907-2939, `is_skipped=False`.
- `test_critique_skip_marked_non_billable_when_skipped` — stesso sito,
  `is_skipped=True` (`CriticReport.empty_skipped()`). Copertura esplicita
  di entrambi i rami (R13).

**N. `tests/test_module_result_cache_billing.py` (NUOVO)** — BLOCKING 2/B,
difetto cache-hit (R12):
- `test_simulate_cache_hit_marked_non_billable_and_no_double_count` — due
  chiamate identiche a `LLMSimulator.simulate()` con caching abilitato
  (default); verifica che la seconda produca `SimulationResult.from_cache
  is True`, che la riga `llm_calls` corrispondente abbia
  `billable_provider_call=0`/`cache_status="hit"`, e che il totale token
  della seconda richiesta (via accumulatore/proxy) non aumenti rispetto
  alla prima.
- `test_hindsight_cache_hit_marked_non_billable_and_no_double_count` —
  stesso schema su `LLMHindsightEvaluator.evaluate()`.
- `test_perspective_cache_hit_marked_non_billable_when_caching_enabled` —
  stesso schema su `LLMPerspectiveEnsemble.evaluate()`, con
  `enable_caching=True` esplicito (default `False` per questo modulo).
- `test_cache_miss_first_call_always_billable` — baseline: la prima
  chiamata (cache-miss) su ciascuno dei 3 moduli resta
  `billable_provider_call=1`.

**O. `tests/test_runtime_modules_retry_token_accounting.py` (NUOVO)** —
BLOCKING 3/C, retry falliti:
- `test_critic_retry_failed_attempt_persists_billable_row_with_tokens` —
  target `critic_module.py:435-537`, un tentativo che solleva
  `JSONParseError` seguito da uno riuscito: verifica una riga `llm_calls`
  extra con `call_outcome="retry_failed"`, `billable_provider_call=1`,
  `token_usage_json` non-NULL.
- `test_simulator_retry_failed_attempt_persists_token_usage` — target
  righe 466-481: **corregge la regressione nota** (oggi il primo tentativo
  fallito, `attempt==0`, non viene mai persistito) — verificare che con
  1° tentativo fallito + 2° riuscito, ENTRAMBI producano una riga
  (comportamento diverso da oggi).
- `test_hindsight_retry_failed_attempt_persists_billable_row_with_tokens` —
  target righe 723-793 (nessun precedente esistente).
- `test_perspective_retry_failed_attempt_persists_billable_row_with_tokens`
  — target righe 707-740, verifica che il campo `prompt` dell'audit row
  identifichi la prospettiva fallita (`perspective.id`/`name`).
- `test_retry_failed_row_excludes_infra_errors_not_parsing_errors` —
  simula un fallimento di `self.policy.generate(...)` stessa (es.
  `ConnectionError`, non un errore di parsing): **nessuna riga
  `retry_failed`** deve essere persistita in questo caso (guardrail
  `attempt_token_usage is not None`, R15).
- `test_retry_failed_then_succeeded_sql_parity` (**R14, esplicitamente
  richiesto**) — un tentativo fallito (es. 40 token) + uno riuscito (es. 60
  token) sullo stesso modulo/richiesta: `SUM(total_tokens) FROM llm_calls
  WHERE run_id=? AND request_id=? AND module=<modulo> AND
  COALESCE(billable_provider_call,1)=1` deve essere esattamente `100`, mai
  `160` (doppio conteggio se il dataclass finale sommasse anche lui) né
  `60` (se il tentativo fallito venisse scartato).
- `test_all_retries_exhausted_still_persists_each_failed_attempt` —
  perspective: prospettiva finisce in `failed_perspectives` dopo
  `max_retries` tentativi falliti; verifica che comunque N righe
  `llm_calls` (una per tentativo) siano state persistite, nessuna riga
  finale "riuscita" essendo mai stata costruita.
- `test_critic_simulator_hindsight_perspective_numeric_fields_unchanged_by_retry_fix`
  (**non-regressione esplicita**) — per ciascuno dei 4 moduli, con un
  retry fallito+riuscito, verifica che `tokens_used`/`prompt_tokens`/
  `completion_tokens` sul dataclass finale riflettano SOLO l'ultimo
  tentativo riuscito, non la somma (R14 — lock del design scelto, non
  dell'alternativa scartata).

**Comandi pytest aggiuntivi v3:**

```bash
python -m pytest tests/test_refusal_handler.py -q
python -m pytest tests/test_deliberation_runner_billable_provider_call.py -q
python -m pytest tests/test_module_result_cache_billing.py -q
python -m pytest tests/test_runtime_modules_retry_token_accounting.py -q
```

`mypy --strict` esteso anche a `moralstack.orchestration.refusal_handler`
(i 4 moduli runtime restano fuori strict, invariato).

### DELTA — test aggiuntivi dopo il verdetto BLOCK (Decisione 8)

Questa sotto-sezione copre SOLO i test nuovi resi necessari dai 5 fix
BLOCKING. La copertura test già descritta sopra (TokenUsage base,
accumulatore base, `models/base.py`/`policy.py`, `constitution/retriever.py`,
embedder, schema SQLite base, proxy, SDK response, e2e) resta valida e non è
ripetuta qui.

**Existing coverage rilevante per il delta:**
- `tests/test_speculative_overlap.py:1-143` — copre `join_for_consumer`/
  `abandon` a livello di eventi orchestrazione, ma **non** verifica
  `run_id`/`request_id` nel payload persistito da `abandon()` (coerente col
  bug prerequisito: oggi il payload non li contiene affatto).
- `tests/test_controller_speculative_lazy.py:41-114` — comportamento-locking
  sul non-blocking di `abandon()` sul path refuse; pattern riusabile
  (barrier/Event, non `sleep`).
- `tests/test_orchestrator.py:1630-1642` — comportamento-locking diretto sul
  builder `_token_usage_json_from_result`, da non rompere.
- `tests/test_perspective_module.py:52-59` — `MockGenerationResult` senza
  `token_usage_source` è già, per costruzione, il caso "attributo assente"
  richiesto da BLOCKING 3.
- `tests/test_observability_contract.py:86-90` — comportamento-locking sui
  payload persistiti per path che includono `"speculative-reuse"`; va
  verificato che `billable_provider_call` (campo additivo) non lo rompa.
- `tests/test_risk_persist_batch.py:28-46,168-169` — **guardrail da NON
  toccare**: si rompe correttamente se `billable_provider_call` viene
  aggiunto per errore al payload locale del risk estimator (fuori scope).
- `tests/test_safe_refusal_generator.py:1-80` — copre solo il wrapper
  stringa; nessun test su `generate_llm_safe_refusal_detailed`/
  `_llm_refusal_call` (gap puro).
- `tests/test_response_assembler.py`, `tests/test_final_revalidation.py` —
  nessuna asserzione su `token_usage_json` nei 2 call site di rifiuto (gap
  puro).
- Nessun file dedicato `test_critic_module.py`/`test_simulator_module.py`/
  `test_hindsight_module.py` (copertura sparsa); `test_perspective_module.py`
  esiste già come file dedicato.

**A. `tests/test_speculative_overlap.py` (ESTENDERE)** — prerequisito +
BLOCKING 1 (**semplificato in v3**: nessun contatore/timer da testare, solo
la propagazione del contesto):
- `test_abandon_bg_thread_persists_context_captured_before_thread_start` —
  con contesto impostato nel thread di test, `merged` emesso da `_bg`
  contiene esattamente `run_id`/`request_id`/`session_id`/`turn_number`
  catturati (cattura il bug prerequisito: oggi sempre `None`).
- `test_abandon_bg_thread_setdefault_preserves_existing_meta_ids` — se
  `meta` ha già `run_id`/`request_id` propri, non vengono sovrascritti.
- `test_abandon_bg_discarded_call_outcome_remains_billable_true` — la riga
  `call_outcome="discarded"` resta `billable_provider_call=True` di default.
- `test_abandon_bg_thread_failure_does_not_propagate` — `Future` che
  solleva un'eccezione: nessun impatto sul thread chiamante (comportamento
  già esistente, non regredire).
- Sincronizzazione: mai `time.sleep`; `threading.Event` iniettato via
  `side_effect` su `emit_llm_call`, poi `event.wait(timeout=5.0)` come
  limite superiore.
- **Rimosso rispetto alla v2** (meccanismo di contatore pendente/finalizzazione
  differita/timer, eliminato in v3 — vedi Decisione 8/BLOCKING 1): nessun
  test su `mark_speculative_pending`/`mark_speculative_resolved`/
  `has_pending_speculative_discard`/`peek_request_token_usage`/
  `request_deferred_finalize` — queste funzioni non esistono più nel design.

**B. `tests/test_request_token_accumulator.py` (funzioni aggiuntive rispetto
al piano base)** — solo BLOCKING 5 (chiave composita; la parte "BLOCKING 1
Parte B" della v2 è stata rimossa):
- `test_record_llm_call_usage_two_run_ids_same_request_id_do_not_mix` —
  lock diretto di BLOCKING 5.
- `test_fifo_cap_eviction_is_per_tuple_not_per_request_id`
- `test_orphan_entry_from_late_speculative_event_is_evicted_by_fifo_cap` —
  un evento `llm.call` che arriva dopo che `finalize_and_persist` ha già
  fatto `pop` sulla stessa tupla crea una nuova entry orfana; verificare che
  il cap FIFO la evinca come qualunque altra entry, nessun leak né
  eccezione (comportamento accettato esplicitamente in R8/v3).

**C. `tests/test_token_usage.py` (funzioni aggiuntive)** — BLOCKING 3:
- `test_from_generation_result_exact_when_source_and_split_present`
- `test_from_generation_result_tolerant_of_missing_token_usage_source_attribute`
  — `SimpleNamespace(tokens_used=10)` senza l'attributo → nessun
  `AttributeError`.
- `test_from_generation_result_missing_when_no_attributes_at_all`
- `test_from_generation_result_invalid_source_value_falls_back_safely`
- `test_combine_empty_list_returns_missing_zero`
- `test_combine_sums_counts_and_source_is_least_certain`
- `test_combine_all_exact_returns_exact`
- `test_combine_single_element_is_identity`

**D. `tests/test_orchestrator.py` (estendere area riga ~1630):**
- `test_token_usage_json_builder_from_deliberative_result` — **esistente,
  non toccare/indebolire**.
- `test_token_usage_json_builder_delegates_to_token_usage_from_generation_result`
  (NUOVO) — patch su `TokenUsage.from_generation_result`, assert chiamata.
- `test_token_usage_json_builder_none_only_when_source_is_missing` (NUOVO) —
  lock esplicito del comportamento **nuovo e intenzionale** deciso in
  Revision log/R7: un oggetto con conteggi a zero ma `source="estimated"`
  esplicito produce JSON non-`None`.

**E. `tests/test_runtime_modules_token_usage_source.py` (NUOVO)** —
BLOCKING 3:
- `test_critic_report_populates_token_usage_source_from_generation_result`
- `test_critic_report_defaults_unknown_when_result_lacks_attribute`
- `test_simulation_result_populates_token_usage_source_via_build_result`
- `test_hindsight_result_populates_token_usage_source`
- `test_perspective_result_token_usage_source_set_post_construction`
- `test_ensemble_result_aggregates_token_usage_source_parallel_path` —
  target righe ~624-634.
- `test_ensemble_result_aggregates_token_usage_source_sequential_path` —
  target righe ~674-684.
- `test_ensemble_result_source_reflects_worst_case_across_mixed_perspective_sources`
  — una prospettiva `"exact"`, una `"missing"` → risultato `"missing"`
  (test più sensibile a R10: un sito dimenticato produce un default errato
  qui). Estendere anche `tests/test_perspective_module.py` con varianti
  mirate riusando i fixture esistenti.

**F. `tests/test_safe_refusal_generator.py` (ESTENDERE)** — BLOCKING 4:
- `test_llm_refusal_call_returns_tuple_text_and_token_usage_on_success`
- `test_llm_refusal_call_returns_missing_token_usage_on_exception`
- `test_generate_llm_safe_refusal_detailed_no_client_returns_missing_token_usage`
- `test_generate_llm_safe_refusal_detailed_single_call_usage_equals_call_usage`
- `test_generate_llm_safe_refusal_detailed_retry_sums_both_calls_usage` —
  2 chiamate mockate con usage distinti, totale = somma.
- `test_generate_llm_safe_refusal_detailed_short_output_fallback_still_reports_real_call_usage`
  — output ≤80 char: il token usage della chiamata "sprecata" non deve
  sparire.
- `test_generate_llm_safe_refusal_wrapper_unaffected_by_tuple_return_change`
  — regressione sul wrapper stringa esistente.

**G. `tests/test_response_assembler.py` / `tests/test_final_revalidation.py`
(ESTENDERE)** — BLOCKING 4:
- `test_assemble_refuse_deliberative_persists_token_usage_json`
- `test_assemble_refuse_single_audit_row_per_generation_not_per_attempt` —
  con `attempts=2`, `record_llm_call` chiamato esattamente una volta con
  l'usage combinato.
- `test_final_revalidation_refusal_persists_token_usage_json`
- `test_final_revalidation_refusal_single_audit_row_with_combined_usage_on_retry`

**H. `tests/test_deliberation_runner_billable_provider_call.py` (NUOVO)** —
BLOCKING 2:
- `test_speculative_reuse_call_marked_non_billable` — righe 2616-2639.
- `test_skipped_empty_guidance_rewrite_marked_non_billable` — righe
  2651-2674.
- `test_leakage_detected_output_protection_marked_non_billable` — righe
  2716-2742.
- `test_real_generate_and_rewrite_calls_remain_billable_by_default` —
  regressione: le chiamate reali non ereditano `False` per un find/replace
  troppo largo.
- Guardrail (non un nuovo test, solo attenzione in review):
  `tests/test_risk_persist_batch.py:168-169` deve restare verde senza
  modifiche.

**I. Plumbing (`observability/service.py`, `sqlite_sink.py`,
`read_store.py`) — ESTENDERE:**
- `test_emit_hook_skips_accumulator_when_billable_provider_call_false`
- `test_emit_batch_hook_filters_per_envelope_not_per_batch` — batch misto
  (target diretto R9/R11).
- `test_emit_default_billable_when_key_absent_from_payload`
- `test_write_llm_call_persists_billable_provider_call_as_integer_0_or_1`
- `test_write_llm_call_null_billable_provider_call_on_legacy_payload_without_key`
- `test_migration_idempotent_adds_billable_provider_call_column`
- `test_get_token_usage_totals_excludes_non_billable_rows`
- `test_get_token_usage_breakdown_excludes_non_billable_rows_per_module`
- `test_get_token_usage_totals_includes_null_billable_provider_call_rows_as_billable`
  — lock diretto dell'acceptance criterion "nessuna riga storica esclusa per
  errore".

**J. `tests/test_controller_token_accounting_speculative.py` (NUOVO)** —
integrazione controller + accumulatore, BLOCKING 1 (**semplificato in v3**:
non testiamo più un branch differito che non esiste, testiamo che il
comportamento sincrono resti invariato e che la riga tardiva sia comunque
persistita correttamente):
- `test_finalize_token_accounting_synchronous_on_refuse_route_regardless_of_speculative`
  — target `controller.py:2528`: `_finalize_token_accounting` chiama
  `finalize_and_persist` immediatamente, senza attendere l'esito di
  `abandon()`.
- `test_finalize_token_accounting_synchronous_on_safe_complete_route` —
  target `:2553`.
- `test_finalize_token_accounting_synchronous_on_deliberative_constrained_generation_route`
  — target `:2594`.
- `test_late_discarded_speculative_call_persisted_but_excluded_from_synchronous_usage`
  — con `Future` risolto DOPO che `_finalize_token_accounting` è già
  girato (via `threading.Event`, non `sleep`): `response.metadata.usage`
  già finalizzato non cambia retroattivamente; la riga `llm_calls` della
  speculativa arriva comunque, e `SUM(...) FROM llm_calls` la include. Lock
  esplicito del comportamento accettato in R8.

**K. `tests/test_token_accounting_e2e.py` (ESTENDERE dal set base):**
- `test_billable_provider_call_parity_between_accumulator_and_sql_reconstruction`
  (**R9, esplicitamente richiesto**) — mix di 2 envelope billable + 2
  `billable_provider_call=False` con conteggi deliberatamente enormi;
  `obs.flush()` come barriera; verifica che 3 fonti indipendenti (totale
  sincrono accumulatore, `SUM(...)` SQL filtrato, `read_store.get_token_usage_totals`)
  coincidano e riflettano solo le righe billable.
- `test_refusal_generation_token_usage_reaches_proxy_usage_field` — REFUSE
  con retry anti-leak (2 chiamate) → `usage` del proxy include la somma.
- `test_runtime_modules_breakdown_reports_non_default_token_usage_source` —
  mock critic/simulator/perspectives/hindsight con source esplicito →
  breakdown corretto per modulo.

**Edge case da coprire esplicitamente (oltre a quelli già nel set base):**
due `run_id` diversi con lo stesso `request_id` fornito dal chiamante;
`Future` che risolve con `meta=None` durante `abandon()`; `abandon()` senza
contesto (proxy non configurato); un evento tardivo della speculativa
scartata che arriva dopo che l'entry `(run_id, request_id)` è già stata
rimossa dall'accumulatore (crea un'entry orfana, evinta dal cap FIFO — R8,
nessun meccanismo di idempotenza dedicato necessario dato che non c'è più
un contatore da decrementare in v3); riga storica
`billable_provider_call IS NULL` mescolata a righe esplicite `0`/`1`; batch
`emit_batch()` misto billable/non-billable; `generate_llm_safe_refusal_detailed`
con `llm_client=None` vs client che fallisce a runtime; output di rifiuto
≤80 caratteri dopo una chiamata riuscita; `EnsembleResult` con `results`
vuoto (`TokenUsage.combine([])` non deve sollevare).

**Test difficili da rendere deterministici (in aggiunta a quelli già
segnalati nel set base):**
1. BLOCKING 1 (**semplificato in v3**, solo thread daemon per la
   propagazione del contesto, nessun contatore/timer): mai `sleep`; `Future`
   per controllare quando il thread background procede,
   `threading.Event` iniettato via `side_effect` su `emit_llm_call` come
   punto di sincronizzazione osservabile per sapere quando l'evento tardivo
   è stato emesso.
2. Comportamento nuovo di `_token_usage_json_from_result` con conteggi a
   zero ma `source` esplicito non-`missing` — risolto esplicitamente in
   Revision log/R7 come comportamento intenzionale (non ambiguo): va
   lockato con `test_token_usage_json_builder_none_only_when_source_is_missing`
   (sezione D sopra), non lasciato aperto.
3. Aggregazione `EnsembleResult` su 2 siti (parallelo/sequenziale): non è un
   problema di timing ma di copertura combinatoria — la suite deve
   esercitare esplicitamente entrambi i metodi di valutazione (R10).

**Comandi pytest aggiuntivi (da inserire nella sequenza già definita sopra,
dopo il gruppo "Unit sui moduli modificati" e prima della full suite):**

```bash
# Unit isolati sui nuovi meccanismi BLOCKING 1/5
python -m pytest tests/test_speculative_overlap.py tests/test_request_token_accumulator.py -q

# Integrazione controller + accumulatore (BLOCKING 1, 3 path esatti)
python -m pytest tests/test_controller_token_accounting_speculative.py tests/test_controller_speculative_lazy.py -q

# Discriminatore billable_provider_call (BLOCKING 2) + guardrail risk estimator
python -m pytest tests/test_deliberation_runner_billable_provider_call.py tests/test_observability_contract.py tests/test_risk_persist_batch.py -q
python -m pytest tests/test_observability_service.py tests/test_observability_sqlite_sink.py tests/test_observability_read_store.py -q

# Provenienza runtime modules (BLOCKING 3)
python -m pytest tests/test_runtime_modules_token_usage_source.py tests/test_perspective_module.py tests/test_modules_context_injection.py tests/test_output_contract.py tests/test_prompt10_fixes.py -q

# Refusal generation (BLOCKING 4)
python -m pytest tests/test_safe_refusal_generator.py tests/test_response_assembler.py tests/test_final_revalidation.py -q
```

`mypy --strict` va esteso anche a:
`moralstack.orchestration.speculative_overlap`,
`moralstack.orchestration.deliberation_runner`,
`moralstack.orchestration.safe_refusal_generator`,
`moralstack.orchestration.response_assembler`,
`moralstack.orchestration.final_revalidation` (oltre ai moduli già elencati
nel comando base).

## Implementation checklist (ordine per Cursor CLI)

1. Creare `moralstack/observability/token_usage.py` con `TokenUsage` + test
   unitari (`tests/test_token_usage.py`).
2. Aggiornare `models/base.py` (+campo, riscrivere `token_usage_json()`),
   `models/policy.py` (`_complete` a 6-tuple + 2 call site),
   `deliberation_runner.py::_token_usage_json_from_result` + relativi test.
3. Aggiornare `constitution/retriever.py` (3 blocchi + firma
   `_persist_constitution_llm_call`) + test di regressione sul bug fixato.
4. Aggiornare `orchestration/embedder.py::OpenAIEmbedder.embed()` + test
   (incluso "LocalEmbedder non istrumentato").
5. Migrazione SQLite: colonne `llm_calls`, tabella `request_token_usage`,
   indice, writer, dispatch, `_FK_ORDER`, nuovo
   `EVENT_REQUEST_TOKEN_USAGE_FINALIZED` + test di migrazione idempotente.
6. Aggiungere metodi di lettura in `read_store.py` (Protocol + impl) + test.
7. Creare `moralstack/observability/request_token_accumulator.py`; hook in
   `service.py::emit()`/`emit_batch()` + test di concorrenza/cap.
8. Aggiungere campi a `orchestration/types.py::ResponseMetadata`; implementare
   `_finalize_token_accounting` e agganciarlo a `_attach_trace_and_return` in
   `controller.py` + verifica byte-equality non regredita.
9. Aggiornare `server/proxy.py::_build_synthetic_chat_completion` e i 2 call
   site + test.
10. Aggiornare `sdk/response.py::GovernanceMetadata` + test.
11. Test di integrazione end-to-end (`tests/test_token_accounting_e2e.py`).
12. Aggiornare docs (`MORALSTACK_CODEBASE_INDEX.md`, `CODEBASE_FACTS.md`,
    `docs/modules/observability.md`, `docs/modules/persistence.md`).
13. **BLOCKING 5**: keyare l'accumulatore su `(run_id, request_id)` (tupla),
    non su `request_id` isolato — prerequisito strutturale ai due punti
    successivi + test dedicato.
14. **BLOCKING 2/B — discriminatore `billable_provider_call`**: schema +
    plumbing `write_queue.py` + inventario completo dei 6 call site
    `deliberation_runner.py` (866-887, 909-937, 2616-2639, 2651-2674,
    2718-2742, 2907-2939 condizionale) + filtro nell'hook dell'accumulatore
    E nelle query SQL, stesso PR per evitare R9 + test di parità esplicito.
15. **BLOCKING 2/B — difetto cache-hit (R12)**: campo `from_cache` su
    `SimulationResult`/`HindsightResult`/`EnsembleResult`, lettura in
    `_simulate`/`_evaluate_hindsight`/`_evaluate_perspectives` + test che
    verifica nessun raddoppio di token su cache-hit.
16. **BLOCKING 3/C — provenienza**: `TokenUsage.from_generation_result`/
    `combine` in `observability/token_usage.py` + campo
    `token_usage_source` su `CriticReport`/`SimulationResult`/
    `HindsightResult`/`PerspectiveResult`/`EnsembleResult` (2 siti) +
    riscrittura `_token_usage_json_from_result` + test su entrambi i siti
    di `EnsembleResult`.
17. **BLOCKING 3/C — retry falliti (Product decision 6)**: accumulo
    per-tentativo nei 4 retry loop (critic/simulator/hindsight/
    perspective), correzione del precedente esistente in
    `simulator_module.py:466-481` (rimuovere `if attempt > 0`) + test di
    parità SQL (R14) + test di non-regressione sui campi numerici
    esistenti (R16).
18. **BLOCKING 4 — refusal generation completo**:
    `safe_refusal_generator.py::_llm_refusal_call` a `tuple[str,
    TokenUsage]`, `RefusalGenerationResult` +campo `token_usage`,
    propagazione in `response_assembler.py`, `final_revalidation.py` E
    **`refusal_handler.py`** (il path REFUSE principale, aggiunto in v3) +
    test su tutti e 3 i percorsi REFUSE.
19. **BLOCKING 1 — race speculative, semplificato in v3**: correggere solo
    la perdita di contesto in `speculative_overlap.py::abandon()` (cattura
    `run_id`/`request_id`/`session_id`/`turn_number`/`cycle` nel thread
    chiamante, `setdefault` in `merged`). Nessun contatore/timer/
    finalizzazione differita: `_finalize_token_accounting` resta invariato
    (Decisione 4). Test: la riga `llm_calls` della speculativa scartata è
    persistita con `run_id`/`request_id` corretti; nessun test richiede che
    sia inclusa in `usage`/`request_token_usage` sincroni (R8, limite
    esplicitamente accettato).
20. Eseguire il gate di non-regressione mirato, poi `python -m pytest`
    completo (inclusi tutti i nuovi test dei punti 13-19), poi mypy strict
    su `moralstack/orchestration` (esteso a `speculative_overlap.py`,
    `deliberation_runner.py`, `safe_refusal_generator.py`,
    `response_assembler.py`, `final_revalidation.py`,
    `refusal_handler.py`), poi pre-commit scoped sui file toccati.

## Rollback plan

- Ogni PR della sequenza (Decisione 7) è indipendentemente revertibile: le
  colonne SQLite aggiunte sono additive/nullable (un revert del codice
  applicativo non richiede un downgrade di schema — le colonne extra restano
  innocue se non più scritte/lette).
- Se il hook in `ObservabilityService.emit()` mostrasse overhead o
  comportamento anomalo, è isolato in un blocco try/except: si disattiva
  rimuovendo la singola chiamata a `record_llm_call_usage` senza toccare il
  resto della pipeline di emit.
- Se `_finalize_token_accounting` in `controller.py` causasse un problema, è
  un singolo metodo aggiuntivo richiamato da un solo punto
  (`_attach_trace_and_return`) — revert = rimuovere quella singola riga di
  chiamata.
- Il campo `usage` nel proxy ha un default sicuro (`{0,0,0}`) se
  `result.response.metadata` non espone i nuovi campi (es. durante un
  rollout misto/rolling deploy) — nessun crash, solo perdita del beneficio.
- Nessuna delle modifiche tocca `final_action`/routing/DCCL — un rollback non
  ha impatto sulle decisioni di governance già prese.

### Rollback dei fix ai 5 BLOCKING (Decisione 8)

- BLOCKING 5 (chiave composita) è un cambio puramente interno alla struttura
  dati dell'accumulatore — revert = tornare a keyare su `request_id`, nessun
  impatto su schema DB o su altri fix (ma va fatto per ultimo in un
  eventuale rollback parziale, essendo un prerequisito di BLOCKING 1/2).
- BLOCKING 2 (`billable_provider_call`): colonna nullable additiva; se il
  filtro venisse rimosso, le query tornano a includere anche le righe
  diagnostiche (comportamento pre-fix, non un crash).
- BLOCKING 3 (`token_usage_source` sui moduli runtime): campi additivi con
  default `"unknown"`; un revert non tocca la logica di business dei
  moduli, solo il breakdown per modulo torna a essere meno preciso.
- BLOCKING 4 (refusal generation): `RefusalGenerationResult.token_usage` ha
  un default (`TokenUsage(0,0,0,"missing")`); un revert del plumbing nei 2
  call site di persistenza non rompe la generazione del rifiuto stesso.
- BLOCKING 1 (speculative accounting, semplificato in v3): un solo cambio
  isolato in `abandon()` (cattura contesto). Revert = tornare alla versione
  che non cattura `run_id`/`request_id` — si riaccetta il bug prerequisito
  (l'evento della speculativa scartata torna a non essere mai persistito),
  ma nessun impatto su `_finalize_token_accounting` (mai toccato in v3) né
  su altri fix.

---

## Invarianti PROJECT_SPEC §5 toccati

- **§5.6 Observability never breaks the request** — il design intero rispetta
  questo invariante per costruzione (ogni nuovo hook è try/except non
  propagante); è l'invariante più direttamente rilevante per questo piano.
- Nessun altro invariante P0 (§5.1-§5.5, §5.7) è modificato nel comportamento;
  il cambiamento al payload `usage` del proxy è additivo e non tocca
  `final_action`, system prompt, hard-signal, `core` overlay o governed
  delivery.

## Note metodologiche

- Ogni claim di questo piano è basato su file:riga letti in questa sessione
  (codebase-cartographer, architect-planner, test-strategist — tre agenti
  separati, evidenza incrociata). Dove i due documenti di partenza
  (`codex_upgrade_plan.md`, `claude_upgrade_plan.md`) citavano righe
  leggermente stale, sono state corrette con la riga verificata.
- **Scoperta non presente nei due piani originali**: il bug di persistenza
  mancante in `constitution/retriever.py` (i 3 call site non passano mai
  `token_usage_json` a `_persist_constitution_llm_call`) — aggiunto al piano
  come parte del fix (PR3), con test di regressione dedicato.
- **v2 — dopo il verdetto BLOCK di Codex**: la revisione (Decisione 8) ha
  richiesto una seconda mappatura mirata (architect-planner) che ha
  scoperto un secondo bug pre-esistente e indipendente:
  `SpeculativeOverlapHandle.abandon()` (`orchestration/speculative_overlap.py:127-170`)
  perde `run_id`/`request_id` nel thread daemon perché non propaga il
  contesto (`contextvars`) — l'evento della speculativa scartata non è mai
  persistito oggi, non solo "in ritardo" come ipotizzato inizialmente da
  Codex. Corretto come prerequisito dello stesso fix (PR14/checklist punto
  17), coerente con PROJECT_SPEC §9 (documentare un difetto scoperto fuori
  scope invece di correggerlo silenziosamente altrove).
- Anche in v2, ogni riferimento `path:line` è stato ri-verificato leggendo
  il codice reale in questa sessione (non solo riportato dal report Codex);
  dove i numeri di riga di Codex erano leggermente stale, sono stati
  corretti. Un ulteriore call site diagnostico in
  `deliberation_runner.py:2651-2674` (`SKIPPED_EMPTY_GUIDANCE`) è stato
  individuato per BLOCKING 2 e non era citato da Codex.
- **v3 — dopo il secondo verdetto BLOCK**: la seconda review Codex ha
  confermato che 3 dei 5 finding originali restavano sostanzialmente
  aperti nella v2 (refusal generation puntava al file sbagliato, il
  discriminatore `billable_provider_call` mancava un sito, il meccanismo
  di finalizzazione differita per le speculative call non chiudeva davvero
  la race), un finding era solo parziale (retry falliti nei moduli runtime
  non contati), e la v2 aveva introdotto un'incoerenza interna nuova
  (semantica zero-token contraddittoria). La mappatura mirata per la v3 ha
  scoperto un **secondo difetto pre-esistente e indipendente**, non
  presente in nessuno dei due piani originali né in nessuna delle due
  review Codex: le righe cache-hit di `simulator_module.py`/
  `hindsight_module.py` (e dormiente su `perspective_module.py`) vengono
  ri-fatturate come nuove chiamate ogni volta che la cache (attiva per
  default su simulator/hindsight) restituisce un risultato — gonfiando
  sistematicamente il conteggio token. Documentato e corretto nello stesso
  fix del discriminatore `billable_provider_call` (BLOCKING 2/B, R12),
  coerente con PROJECT_SPEC §9.
- Anche in v3, ogni riferimento `path:line` è stato ri-verificato leggendo
  il codice reale (non solo riportato da Codex); il call site reale del
  path REFUSE principale (`refusal_handler.py:94,147`, raggiunto da
  `_route_refuse`/`controller.py:1551-1578,2529`) è stato identificato con
  precisione, correggendo il targeting errato del v2 su
  `response_assembler.py`/`final_revalidation.py` (che restano comunque
  nello scope, essendo call site legittimi per altri percorsi REFUSE, non
  un errore — solo incompleti da soli).
- Non eseguito in questa fase: la suite di test, mypy, pre-commit (nessun
  codice applicativo modificato — solo piano). Vanno eseguiti durante
  l'implementazione (Cursor CLI) secondo la checklist sopra.
