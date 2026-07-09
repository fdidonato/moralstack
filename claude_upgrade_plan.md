# MoralStack — Piano di upgrade integrale (`main`, `0.6.1`)

> Documento **autonomo**: contiene l'intera analisi e il piano operativo, senza
> rimandi ad altri file. Nasce dal confronto avversariale di due review
> indipendenti dello stesso branch (una orientata alla persistenza e al prodotto,
> una orientata agli invarianti di sicurezza e alla documentazione) e dalla
> **ri-verifica diretta sul codice** di ogni punto in cui le due divergevano.
> Dove una delle due review aveva torto — **inclusa la mia** — il punto è corretto
> qui con l'evidenza `file:riga`.
>
> **Natura dell'analisi**: statica. Non è stata eseguita la suite
> (`python -m pytest`) né un load test reale. Le soglie quantitative (es. "~40
> thread", "fsync per commit") sono dedotte dai default delle librerie e dalla
> semantica SQLite, non misurate. Le catene di causa sono verificate pezzo per
> pezzo sul sorgente.

---

## 1. Introduzione

MoralStack è un *governance engine* per LLM: decide se un modello può rispondere,
e con quale forma (`NORMAL_COMPLETE`, `SAFE_COMPLETE`, `REFUSE`). La base è
tecnicamente seria — SDK pubblico, proxy OpenAI-compatibile, ledger semantico delle
decisioni, layer di compliance al contratto del developer (DCCL), risk estimator
strutturato e calibrato, osservabilità SQLite/JSONL con export per audit. Non è una
demo.

Ma non è ancora una base presentabile come **production-grade per workload non
fidati, multi-tenant o audit-grade**. Tre questioni lo impediscono, in ordine di
gravità:

1. **Sicurezza (P0).** L'invariante più importante del sistema — "i segnali hard
   non sono sovrascrivibili da un contratto del developer" — non è implementato come
   *precondizione globale non bypassabile*, ma come parte del routing standard. Il
   fast-path del DCCL consegna **prima** che il gate deterministico hard-signal
   possa girare.

2. **Audit.** La persistenza non garantisce che gli eventi emessi siano salvati
   sotto concorrenza o crash. Per un progetto che vende compliance e audit trail
   (AI-Act art.12), è un difetto strutturale, non operativo.

3. **Prodotto.** `govern(client)` **non governa il modello dell'utente**: lo
   sostituisce con un modello interno per la generazione finale. È una scelta
   deliberata e (in parte) documentata, ma README e docstring del proxy
   raccontano ancora la semantica vecchia, contraddittoria.

Il resto (multi-turn, embedder, latenza, wiring, benchmark, file enormi) è reale ma
secondario rispetto a questi tre.

---

## 2. Punti di forza rilevati (verificati)

Sono solidi e vanno **preservati** durante gli interventi: alcuni fix qui sotto
rischiano di romperli se fatti male.

1. **Ledger storage robusto e ben isolato.** `InMemoryLedgerStorage` ha LRU
   (default 1000) + TTL (default 3600 s) con expiry lazy in lettura e
   `popitem(last=False)` (`orchestration/ledger_storage.py:59-60,100-125`). La
   chiave del ledger è **esatta su `(contract_hash, posture, domain)`**
   (`orchestration/ledger.py:45-63`) — *non* su `conversation_id` (vedi correzione
   in § 4). Niente memory leak qui.

2. **Difese del ledger col bisturi.** Skip su posture `ESCALATED`, skip su
   `turn_index < 1`, secondary intent check (intent_clarity + request_type oltre la
   similarità) e — il punto che conta — **il contenuto non è mai cachato**, solo i
   metadata di governance; il testo è sempre rigenerato
   (`orchestration/ledger.py:245-285`).

3. **Risk estimator strutturato e calibrato.** Mini-estimator paralleli
   (`models/risk/estimator.py:850-854`, `ThreadPoolExecutor(max_workers=3)`),
   q-signal estesi fino a q17 con q17 che può forzare il denial
   (`models/risk/calibration.py`). Più difendibile di un singolo judge LLM
   monolitico.

4. **Policy di safe-complete disciplinata.** Limiti forti su hard violations e
   operational risk alto, SAFE_COMPLETE forzato quando l'actionability è alta,
   NORMAL_COMPLETE mantenuto nella zona grigia senza trigger
   (`orchestration/safe_complete_policy.py`). Non è solo un prompt.

5. **Pipeline fail-closed.** Su fallimento si rifiuta (governed refusal); il
   passthrough è deprecato/mappato a refusal. Enshrined come invariante
   (`.claude/rules/governed-delivery.md`).

6. **Separazione decisione/generazione e governed delivery.** Il testo consegnato è
   sempre prodotto dalla pipeline governata; il client wrappato non genera la
   risposta finale (`orchestration/delivery.py`, `sdk/wrapper.py:641-648`). È una
   scelta coerente con i P0 (anche se va comunicata meglio, § 7-P5).

7. **Tipizzazione e self-audit.** `mypy strict` sul package orchestration; ledger
   delle facts con citazioni riga-per-riga e sezione bug latenti. Disciplina rara.

8. **Copertura test ampia e behavior-locking.** Compliance fast-path, q17 hard
   signal, ledger, proxy OpenAI-compatibile, cache context isolation, observability,
   SDK bootstrap. Non elimina i bug architetturali ma rende realistico correggerli
   senza procedere alla cieca.

---

## 3. Problemi rilevati (lista consolidata, per gravità)

| ID | Gravità | Area | Sintesi | Evidenza chiave |
|---|---|---|---|---|
| **P1** | **Critica** | Sicurezza | Il fast-path DCCL (`MATCH`) consegna prima del gate hard-signal deterministico | `controller.py:2086-2211` vs `:2317-2318`; auto-trace `:1496-1497`; `dccl.py:276` ignora il rischio |
| **P2** | **Alta/Critica** (audit) | Persistenza | Split sync/async, non atomica, non crash-safe, collo di bottiglia SQLite sotto concorrenza | `persistence/sink.py:5-7,238`; `sqlite_sink.py:497-504,1546-1560`; `observability/write_queue.py:40-48` |
| **P3** | **Alta** | Proxy/multi-tenant | Correlation store non bounded e non tenant-aware (collisione history → fusione conversazioni; OOM) | `server/conversation_correlation.py:95-129` |
| **P4** | **Alta** | Multi-turn safety | Il contesto perde il setup safety-relevant: finestra `[-3:]` + `[:200]` + troncamento per budget | `estimator.py:231-235`; `conversation_context.py:120-150` |
| **P5** | **Alta** (fiducia/prodotto) | API/Docs | `govern()` governed-only ma README/proxy descrivono ancora il forwarding; version drift; `final_revalidation.py` morto | `README.md:301-309` vs `:350-372`; `proxy.py:5-8,513,552-553`; `final_revalidation.py` no call site |
| **P6** | **Minore** | Wiring | `cached_from_decision_id` mai assegnato; `observability_mode="off"` inerte; docstring `q1-q13` stale | `headers.py:37`; `config.py:23,64-77`; `estimator.py:716` |
| **P7** | **Media** | Latenza/Coupling | Embedder OpenAI-only nel critical path + doppia embedding lookup/store | `embedder.py:100-107`; `ledger.py:259,342` |
| **P8** | **Media** (alta per adozione) | Performance | Nessun triage deterministico a monte: anche il banale paga 3 LLM (+ DCCL LLM sul proxy) | `estimator.py:850-854` |
| **P9** | **Media** | Credibilità | Benchmark/README non allineati (compliance 98.8 vs 97.56; leakage "0" vs artefatto =1) | `README.md:127-150` vs `benchmark_outputs/*.json` |
| **P10** | **Minore** | Manutenibilità | God files: `controller.py` (2671), `deliberation_runner.py` (3286) | conteggio LOC |
| **P11** | **Media** (audit/costo) | Token accounting | Conteggio token incompleto e non aggregabile: embedder non tracciato, fallback stimato non etichettato, JSON non sommabile, nessun totale per modulo/per domanda | `policy.py:243-245`; `base.py:120-132`; `embedder.py:169`; `read_store` (nessuna aggregazione) |

---

## 4. Correzioni e divergenze tra le due review (onestà metodologica)

Tre punti dove la verifica sul codice ha corretto una delle due review:

1. **Il ledger NON è keyed da `conversation_id`** (correzione di un'imprecisione
   *mia*). La mia analisi precedente affermava che la collisione della correlation
   store contaminava anche il ledger. **Falso**: `LedgerKey =
   (contract_hash, posture, domain)` (`ledger.py:45-63`). La collisione contamina
   **lock per-conversazione e session store** (`proxy.py:271-272,314,370-371`), non
   la chiave del ledger. P3 resta grave, ma va formulato con esattezza (§ 7-P3).

2. **La semantica `govern()` non è "documentata pulita"** (correzione di
   un'altra mia conclusione). Il docstring SDK è corretto (`wrapper.py:641-648`), ma
   README (`:301-309`) e proxy (`:5-8,552-553`) descrivono ancora il forwarding
   upstream → la documentazione pubblica è **auto-contraddittoria**. P5 lo tratta.

3. **Il fix "ingenuo" di P1 proposto da entrambe non compila/non funziona**
   (correzione tecnica valida per tutte e due). Entrambe le review suggerivano di
   chiamare `is_hard_signal_refuse(decision_prepolicy, …)` prima del fast-path. Ma
   `is_hard_signal_refuse` ha un gate `if decision.final_action != "REFUSE": return
   False` (`path_router.py:51-52`), e al punto del fast-path DCCL **non esiste
   ancora una decision** (`decide_action` gira a `:2289`, dopo). Serve un predicato
   **decision-independent** che legga il rischio direttamente. È il cuore del fix
   P1 (§ 7-P1).

Convergenze ad alta confidenza (due derivazioni indipendenti, stesse righe): P1, P3,
P4, P7, P8, P6, P10. Su P2 (persistenza) la profondità l'ha aggiunta una review e
l'altra l'ha adottata; su P5/P9 (docs/benchmark) viceversa. Il fatto che due letture
separate convergano su P1 con lo stesso fix è il segnale più forte: è il primo da
chiudere.

---

## 5. Threat model esplicito (condiziona le gravità)

- **SDK locale**: il deployer è la parte fidata (è il *suo* contratto). Un contratto
  malevolo ≈ il deployer danneggia il proprio prodotto → P1 ha gravità *bassa* qui.
- **Proxy esposto / multi-tenant / benchmark**: il "contratto" = ultimo messaggio
  `system`/`developer` del request body (`conversation_context.py:181-202`), passato
  alla request governata (`proxy.py:299-306`). È input potenzialmente **non fidato**
  → P1 e P3 hanno gravità *alta*.
- Se il proxy gira solo dietro un'app fidata che setta lei il system prompt, P1
  scende anche lì. Le gravità "alte" assumono il caso peggiore (proxy esposto).

---

## 6. Criteri di priorità

Ordine guidato da **rischio non mitigabile a valle** prima, **ritorno/effort** poi:

1. Prima ciò che, se sbagliato, produce un danno *non recuperabile* (consegna di
   contenuto dannoso, perdita di audit trail): **P1, P2**.
2. Poi ciò che blocca un deployment reale dichiarato (multi-tenant, multi-turn):
   **P3, P4**.
3. Poi ciò che erode la fiducia e la DX a costo basso (documentazione, wiring):
   **P5, P6**.
4. Poi le leve di costo/adozione che non toccano la sicurezza: **P7, P8**.
5. Infine credibilità pubblica e manutenibilità: **P9, P10**.

**Regola dura, su cui entrambe le review concordano**: *non aggiungere nuovi
fast-path finché P1 non è chiuso*; *non dichiarare audit completeness finché P2 non
è chiuso*.

---

## 7. Piano di intervento (step-by-step, per priorità)

Per ogni intervento: **obiettivo**, **file da toccare**, **passi concreti con
codice**, **test di accettazione**, **invarianti toccati / rischi**.

---

### P1 — Hard-signal floor non bypassabile prima di OGNI fast-path  ·  Gravità: Critica

**Obiettivo.** Rendere l'invariante "hard-signal supremacy" una *precondizione*: nessuna
scorciatoia (DCCL `MATCH`, ledger cache-hit, conversational fast-path) può consegnare
se il `risk_estimation` porta evidenza P0, indipendentemente dal contratto.

**File da toccare.**
- `moralstack/orchestration/path_router.py` (nuovo predicato).
- `moralstack/orchestration/controller.py` (gate prima del fast-path DCCL e del cache-hit).
- `tests/test_compliance_fast_path.py`, `tests/test_ledger_fast_path_gate_rejected_e2e.py` (nuovi casi).

**Passo 1 — Predicato decision-independent.** In `path_router.py`, accanto a
`_HARD_SEMANTIC_SIGNALS` (già definito a `:17-28`), aggiungere:

```python
from moralstack.models.risk.schema import RiskCategory          # se non già importato
from moralstack.models.risk.action import (                     # coerce + enum
    RiskPolicyAction, coerce_risk_policy_action,
)

def risk_has_hard_signal(
    risk_estimation: RiskEstimationProtocol,
    op_risk: OperationalRisk,
) -> bool:
    """
    Decision-independent P0 floor. True when the risk estimation alone carries
    non-overridable hard evidence, regardless of any contract MATCH or cache hit.
    Designed to invalidate fast-path shortcuts BEFORE delivery.

    NOTE: do NOT use is_hard_signal_refuse() here — it gates on
    decision.final_action == "REFUSE", which does not exist yet at fast-path time.
    """
    if op_risk == OperationalRisk.HIGH:
        return True
    if getattr(risk_estimation, "risk_category", None) == RiskCategory.CLEARLY_HARMFUL:
        return True
    if coerce_risk_policy_action(getattr(risk_estimation, "risk_policy_action", None)) == RiskPolicyAction.DENY:
        return True
    if getattr(risk_estimation, "q17_minor_exploitation", False):
        return True
    signals = {str(s).strip() for s in (getattr(risk_estimation, "semantic_signals", None) or []) if str(s).strip()}
    return bool(signals & _HARD_SEMANTIC_SIGNALS)
```

I campi usati esistono tutti su `RiskEstimation` (`models/risk/schema.py:44-48,83`):
`risk_category`, `operational_risk`, `semantic_signals` (valori tipo
`"Q17:minor_exploitation"`), `risk_policy_action`, `q17_minor_exploitation`.

**Passo 2 — Gate sul fast-path DCCL.** In `controller.py:process()`, subito dopo
`cv = call_ctx.compliance_verdict` (`:2085`) e **prima** di `if cv is not None and
cv.decision == ComplianceDecision.MATCH:` (`:2086`):

```python
from moralstack.orchestration.path_router import risk_has_hard_signal

op_risk_pre = getattr(risk_estimation, "operational_risk", OperationalRisk.NONE)
if (
    cv is not None
    and cv.decision == ComplianceDecision.MATCH
    and risk_has_hard_signal(risk_estimation, op_risk_pre)
):
    # P0 floor: hard signals are non-overridable by a developer contract.
    self._events.emit_orchestration_event(
        request_id=request.request_id or "",
        stage="compliance_layer",
        component="dccl",
        event_type=COMPLIANCE_MATCH_DOWNGRADED,
        decision="downgraded",
        status="ok",
        payload={"reason": "hard_signal_floor", "op_risk": str(op_risk_pre)},
    )
    if spec_handle is not None:
        try:
            spec_handle.abandon("hard_signal_floor", "DELIBERATIVE_PATH")
        except Exception:
            _LOG.debug("spec_handle.abandon failed on hard_signal_floor", exc_info=True)
    cv = None  # invalida il MATCH → la pipeline standard procede e rifiuterà
```

Con `cv = None` il blocco `:2086` non scatta; il flusso prosegue a `decide_action`
(`:2289`) → `get_route` → REFUSE/SAFE_COMPLETE. **Costo**: un `if` su dati già in
memoria. **Qualità**: invariata (quelle categorie non sono autorizzabili per design).

**Passo 3 — Stesso floor sul cache-hit del ledger.** Il ledger lookup/patch è a
`controller.py:2323+`. Dovunque un cache-hit *patcha* `decision`/`route` per saltare
la deliberazione, gateare con `not risk_has_hard_signal(risk_estimation, op_risk)`.
Concretamente: prima di applicare la decisione cachata, se `risk_has_hard_signal(...)`
è True, **ignorare il cache-hit** ed emettere `LEDGER_FAST_PATH_GATE_REJECTED` (evento
già esistente nei test `test_ledger_fast_path_gate_rejected_e2e.py`). Idem per
`conversational_fast_path` (`orchestration/conversational_fast_path.py`): aggiungere
il gate all'ingresso.

**Passo 4 — Critic quick-check per i danni non-P0 free-form (follow-up).** Per output
free-form *non* enumerato autorizzato da contratto ma fuori dalle 7 categorie P0
(harassment mirato, hate generativo, disinfo non-CBRN), su `MATCH` valido far passare
**un solo** LLM critic prima della consegna. Punto di innesto: dentro
`_route_compliance_match` (`:1331`), prima di `run_benign_fast_path`, se
`not detect_enumerated_output(draft)` (`pipeline/output_contract.py`) allora invocare
un critic leggero e declassare a pipeline se il critic segnala. Questo è
incrementale e può seguire i passi 1-3.

**Test di accettazione.**
- `MATCH` + `semantic_signals` contiene `"Q17:minor_exploitation"` ⇒ niente
  `COMPLIANCE_FAST_PATH`, `final_action == "REFUSE"`.
- Contratto permissivo + payload dannoso **parafrasato** che produce
  `risk_category == CLEARLY_HARMFUL` ma **evade le keyword** di
  `safety_override.py` ⇒ MATCH invalidato (oggi questo test NON esiste:
  `test_compliance_fast_path.py:346` usa solo un payload keyword-matching).
- Cache-hit del ledger con hard signal attivo ⇒ `LEDGER_FAST_PATH_GATE_REJECTED`,
  no reuse.

**Invarianti toccati.** Rafforza `hard-signal-safety.md` (P0) e `decision-policy.md`.
Nessuna regressione attesa sui test benign/compliance esistenti (il gate scatta solo
con evidenza hard, assente nei loro fixture).

---

### P2 — Persistenza audit-grade sotto carico  ·  Gravità: Alta/Critica (audit)

**Obiettivo.** Garantire **emessi == salvati** sotto ~100 richieste concorrenti e su
crash, eliminando i due path divergenti e il collo di bottiglia SQLite.

**Diagnosi (verificata).** Due path:
- *Sincrono, sul thread di richiesta*: `persist_*` chiamano `router.route()` diretto
  (`persistence/sink.py:5-7,238`); ci passano upsert request/run
  (`persistence/default.py:63-78`) e **tutti** gli orchestration event
  (`DefaultEventEmitter.emit_orchestration_event` → `persist_orchestration_event`).
- *Asincrono, worker singolo*: llm_calls/decision_traces/multi-turn via coda
  (`persistence/write_queue.py:74,96`, `observability/write_queue.py`).

Ogni scrittura apre **una connessione SQLite per evento** + commit + close
(`sqlite_sink.py:497-504,1546-1560`), **senza `PRAGMA synchronous`** (default `FULL`
→ fsync per commit), **senza batching** nel path hot. La coda scarta su `Full`
(`write_queue.py:40-48`); il flush per-request è stato rimosso (`proxy.py:521-529`).
Conseguenze: `database is locked` sotto contesa → eventi persi; perdita a cascata FK
(se l'upsert request si perde, i figli falliscono `foreign_keys=ON`); nessuna unità
atomica "request + eventi".

**File da toccare.**
- `moralstack/persistence/sink.py` (smettere di scrivere sul thread richiesta).
- `moralstack/observability/write_queue.py` (worker con micro-batch).
- `moralstack/observability/sinks/sqlite_sink.py` (connessione persistente + `synchronous`).
- `moralstack/observability/service.py` (API batch).
- nuovo `tests/test_persistence_load.py`.

**Passo 1 — Un solo path: niente DB write sul thread di richiesta.** In
`persistence/sink.py`, sostituire i `router.route(envelope)` sincroni con un enqueue
asincrono. Esempio per `persist_orchestration_event` (`:238`):

```python
# era: router.route(envelope); return None
from moralstack.observability.service import get_obs
get_obs().emit(envelope)   # fire-and-forget, FIFO, context snapshot
return None
```

Idem per `persist_llm_call`, `persist_decision_trace`, `persist_debug_event`. Anche
gli upsert (`persistence/default.py` → `create_run`/`upsert_request`) vanno convertiti
in eventi `EVENT_RUN_STARTED`/`EVENT_REQUEST_UPSERTED` accodati, **non** scritture
dirette. L'ordine FK è preservato perché lo stesso thread accoda upsert prima degli
eventi figli e la coda è FIFO (vedi Passo 3 per la garanzia nel batch).

**Passo 2 — Connessione persistente + `synchronous=NORMAL`.** In `sqlite_sink.py`,
fare in modo che il **worker** possieda una connessione di lunga durata invece di
aprirne una per evento. Aggiungere a `_get_connection` (`:497-504`) il PRAGMA mancante
e usarla una sola volta nel writer:

```python
def _get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # <-- aggiunto: 1 fsync per checkpoint, non per commit
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn
```

`SqliteEventSink` deve tenere `self._conn` aperta (creata lazy nel thread worker) e
riusarla in `_dispatch_batch`; chiuderla solo a `close()`/shutdown. Questo elimina il
churn connect/close per evento.

**Passo 3 — Micro-batching nel worker con ordine FK.** Riscrivere
`ObservabilityWriteQueue._worker` perché dreni a finestre e usi `route_batch`:

```python
def _worker(self) -> None:
    while True:
        first = self._queue.get()
        if first is _SENTINEL:
            self._queue.task_done(); break
        window = [first]
        deadline = time.monotonic() + 0.05   # 50 ms
        while len(window) < 500 and time.monotonic() < deadline:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _SENTINEL:
                self._queue.put(_SENTINEL); break
            window.append(item)
        try:
            _route_window(window)   # ordina: run/request PRIMA dei figli, poi route_batch per tipo
        except Exception as e:
            logger.warning("observability: batch write failed: %s", e)
        finally:
            for _ in window:
                self._queue.task_done()
```

`_route_window` raggruppa per `event_type`, emette prima `EVENT_RUN_STARTED` /
`EVENT_REQUEST_UPSERTED`, poi i figli, e chiama `SqliteEventSink.write_batch` (già
presente, `sqlite_sink.py:1282-1299`) **in una sola transazione per finestra** sulla
connessione persistente. Si passa da migliaia di fsync/s a poche decine.

**Passo 4 — Backpressure contata invece di drop silenzioso.** In `submit()`
(`write_queue.py:40-48`), sostituire `put_nowait` + drop con un `put(timeout=…)`
breve; su timeout, **incrementare un contatore persistito** (`events_dropped`) e
loggare a `ERROR`, non `WARNING`. Per un sistema di audit "ho perso N eventi" deve
essere un dato interrogabile, non una riga di log.

**Passo 5 — Durabilità su crash (scegliere una).**
- (a) *WAL applicativo*: append degli envelope su file prima dell'enqueue, con replay
  all'avvio e troncamento dopo il commit della finestra. Più semplice, single-node.
- (b) *Writer out-of-process*: un piccolo processo che possiede il DB; il proxy gli
  manda gli eventi (socket/named pipe). Abilita anche il multi-worker del proxy (lo
  stato di scrittura non è più in-process) → si lega a P3.

**Passo 6 — Stress + crash test (la prova di accettazione).** Nuovo
`tests/test_persistence_load.py`:
- 100 thread, ciascuno emette K eventi con `request_id` distinto; `obs.flush()`;
  assert `righe_persistite == eventi_emessi` per ogni tabella, **zero drop non
  contati**.
- Scenario kill: emetti, simula crash prima dello shutdown drain, riavvia, verifica
  replay (strada 5a) **o** che `events_dropped` contabilizzi esattamente la perdita.

**Test di accettazione.** I due test sopra verdi; nessuna scrittura DB residua sul
thread di richiesta (grep di `router.route(` fuori dal worker = 0).

**Invarianti toccati.** `observability.md` (telemetria best-effort, mai rompe la
request): mantenuto — gli enqueue restano in try/except. Attenzione a non introdurre
blocchi che propaghino eccezioni al chiamante.

---

### P3 — Correlation store bounded e tenant-aware  ·  Gravità: Alta

**Obiettivo.** Eliminare la crescita illimitata e la collisione cross-tenant della
correlation store del proxy.

**Precisazione (corretta in § 4).** Il `conversation_id` risolto chiavea **lock
per-conversazione e session store** (`proxy.py:271-272,314,370-371`), **non** il
ledger (`LedgerKey = contract_hash/posture/domain`). Quindi due tenant con history
identica condividono lock e stato di sessione, non la cache del ledger. Resta grave
(isolamento/privacy), ma va detto con esattezza.

**File da toccare.**
- `moralstack/server/conversation_correlation.py`.
- `moralstack/server/proxy.py` (lettura header + principal).
- `tests/test_server_proxy.py` / nuovo `tests/test_conversation_correlation.py`.

**Passo 1 — Bound su memoria.** Sostituire il `dict` nudo (`:95-98`) con una
`cachetools.TTLCache(maxsize=…, ttl=…)` (o `OrderedDict` + `popitem(last=False)` +
timestamp). Esempio:

```python
from cachetools import TTLCache
class ConversationCorrelationStore:
    def __init__(self, maxsize: int = 50_000, ttl: float = 3600.0) -> None:
        self._lock = threading.RLock()
        self._history_to_conversation: TTLCache[str, str] = TTLCache(maxsize=maxsize, ttl=ttl)
```

`resolve`/`observe_completed_turn` restano uguali (TTLCache è dict-like); l'eviction
è automatica.

**Passo 2 — Salt per principal.** La correlazione per-hash deve essere un fallback,
non la via primaria. In `proxy.py`, leggere prima `X-Moralstack-Conversation-Id`
(già supportato come header); quando si cade sul fallback hash, salare l'hash con il
principal autenticato (API key/tenant id ricavato da `Authorization`/header). In
`canonical_history_hash` (`:61-69`) accettare un `salt: str = ""` e includerlo nel
blob:

```python
def canonical_history_hash(messages, *, salt: str = "") -> str:
    normalized = [_canonical_message_record(m) for m in messages]
    blob = json.dumps([salt, normalized], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

`resolve(messages, *, principal: str = "")` passa `salt=principal`. Due tenant con
history identica non collidono più.

**Test di accettazione.**
- Sotto carico sintetico la store non supera `maxsize` (test di eviction).
- Due richieste con history identica ma `principal` diversi ⇒ `conversation_id`
  distinti.

**Invarianti toccati.** Nessuno P0. Verificare che la continuità conversazionale
COMPL-AI (history-based) regga con il salt vuoto quando non c'è principal (retro-
compatibilità).

---

### P4 — Multi-turn: safety-summary monotono + posture sticky  ·  Gravità: Alta

**Obiettivo.** Impedire l'escalation graduale: il risk estimator non deve mai vedere
*meno segnale di sicurezza* di quanto la conversazione ne contenga, anche se vede
meno testo; e la conversazione "in salita" deve perdere l'eleggibilità a fast-path/
cache prima di scattare su un singolo turno.

**Diagnosi (verificata).** Il risk estimator costruisce il contesto da
`conversation_history[-3:]` con `content[:200]` (`estimator.py:231-235`); il
transcript role-serialized tiene i recenti e scarta i vecchi
(`conversation_context.py:120-150`); `conversation_safety_summary` esiste
(`conversation_state.py:87`) ma non risulta reiniettato nel risk estimator.

**File da toccare.**
- `moralstack/orchestration/conversation_state.py` (popolamento safety-summary, posture sticky).
- `moralstack/orchestration/controller.py` (`:562-579` update stato; iniezione nel risk path).
- `moralstack/models/risk/estimator.py` (`:231-260` accettare e usare il safety-summary).
- `tests/` nuovo `test_multiturn_gradual_escalation.py`.

**Passo 1 — Safety-summary monotono dei turni droppati.** In
`conversation_state.py`, quando un turno esce dalla finestra `[-3:]`/budget,
aggiornare `conversation_safety_summary` con i suoi segnali safety-relevant (q-signal
hard visti, posture, request_type), **senza mai cancellarli** (unione monotona). Il
controller (`:562-579`) già aggiorna lo stato: estendere lì per calcolare il summary.

**Passo 2 — Reiniezione nel risk estimator.** In `estimator.py` (`_risk_context_messages`,
`:231-235`), prependere al contesto un blocco sintetico `SAFETY-SUMMARY (prior
turns): …` derivato da `conversation_safety_summary`. Così la finestra `[-3:]` non
perde il setup: il testo resta corto ma il *segnale* è preservato.

**Passo 3 — Posture sticky/monotona.** In `conversation_state.py:should_full_refresh`
(e nel calcolo posture in `controller.py:_compute_governance_posture`), far salire la
posture su un *trend* (es. ≥2 `SAFE_COMPLETE` negli ultimi N, o derivata di rischio
crescente) e renderla non-decrescente entro la conversazione. Quando la posture è
`ELEVATED`/`ESCALATED`, **disabilitare fast-path DCCL, cache-hit del ledger e
conversational fast-path** (si lega al gate di P1 Passo 3).

**Test di accettazione.**
- Escalation a N turni, ognuno benigno singolarmente ma trend crescente ⇒ al turno
  di payload il sistema entra in deliberazione (no fast-path, no cache).
- Il risk estimator riceve un `SAFETY-SUMMARY` non vuoto quando i turni vengono
  troncati.

**Invarianti toccati.** Rafforza `hard-signal-safety.md` in multi-turn. Attenzione a
non rompere il test di byte-equality single-turn (`prompt-transparency.md`): il
safety-summary va iniettato **solo** quando esiste `conversation_history`, mai nel
path single-turn.

---

### P5 — `govern()`: posizionamento + riconciliazione docs + codice morto  ·  Gravità: Alta (fiducia)

**Obiettivo.** Eliminare la contraddizione tra ciò che il codice fa (governed
delivery: il client utente non genera) e ciò che la documentazione pubblica promette;
e decidere il destino di `final_revalidation.py`.

**Diagnosi (verificata).** README contiene due descrizioni opposte: tabella
forwarding (`:301-309`) vs nota governed-delivery (`:350-372`). Proxy docstring ancora
forwarding (`:5-8,552-553`); `version="0.5.0"` (`:513`) con package `0.6.1`.
`final_revalidation.py` definisce `revalidate_final_output` & co. ma **nessun call
site live** (solo la costante `DEFAULT_POST_REVALIDATION_REFUSAL` è importata).

**File da toccare.**
- `README.md`, `moralstack/server/proxy.py` (docstring + version).
- `moralstack/orchestration/final_revalidation.py` (collegare o rimuovere).

**Passo 1 — Documentazione: una sola semantica.** Rimuovere dalla README la tabella
di routing che dice "passes unchanged to your OpenAI client" / "your OpenAI client is
called" (`:307-309`). Sostituirla con la verità: per ogni `final_action` il testo è
generato dal policy model interno (`GovernanceConfig.model`/`OPENAI_MODEL`); `model=`
nella request è solo alias. Allineare i docstring del proxy (`:5-8,552-553`) e
correggere `version="0.5.0"` → leggere la versione dal package
(`importlib.metadata.version("moralstack")`), non hardcodarla.

**Passo 2 — Decisione di prodotto (richiede scelta dell'owner).**
- **Strada A — onestà di posizionamento.** Non venderlo come wrapper generativo;
  chiamarlo "governed generation / proxy con policy model interno". Esporre
  `generation="internal"` come unico modo. Nessun cambio di comportamento.
- **Strada B — `generation="upstream_then_verify"`.** Per
  `NORMAL_COMPLETE`/`SAFE_COMPLETE`, generare col client dell'utente (system steering
  governato) e poi **rivalidare** l'output. Questo **rompe** l'invariante
  `governed-delivery.md` → va opt-in esplicito, con design/test/docs dedicati, mai
  silenzioso.

**Passo 3 — `final_revalidation.py`.** È esattamente l'infrastruttura della Strada B.
Due esiti coerenti:
- Se si sceglie **Strada B**, collegare `revalidate_final_output` nel path di delivery
  dietro il flag `generation="upstream_then_verify"`.
- Se si sceglie **Strada A**, **rimuovere** la logica non collegata (tenere solo le
  costanti usate) per non simulare garanzie non attive. Codice morto in un sistema
  safety-relevant illude il lettore.

**Test di accettazione.**
- Nessuna stringa "forward" / "passes unchanged" residua in README/proxy (grep = 0).
- `version` del proxy == versione del package (test).
- Se Strada B: test che il modello/credenziali del client passato a `govern()` sono
  onorati su `NORMAL_COMPLETE` con revalidation attiva. Se Strada A: nessun simbolo
  di `final_revalidation` non importato resta nel package (o è rimosso).

**Invarianti toccati.** Strada A: nessuno. Strada B: modifica `governed-delivery.md`
→ va aggiornata la regola e i test di byte-equality.

---

### P6 — Wiring minori e docstring  ·  Gravità: Minore (effort molto basso)

**Obiettivo.** Chiudere tre incoerenze che erodono la fiducia in un sistema che vende
auditabilità.

**File e fix.**
1. **`cached_from_decision_id` mai assegnato.** Letto solo a `server/headers.py:37`;
   nessuna assegnazione nel package → `X-Moralstack-Cached-From` non esce mai. Fix:
   nel punto dove un cache-hit del ledger produce la decisione
   (`controller.py:2323+`), settare l'attributo sul metadata/result (es.
   `result.metadata.cached_from_decision_id = entry.decision_id`). Aggiungere test
   che l'header esce su cache-hit.
2. **`observability_mode="off"` inerte.** Campo a `sdk/config.py:58` con default
   `"off"`, ma `ObservabilityMode = Literal["db_only","dual","file_only"]`
   (`observability/config.py:23`) non lo conosce. Fix: **o** aggiungere `"off"` al
   Literal + un `NullSink` no-op selezionato in `router.route` quando mode=="off",
   **o** rimuovere il campo dall'SDK con deprecation. Decidere; non lasciarlo
   inerte. Aggiornare `tests/test_sdk_config.py` perché verifichi il *comportamento*,
   non solo che la dataclass accetti il valore.
3. **Docstring `q1-q13` stale.** `models/risk/estimator.py:716` → correggere in
   `q1-q17` (q17 = `minor_exploitation`, vedi `:603` e `calibration.py`).

**Test di accettazione.** Header `cached_from` emesso su cache-hit; `observability_mode`
o wirato (test che "off" disattiva le scritture) o rimosso; docstring allineato.

---

### P7 — Embedder locale di default + no doppia embedding  ·  Gravità: Media

**Obiettivo.** Togliere l'hard-coupling su OpenAI dal critical path del ledger e il
doppio lavoro lookup→store.

**Diagnosi (verificata).** `OpenAIEmbedder` è l'unica impl concreta
(`embedder.py:100-107`), richiede `OPENAI_API_KEY` (`:133-140`); il ledger embeddizza
in lookup (`ledger.py:259`) e di nuovo in store (`ledger.py:342`).

**File da toccare.**
- `moralstack/orchestration/embedder.py` (nuova impl locale).
- `moralstack/sdk/bootstrap.py` (`:65-67,89-95`, default).
- `moralstack/orchestration/ledger.py` (memoizzazione).

**Passo 1 — Default locale dietro `EmbedderProtocol`.** Aggiungere `LocalEmbedder`
basato su `fastembed` (ONNX/CPU) o `model2vec` (static embeddings). Renderlo il
default del bootstrap; `OpenAIEmbedder` resta opzione esplicita. Per soglia 0.92 un
buon embedding locale basta.

**Passo 2 — Memoizzazione del prompt corrente.** In `ledger.py`, calcolare
l'embedding una volta e passarlo da lookup a store. Es. il metodo che fa lookup
ritorna anche l'embedding usato; `store` lo accetta come parametro opzionale invece di
ricalcolarlo a `:342`.

**Test di accettazione.** L'embedder di default non effettua chiamate di rete (test
offline); lo stesso prompt non viene embeddato due volte in un ciclo miss→store
(spy sul metodo `embed`, call_count == 1).

**Invarianti toccati.** Nessuno; verificare che l'isolamento cache per fingerprint
regga col nuovo embedder (`tests/test_cache_context_isolation.py`).

---

### claude — Cascade fast-path (triage deterministico)  ·  Gravità: Media (alta per adozione)

**Obiettivo.** Abbattere la latenza mediana senza toccare il worst-case di sicurezza,
inserendo un triage *davanti* alla pipeline (non al posto).

**Diagnosi (verificata).** Anche il banale paga 3 LLM (`estimator.py:850-854`) + draft
speculativo + (sul proxy con system prompt) il judge LLM del DCCL.

**Design (tre tier).**
- **Tier 0** — deterministico, sub-ms, zero rete: euristiche/regex + modellino locale
  (static embeddings + classificatore lineare). Instrada l'ovviamente-benigno →
  generazione e l'ovviamente-dannoso → REFUSE; **su tutto il resto si astiene**.
- **Tier 1** — una sola chiamata LLM economica (gpt-4o-mini) sulla zona grigia: il
  risk estimator collassato a una chiamata.
- **Tier 2** — la pipeline deliberativa attuale, solo per gli ambigui/dual-use veri.

**Invariante non negoziabile.** Asimmetria: early-exit benigno solo con confidenza
*calibrata* alta e posture normale; qualunque incertezza → defer; early-exit refuse
può essere aggressivo (sbaglia verso over-refusal). Marker jailbreak/encoding strani
→ defer forzato. Si gatea sul floor di P1 e sulla posture di P4.

**Passi.** (1) Distillare il Tier 0 dalle decisioni della pipeline costosa (dataset:
benchmark + logica q-signal in `calibration.py`). (2) Calibrazione con temperature
scaling/conformal prediction per falsi negativi ≤ ε sull'uscita benigna. (3)
Innesto: nuovo modulo `orchestration/cascade.py` chiamato all'inizio di
`controller.process()`, prima del risk estimator, con early-exit solo verso le
direzioni sicure. (4) Monitoraggio drift: campionare una frazione delle uscite
benigne, rieseguirle offline nella pipeline completa, misurare il disaccordo,
ri-calibrare.

**Test di accettazione.** Latenza mediana ridotta su un mix realistico; worst-case di
sicurezza invariato (tutti i casi hard/ambigui ancora deliberati); tasso di errore
dell'early-exit benigno monitorato e sotto soglia.

**Invarianti toccati.** Rischioso: tocca il routing. Va dopo P1 (il floor) e P4 (la
posture), mai prima.

---

### P9 — Benchmark canonico riproducibile  ·  Gravità: Media (credibilità)

**Obiettivo.** Un solo artefatto benchmark per release, da cui la README importa i
numeri — niente tre verità nella stessa repo.

**Diagnosi (verificata).** README dichiara "98.8% compliance" e "leakage 0 (0%)"
(`README.md:127-150`); gli artefatti mostrano compliance `0.9881` (run vecchia) vs
`0.9756` (due run recenti) e un detail con `moralstack_leakage_count: 1`
(`benchmark_outputs/benchmark_d86681a3-2420-4fc7-…json`).

**Passi.** (1) Definire un formato canonico: `commit`, `dataset_hash`, `judge_model`,
`config`, `raw_logs`, `summary` generato automaticamente. (2) Script di riproduzione
versionato. (3) README che **importa** i numeri dall'artefatto canonico (es. via un
blocco generato), non scritti a mano. (4) Rimuovere/archiviare gli artefatti
divergenti non canonici.

**Test di accettazione.** I numeri in README coincidono byte-per-byte con il summary
canonico; nessun headline contraddetto da un artefatto presente nella repo.

---

### P10 — Split dei god files  ·  Gravità: Minore (dopo gli invarianti)

**Obiettivo.** Ridurre la barriera d'ingresso e il rischio di regressione su
`controller.py` (2671 LOC) e `deliberation_runner.py` (3286 LOC). **Non** prima di
P1-P4: un refactor su file safety-relevant prima di aver chiuso gli invarianti
aumenta il rumore senza ridurre il rischio.

**Split consigliato (per responsabilità).**
- `compliance_routing.py`: DCCL gating, invalidation P0, critic quick-check.
- `risk_context.py`: windowing, transcript, safety-summary (si lega a P4).
- `fast_paths.py`: ledger, conversational, DCCL, precondizioni cascade (si lega a P1/P8).
- `delivery_controller.py`: `final_action` → governed text.
- `observability_writer.py`: bridge evento → persistenza (si lega a P2).

**Metodo.** Estrazioni a iso-comportamento, una per PR, con la suite verde a ogni
passo. Nessun cambio di logica nello stesso commit dello spostamento.

**Test di accettazione.** Suite invariata verde; nessun import circolare; diff di
puro spostamento (rivedibile riga-per-riga).

---

### P11 — Token accounting completo, preciso e aggregabile  ·  Gravità: Media (audit/costo)

**Obiettivo.** Poter calcolare, alla fine di ogni domanda/risposta, **quanti token
input e output** sono stati scambiati **per ogni modulo/uso LLM** e il **totale per
richiesta**, senza perdere informazione e senza stime silenziose.

**Diagnosi (verificata).**
- I token sono catturati al livello policy da `response.usage`
  (`models/policy.py:239-249`) e impacchettati in `GenerationResult.token_usage_json`
  (`models/base.py:120-132`) come stringa JSON `{prompt, completion, total}`.
- Persistiti per-chiamata in `llm_calls`, che ha `module`/`phase`/`token_usage_json`
  (`sqlite_sink.py:554-559`). Deliberation/critic/simulator/hindsight/risk/DCCL
  passano il token usage (call site verificati in `deliberation_runner.py`,
  `estimator.py`, `dccl.py:536`).
- **Buchi**: (a) fallback **stimato** 70/30 non etichettato (`policy.py:243-245`);
  (b) **embedder non tracciato** — `embeddings.create` scarta `response.usage`
  (`embedder.py:169`) e non registra `llm_call` (in multi-turn sono 2 embedding/turno
  invisibili); (c) `token_usage_json` è **testo JSON**, non colonne numeriche → non
  sommabile in SQL senza `json_extract`; (d) **nessuna aggregazione** in `read_store`
  (verificato: zero query sui token); (e) gli `llm_calls` passano per la coda lossy di
  P2 → token persi sotto carico/crash; (f) `cost_tracker` wirato solo nella CLI
  `--verbose` (`cli/shell.py`), non in proxy/SDK.

**File da toccare.**
- `moralstack/models/policy.py` (etichettare la stima).
- `moralstack/models/base.py` (esporre numerici + flag estimated).
- `moralstack/orchestration/embedder.py` (catturare usage embedding).
- `moralstack/orchestration/ledger.py` (registrare le chiamate embedding come llm_call).
- `moralstack/observability/sinks/sqlite_sink.py` (colonne numeriche su `llm_calls`).
- `moralstack/observability/read_store.py` (aggregazione per modulo + totale).
- `moralstack/orchestration/controller.py` + un contextvar (accumulatore per-richiesta autoritativo).
- `moralstack/sdk/response.py` (esporre il breakdown sulla risposta).

**Passo 1 — Etichettare la stima.** In `policy.py:243-245`, quando `usage` è assente,
impostare anche `estimated=True` nel risultato. In `base.py:token_usage_json`
includere `"estimated": bool`. Così un token stimato non si confonde con uno misurato.

**Passo 2 — Catturare i token dell'embedder.** In `embedder.py`, leggere
`response.usage` di `embeddings.create` (gli embedding OpenAI riportano
`prompt_tokens`/`total_tokens`; `completion_tokens=0`) e ritornarlo insieme al
vettore. In `ledger.py` (lookup `:259`, store `:342`), dopo la P7 che memoizza
l'embedding, registrare **una** `record_llm_call` con `module="embedder"`,
`phase="ledger_lookup"`/`"ledger_store"`, `call_kind="embedding"` e il token usage.
Senza questo, il totale per domanda è sistematicamente sottostimato in multi-turn.

**Passo 3 — Colonne numeriche aggregabili.** Aggiungere a `llm_calls`
(`sqlite_sink.py`, schema + INSERT a `:553-559`) le colonne
`prompt_tokens INTEGER`, `completion_tokens INTEGER`, `total_tokens INTEGER`,
`tokens_estimated INTEGER` (0/1), popolate dai campi del `GenerationResult`. Mantenere
`token_usage_json` per retro-compatibilità, ma le aggregazioni useranno le colonne
numeriche. (Migrazione: `ALTER TABLE llm_calls ADD COLUMN …` idempotente in `init_db`.)

**Passo 4 — Accumulatore per-richiesta autoritativo (anti-perdita).** Per garantire il
**totale per domanda** anche se singole righe `llm_calls` vengono droppate dalla coda
(P2), introdurre un contextvar `request_token_accumulator` che ogni `generate()` /
embedding incrementa (per modulo). A fine `controller.process()`, scrivere un riepilogo
autoritativo sulla riga `request` (nuovo evento `EVENT_REQUEST_META_UPDATED` con
`meta.token_usage = {per_module: {...}, total_input, total_output, total}`). Questo
riepilogo è **un solo write** e non dipende dalla completezza delle N righe per-call.
Il breakdown per modulo resta sulle righe `llm_calls`; il totale autoritativo sta sulla
`request`.

**Passo 5 — Aggregazione leggibile.** In `read_store.py`, aggiungere:
- `token_usage_by_module(request_id) -> {module: {input, output, total, estimated_share}}`
  via `SELECT module, SUM(prompt_tokens), SUM(completion_tokens), SUM(total_tokens)
  FROM llm_calls WHERE request_id=? GROUP BY module`.
- `token_usage_total(request_id)` dalla `meta.token_usage` della `request` (autoritativo),
  con fallback alla somma delle righe.
Esporre il breakdown nella UI conversazione e nell'export AI-Act, e come campo della
risposta (`sdk/response.py`: `GovernedResponse.token_usage`).

**Passo 6 — Dipendenza da P2.** La completezza per-modulo dipende dalla completezza di
`llm_calls`: P11 va consolidato **dopo o insieme** a P2. Il Passo 4 (accumulatore)
rende il *totale per domanda* robusto anche prima di P2; il *breakdown per modulo*
diventa affidabile solo quando P2 garantisce emessi==salvati.

**Test di accettazione.**
- Una domanda multi-turn produce un `token_usage` per modulo che include
  `embedder` con valori > 0 (oggi assente).
- `total` per richiesta == somma input+output di tutti i moduli (entro la quota
  `estimated`); un token stimato è marcato `estimated=True`.
- Stress test (P2): il totale autoritativo per-richiesta resta corretto anche con drop
  di righe `llm_calls` (l'accumulatore non passa per la coda lossy).
- Query `token_usage_by_module` e campo `GovernedResponse.token_usage` presenti e
  testati.

**Invarianti toccati.** `observability.md` (best-effort): l'accumulatore in memoria non
deve rompere la request — incrementi in try/except. Nessun P0.

---

## 8. Sequenza operativa consigliata

1. **P1** (floor hard-signal) — sblocca la storia di sicurezza; *gate per tutti i fast-path*.
2. **P2** (persistenza) — sblocca l'audit completeness.
3. **P3** (correlation store) + **P4** (multi-turn) — sbloccano il proxy multi-tenant e l'anti-escalation.
4. **P5** (docs/govern/codice morto) + **P6** (wiring) — fiducia e DX a basso costo.
5. **P7** (embedder) — indipendenza da OpenAI.
6. **P8** (cascade) — latenza/adozione, solo dopo P1+P4.
7. **P9** (benchmark) + **P10** (split) — credibilità e manutenibilità.
8. **P11** (token accounting) — dopo/insieme a **P2**: l'accumulatore per-richiesta
   (Passo 4) può precedere P2 e rende subito robusto il *totale per domanda*; il
   *breakdown per modulo* affidabile richiede prima la completezza di P2.

Percorso critico verso "credibile in produzione": **P1 → P2 → P3 → P4**. Il resto è
parallelizzabile.

---

## 9. Criteri di accettazione globali

- [ ] Nessun fast-path (DCCL, ledger, conversational) consegna se `risk_has_hard_signal`
      è True; test con payload **parafrasato** che evade le keyword.
- [ ] Stress test persistenza: 100 richieste concorrenti, `eventi_emessi ==
      eventi_persistiti`, zero drop non contati; crash test con replay o perdita
      contabilizzata.
- [ ] Correlation store con TTL+maxsize; collisione cross-principal eliminata via salt.
- [ ] Risk estimator riceve un safety-summary non vuoto sui turni troncati; posture
      sticky su trend crescente.
- [ ] README, docstring SDK e proxy raccontano **una sola** semantica di delivery;
      `version` allineata; `final_revalidation.py` collegato o rimosso.
- [ ] Header `cached_from` emesso; `observability_mode` wirato o rimosso; docstring q1-q17.
- [ ] Embedder di default offline; nessuna doppia embedding per ciclo.
- [ ] (P8) Latenza mediana ridotta con worst-case di sicurezza invariato; drift
      dell'early-exit monitorato.
- [ ] (P9) README importa i numeri da un artefatto benchmark canonico riproducibile.
- [ ] (P11) Per ogni domanda è calcolabile il token usage input/output **per modulo**
      (incluso `embedder`) e il **totale**; i token stimati sono marcati; il totale
      autoritativo per-richiesta regge anche con drop di righe `llm_calls`.

---

## 10. Cosa NON fare

- Non aggiungere nuovi fast-path prima che **P1** sia chiuso.
- Non dichiarare audit completeness prima che **P2** sia chiuso.
- Non implementare `upstream_then_verify` come modifica silenziosa: rompe un
  invariante P0, richiede opt-in esplicito (**P5** Strada B).
- Non vendere `govern(client)` come wrapper generativo del modello utente finché
  resta governed-only.
- Non usare SQLite per 100+ richieste concorrenti senza batching + backpressure
  misurata (**P2**).
- Non iniziare lo split dei file (**P10**) prima dei fix di sicurezza/audit.
- Non usare il `is_hard_signal_refuse` esistente come gate del fast-path: ha il vincolo
  `final_action == "REFUSE"` e al punto del fast-path non c'è ancora una decision.

---

## 11. Metodo e limiti di verifica

- Ogni `file:riga` citato è stato aperto e letto in questa sessione sul codice a
  `0.6.1`. Dove le due review divergevano, ho privilegiato il codice: ledger keyed
  da `(contract_hash, posture, domain)` (non `conversation_id`), e `is_hard_signal_refuse`
  inutilizzabile come gate pre-fast-path.
- **Non eseguito**: la suite (`python -m pytest`), un load test reale della
  persistenza, un PoC end-to-end di P1 nel proxy. Sono i tre passi che trasformano
  queste deduzioni in dimostrazioni e vanno fatti prima di mergeare i fix.
- I frammenti di codice nel piano sono **direzioni precise**, non patch validate:
  ognuno richiede l'esecuzione della suite e test avversariali dedicati prima di
  considerarsi chiuso.
