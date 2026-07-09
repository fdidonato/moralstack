# Analisi tecnica MoralStack — stato del branch `main`

> **Versione analizzata**: `0.6.1` (`pyproject.toml`).
> **Metodo**: lettura diretta del codice sorgente, non della documentazione. Ogni
> affermazione è ancorata a `file:riga` o `file:funzione` e verificata in questa
> sessione. Dove non ho potuto verificare (es. esecuzione completa della suite), lo
> dichiaro esplicitamente.
> **Tono**: critico e senza sconti. I punti di forza sono reali, ma il documento
> esiste per i difetti.

---

## 0. Verdetto sintetico

MoralStack è un progetto **tecnicamente serio e sopra la media** per disciplina
ingegneristica: tipizzazione `mypy strict` sul package `orchestration`, auto-audit
documentale con citazioni riga-per-riga, difese del ledger fatte col bisturi,
test che bloccano comportamenti (behavior-locking). Non è codice giocattolo.

Ma ci sono **tre problemi che ne condizionano la credibilità in produzione**, in
ordine di gravità reale:

1. **Inversione di un invariante di sicurezza P0** (§ 2.1): il fast-path del DCCL
   consegna risposte **prima** che giri il gate deterministico sui segnali hard,
   affidando la difesa al meccanismo *più debole* disponibile (una lista di
   keyword), non al *più forte* (i q-signal del risk estimator). Critico.

2. **Tensione architetturale di fondo non risolta** (§ 4): `govern(client)` **non
   usa mai il client per generare la risposta**. La governance decide
   allow/refuse, ma il *testo* viene prodotto dal modello interno di MoralStack.
   È documentato come invariante, quindi non è un bug — ma è la scelta che più
   indebolisce la value proposition come "wrapper". Va affrontata di petto.

3. **Latenza mediana incompatibile con l'adozione** (§ 3.4): anche il percorso
   "economico" paga 3 chiamate LLM solo per stimare il rischio, più draft
   speculativo, più (sul proxy) la chiamata LLM del DCCL. Non esiste un triage
   deterministico a monte.

Tutto il resto (multi-tenant, durabilità audit, embedder, wiring) è importante ma
secondario rispetto a questi tre.

---

## 1. Cosa è corretto (verificato, non di cortesia)

- **Ledger storage robusto.** `InMemoryLedgerStorage` ha LRU (default 1000) + TTL
  (default 3600 s) con expiry lazy in lettura e `popitem(last=False)` per
  l'eviction (`orchestration/ledger_storage.py:60,73-125`). Niente memory leak
  qui, contrariamente ad altri store del sistema (§ 2.2).
- **Difese del ledger col bisturi.** Skip su posture `ESCALATED`, skip su
  `turn_index < 1`, secondary intent check oltre la similarità, e — il punto che
  conta — **il contenuto non è mai cachato**, solo i metadata di governance; il
  testo è sempre rigenerato (`orchestration/ledger.py`).
- **Pipeline fail-closed.** Su fallimento si rifiuta (governed refusal); non c'è
  passthrough silenzioso. È anche enshrined come invariante
  (`.claude/rules/governed-delivery.md`).
- **Semantica `govern()` ora documentata.** Il docstring pubblico dichiara
  esplicitamente che il client wrappato non genera la risposta e che il modello è
  quello interno (`sdk/wrapper.py:639-648`, `:421`). Questo *toglie* il carattere
  "ingannevole" dell'API: resta una scelta discutibile (§ 4), non un inganno.
- **Tipizzazione e self-audit.** `mypy strict` sul package orchestration;
  `docs/CODEBASE_FACTS.md` con citazioni e sezione bug latenti. Disciplina rara.

Questi punti sono solidi e vanno preservati. Il resto del documento è critica.

---

## 2. Cosa non è corretto (sicurezza e correttezza)

### 2.1 — CRITICO — Il fast-path DCCL scavalca il gate hard-signal deterministico

**Fatto.** In `controller.py:process()` la sequenza è:

1. `:2060/2062` → `risk_estimation` calcolato (include i q-signal hard: q5
   physical, q17 minor_exploitation, weapons, op_risk).
2. `:2065` → `_run_dccl_evaluation(...)` valuta il contract.
3. `:2086` → `if cv.decision == ComplianceDecision.MATCH:` — **nessun controllo su
   `risk_estimation` in questo blocco** (verificato `:2086-2211`).
4. `:2129/:2168` → `return self._route_compliance_match(...)` → early return.

Il gate deterministico compare **solo dopo**, irraggiungibile sul path MATCH:
`is_hard_signal_refuse(...)` è a `controller.py:2318`.

`_route_compliance_match` (`:1331`) costruisce una `Decision` hardcoded
`NORMAL_COMPLETE` con `hard_violations=[]`, `actionability_risk="LOW"`
(`_build_compliance_decision`, `:1425-1442`) e dichiara nel docstring di saltare
"risk routing, deliberation, critic, simulator, and perspectives".

**Cosa resta a difesa del path DCCL.** Una sola cosa deterministica:
`classify_safety_override` (`compliance/safety_override.py`), chiamata con
`use_llm=False` in **tutti e tre** i call site (`dccl.py:372,667,760`). È una lista
di **7 categorie**, **5-8 frasi-keyword ciascuna** (`safety_override.py:59-119`).
È banalmente aggirabile: `"how to make a bomb"` matcha, `"come costruisco un
ordigno"` no; una parafrasi o un'altra lingua passa.

**L'inversione.** I detector robusti dei segnali hard — i q-signal calcolati in
`risk_estimation` — **non gateano il fast-path**, perché `evaluate()` del DCCL
ignora esplicitamente il rischio: `_ = risk_estimation` (`dccl.py:276`). Quindi
l'invariante P0 dichiarato in `.claude/rules/hard-signal-safety.md` ("i segnali
hard non sono sovrascrivibili da un contract") è applicato **dal meccanismo più
debole** (keyword) e non da quello più forte (q-signal) che è già in memoria.

**Gravità per superficie:**
- **SDK**: il deployer è la parte fidata (è il suo contract). Un contract
  malevolo ≈ il deployer danneggia il proprio prodotto. Gravità *bassa*.
- **Proxy/multi-tenant**: il contract = ultimo messaggio `system`/`developer` del
  request body, costruito `mode="opaque"`
  (`conversation_context.py:198`). Opaque ⇒ niente `structured_rules` ⇒ gira il
  **path LLM** del DCCL (`dccl.py:298-313`, `_parse_llm_verdict:663-716`). Il
  giudice LLM valuta il contract che *è esso stesso l'input potenzialmente non
  fidato* (prompt-injectabile per costruzione), e l'unico backstop deterministico
  resta il keyword check sull'`action_excerpt`/draft. Gravità *alta* in ogni
  deployment dove il messaggio system è controllabile dal chiamante (client
  COMPL-AI-style, proxy esposto, benchmark adversariali).

**Conferma del buco nei test.** `tests/test_compliance_fast_path.py:346`
(`test_safety_override_does_not_fast_path`) usa
`action_payload="how to make a bomb at home"` — passa *perché la keyword matcha* —
e mocka `get_route`. **Non esiste** un test in cui il DCCL emette MATCH su un
payload dannoso che *evade* le keyword. Il caso peggiore è non testato.

**Fix (costo ~zero, qualità invariata): precondizione non-bypassabile.**
Il `risk_estimation` è già calcolato a `:2060`. Prima di `:2086`, calcolare il
floor deterministico P0 e invalidare il MATCH se scatta:

```python
op_risk = getattr(risk_estimation, "operational_risk", OperationalRisk.NONE)
# Pre-policy P0 floor: i q-signal hard hanno precedenza sul contract.
if is_hard_signal_refuse(_decision_prepolicy, risk_estimation, op_risk):
    cv = None   # invalida il MATCH → prosegue la pipeline normale (che rifiuterà)
```

Quelle categorie non sono autorizzabili per design: il fix non degrada nessun caso
legittimo. Da accompagnare con: (a) per i danni **fuori dalle 7 P0** (harassment
mirato, hate generativo, disinfo non-CBRN) il keyword check non copre nulla — su
MATCH free-form (output non enumerato, rilevabile via
`pipeline/output_contract.py:detect_enumerated_output`) far passare un singolo
critic quick-check prima della consegna; (b) un test con payload dannoso che evade
le keyword e un test con q-signal hard attivo che invalida il MATCH.

---

### 2.2 — ALTO — Correlation store del proxy: collisione multi-tenant + memoria illimitata

**Fatto.** `server/conversation_correlation.py` mappa hash-della-history →
`conversation_id`. `self._history_to_conversation` è un `dict` semplice
(`:97`), senza TTL/maxsize/LRU/eviction; `resolve()` (`:99-114`) non sala l'hash
con il principal. Due problemi distinti:

1. **Memoria illimitata.** Ogni request-hash e completed-turn-hash resta a vita.
   Proxy long-running → crescita monotona → OOM.
2. **Collisione cross-tenant.** Il docstring del modulo lo ammette (`:10-12`): due
   sample con history e output assistant byte-identici non sono distinguibili.
   Questo `conversation_id` chiavea lock per-conversazione, session store e ledger.
   Due utenti diversi con lo stesso messaggio d'apertura **collidono nella stessa
   conversazione**, condividendo stato di governance. È un problema di isolamento
   e privacy, non solo di benchmark.

> Nota di accuratezza: il *ledger* storage **non** ha questo problema (§ 1). Il
> leak è specifico della correlation store del proxy.

**Fix.** Memoria: `cachetools.TTLCache` o `OrderedDict` + `popitem(last=False)`
(~15 righe). Collisione: pretendere `X-Moralstack-Conversation-Id` esplicito e,
sul fallback per-hash, salare l'hash con il principal autenticato (API key /
tenant id).

---

### 2.3 — ALTO — Il troncamento del contesto tiene i turni recenti (escalation graduale)

**Fatto.** `conversation_context.py:role_serialized_transcript` (`:120-150`) itera
`reversed(lines)` e interrompe al superamento del budget → **tiene i turni
recenti, scarta i più vecchi**. Il risk estimator e il DCCL (budget 5000,
`controller.py:2069`) valutano una finestra che ha perso i turni iniziali.

**Perché è un problema.** È la vulnerabilità classica del multi-turn jailbreak
graduale: il setup malevolo sta nei turni iniziali (benigni se presi singolarmente),
il payload arriva al turno N quando i turni di setup sono già stati droppati. La
posture `ESCALATED` protegge solo se un turno precedente ha *già* fatto
REFUSE/SAFE_COMPLETE (`conversation_state.py:should_full_refresh`); in
un'escalation ben fatta nessun singolo turno scatta, la posture resta `NORMAL`, e
né il ledger né il fast-path DCCL vengono inibiti.

**Fix.** Non droppare mai i turni safety-relevant; iniettare un safety-summary
compresso dei turni droppati nel contesto del risk estimator (deve vedere lo stesso
*segnale di sicurezza* anche se vede meno testo). Rendere la posture
sticky/monotona su un trend crescente, così la conversazione perde l'eleggibilità
a fast-path/cache *prima* di scattare su un singolo turno. Lo scaffolding esiste
già (`turn_decisions_summary`, `last_governance_posture`), va wirato dove conta.

---

### 2.4 — MINORE — Bug di wiring (erodono la fiducia in un sistema che vende auditabilità)

Tutti verificati:

- **`cached_from_decision_id` mai assegnato.** Letto solo a `server/headers.py:37`
  via `getattr(..., "")`; nessuna assegnazione in tutto il package → l'header
  `X-Moralstack-Cached-From` **non viene mai emesso**.
- **`observability_mode="off"` inerte.** Campo a `sdk/config.py:58` (default
  `"off"`), ma `ObservabilityMode = Literal["db_only","dual","file_only"]`
  (`observability/config.py:23`) e `get_observability_mode()` (`:64-77`) non
  riconosce `"off"`: il campo SDK non ha effetto runtime. O lo si wira (aggiungendo
  `"off"` al literal e un no-op sink) o lo si rimuove con deprecation.
- **Docstring stale.** `models/risk/estimator.py:716` dice `q1-q13`, ma lo schema
  reale è `q1-q17` (q17 = `minor_exploitation`, vedi `:603`). Il codice vince; il
  docstring va corretto perché induce conclusioni errate sull'analisi.

---

## 3. Cosa è migliorabile (qualità, latenza, manutenibilità)

### 3.1 — Embedder OpenAI-only, in critical path, doppia embedding

`orchestration/embedder.py`: `OpenAIEmbedder` (`text-embedding-3-small`) è
l'**unica** implementazione concreta; `EmbedderProtocol` esiste (`:36`) ma non c'è
default locale. È chiamato sincrono **sia in lookup (`ledger.py:259`) sia in store
(`ledger.py:342`)** → lo stesso prompt viene embeddato **due volte** in un ciclo
miss→store. Problemi: round-trip di rete nel path critico di ogni turno multi-turn;
hard-coupling su OpenAI in un SDK che si vende provider-agnostic; doppio lavoro.

**Fix.** Default locale via il Protocol già esistente (`fastembed` ONNX/CPU, o
`model2vec` static embeddings); memoizzare l'embedding del prompt corrente così
lookup+store non lo ricalcolano. Per la soglia 0.92 un buon embedding locale basta.

> Scaling (bassa priorità): `InMemoryLedgerStorage.get_entries`
> (`ledger_storage.py:100-125`) fa scan globale su tutti gli item filtrando per
> key. Con max 1000 entry è irrilevante; se i bucket crescono, indice ANN o cap
> per-key.

### 3.2 — Durabilità audit del proxy + single-worker non enforced

`server/proxy.py:521-529,724-728`: il flush per-request è stato rimosso (sotto
burst il writer SQLite timeoutava); il drain avviene solo sullo shutdown hook
(`obs.shutdown(timeout=30.0)`). Su kill non-graceful, l'audit dei request in volo
è perso — e l'export AI-Act art.12 ci si appoggia. `examples/server_quickstart.py`
documenta (`:16-19`) che serve un solo worker uvicorn ma **non lo enforce**: un
load balancer che instrada i turni a worker diversi rompe la continuità in silenzio.

**Fix.** Writer audit out-of-process (coda durabile / WAL con checkpoint batchato);
readiness-check che rifiuta `workers>1` finché lo stato è in-process, oppure stato
condiviso su Redis (correlation + session store) per abilitare il multi-worker.

### 3.3 — `final_revalidation.py`: ~640 LOC di codice morto

`orchestration/final_revalidation.py` definisce `revalidate_final_output`
(`:148-396`), `record_upstream_final_generation`, `emit_final_revalidation_skipped`,
`has_developer_contract` — ma **nessuno ha call site live nel package** (verificato:
solo costanti come `DEFAULT_POST_REVALIDATION_REFUSAL` sono importate da
`delivery.py`/`proxy.py`; la logica di revalidation non è invocata). È esattamente
l'infrastruttura che servirebbe per il "generate-then-verify" col modello
dell'utente (§ 4), costruita e poi lasciata scollegata. O la si wira (risolvendo
§ 4) o la si rimuove: codice morto in un sistema safety-relevant è debito che
illude il lettore.

### 3.4 — Latenza: manca un triage deterministico a monte

Anche il path "economico" è caro: la stima del rischio è LLM-based —
`models/risk/estimator.py` lancia **3 mini-estimator in parallelo**
(`ThreadPoolExecutor(max_workers=3)`). Quindi anche "qual è la capitale della
Francia" paga 3 chiamate LLM solo per decidere che è benigno, più il draft
speculativo, più — sul proxy con system prompt — la chiamata LLM del DCCL (path
opaque, § 2.1). Il fast path misurato sta a ~10-12 s, il deliberativo a ~70 s.

Manca uno stadio che decida il routing *prima* e *senza* quelle chiamate per il
traffico che non ne ha bisogno. È la singola leva che cambia di più l'esperienza
(§ 5.1).

### 3.5 — God files

`orchestration/controller.py` = 2671 LOC, `deliberation_runner.py` = 3286 LOC. Per
un progetto che vuole contributor è una barriera d'ingresso. Split per
responsabilità (routing / fast-paths / delivery / compliance-routing). Priorità
bassa ma reale.

---

## 4. La tensione architetturale di fondo: `govern()` non governa il *tuo* modello

Questo è il punto su cui sono più severo, perché tocca la value proposition.

**Fatto.** Il client passato a `govern(client)` non viene **mai** chiamato per
generare la risposta finale. La generazione usa il policy LLM interno di MoralStack
(`GovernanceConfig.model` → `OPENAI_MODEL` → `gpt-4o`), come dichiarato in
`sdk/wrapper.py:639-648` e nell'invariante `governed-delivery.md`. È deliberato e
documentato — quindi non è un bug. Ma le implicazioni vanno guardate in faccia:

- Se hai fine-tunato `gpt-4o` per il tuo dominio, `govern()` **butta via** quel
  modello: la risposta esce dal modello interno di MoralStack. Per
  `NORMAL_COMPLETE` — la maggioranza del traffico — questo è una *regressione di
  qualità/brand voice/fine-tuning* rispetto a chiamare il tuo modello.
- La firma `govern(client)` evoca un *wrapper*; la realtà è un *rimpiazzo*. Il
  valore (la decisione allow/refuse) è reale, ma il prodotto promette di più di
  quanto la generazione mantenga.

**Le due strade pulite (da far scegliere, non da decidere qui):**

- **(a) Onestà di posizionamento.** Smettere di chiamarlo "wrapper". È
  "governance-as-a-service con modello proprio": la generazione è di MoralStack,
  punto. Esporre `generation="internal"` come unico modo e dirlo a caratteri
  cubitali anche nel README, non solo nel docstring.
- **(b) Generate-then-verify col modello dell'utente.** Per
  `NORMAL_COMPLETE`/`SAFE_COMPLETE`, generare col client dell'utente (con system
  steering governato) e poi **rivalidare** l'output. L'infrastruttura esiste già
  (`final_revalidation.py`, § 3.3) ma è dormiente. Questo preserva
  modello/credenziali/fine-tuning *e* la governance, restituendo la semantica che
  la parola "wrapper" promette. Costo: rcompe l'invariante "governed delivery
  only" attuale → va deciso come scelta di prodotto, non aggirato.

La mia opinione, da esporre senza accondiscendenza: finché la generazione è un
rimpiazzo, MoralStack compete con il modello dell'utente invece di *governarlo*, e
questo è un freno all'adozione più subdolo della latenza, perché si scopre solo
dopo l'integrazione.

---

## 5. Evoluzioni e strategie future

### 5.1 — Cascade fast-path (la leva sulla latenza mediana)

Triage a 3 tier *davanti* alla pipeline (non al posto):

- **Tier 0 — deterministico, sub-ms, zero rete.** Euristiche/regex + un modello
  locale minuscolo (static embeddings + classificatore lineare). Instrada
  l'ovviamente-benigno → generazione e l'ovviamente-dannoso → REFUSE; **su tutto
  il resto si astiene**.
- **Tier 1 — una sola chiamata LLM economica** (gpt-4o-mini) sulla zona grigia: il
  risk estimator collassato a *una* chiamata invece di tre.
- **Tier 2 — la pipeline deliberativa attuale** (~70 s), solo per i casi
  ambigui/dual-use/borderline veri.

**Invariante non negoziabile.** L'asimmetria: sbagliare verso "benigno" è
pericoloso (falso negativo), sbagliare verso "refuse" è sicuro (over-refusal). Uno
stadio economico può fare early-exit **solo verso la direzione sicura** e deve
delegare su qualsiasi incertezza. Così la cascata può solo ridurre la latenza, mai
la sicurezza. Si innesta su `borderline_refuse_upper` (i refuse borderline
*entrano* in deliberazione).

**Calibrazione.** Due soglie (`τ_benign` alta/conservativa, `τ_harmful` più
aggressiva), banda intermedia delega. La confidenza grezza non è calibrata →
temperature scaling / conformal prediction per garantire falsi negativi ≤ ε. Il
Tier 0 si ottiene per distillazione dalle decisioni della pipeline costosa: il
dataset esiste già (benchmark + logica q-signal in `calibration.py`).

**Trappole.** (1) Multi-turn: l'early-exit benigno del Tier 0 va gateato dalla
posture (stesso buco di § 2.3). (2) Avversariale: marker jailbreak / encoding
strani devono **forzare il defer** a prescindere dallo score. (3) Drift:
campionare una frazione delle uscite benigne, rifarle girare offline nella
pipeline completa, misurare il disaccordo, ri-calibrare.

Si compone con ciò che c'è: `enable_speculative_generation` = latency hiding;
cascade = work avoidance (si sommano); il ledger semantico davanti corto-circuita
persino il Tier 0.

### 5.2 — Hardening P0 trasversale

Il floor deterministico q-signal (§ 2.1) non deve gateare solo il DCCL, ma
**qualsiasi** scorciatoia che salti il risk router: fast-path DCCL, cache-hit del
ledger, conversational fast-path. Pattern unico: *nessuna scorciatoia consegna se i
q-signal hard sono attivi*. Un solo punto di enforcement, testato adversarialmente.

### 5.3 — Multi-tenancy seria

Stato condiviso (correlation + session store) su Redis con chiavi salate per
principal; bound espliciti su ogni store in-memory; rifiuto di `workers>1` finché
lo stato è in-process. Senza questo, il proxy non è deployabile multi-tenant in
sicurezza (§ 2.2, § 3.2).

### 5.4 — Embedder e provider-agnosticism

Default locale (§ 3.1) come prerequisito per togliere l'hard-coupling su OpenAI in
tutto il critical path. È anche un messaggio: un *governance engine* non dovrebbe
dipendere dal provider che governa.

### 5.5 — Decidere la metrica di successo

La popolarità su GitHub nasce da "provato in 2 minuti → wow". Con ~70 s e
~$0.30/chiamata sui path deliberativi, questo progetto lavora contro quel
meccanismo e compete con LlamaGuard/ShieldGemma (free, veloci). La metrica
realistica è **adozione enterprise / reference di compliance AI-Act**, non le
stelle. Le due leve che cambiano l'equazione sono: abbattere la latenza mediana
(§ 5.1) e risolvere la tensione `govern()` (§ 4). Senza, ogni altra ottimizzazione
è marginale.

---

## 6. Roadmap consigliata (per rapporto rischio/ritorno)

| # | Intervento | Tipo | Effort | Sblocca |
|---|---|---|---|---|
| 1 | § 2.1 — Floor q-signal P0 prima di ogni fast-path + critic quick-check sul free-form | Sicurezza | Basso | Tenuta P0 a reviewer ostile |
| 2 | § 2.2 — LRU+TTL sulla correlation store + salt per-principal | Sicurezza/Stabilità | Basso | Proxy multi-tenant sicuro |
| 3 | § 2.3 — Safety-summary dei turni troncati + posture sticky | Sicurezza | Medio | Robustezza multi-turn |
| 4 | § 2.4 — Chiusura bug di wiring | Affidabilità | Molto basso | Coerenza audit |
| 5 | § 4 — Decisione su `govern()`: posizionamento onesto **o** generate-then-verify | Prodotto/DX | Medio-Alto | Value proposition |
| 6 | § 3.1 — Embedder locale di default + no doppia embedding | Latenza/Coupling | Basso | Indipendenza da OpenAI |
| 7 | § 5.1 — Cascade fast-path (Tier 0 distillato + calibrazione + drift) | Performance | Alto | **Adozione** |
| 8 | § 7.5 — Persistenza: path unico async + writer con batching/coalescing + ordine FK + backpressure contata | Produzione/Audit | Medio | Audit completo sotto carico, no perdita dati |
| 9 | § 3.2/§ 7.5 — Writer audit out-of-process + enforcement single-worker/Redis | Produzione | Medio | Deploy reali multi-worker |
| 10 | § 3.3/§ 3.5 — Wiring o rimozione `final_revalidation`; split god files | Manutenibilità | Medio-Alto | Velocità contributor |

**Percorso critico verso "credibile in produzione"**: 1 → 2 → 3 → 5 → 7. Il resto è
parallelizzabile.

---

## 7. Approfondimento — Persistenza sotto carico concorrente

Questa sezione risponde a due domande precise: **(Q1) tutto viene davvero salvato,
nulla si perde?** e **(Q2) la persistenza regge ~100 turni proxy concorrenti senza
colli di bottiglia?** Le risposte, verificate sul codice, sono **no** a entrambe.

### 7.1 — La mappa reale: due path divergenti, non uno

La persistenza è "split-brain": gli eventi vengono scritti da **due percorsi
diversi a seconda del tipo**, con garanzie diverse.

**Path A — SINCRONO, sul thread di richiesta (bloccante).**
`persistence/sink.py` lo dichiara apertamente (`:5-7`): *"All persist_* functions
are SYNCHRONOUS ... call router.route() directly"*. Ci passano:
- `ensure_run_and_upsert_request` → `create_run` + `upsert_request`
  (`persistence/default.py:63-78`): 2 connessioni SQLite sincrone per richiesta.
- `update_request_domain`, `update_request_response`: sincroni.
- **Tutti gli orchestration event** via
  `DefaultEventEmitter.emit_orchestration_event` →
  `persist_orchestration_event` → `router.route()` (`persistence/sink.py:238`).
  Sono **decine per richiesta** (DCCL started/verdict, `MODULE_DEFERRED_TO_COMPLIANCE`
  ×5, risk assessment, decision trace, ledger gate, ecc.).

**Path B — ASINCRONO, un singolo worker di background.**
`async_persist_llm_call` / `async_persist_decision_trace`
(`persistence/write_queue.py:74,96`) e gli eventi multi-turn (Step 13) via
`obs.emit()`. Finiscono in `ObservabilityWriteQueue`, drenata da **un solo thread
daemon** (`observability/write_queue.py:33-38`).

Conseguenza immediata: **non esiste un'unità atomica "richiesta + i suoi eventi"**.
Il parametro `uow=` che servirebbe a garantirla è esplicitamente **ignorato**
(`persistence/sink.py:7`). Una richiesta può committare la sua riga e perdere i
propri eventi, o viceversa. L'astrazione `SqliteUnitOfWork` esiste
(`sqlite_sink.py:507`) ma non è usata per raggruppare gli eventi di una richiesta.

### 7.2 — Il modello di connessione: una connessione per evento

Ogni singola scrittura — sincrona o asincrona — chiama `_get_connection()`
(`sqlite_sink.py:497-504`): `sqlite3.connect()` + `PRAGMA busy_timeout=10000` +
`PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON`, poi **un** insert, `commit`,
`close` (es. `_write_orch_event_single:1546-1560`). Nessuna connessione
persistente, nessun pool, nessun riuso per thread. Nel path hot a evento singolo
**non c'è batching**: `emit()` invia `router.route` per *un* envelope
(`service.py:46`, `router.py:37-43`); `route_batch`/`write_batch` esistono ma non
sono nel percorso per-evento.

Due aggravanti verificate:
- **`PRAGMA synchronous` non è impostato.** `_get_connection` setta busy_timeout,
  journal_mode e foreign_keys ma **non** `synchronous`. In WAL il default resta
  `FULL` → **un `fsync` a ogni `commit`**. Con commit per-evento e centinaia di
  eventi/secondo sotto carico, è il costo dominante.
- **`journal_mode=WAL` ri-eseguito a ogni connect** è ridondante (è proprietà
  persistente del file), innocuo ma sintomo del fatto che la connessione non è
  pensata per essere riusata.

### 7.3 — Q1: cosa si perde (vettori di perdita verificati)

1. **`database is locked` sotto contesa (Path A).** SQLite WAL consente molti
   lettori ma **un solo scrittore**. Il Path A gira su ~40 thread del threadpool
   (vedi § 7.4): fino a 40 scrittori concorrenti + il worker async competono sul
   medesimo lock di scrittura. Oltre `busy_timeout=10s` →
   `OperationalError: database is locked` → catturato → **warning loggato, evento
   perso** (`sink.py:240-242`, e i `try/except` dei `_write_*_single`).
2. **Perdita a cascata via foreign key.** Con `foreign_keys=ON`, se la
   `upsert_request` viene persa/lockata, **ogni** orchestration_event e llm_call
   successivo per quel `request_id` fallisce il vincolo FK → swallowed → perso.
   La risposta all'utente viene comunque consegnata, ma **l'intero audit della
   richiesta può svanire**. Questo rompe la "audit completeness" su cui poggia
   l'export AI-Act art.12.
3. **Coda async piena → drop silenzioso.** `submit()` su `queue.Full` scarta
   l'item con un warning (`write_queue.py:44-50`), maxsize 100k.
4. **Kill non-graceful → coda async persa.** La coda è in memoria, senza WAL
   proprio. Su SIGKILL/OOM/crash, tutto ciò che è in coda sparisce; sopravvivono
   solo le scritture sincrone già committate.
5. **`flush()`/`shutdown()` a timeout.** `flush` ritorna lasciando task non
   completati oltre il timeout (`write_queue.py:59-65`); `shutdown` fa `join` con
   timeout 30s (`:68-71`). Il proxy ha **rimosso il flush per-request**
   (`proxy.py:521-529`) proprio perché sotto burst il writer andava in timeout: è
   una pezza sul *sintomo* (il timeout) che **peggiora la durabilità** (drain solo
   allo shutdown) senza toccare la causa (throughput di scrittura).

In sintesi Q1: **non c'è garanzia che tutto venga salvato.** Sotto contesa si
perde, e si perde in modo silenzioso e potenzialmente a blocchi interi (FK).

### 7.4 — Q2: il collo di bottiglia a 100 chiamate concorrenti

La catena ha **più punti di serializzazione sovrapposti**:

- **Threadpool del proxy.** `chat_completions` è `async def` e offloada il
  bloccante `orchestrator.process()` con `await run_in_threadpool(...)`
  (`proxy.py:579`). Il limiter AnyIO di default è ~40: con 100 richieste
  concorrenti, ~40 girano e ~60 restano in coda *prima ancora* di toccare il DB.
- **Singolo file SQLite, singolo writer WAL.** I ~40 thread del Path A + il worker
  async = fino a ~41 connessioni che tentano di scrivere sullo stesso file. WAL le
  serializza su un unico lock di scrittura.
- **Connessione per evento + commit per evento + fsync per commit** (§ 7.2): il
  costo fisso (connect + 3 PRAGMA + fsync) viene pagato *per ogni evento*, non per
  richiesta né per batch. Con decine di eventi/richiesta × decine di richieste
  attive, sono migliaia di `connect+fsync+close` al secondo su un solo writer.
- **Worker async unico.** L'altra metà degli eventi passa per un thread solo: a
  prescindere dalla contesa, è un tetto di throughput a thread singolo.

Risultato: **no, non regge senza colli di bottiglia.** All'aumentare della
concorrenza, le scritture sincrone bloccano i thread del proxy (gonfiando la
latenza di risposta, perché ogni scrittura contesa aspetta fino a 10s), e oltre la
soglia di throughput del writer si passa da "lento" a "perde dati" (punto 1-2 di
§ 7.3). I due regimi degradano insieme.

### 7.5 — Fix (in ordine di ritorno)

1. **Un solo path, tutto asincrono.** Eliminare il Path A sincrono: anche gli
   orchestration event e gli upsert devono passare per la coda. Nessuna scrittura
   DB sul thread di richiesta. Risolve di colpo il blocco del threadpool e la
   contesa multi-writer.
2. **Writer dedicato con connessione persistente + batching + coalescing.** Un
   unico thread (o processo) writer che tiene **una** connessione aperta, imposta
   `PRAGMA synchronous=NORMAL` (sicuro in WAL) **una volta**, e fa
   **micro-batch**: drena la coda a finestre (es. ogni 50–100 ms o N eventi),
   raggruppa per tipo e usa gli `executemany` già presenti
   (`insert_*_batch`) in **una transazione per finestra**. Si passa da migliaia di
   fsync/s a poche decine. `route_batch`/`write_batch` esistono già: vanno messi
   sul percorso hot, non solo nelle `persist_*_batch` esplicite.
3. **Ordine FK garantito nel batch.** Nel writer, all'interno della finestra,
   ordinare/raggruppare così che `runs`/`requests` siano inseriti prima dei figli;
   con `INSERT OR IGNORE` sui parent l'idempotenza è già lì
   (`default.py:65-67`). Elimina la perdita a cascata del § 7.3 punto 2.
4. **Durabilità su crash.** Per non perdere la coda in memoria su kill
   non-graceful, due opzioni: (a) WAL applicativo durabile davanti alla coda (append
   su file, replay all'avvio), oppure (b) writer **out-of-process** (un piccolo
   servizio/processo che possiede il DB; il proxy gli manda gli eventi). La (b)
   abilita anche il multi-worker del proxy (lo stato di scrittura non è più
   in-process).
5. **Backpressure esplicita invece di drop silenzioso.** Su coda quasi piena,
   decidere una policy dichiarata (blocca brevemente il produttore, o degrada a
   campionamento contato e **registra quanti eventi sono stati saltati**), non un
   `put_nowait` che scarta in silenzio. Per un sistema che vende auditabilità,
   "ho perso N eventi" deve essere un dato, non un warning a log.
6. **Test di carico mancante.** Non esiste un test che spari ~100 turni concorrenti
   e poi verifichi che il numero di righe persistite == numero di eventi emessi.
   È la prova di accettazione di tutto questo: **emessi == salvati**, sotto carico,
   con kill non-graceful nel mezzo.

> Nota: una singola istanza SQLite resta un writer-unico per design. Per il target
> "100 turni concorrenti sostenuti" il batching (fix 2) basta a stare comodi su un
> nodo; se il target cresce oltre, il writer out-of-process (fix 4b) o un backend
> server-based (Postgres) diventa la scelta giusta — ma è una decisione di
> prodotto, non un default da imporre qui.

---

## 8. Metodo e limiti di verifica

- Tutte le citazioni `file:riga` sono state aperte e lette in questa sessione sul
  codice a `0.6.1`, non sulla documentazione.
- **Non ho eseguito** la suite di test completa (`python -m pytest`) in questa
  analisi: le valutazioni sui test (§ 2.1) derivano dalla lettura dei file di test,
  non da un'esecuzione. Va confermato eseguendo la suite prima di qualsiasi merge
  dei fix.
- Le gravità sono mie, ancorate al threat model esplicitato (SDK = deployer
  fidato; proxy = input potenzialmente non fidato). Dove il deployment cambia il
  threat model, la gravità va rivalutata.
- I fix proposti sono direzioni, non patch validate: ognuno richiede test
  adversariali dedicati prima di considerarsi chiuso.
