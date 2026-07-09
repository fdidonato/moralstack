# Piano: rimozione del package deprecato `moralstack/persistence/`

**Stato: APPROVATO (con modifiche integrate) — terza review Codex, 2026-07-03
17:48, verdetto `APPROVE_WITH_CHANGES`, zero blocking issue. Vedi §14 per lo
storico completo delle tre review.**

## 1. Goal

Verificare che `moralstack/persistence/` sia davvero deprecato in favore di
`moralstack/observability/` e, in caso positivo, eliminare il package deprecato
spostando le sue uniche parti non duplicate (design pattern DI, non telemetria)
nella loro collocazione naturale, e aggiornando ogni call site a importare
direttamente da `moralstack.observability.*` / `moralstack.orchestration.*`.

**Verdetto della verifica: confermato.** `moralstack/persistence/__init__.py:1-16`
dichiara esplicitamente "deprecated; use moralstack.observability instead." ed
emette `DeprecationWarning` a import-time. Tutti i submodule (`config.py`,
`context.py`, `db.py`, `sink.py`, `write_queue.py`) sono wrapper puri che
delegano a `moralstack.observability.*` senza logica propria. Solo 3 file
(`port.py`, `default.py`, `null.py`) contengono un design pattern
(`PersistencePort` Protocol + 2 implementazioni) che non ha equivalente in
observability e va rilocato, non semplicemente cancellato.

## 2. Current behavior (verificato)

- `moralstack/persistence/__init__.py:1-16` — re-esporta ~40 simboli da
  `moralstack.observability.*` e dai propri submodule; emette
  `DeprecationWarning(stacklevel=2)` a ogni import del pacchetto.
- `config.py`, `context.py` — alias 1:1 su
  `moralstack.observability.config/context`. Zero logica propria (verificato
  leggendo i file per intero).
- `db.py` — **non è un alias puro** (correzione post-review Codex): oltre a
  re-esportare le funzioni di scrittura da `observability.sinks.sqlite_sink`,
  istanzia un **proprio** `_SqliteReadStore()` a livello di modulo
  (`db.py:61`, chiamato `_rs`) — un'istanza separata da quella usata
  internamente da `ObservabilityService` (`observability/service.py:38`,
  `self._read_store`) — ed espone funzioni standalone (`get_run`,
  `get_request`, `get_llm_calls_for_request`, ecc., `db.py:64-118`) che
  delegano a quell'istanza. `SqliteReadStore` è stateless rispetto al DB
  (legge sempre da disco), quindi le due istanze sono equivalenti in pratica,
  ma nessun call site di produzione usa queste funzioni standalone — solo
  test (confermato via grep). Nessuna implicazione sul design (§6), solo
  correzione della descrizione.
- `sink.py` (463 righe) e `write_queue.py` (107 righe) — **non sono semplici
  re-export**: costruiscono `EventEnvelope` via `make_envelope()` e chiamano
  `get_obs().emit()`/`emit_batch()`. Espongono `persist_llm_call`,
  `persist_decision_trace`, `persist_debug_event`, `persist_orchestration_event`
  (+ varianti `*_batch`) e `async_persist_llm_call`, `async_persist_decision_trace`,
  `async_persist_debug_event`. **Non esistono altrove**: grep su
  `moralstack/observability/` per le stesse firme → 0 risultati.
- `port.py:13-43` — `PersistencePort(Protocol)` con 3 metodi
  (`set_request_context`, `ensure_run_and_upsert_request`,
  `update_request_domain`). Nessun equivalente in observability.
- `default.py:19-89` — `DefaultPersistence`, implementazione reale del
  protocol: usa context vars + `init_db`/`create_run`/`upsert_request`/
  `update_request_domain` di observability, con lazy-init (`_db_initialized`)
  e try/except che logga (`logger.warning`) senza mai propagare eccezioni.
- `null.py:11-33` — `NullPersistence`, no-op puro, stesso protocol.
- **Precedente architetturale diretto nello stesso package**
  (`moralstack/orchestration/`): lo stesso pattern Protocol+impl è già usato per
  `EventEmitter`/`DefaultEventEmitter`
  (`orchestration/event_emitter.py:8`, `orchestration/default_event_emitter.py`),
  consumato da `controller.py:166` esattamente come `PersistencePort`/
  `NullPersistence` lo sono a `controller.py:158,165`.
- **Call site di produzione reali** (submodule diretti, non solo il top-level
  deprecato) — 14 file:
  `moralstack/orchestration/controller.py:110-111,158,165,1136,2078-2079,2274`,
  `moralstack/runtime/orchestrator.py:48,181`,
  `moralstack/server/proxy.py:48`,
  `moralstack/constitution/retriever.py:24`,
  `moralstack/orchestration/final_revalidation.py:27`,
  `moralstack/orchestration/deliberation_runner.py:74`,
  `moralstack/orchestration/default_event_emitter.py:8`,
  `moralstack/orchestration/persistence_helpers.py:12-15`,
  `moralstack/orchestration/diagnostics.py:121` (import lazy),
  `moralstack/runtime/modules/{critic,hindsight,perspective,simulator}_module.py`
  (import lazy dentro i rami `except` di retry — non presenti nell'evidenza
  iniziale, scoperti solo grazie alle modifiche non committate in `git status`).
- `scripts/benchmark_moralstack.py:2456-2458,2615,2634,2751` — 6 import diretti
  da `moralstack.persistence`/`.config`/`.context`/`.db` (top-level
  re-export). Nessun test esercita questo script (grep `benchmark_moralstack`
  su `tests/` → 0 risultati).
- 19 file in `tests/` referenziano `moralstack.persistence` (elenco completo in
  §7 "Tests to add / modify").
- Schema DB: unico e reale in `moralstack/observability/sinks/sqlite_sink.py`
  (12 tabelle); `persistence/db.py` non ha schema proprio.
- `pyproject.toml:76` usa wildcard `include = ["moralstack*"]` — nessuna voce
  esplicita per `moralstack.persistence`, nessuna modifica di packaging
  necessaria.
- `moralstack/__init__.py` (SDK pubblico) e `moralstack/sdk/*` — zero
  riferimenti a `persistence`: nessun rischio sull'API pubblica SDK.
- `docs/modules/persistence.md` e `docs/modules/observability.md:445-459` hanno
  già una migration table symbol-by-symbol, ma **non coprono**
  `PersistencePort`/`DefaultPersistence`/`NullPersistence` — gap documentale
  preesistente. La riga `docs/modules/observability.md:458` è inoltre
  auto-referenziale/errata (mappa `persistence.sink` → `persistence.sink`
  invece che a una destinazione reale) — bug documentale preesistente da
  correggere comunque per PROJECT_SPEC §9.

## 3. Target behavior

- `moralstack/persistence/` non esiste più.
- `PersistencePort`, `DefaultPersistence`, `NullPersistence` vivono in
  `moralstack/orchestration/` come file separati (`persistence_port.py`,
  `default_persistence.py`, `null_persistence.py`), speculari al pattern
  `EventEmitter`/`DefaultEventEmitter` già esistente.
- Le funzioni `persist_*`/`async_persist_*` (sink.py + write_queue.py, escluse
  `get_write_queue()`/`PersistenceWriteQueue` che sono dead code — zero
  consumer in tutto il repo, verificato via grep) vivono in un unico nuovo
  file `moralstack/observability/emit_helpers.py`.
- Ogni call site importa direttamente dalla nuova collocazione, con **zero
  cambi di logica o firma**.
- Comportamento osservabile identico byte-per-byte, in particolare
  l'invariante "observability never breaks the request"
  (`.claude/rules/observability.md`): stessi try/except, stesso testo di log,
  nessuna nuova eccezione che risale al chiamante.

## 4. Assumptions

- Nessun consumer esterno al repo (script di terzi, notebook, integrazioni)
  importa `moralstack.persistence` — non verificabile da codice, trattata come
  ipotesi. Mitigata mantenendo `DeprecationWarning` visibile fino a questo
  cambiamento e documentando la rimozione nel CHANGELOG.
- `scripts/benchmark_moralstack.py` è l'unico script del repo che importa
  persistence (confermato via grep su `scripts/`).

## 5. Constraints — invarianti PROJECT_SPEC §5 toccati

- **Invariante 6 — "Observability never breaks the request"**
  (`.claude/rules/observability.md`): `DefaultPersistence.ensure_run_and_upsert_request`/
  `update_request_domain` (`default.py:63-64,77-78,85-89`) avvolgono già tutto
  in `try/except Exception` con `logger.warning`, mai `raise`. Lo spostamento
  deve preservare questo comportamento carattere per carattere. Stesso vincolo
  per ogni funzione `persist_*`/`async_persist_*` spostata in
  `emit_helpers.py`.
- **Correzione post-seconda-review Codex (BLOCK, vedi §14):** il file
  `.claude/rules/observability.md` è **path-scoped** via frontmatter
  (`paths: moralstack/observability/**`, `moralstack/persistence/**`,
  `.claude/rules/observability.md:1-5`) — si carica automaticamente solo
  quando si apre un file sotto quei path. Dopo lo spostamento,
  `DefaultPersistence` (che contiene esattamente il try/except che realizza
  l'invariante) vivrebbe in `moralstack/orchestration/`, **fuori** da quello
  scope: un futuro editor che apra `default_persistence.py` non vedrebbe più
  il rule caricarsi automaticamente. **Il piano deve aggiornare il
  frontmatter di `.claude/rules/observability.md`** aggiungendo
  `moralstack/orchestration/default_persistence.py` (e, per coerenza,
  `moralstack/orchestration/persistence_port.py` e
  `moralstack/orchestration/null_persistence.py`, parte dello stesso
  sottosistema) all'elenco `paths:`. Vedi §8/§9 per il file da modificare.
- **Governed-delivery**: `server/proxy.py` è sotto
  `.claude/rules/governed-delivery.md` ed emette marker di audit
  governed-delivery tramite `persist_orchestration_event`
  (`server/proxy.py:48,427-462`). Lo spostamento dell'import (§8) non cambia
  la logica di questi marker, ma va trattato come superficie toccata
  dall'invariante 7 (governed delivery), non solo come un import qualunque —
  il test in §7.2 punto 5 copre esplicitamente questo.
- Nessun altro invariante di §5 è toccato: non è decision policy (i `persist_*`
  sono side-effect di telemetria/audit-trail, non influenzano `final_action`),
  non è prompt transparency, non è hard-signal.
- **Constitution-domains** (`.claude/rules/constitution-domains.md`,
  `_normalize_runtime_domain` in `controller.py:123-136`): questo invariante
  riguarda la normalizzazione **runtime** del dominio (mai propagare "core"
  come `domain_overlay` a governance), non la persistenza per dashboard. Va
  toccato solo come vincolo di lettura per il test in §7.2 punto 1 — vedi la
  correzione di scope lì sotto: NON introdurre né correggere in questo
  refactoring il gap pre-esistente scoperto durante la review (persistenza
  del valore "core" non normalizzato nell'upsert iniziale).
- Scope: solo spostamento file + aggiornamento import (PROJECT_SPEC §6 —
  minimal change, no refactoring non richiesto). Il gap pre-esistente sul
  dominio "core" (sotto) è **fuori scope**: va solo documentato in
  `docs/CODEBASE_FACTS.md` come ipotesi/difetto noto (PROJECT_SPEC §9), non
  corretto qui.

## 6. Design

### Decisione 1 — `PersistencePort` / `DefaultPersistence` / `NullPersistence`

**Raccomandazione: replicare esattamente lo split già esistente per
`EventEmitter`/`DefaultEventEmitter`** nello stesso package
`moralstack/orchestration/`:

1. **Nuovo file** `moralstack/orchestration/persistence_port.py` — contenuto
   verbatim di `persistence/port.py` (solo il `Protocol`).
2. **Nuovo file** `moralstack/orchestration/default_persistence.py` —
   contenuto di `persistence/default.py`, import interni aggiornati per
   puntare direttamente a observability (bypassando la catena di wrapper):
   - `from moralstack.observability.config import get_db_path`
   - `from moralstack.observability.context import get_current_run_id, set_current_request_id`
   - `from moralstack.observability.sinks.sqlite_sink import create_run, init_db, update_request_domain, upsert_request`
   Logica interna (`_ensure_db_initialized`, try/except di logging) invariata.
3. **Nuovo file** `moralstack/orchestration/null_persistence.py` — contenuto
   verbatim di `persistence/null.py`.

**Nota sui nomi dei logger (segnalata dalla seconda review Codex):**
`default.py:16` e `sink.py:31` usano `logging.getLogger(__name__)` —
spostare verbatim il codice cambia inevitabilmente il nome del logger
(`moralstack.persistence.default` → `moralstack.orchestration.default_persistence`;
`moralstack.persistence.sink` → `moralstack.observability.emit_helpers`).
**Decisione**: accettare il cambio di nome (è la convenzione standard già
usata in tutto il repo per `__name__`-based logging, es.
`default_event_emitter.py`; non ha senso hardcodare un nome di logger
"legacy" solo per compatibilità con filtri di log esterni non documentati).
Se l'utente ha configurazioni di log filtering esterne che dipendono dal
nome `moralstack.persistence.*`, va segnalato nel CHANGELOG (§9) come parte
del breaking change, non risolto con un logger name hardcoded.

Perché non un unico file consolidato: romperebbe la convenzione già stabilita
nello stesso package per `event_emitter.py`/`default_event_emitter.py`
(file separati), rendendo `PersistencePort` un'eccezione stilistica
ingiustificata rispetto al pattern gemello già presente.

### Decisione 2 — `persist_*` / `async_persist_*`

**Raccomandazione: spostamento verbatim in un unico nuovo file**
`moralstack/observability/emit_helpers.py`:

- Entrambi i moduli sorgente (`sink.py`, `write_queue.py`) già importano
  esclusivamente da `moralstack.observability.*` (context, events, service) —
  zero import interni da aggiornare, solo il docstring di modulo.
- Sono funzioni di telemetria pura, coerenti con la responsabilità di
  `moralstack/observability/`.
- Contenuto: le 4 funzioni sync + varianti batch da `sink.py`
  (`persist_llm_call`, `persist_decision_trace`, `persist_debug_event`,
  `persist_orchestration_event` + `*_batch`) più le 3 funzioni async da
  `write_queue.py` (`async_persist_llm_call`, `async_persist_decision_trace`,
  `async_persist_debug_event`).
- **Escluse**: `get_write_queue()` e l'alias `PersistenceWriteQueue`
  (`write_queue.py:28,31-33`) — zero consumer in tutto il repo (grep su
  `moralstack/` e `tests/`), dead re-export da eliminare, non da spostare.
  Segnalare esplicitamente questa scelta nel commit affinché il reviewer possa
  contestarla.
- Nessun rischio di circular import: `observability/service.py` non importa le
  funzioni `persist_*`, solo la classe `ObservabilityWriteQueue`; il nuovo
  `emit_helpers.py` importerà `get_obs` da `service.py` senza creare un ciclo.

Le funzioni `get_run`, `get_request`, `get_requests_for_run`,
`get_llm_calls_for_request`, `get_decision_traces_for_request`,
`get_orchestration_events_for_request`, `get_debug_events_for_request`,
`get_all_runs` (`persistence/db.py`) sono wrapper su `SqliteReadStore()` **senza
alcun call site in production code** (solo test) — nessun nuovo modulo di
produzione necessario, i test che le usano vanno aggiornati a istanziare
`SqliteReadStore()` direttamente (pattern già usato in
`tests/test_observability_read_store.py`).

### Ordine di esecuzione sicuro

1. Creare i 4 nuovi file (`orchestration/persistence_port.py`,
   `orchestration/default_persistence.py`, `orchestration/null_persistence.py`,
   `observability/emit_helpers.py`). Il vecchio package resta intatto in
   questo step — permette di validare i nuovi file in isolamento senza
   rompere nulla.
2. Aggiornare, uno alla volta, i call site di produzione (solo import,
   nessuna modifica di logica) — elenco completo in §8 "Files to modify".
3. Eseguire la suite mirata (vedi §7, comandi) per intercettare i `patch()`
   il cui target dipende dagli import lazy attuali (vedi rischio sotto).
4. Aggiornare i 19 file di test (import diretti, vedi §7).
5. Rinominare `tests/test_persistence_config.py` →
   `tests/test_observability_config.py` (unica copertura esistente per
   `get_db_path`/`get_persist_mode`/`get_ui_credentials`, non va persa).
6. Solo dopo che tutti i call site (produzione + test + script) sono
   aggiornati e la suite passa: eliminare l'intera cartella
   `moralstack/persistence/` e `docs/modules/persistence.md`.
7. Aggiornare la documentazione (§9) nello stesso commit di eliminazione.
8. Full suite `python -m pytest -q` finale.

### Alternatives considered (rejected)

- **Riscrivere ogni call site per usare `obs.emit(make_envelope(...))`
  direttamente**, eliminando lo strato di convenienza `persist_*`. Scartata:
  tocca ~15 call site con logica/kwargs diversi per evento, diff molto più
  ampio del necessario, viola PROJECT_SPEC §6.
- **Consolidare `PersistencePort`+`DefaultPersistence`+`NullPersistence` in un
  solo file.** Scartata: rompe la convenzione già stabilita nello stesso
  package per `EventEmitter`/`DefaultEventEmitter`.
- **Spostare `PersistencePort` dentro `moralstack/observability/`.** Scartata:
  è un'astrazione di dependency-injection consumata solo dal layer
  orchestration/runtime, non è telemetria;
  `docs/MORALSTACK_CODEBASE_INDEX.md:29` la descrive già come "DB/file
  persistence ports used by the controller" — appartiene semanticamente
  all'orchestration layer.

## 7. Tests to add / modify

### 7.1 Verifica dei 19 file esistenti (verdetto per ciascuno)

Nessuno dei 19 va **eliminato per intero**. Solo import da aggiornare, tranne
un'eccezione puntuale e 5 file "accoppiati" a un dettaglio di implementazione
(import lazy) che richiedono verifica manuale del nuovo target di patch.

| File | Cosa pinna | Azione |
|---|---|---|
| `test_persistence_config.py` | `get_db_path`/`get_persist_mode`/`get_ui_credentials`, incluse regole di precedenza legacy-alias | Rinominare in `test_observability_config.py`, solo import |
| `test_persistence_load.py` | Load/concorrenza (100 thread) su `persist_*` | Solo import |
| `test_persistence_llm_calls.py` | No troncamento oltre 10k char; ordinamento cycle/sequence/started_at | Solo import — alto valore, non toccare asserzioni |
| `test_persistence_uow.py` | `SqliteUnitOfWork` (commit, `uow=` ignorato, batch, no-op file_only) | Solo import |
| `test_observability_write_queue.py` | Coda windowed; usa `persist_llm_call*` solo per generare traffico | Solo import |
| `test_domain_prefilter_cache.py` | Cache prefilter + eventi osservabilità | Solo import |
| `test_observability_read_store_token_usage.py` | Righe 37-120 testano `SqliteReadStore` direttamente; righe 122-126 asseriscono `callable(persistence_db.get_token_usage_totals/breakdown)` — testa solo l'esistenza dell'alias deprecato | **Rimuovere** l'import `persistence_db` e le 2 asserzioni `callable(...)`; resto invariato |
| `test_runtime_observability.py` | Persistenza eventi via wrapper, lettura via `obs.read_store.*` | Solo import |
| `test_reports.py`, `test_report_journey_order.py`, `test_report_durations.py`, `test_prompt_audit_fixes.py` | Report/journey/durate seminati via wrapper | Solo import |
| `test_controller_token_accounting_speculative.py`, `test_controller_speculative_lazy.py` | `NullPersistence()` iniettata come DI stub | Solo import |
| `test_runtime_modules_retry_token_accounting.py` | `monkeypatch.setattr("moralstack.persistence.write_queue.async_persist_llm_call", ...)` per retry billing dei 4 moduli runtime | **ACCOPPIATO**: il target deve seguire dove `async_persist_llm_call` viene rilocato E dove i 4 moduli runtime puntano il loro import lazy — verifica manuale |
| `test_compliance_fast_path.py`, `test_compliance_orchestrator_integration.py`, `test_observability_contract.py`, `test_sdk_dccl.py` | `patch("moralstack.persistence.sink.persist_orchestration_event", ...)` che funziona solo perché `controller.py:1136` fa import lazy | **ACCOPPIATO**: nuovo target `patch("moralstack.observability.emit_helpers.persist_orchestration_event", ...)` — se il call site smette di essere import lazy, il patch fallirebbe silenziosamente (mock mai chiamato) invece che con errore — verificare `call_count` esplicito dopo la migrazione, non solo l'assenza di eccezioni |

### 7.2 Copertura di regressione mancante da colmare PRIMA del refactoring

Gap confermato: **nessun test collega `OrchestrationController` reale +
`DefaultPersistence()` reale + una riga vera in `requests`**
(`test_orchestrator.py:1427-1474` usa una `FakePersistence` locale;
`test_server_proxy.py:1072-1114` usa `MagicMock()` per l'intero orchestrator —
la riga `requests` verificata lì viene scritta dall'upsert diretto di
`proxy.py:696-698`, un path parallelo e indipendente da `DefaultPersistence`).
L'unico punto che istanzia `DefaultPersistence()` in produzione è
`runtime/orchestrator.py:48,181`, mai esercitato end-to-end da un test.

Aggiungere **prima** di toccare `moralstack/persistence/`:

1. `tests/test_orchestrator_default_persistence_e2e.py` (nuovo) —
   `OrchestrationController(persistence=DefaultPersistence())` reale, DB
   SQLite di test (`tmp_path`), `.process("Hello")`, assert:
   - riga in `requests` con `prompt`/`domain` corretti;
   - `update_request_domain` applicato dopo il risk assessment con dominio da
     `risk_estimation.detected_domain` **normalizzato** — scope corretto
     dopo la review Codex (BLOCK, vedi §14): l'unico punto dove
     `_normalize_runtime_domain("core")` viene applicato prima della
     persistenza è il blocco post-risk-assessment
     (`controller.py:2267-2274`, che chiama `update_request_domain` solo se
     `_domain is not None`). Assert quindi: con `risk_estimation.detected_domain
     == "core"`, **nessuna** chiamata a `update_request_domain` (comportamento
     verificato, non "core non persistito ovunque" — vedi punto sotto).
   - **Gap pre-esistente NON coperto da questo test, fuori scope** (scoperto
     durante la review Codex, da NON correggere in questo refactoring
     `PROJECT_SPEC §9`): l'upsert **iniziale**
     (`controller.py:2078-2086`) passa `domain=request.get_domain()`
     **senza normalizzazione** a `ensure_run_and_upsert_request`
     → `default.py:68-75` → `upsert_request`. Se un caller esterno
     costruisce una request con `request.get_domain() == "core"` (dominio
     dichiarato esplicitamente, non rilevato da risk assessment), quel
     valore **viene scritto** nella colonna `requests.domain` senza passare
     da `_normalize_runtime_domain`, e la normalizzazione successiva
     (§2267-2274) non lo sovrascrive perché skippa l'update quando
     `_domain is None`. Questo è un comportamento **preesistente e
     indipendente dallo spostamento del package** — non introdotto né
     peggiorato da questo piano. Documentarlo in
     `docs/CODEBASE_FACTS.md` (sezione ipotesi/difetti noti) nello stesso
     commit di questo refactoring, senza correggerlo qui.
2. `tests/test_orchestrator_default_persistence_failure_does_not_break_request.py`
   (nuovo) — monkeypatch di `init_db`/`create_run`/`upsert_request` per
   sollevare eccezione; assert che `.process()` non propaghi e produca comunque
   risposta (lock esplicito di `.claude/rules/observability.md` sul punto
   preciso `ensure_run_and_upsert_request`).
3. `tests/test_persistence_sink_contract.py` (nuovo, characterization scritta
   **contro il codice attuale** prima dello spostamento, poi ripuntata al
   nuovo path). **Correzione post-review Codex (BLOCK, vedi §14):** `sink.py`
   e `write_queue.py` hanno contratti di errore DIVERSI e vanno caratterizzati
   separatamente — non esiste un contratto unico "get_obs().emit() che
   solleva → False/None" valido per entrambi:
   - **Sync (`sink.py`)** — ogni funzione ha un proprio `try/except` attorno a
     `get_obs().emit()` (es. `persist_llm_call` a `sink.py:113-120`):
     - `persist_llm_call(...)` senza `run_id`/`request_id` → `False`, non
       chiama `get_obs().emit()`;
     - `persist_orchestration_event(...)` → sempre `None` anche a successo
       (`sink.py:246`);
     - `uow=<oggetto>` → `logger.warning` esattamente 1 volta (flag globale
       `_uow_warned`, `sink.py:33-42`);
     - `get_obs().emit()` che solleva → la funzione ritorna `False`/`None` e
       logga con `logger.warning`, non propaga (`sink.py:113-120`).
   - **Async (`write_queue.py`)** — **nessuna funzione ha un proprio
     try/except**: `async_persist_llm_call`/`async_persist_decision_trace`/
     `async_persist_debug_event` chiamano `get_obs().emit(envelope)` senza
     guardia locale (`write_queue.py:74,96,113`), oltre ai soli early-return
     per contesto mancante (`write_queue.py:40-41,81-82,102-103`). Il
     comportamento "mai raise" oggi dipende **interamente** da
     `ObservabilityService.emit()` che è già safe internamente
     (`observability/service.py:44-64`: try/except attorno a
     `self._queue.submit_envelope(envelope)`). Il test deve verificare questo
     contratto reale: se `get_obs()` è monkeypatchato per restituire un
     oggetto il cui `.emit()` solleva, le funzioni `async_persist_*`
     **propagano** l'eccezione (comportamento attuale, confermato) — non
     asserire il contrario.
   - **Decisione presa con l'utente**: si preserva questa asimmetria
     com'è oggi (minimal change, PROJECT_SPEC §6); nessun nuovo try/except
     viene aggiunto ai wrapper async durante lo spostamento in
     `emit_helpers.py`. Il test di caratterizzazione documenta l'asimmetria
     esplicitamente invece di assumerla uniforme.
4. `tests/test_benchmark_moralstack_imports.py` (nuovo — nessun test attuale
   importa lo script). **Correzione post-review Codex:** i 6 import di
   `moralstack.persistence.*` in `scripts/benchmark_moralstack.py` sono dentro
   rami di esecuzione lazy (`scripts/benchmark_moralstack.py:2456-2458,2615,2634,2751`),
   non a livello di modulo — un semplice `import scripts.benchmark_moralstack`
   intercetta solo errori a livello di modulo, non questi rami. Il test deve
   invece: (a) fare un controllo statico/AST che verifichi l'assenza di
   riferimenti a `moralstack.persistence` nel sorgente dello script dopo la
   migrazione, **oppure** (b) invocare direttamente le funzioni/rami che
   contengono quegli import (con i minimi argomenti/mock necessari per
   raggiungerli) per farli eseguire davvero.
5. **Correzione post-seconda-review Codex (BLOCK, vedi §14): nessun nuovo
   test file** — esiste già copertura diretta e completa per il marker di
   audit governed-delivery: `tests/test_server_proxy.py:1216-1270`
   (`test_proxy_output_finalized_event_persisted`) crea l'app reale con
   `create_app()`, esercita l'endpoint HTTP, e legge la riga
   `PROXY_OUTPUT_FINALIZED` **direttamente dal DB SQLite** (non da un mock),
   asserendo `decision`, `final_text_source`, `governed_delivery`,
   `wrapped_client_delivery_call`, `finish_reason`. Questo è già un test
   comportamentale end-to-end sul path esatto (`server/proxy.py:48,427-462`)
   che il piano tocca. Il piano deve solo: (a) mantenere questo test
   funzionante dopo la rilocazione dell'import in `proxy.py:48` (nessuna
   modifica di asserzioni necessaria, la riga in DB è invariata), (b)
   eseguirlo esplicitamente nel comando di verifica mirata (§7.6) come
   regression gate sul governed-delivery invariant.

### 7.3 Test da aggiungere per la nuova collocazione di Port/Default/Null

Nuovo file `tests/test_orchestration_persistence_port.py`:

- `test_null_persistence_all_methods_are_noop` — con e senza argomenti
  opzionali (`conversation_id`, `turn_index`, `parent_request_id`).
- `test_default_persistence_lazy_db_init_flag` — `_db_initialized` False→True,
  seconda chiamata non richiama `init_db` (spy, `call_count == 1`).
- `test_default_persistence_noop_when_no_run_id` — no chiamata a
  `get_db_path`/`create_run`/`upsert_request`.
- `test_default_persistence_noop_when_no_db_path`.
- `test_default_persistence_swallows_exception_and_logs_warning` — testo
  esatto del log (`"persistence: ensure_run_and_upsert_request failed"`,
  `default.py:78`; equivalente per `update_request_domain`, `default.py:89`).
- `test_persistence_port_protocol_shape` — `DefaultPersistence`/
  `NullPersistence` rispettano la firma del `Protocol` (previene rotture
  silenziose del duck-typing se Port finisce in modulo diverso da Default/Null).

### 7.4 Edge case aggiuntivi

- `persist_llm_calls_batch`/`persist_orchestration_events_batch` con lista
  vuota → `True` senza toccare `get_obs()` (`sink.py:264-265,416-417`).
- Multi-thread: `ensure_run_and_upsert_request` concorrente, stesso `run_id`,
  `request_id` diversi → `INSERT OR IGNORE` su `runs` senza conflitto, testato
  contro `DefaultPersistence` reale (non solo `create_run`/`upsert_request`
  diretti).
- Verificare in `pyproject.toml` `[tool.pytest.ini_options]` se
  `filterwarnings` intercetta il `DeprecationWarning` del pacchetto (nessuna
  occorrenza trovata al momento, ma da ricontrollare al momento
  dell'implementazione).

### 7.5 Fixtures — nessuna azione richiesta

`tests/conftest.py:14-34` non referenzia `moralstack.persistence` (solo env
var `MORALSTACK_DB_PATH`/`MORALSTACK_OBSERVABILITY_DB_PATH`). Le fixture
locali `_fresh_obs_singleton` ripetute in vari test file resettano
`service_module._obs_instance`/`router._sqlite_sink` — indipendenti da
persistence, nessuna modifica necessaria.

### 7.6 Comandi

```powershell
# Iterazione mirata durante il refactoring
python -m pytest tests/test_persistence_config.py tests/test_persistence_load.py `
  tests/test_persistence_llm_calls.py tests/test_persistence_uow.py `
  tests/test_observability_write_queue.py tests/test_observability_read_store_token_usage.py `
  tests/test_runtime_observability.py tests/test_domain_prefilter_cache.py -v

# Call site accoppiati a import lazy — isolare eventuali fallimenti
python -m pytest tests/test_compliance_fast_path.py tests/test_compliance_orchestrator_integration.py `
  tests/test_observability_contract.py tests/test_sdk_dccl.py `
  tests/test_runtime_modules_retry_token_accounting.py -v

# Governed-delivery audit regression gate (esistente, non nuovo — vedi §7.2 punto 5)
python -m pytest "tests/test_server_proxy.py::TestObservabilityPersistence::test_proxy_output_finalized_event_persisted" -v

# Gap da colmare PRIMA del refactoring (nuovi test §7.2/§7.3)
python -m pytest tests/test_orchestrator_default_persistence_e2e.py `
  tests/test_orchestrator_default_persistence_failure_does_not_break_request.py `
  tests/test_persistence_sink_contract.py tests/test_benchmark_moralstack_imports.py `
  tests/test_orchestration_persistence_port.py -v

# Suite completa prima di dichiarare il task fatto
python -m pytest

# Gate qualità — HEAD non è pre-commit-clean (vedi memoria di sessione),
# usare --files sui file effettivamente toccati, non -a
python -m pre_commit run --files <elenco file toccati>
```

## 8. Files to modify

**Nuovi file:**
- `moralstack/orchestration/persistence_port.py` — `PersistencePort` Protocol, verbatim.
- `moralstack/orchestration/default_persistence.py` — `DefaultPersistence`, import aggiornati a observability diretta.
- `moralstack/orchestration/null_persistence.py` — `NullPersistence`, verbatim.
- `moralstack/observability/emit_helpers.py` — funzioni `persist_*`/`*_batch`/`async_persist_*` (esclusi `get_write_queue`/`PersistenceWriteQueue`, dead code).

**Da eliminare (solo dopo che tutti i call site sono aggiornati):**
- Intera cartella `moralstack/persistence/` (9 file).
- `docs/modules/persistence.md`.

**Regola da aggiornare (blocking round 1, ampliata dalla terza review Codex —
vedi §5/§14):**
- `.claude/rules/observability.md:1-5` — aggiungere al frontmatter `paths:`
  le nuove collocazioni: `moralstack/orchestration/default_persistence.py`,
  `moralstack/orchestration/persistence_port.py`,
  `moralstack/orchestration/null_persistence.py` (oltre ai path esistenti
  `moralstack/observability/**` che già copre `emit_helpers.py`). **Aggiunta
  dalla terza review**: includere anche
  `moralstack/orchestration/persistence_helpers.py` — contiene lo stesso
  pattern di swallowing delle eccezioni di telemetria
  (`record_llm_call`/`record_decision_trace`, righe 40-47,58-68) e va
  toccato da questo piano (§8), quindi deve caricare l'invariante quando
  viene aperto. `moralstack/orchestration/default_event_emitter.py` (già
  toccato da questo piano) segue lo stesso pattern per gli
  `orchestration_event`; valutare se aggiungerlo allo stesso glob per
  coerenza (non obbligatorio: quel file non contiene codice nuovo introdotto
  da questo refactoring, solo un import aggiornato).

**Import da aggiornare (solo import statement):**
- `moralstack/orchestration/controller.py` (righe ~110-111, ~1136 — **mantenere
  l'import a `~1136` locale/lazy dentro la funzione**, solo il path cambia da
  `moralstack.persistence.sink` a `moralstack.observability.emit_helpers`;
  non promuoverlo a import di modulo, altrimenti i test che fanno
  `patch("...persist_orchestration_event", ...)` su quel target smettono di
  intercettare la chiamata senza errore — vedi §7.1 riga sui 4 file
  "ACCOPPIATI" e la review Codex in §14)
- `moralstack/runtime/orchestrator.py` (riga ~48)
- `moralstack/server/proxy.py` (riga ~48)
- `moralstack/constitution/retriever.py` (riga ~24)
- `moralstack/orchestration/final_revalidation.py` (riga ~27)
- `moralstack/orchestration/deliberation_runner.py` (riga ~74)
- `moralstack/orchestration/default_event_emitter.py` (riga ~8)
- `moralstack/orchestration/persistence_helpers.py` (righe ~12-15)
- `moralstack/orchestration/diagnostics.py` (riga ~121, import lazy)
- `moralstack/runtime/modules/critic_module.py` (import lazy, ~riga 527)
- `moralstack/runtime/modules/hindsight_module.py` (import lazy, ~riga 802)
- `moralstack/runtime/modules/perspective_module.py` (import lazy, ~riga 774)
- `moralstack/runtime/modules/simulator_module.py` (import lazy, ~righe 477, 592)
- `scripts/benchmark_moralstack.py` (righe ~2456-2458, ~2615, ~2634, ~2751)

**Test da modificare/rinominare** — vedi §7.1/§7.5 per il dettaglio completo
(19 file esistenti + 5 nuovi file di test).

## 9. Docs to update (PROJECT_SPEC §8)

- `docs/modules/observability.md` — rimuovere/storicizzare la sezione
  "Migration from persistence" (righe ~445-459); correggere la riga 458
  auto-referenziale errata (bug preesistente); **aggiunta post-seconda-review
  Codex**: aggiungere `emit_helpers.py` all'albero dei moduli (righe ~11-25)
  e una riga nella tabella delle responsabilità (righe ~31-40) per il nuovo
  layer di helper di emissione telemetria.
- `docs/MORALSTACK_CODEBASE_INDEX.md` — rimuovere la voce `persistence/` dalla
  mappa moduli (riga ~29) e la sezione "### Persistence" (righe ~204-209);
  aggiungere la nuova collocazione di `PersistencePort`/`DefaultPersistence`/
  `NullPersistence` nella sezione orchestration e di `emit_helpers.py` in
  quella observability; aggiornare la riga che menziona
  `test_persistence_*.py` (~563).
- `README.md:119` (trovato dalla review Codex, non nell'evidenza iniziale) —
  rimuovere la voce `moralstack/persistence/` — DB and file persistence
  modes" dall'elenco dei package principali.
- `docs/modules/README.md:50` (trovato dalla review Codex) — rimuovere la
  riga della tabella che linka a `./persistence.md` (file che questo piano
  elimina, §8).
- `docs/modules/benchmark.md:110` (trovato dalla **terza** review Codex,
  APPROVE_WITH_CHANGES) — link `[Persistence](./persistence.md)` verso un
  file che questo piano elimina; aggiornare il link/testo per non puntare più
  a `persistence.md` (rimandare a `docs/modules/observability.md`).
- `docs/CODEBASE_FACTS.md` — aggiornare eventuali fatti che menzionano
  `moralstack/persistence/*` come modulo vivente; **aggiungere nella sezione
  ipotesi/difetti noti** il gap pre-esistente scoperto durante la review
  Codex: `request.get_domain() == "core"` viene persistito senza
  normalizzazione nell'upsert iniziale (`controller.py:2078-2086` →
  `default.py:68-75`), perché `_normalize_runtime_domain` viene applicato
  solo nel blocco post-risk-assessment (`controller.py:2267-2274`), che non
  sovrascrive un valore già scritto. Fuori scope per questo refactoring
  (PROJECT_SPEC §9) — solo da documentare, non da correggere qui.
- `docs/traces/observability_db_to_ui.md` e
  `docs/traces/governance_decision_flow.md` (~righe 7-8 e 45-47) —
  sostituire i riferimenti a `persistence.set_request_context(...)` con il
  nuovo path (`moralstack/orchestration/`).
- `docs/architecture_spec.md:1250` (trovato dalla review Codex, non
  nell'evidenza iniziale) — sostituire "from `moralstack.persistence.context`"
  con "from `moralstack.observability.context`".
- `moralstack/observability/context.py:5` — il commento di modulo dice
  ancora "Migrated from moralstack.persistence.context"; aggiornarlo per non
  referenziare un modulo che non esiste più (nota storica di provenienza non
  più necessaria una volta rimosso il pacchetto).
- `CHANGELOG.md` — aggiungere una voce esplicita per il breaking change:
  rimozione del package pubblico deprecato `moralstack.persistence` (deciso
  con l'utente: accettabile ora, progetto pre-1.0 alla versione 0.6.1),
  con la migration table già presente in `docs/modules/observability.md`
  come riferimento per chi importava il pacchetto direttamente. Includere
  esplicitamente: (a) rimozione di `PersistenceWriteQueue`/`get_write_queue`
  (API pubblica, zero consumer interni, §10); (b) cambio dei nomi dei logger
  `moralstack.persistence.default`/`moralstack.persistence.sink` verso i
  nuovi moduli (nota sopra in §6), per chi ha filtri di log esterni; (c)
  **aggiunta dalla terza review Codex**: il changelog non deve limitarsi a
  `PersistenceWriteQueue`/`get_write_queue` — l'intero package e tutti i suoi
  submodule spariscono senza compatibilità per-simbolo, incluse superfici
  pubbliche non ancora esplicitate altrove nel piano:
  `PersistMode` (alias di `ObservabilityMode`, `config.py:11`) e le funzioni
  di lettura standalone di `db.py:80-118` non coperte dal punto 2 della
  Decisione 2 (§6) perché senza consumer di produzione —
  `get_token_usage_totals`, `get_token_usage_breakdown`, `get_runs_page`,
  `get_request_domains`, `get_models_used_for_run`. Formulazione consigliata:
  "l'intero pacchetto deprecato `moralstack.persistence` e tutti i suoi
  submodule sono stati rimossi; nessun alias di compatibilità sopravvive.
  Chi importava un simbolo specifico deve migrare all'equivalente diretto in
  `moralstack.observability.*` (vedi migration table storica) o, per le
  funzioni di lettura, istanziare `SqliteReadStore()` direttamente."
- **Decisione esplicita sul path di import supportato per gli helper**
  (richiesta dalla terza review Codex): `moralstack/observability/__init__.py`
  **non** re-esporta oggi `persist_*`/`async_persist_*`/`get_persist_mode`
  nel proprio `__all__` (verificato, righe ~92-155) — l'unico path
  supportato per gli helper spostati resta l'import diretto dal submodulo
  `moralstack.observability.emit_helpers`, non un re-export da
  `moralstack.observability` top-level. Nessuna modifica a
  `observability/__init__.py` prevista da questo piano (fuori scope,
  minimal change). Documentare questa scelta nella migration table di
  `docs/modules/observability.md` così che non resti implicita.

## 10. Risks

- **Patch target accoppiato a import lazy** (dettaglio §7.1): 4 file di test
  patchano `moralstack.persistence.sink.persist_orchestration_event` contando
  sull'import lazy dentro `controller.py:1136`. Se il nuovo target di patch
  (`moralstack.observability.emit_helpers.persist_orchestration_event`) non
  viene aggiornato in sincrono, il mock smette di intercettare silenziosamente
  (nessun errore, solo assert che passano per motivi sbagliati o falliscono
  su `call_count`) — va verificato esplicitamente `call_count`, non solo
  l'assenza di eccezioni.
- **Script esterno non testato**: `scripts/benchmark_moralstack.py` non è
  coperto da pytest; un errore di import lì non verrebbe rilevato dalla suite
  — mitigato dal nuovo `test_benchmark_moralstack_imports.py` (§7.2).
- **Bug documentale preesistente**: `docs/modules/observability.md:458` ha
  già oggi una riga di migration table auto-referenziale errata — da
  correggere comunque (PROJECT_SPEC §9).
- **Blast radius**: alto in numero di file toccati (~14 produzione/script +
  19+5 test + 4 doc), ma basso in rischio comportamentale — nessuna riga di
  logica cambia, solo path di import. Rischio reale: import dimenticato,
  rilevabile a import-time con `pytest --collect-only` prima ancora di
  eseguire i test.
- **Consumer esterni al repo**: non verificabile da codice (assumption §4),
  mitigata da changelog esplicito.
- **`PersistenceWriteQueue`/`get_write_queue` sono API pubblica, non solo
  dead code interno** (correzione post-review Codex): sono re-esportate nel
  `__all__` di `moralstack/persistence/__init__.py:49,73,122-123` e il
  packaging (`pyproject.toml:76`, wildcard `moralstack*`) le include nel
  pacchetto distribuito. Zero consumer *interni* al repo (confermato via
  grep), ma un consumer esterno che le importasse subirebbe un breaking
  change. **Deciso con l'utente**: accettabile ora (progetto pre-1.0,
  v0.6.1, deprecazione già esplicita da tempo con `DeprecationWarning`) —
  documentare comunque il breaking change nel `CHANGELOG.md` (§9).

## 11. Acceptance criteria

- [ ] `moralstack/persistence/` non esiste più nel repository.
- [ ] **Grep allargato dalla terza review Codex** (il pattern originale
      avrebbe mancato i link markdown tipo `docs/modules/benchmark.md:110`
      che referenziano `./persistence.md` senza mai scrivere
      "moralstack.persistence"):
      `grep -rE "moralstack[./]persistence|persistence\.md|PersistMode" moralstack/ scripts/ tests/ docs/`
      non produce risultati (eccetto note storiche esplicite nel CHANGELOG e
      in `docs/refactoring_diary.md`, diario datato di refactoring passati —
      lasciato come record storico, non aggiornato, vedi §14).
- [ ] `PersistencePort`, `DefaultPersistence`, `NullPersistence` esistono in
      `moralstack/orchestration/` con comportamento identico (stesso
      try/except, stesso logging), verificato dai test nuovi §7.3 e da quelli
      esistenti aggiornati solo negli import.
- [ ] `emit_helpers.py` in `moralstack/observability/` espone le stesse 11
      funzioni con firme identiche.
- [ ] Gap di regressione colmato: `test_orchestrator_default_persistence_e2e.py`
      e `test_orchestrator_default_persistence_failure_does_not_break_request.py`
      passano contro il codice PRIMA e DOPO lo spostamento (characterization).
- [ ] `python -m pytest -q` (suite completa) passa, zero test saltati/eliminati
      per aggirare il cambiamento (a parte il rename giustificato di
      `test_persistence_config.py` e la rimozione mirata di 2 asserzioni in
      `test_observability_read_store_token_usage.py`, vedi §7.1).
- [ ] `pyproject.toml` non richiede modifiche (verificato: wildcard packaging).
- [ ] Documentazione (§9) non contiene più riferimenti a
      `moralstack/persistence/` come modulo vivente, incluso `README.md`,
      `docs/modules/README.md`, `docs/architecture_spec.md`.
- [ ] `.claude/rules/observability.md` copre via frontmatter `paths:` la
      nuova collocazione di `default_persistence.py` (e dei file gemelli
      port/null in orchestration) — verificabile aprendo il file e
      controllando che il rule si carichi.
- [ ] Gap pre-esistente sul dominio "core" documentato in
      `docs/CODEBASE_FACTS.md` come ipotesi/difetto noto, non corretto in
      questo cambiamento.
- [ ] Nessuna modifica di comportamento nell'invariante "observability never
      breaks the request" — verificabile per diff: i blocchi try/except
      spostati sono identici carattere per carattere.
- [ ] `pre-commit run --files <toccati>` verde.

## 12. Checklist implementazione (ordine)

1. [ ] Creare i 4 nuovi file (§6, Decisioni 1-2) e aggiornare il frontmatter
       `paths:` di `.claude/rules/observability.md` con le nuove
       collocazioni in `moralstack/orchestration/` (§5/§8, blocking fix).
2. [ ] Scrivere i test di caratterizzazione §7.2/§7.3 **contro il codice
       attuale** (import ancora da `moralstack.persistence.*`), farli passare.
3. [ ] Aggiornare i call site di produzione uno alla volta (§8), ripuntando i
       test di caratterizzazione al nuovo path a mano a mano.
4. [ ] Eseguire la suite mirata (§7.6) per intercettare i `patch()` accoppiati
       a import lazy; correggere i target uno a uno con verifica `call_count`.
5. [ ] Aggiornare i 19 file di test esistenti (solo import, tranne
       `test_observability_read_store_token_usage.py`).
6. [ ] Rinominare `test_persistence_config.py` → `test_observability_config.py`.
7. [ ] Aggiornare `scripts/benchmark_moralstack.py`; validare con
       `test_benchmark_moralstack_imports.py`.
8. [ ] Full suite verde → eliminare `moralstack/persistence/` e
       `docs/modules/persistence.md`.
9. [ ] Aggiornare documentazione (§9).
10. [ ] Full suite finale + `pre-commit run --files <toccati>`.

## 13. Rollback plan

Ogni step (creazione nuovi file → aggiornamento call site → eliminazione
package → pulizia doc) è un commit separato e reversibile con `git revert`
singolo, poiché il vecchio package resta funzionante fino allo step 8. In
caso di rottura post-merge: revertire prima il commit di eliminazione
(ripristina `moralstack/persistence/`, suite torna verde), poi indagare i
call site aggiornati in isolamento senza pressione temporale.

## 14. Review history

**Codex plan review — 2026-07-03 17:17 — Verdict: BLOCK.**
Salvata in `ai/reviews/codex-plan-review-remove-deprecated-persistence-package-20260703-171703.md`
(richiesta composta in `ai/prompts/generated-codex-plan-review-remove-deprecated-persistence-package-20260703-171703.md`).

**Blocking issue risolto**: il piano originale asseriva un unico contratto di
errore ("get_obs().emit() che solleva → False/None, non propaga") per
*entrambi* `sink.py` e `write_queue.py`. Verificato (`write_queue.py:36-113`)
che i wrapper async **non** hanno un proprio try/except attorno a
`get_obs().emit()`, a differenza dei wrapper sync in `sink.py:113-120` che
ce l'hanno. Il test di caratterizzazione proposto sarebbe quindi fallito
contro il codice attuale o avrebbe imposto silenziosamente un cambio di
comportamento in un'area P0 (`.claude/rules/observability.md`). **Corretto**
in §7.2 punto 3: contratti separati per sync/async, con decisione esplicita
(presa con l'utente) di preservare l'asimmetria attuale senza aggiungere
try/except ai wrapper async.

**Non-blocking pieghe integrate**: elenco docs completato
(`docs/architecture_spec.md:1250`, `moralstack/observability/context.py:5`,
`CHANGELOG.md`, §9); descrizione di `persistence/db.py` corretta (non è un
alias puro, istanzia un proprio `_SqliteReadStore`, §2); test benchmark
riprogettato per eseguire davvero i rami lazy invece di limitarsi all'import
di modulo (§7.2 punto 4); aggiunto test di copertura per i marker di audit
governed-delivery in `server/proxy.py` (§7.2 punto 5); rischio sull'API
pubblica di `PersistenceWriteQueue`/`get_write_queue` reso esplicito (§10);
rafforzata l'istruzione di mantenere lazy l'import in `controller.py:1136`
(§8).

**Decisioni prese con l'utente** (non assunte unilateralmente, vedi
`AskUserQuestion` in sessione): (1) rimozione del package accettabile ora,
non rimandata a un altro rilascio; (2) asimmetria sync/async nei wrapper
`persist_*`/`async_persist_*` preservata com'è, nessuna correzione di
comportamento fuori scope.

**Prossimo passo**: ri-sottoporre il piano a Codex
(`/ai-review-plan-with-codex ai/plans/remove-deprecated-persistence-package.md`)
per verificare che il blocco sia risolto prima di procedere con
`/ai-implement-with-cursor`.

---

**Codex plan review #2 — 2026-07-03 17:41 — Verdict: BLOCK.**
Salvata in `ai/reviews/codex-plan-review-remove-deprecated-persistence-package-20260703-174143.md`
(richiesta composta in `ai/prompts/generated-codex-plan-review-remove-deprecated-persistence-package-20260703-174143.md`).

**Blocking issue #1 risolto — rule path-scoping**: `.claude/rules/observability.md`
si carica automaticamente solo per file sotto `moralstack/observability/**` e
`moralstack/persistence/**` (frontmatter, `.claude/rules/observability.md:1-5`).
Dopo lo spostamento, `DefaultPersistence` (dove vive il try/except che
realizza l'invariante) sarebbe finito fuori da quello scope. **Corretto**:
aggiunto alla checklist (§12 punto 1) e ai file da modificare (§8) l'update
del frontmatter `paths:` per includere i 3 nuovi file in
`moralstack/orchestration/`.

**Blocking issue #2 risolto — scope del test sul dominio "core" ambiguo/errato**:
verificato (`controller.py:2078-2086`, `default.py:68-75`,
`controller.py:123-136,2267-2274`) che l'upsert **iniziale** persiste
`request.get_domain()` **senza normalizzazione**; solo il blocco
post-risk-assessment applica `_normalize_runtime_domain` prima di un
eventuale `update_request_domain`, e non sovrascrive un valore "core" già
scritto dall'upsert iniziale se il nuovo `_domain` risulta `None`. Il test
proposto in §7.2 punto 1 asseriva genericamente "core non persistito" — falso
per il path del dominio dichiarato dalla request. **Corretto**: lo scope del
test è ora limitato al path verificabile e corretto (dominio da
`risk_estimation.detected_domain` via il blocco post-risk-assessment); il gap
sul dominio dichiarato dalla request è documentato come difetto pre-esistente
**fuori scope** in `docs/CODEBASE_FACTS.md` (§9), non introdotto né corretto
da questo refactoring (PROJECT_SPEC §9).

**Non-blocking pieghe integrate**: `README.md:119` e
`docs/modules/README.md:50` aggiunti alla lista docs (§9); tabella
responsabilità/albero moduli di `docs/modules/observability.md` esteso con
`emit_helpers.py`; §5 ora riconosce esplicitamente la superficie
governed-delivery toccata in `server/proxy.py`; punto 5 di §7.2 sostituito
— non serve un nuovo test, esiste già copertura end-to-end diretta in
`tests/test_server_proxy.py:1216-1270`
(`test_proxy_output_finalized_event_persisted`), aggiunta al comando di
verifica mirata (§7.6); nota sui nomi dei logger che cambiano con lo
spostamento (`moralstack.persistence.default/sink` → nuovi moduli) aggiunta
in §6 con decisione esplicita di accettare il cambio (convenzione standard
del repo) e documentarlo nel CHANGELOG (§9).

**Prossimo passo**: ri-sottoporre il piano a Codex per un terzo giro di
review (`/ai-review-plan-with-codex ai/plans/remove-deprecated-persistence-package.md`)
prima di procedere con `/ai-implement-with-cursor`.

---

**Codex plan review #3 — 2026-07-03 17:48 — Verdict: APPROVE_WITH_CHANGES
(zero blocking issue).**
Salvata in `ai/reviews/codex-plan-review-remove-deprecated-persistence-package-20260703-174806.md`
(richiesta composta in `ai/prompts/generated-codex-plan-review-remove-deprecated-persistence-package-20260703-174806.md`).

**Non-blocking pieghe integrate** (tutte verificate sul codice prima di
integrarle):
- `docs/modules/benchmark.md:110` linka `./persistence.md` — aggiunto a §9,
  e il grep di acceptance (§11) è stato allargato da
  `moralstack.persistence` (che non avrebbe intercettato un link markdown) a
  un pattern che copre anche `persistence.md` e `PersistMode`.
- Blast radius pubblico ampliato: `PersistMode` (alias, `config.py:11`) e le
  funzioni di lettura standalone di `db.py:80-118`
  (`get_token_usage_totals`, `get_token_usage_breakdown`, `get_runs_page`,
  `get_request_domains`, `get_models_used_for_run`) sono state aggiunte
  esplicitamente alla voce CHANGELOG (§9) invece di restare implicite nella
  Decisione 2 di §6.
- Frontmatter di `.claude/rules/observability.md` esteso oltre le 3 classi
  DI: aggiunto anche `moralstack/orchestration/persistence_helpers.py`
  (stesso pattern di swallowing eccezioni, righe 40-47/58-68), toccato da
  questo piano (§8).
- Decisione esplicita registrata (§9): l'unico path di import supportato per
  gli helper spostati è `moralstack.observability.emit_helpers` — nessuna
  modifica a `moralstack/observability/__init__.py` (che oggi non
  ri-esporta questi simboli nel proprio `__all__`, verificato) è prevista;
  fuori scope, minimal change.

**Domande di Codex, risposte con una decisione proporzionata (non
bloccanti, non richiedono un altro giro di `AskUserQuestion`)**:
- `docs/refactoring_diary.md:115-118` — verificato: è un diario datato di
  refactoring passati ("Black check — baseline (2025-02-24)" alla riga
  successiva), non documentazione vivente. **Deciso**: lasciarlo invariato
  come record storico, non toccarlo in questo cambiamento — coerente con
  come i CHANGELOG storici già si comportano altrove nel repo. Riflesso
  nell'acceptance criteria di §11.
- Path di import supportato per gli helper: risolto sopra (solo
  `emit_helpers.py`, nessun re-export in `observability/__init__.py`).

**Il piano è ora considerato pronto per l'implementazione.**

---

**Implementazione + Codex diff review — 2026-07-04.**
Implementato via Cursor CLI (run interrotto per limite di sessione del
coordinatore, ma `cursor-agent` aveva completato); ripreso e verificato
manualmente. Full suite `1986 passed`. Un solo fix sostanziale post-Cursor:
E402 in `emit_helpers.py` (import `get_obs` spostato in cima, semanticamente
neutro; asimmetria sync/async preservata) + nota di contratto in
`docs/modules/observability.md`. Debris `MagicMock/` (test out-of-scope) rimosso.

Codex diff review (foreground, read-only) salvata in
`ai/reviews/codex-diff-review-remove-deprecated-persistence-package-20260704-102408.md`
(richiesta in `ai/prompts/generated-codex-diff-review-...-102408.md`, diff in
`ai/reviews/diff-remove-deprecated-persistence-package-20260704-102408.md`).
**Verdetto: `APPROVE_WITH_CHANGES`, zero blocking.** Unica deviazione non
bloccante (verificata sul codice): mancavano gli assert `call_count` sui test
accoppiati all'import lazy richiesti da §7.1/§10. **Risolto**: aggiunti
binding del mock + `assert mock_dccl_persist.call_count >= 1` ai 4 siti nominati
(`test_sdk_dccl.py` ×2, `test_compliance_fast_path.py`,
`test_compliance_orchestrator_integration.py`); i 25 test dei 4 file accoppiati
passano, la guardia ora verifica attivamente che l'import lazy di
`controller.py:1136` risolva al target patchato in `emit_helpers`.
