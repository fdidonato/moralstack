# MoralStack Upgrade Plan - Review adversarial integrata

Data: 2026-06-26
Branch locale: `main`
Versione package rilevata: `0.6.1` in `pyproject.toml:7`
Tipo di lavoro: piano tecnico operativo, non patch applicata

## Introduzione

Questo documento e' una sintesi autonoma e operativa delle evidenze raccolte sul branch `main` di MoralStack. Non richiede la lettura di altri report: include punti di forza, problemi rilevati, priorita' e piano di intervento dettagliato.

Il giudizio e' netto: MoralStack e' una base tecnicamente seria, ma non e' ancora pronta per essere trattata come production-grade in scenari proxy non fidati, multi-tenant o audit-grade. Le due aree che bloccano la credibilita' sono:

1. **Sicurezza P0:** il fast-path DCCL puo' consegnare un `MATCH` prima che venga applicato il gate deterministico hard-signal.
2. **Audit e produzione:** la persistenza osservabilita' non garantisce completezza sotto carico, crash o concorrenza; alcuni path scrivono sincronicamente, altri via coda in memoria.

La terza questione e' strategica: `govern(client)` non usa il client dell'utente per generare la risposta finale. Il codice SDK lo dichiara, ma README e proxy contengono ancora descrizioni storiche contraddittorie. Se questa scelta resta, va posizionata come governed generation con modello interno; se si vuole davvero governare il modello dell'utente, serve una modalita' esplicita upstream-then-verify.

## Punti di forza rilevati

1. **Separazione decisione/generazione chiara nel codice.** `sdk/wrapper.py:641-648` dichiara che il testo consegnato arriva dalla pipeline governata, non dal client wrappato. `orchestration/delivery.py:9-18` ribadisce che il delivery non importa ne' chiama il client upstream.

2. **Fail-closed coerente.** I percorsi di errore producono refusal governata, non passthrough silenzioso. `sdk/config.py:124-137` e `sdk/response.py:348-365` mappano il passthrough deprecato verso comportamento sicuro.

3. **Risk estimator strutturato.** Il risk estimator usa mini-estimator paralleli (`models/risk/estimator.py:713-721`, `models/risk/estimator.py:850-854`) e calibrazione q1-q17 (`models/risk/calibration.py:82-119`). q17 minor exploitation puo' forzare denial (`models/risk/calibration.py:567-568`).

4. **Policy di routing non basata solo su prose.** `orchestration/path_router.py:42-66` implementa hard-signal refuse su segnali e reason code strutturati. `orchestration/safe_complete_policy.py` mantiene limiti su hard violations e actionability.

5. **Ledger semantico difensivo.** `orchestration/ledger.py:245-260` salta posture `ESCALATED` e turni non eleggibili; `orchestration/ledger.py:278-285` fa secondary intent check. `orchestration/ledger_storage.py:59-60` e `orchestration/ledger_storage.py:100-124` applicano LRU e TTL. Il ledger non cachea contenuto finale, ma metadata di governance.

6. **OpenAI-compatible proxy con streaming sintetico governato.** Il proxy serializza richieste con stesso `conversation_id` (`server/proxy.py:76-113`), risolve id da header/extra body/lineage (`server/proxy.py:122-134`, `server/proxy.py:271-272`) e non streamma token upstream live.

7. **Osservabilita' ricca.** `observability/events.py:13-45` definisce eventi per run, request, LLM call, orchestration, decision trace, state, ledger, session store e proxy finalization. `observability/sinks/sqlite_sink.py` contiene schema e writer per molte tabelle analitiche.

8. **Test behavior-locking gia' presenti.** Esistono test per q17 hard signal, compliance fast-path, proxy, observability, cache context isolation, output contract, SDK wrapper/config e persistence. Non coprono ancora tutti i casi avversariali, ma la base di test e' reale.

9. **Tipizzazione rigorosa su orchestration.** `pyproject.toml:137-138` abilita `strict = true` per `moralstack.orchestration.*`.

## Problemi rilevati

| ID | Priorita' | Area | Problema |
|---|---:|---|---|
| P0-1 | Alta critica | Safety | DCCL `MATCH` bypassa il gate hard-signal deterministico. |
| P0-2 | Alta critica | Audit | Persistenza split sync/async, non atomica, non crash-safe e con colli SQLite. |
| P0-2b | Alta | Token/costo/audit | Token accounting per modello/modulo/request incompleto, non normalizzato e non esposto nel proxy. |
| P0-3 | Alta | Proxy multi-tenant | Correlation store non bounded, process-local e non tenant-aware. |
| P0-4 | Alta | Multi-turn safety | Contesto risk/DCCL perde setup safety-relevant su conversazioni lunghe. |
| P1-1 | Alta prodotto | Delivery / DX | `govern(client)` e' governed-only, ma docs pubbliche promettono ancora forwarding. |
| P1-2 | Alta adozione | Latenza | Manca un triage deterministico: casi banali pagano chiamate LLM multiple. |
| P1-3 | Media | Provider/costo | Embedder solo OpenAI, chiamato in lookup e store. |
| P1-4 | Media | Revalidation | `final_revalidation.py` esiste ma non e' collegato ai path live. |
| P1-5 | Media | Benchmark | README e artefatti benchmark non raccontano una sola verita'. |
| P2-1 | Minore-media | Wiring | Header cache, `observability_mode`, docstring q-signal non allineati. |
| P2-2 | Minore-media | Manutenibilita' | `controller.py` e `deliberation_runner.py` sono file troppo grandi. |

## Evidenze essenziali dei problemi

### P0-1 - DCCL bypass hard-signal

Nel controller il risk viene calcolato, poi gira DCCL, poi un `ComplianceDecision.MATCH` puo' ritornare prima del router standard:

- risk e DCCL: `orchestration/controller.py:2014-2065`
- ramo `MATCH`: `orchestration/controller.py:2086`
- return fast-path: `orchestration/controller.py:2129`, `orchestration/controller.py:2168`
- router standard e hard-signal refuse: `orchestration/controller.py:2317-2318`
- `_route_compliance_match` salta risk routing, critic, simulator, perspectives e deliberation: `orchestration/controller.py:1343-1348`
- trace auto-incriminante: `risk_estimation_used_for_decision=False` a `orchestration/controller.py:1496`

Il DCCL riceve `risk_estimation`, ma la ignora (`compliance/dccl.py:262-276`). La safety override DCCL usa `use_llm=False` in tutti i call-site (`compliance/dccl.py:372`, `compliance/dccl.py:667`, `compliance/dccl.py:760`) e si basa su sette categorie keyword (`compliance/safety_override.py:30-46`, `compliance/safety_override.py:59-118`).

### P0-2 - Persistenza non audit-grade

Esistono due path diversi:

- `persistence/sink.py:5-7` dichiara funzioni sincrone che chiamano `router.route()` direttamente e ignorano `uow=`.
- `persistence/sink.py:188-241` scrive orchestration event sincroni.
- `persistence/write_queue.py:37-96` invia LLM call e decision trace via coda async.
- `observability/write_queue.py:31-48` usa `queue.Queue(maxsize=100_000)` e scarta su `queue.Full`.
- `observability/sinks/sqlite_sink.py:497-502` apre una nuova connessione SQLite per operazione, con WAL e foreign keys, ma senza `PRAGMA synchronous`.
- `observability/sinks/sqlite_sink.py:1517-1560` mostra il writer single orchestration event con commit/close.
- `server/proxy.py:516-529` drena solo allo shutdown; `server/proxy.py:521-524` spiega che il flush per-request e' stato rimosso.

Il risultato e' una risposta consegnata all'utente con audit potenzialmente incompleto sotto lock contention, crash, coda piena o perdita del parent request row.

### P0-2b - Token accounting incompleto

Oggi esiste un campo `token_usage_json`, ma non basta per rispondere in modo affidabile a: "quanti token input/output ho speso per ogni modello, per ogni modulo/uso LLM, e in totale per questa domanda/risposta?".

Evidenze:

- `GenerationResult.token_usage_json()` serializza solo `prompt_tokens`, `completion_tokens`, `total_tokens` (`models/base.py:120-133`).
- `OpenAIPolicy` legge `response.usage`, ma se mancano prompt/completion fa uno split stimato 70/30 (`models/policy.py:239-245`). Questa stima poi puo' essere trattata come dato reale se non viene marcata.
- `persistence/write_queue.py:37-74` persiste `token_usage_json` dentro `EVENT_LLM_CALL`, ma non normalizza input/output/total in campi separati.
- `observability/sinks/sqlite_sink.py:145-172` mostra che `llm_calls` ha `token_usage_json TEXT`, non colonne query-friendly come `input_tokens`, `output_tokens`, `total_tokens`, `token_source`.
- Il proxy OpenAI-compatible ritorna `usage` tutto a zero (`server/proxy.py:180-194`), quindi il client non vede il costo reale della domanda/risposta governata.
- Alcuni moduli passano `token_usage_json` quando disponibile (`compliance/dccl.py:536`, `models/risk/estimator.py:889-915`, `orchestration/deliberation_runner.py:2933-3264`), ma la copertura dipende dal singolo call-site e dal tipo di result. Non esiste un aggregatore request-level canonico.

Il problema non e' solo di reporting: senza token accounting completo non puoi calcolare costo per request, costo per modulo, costo per modello, regressioni di latenza/costo, ne' confrontare fast-path e deliberative path in modo serio.

### P0-3 - Correlation store proxy

`server/conversation_correlation.py:61-69` calcola hash da role/content. `ConversationCorrelationStore` mantiene un `dict` semplice (`server/conversation_correlation.py:95-98`), popolato da `resolve` e `observe_completed_turn` (`server/conversation_correlation.py:99-129`). Non ci sono TTL, maxsize, eviction o salt tenant. Il `conversation_id` risultante chiavea lock e session store (`server/proxy.py:271-272`, `server/proxy.py:314`, `server/proxy.py:370-371`).

Nota di precisione: il ledger semantico non e' keyed dal `conversation_id`; usa `contract_hash`, `posture`, `domain` (`orchestration/ledger.py:50-63`, `orchestration/ledger.py:254`, `orchestration/ledger.py:343`). Il rischio collisione riguarda soprattutto continuita' conversazionale, privacy, lock e state store.

### P0-4 - Contesto multi-turn insufficiente

`ConversationContext.role_serialized_transcript` conserva i turni recenti entro budget (`orchestration/conversation_context.py:120-150`). Il DCCL usa budget 5000 (`compliance/dccl.py:614`). Il risk estimator usa gli ultimi tre turni (`models/risk/estimator.py:231-235`, `models/risk/estimator.py:258-260`), e i moduli runtime fanno lo stesso (`runtime/modules/message_context.py:30-38`, `runtime/modules/message_context.py:52-54`). `conversation_safety_summary` esiste (`orchestration/conversation_state.py:87`), ma non risulta cablato come fonte primaria per reiniettare il rischio cumulativo nel risk estimator.

### P1-1 - Delivery e documentazione contraddittorie

Il codice SDK e' chiaro: `sdk/wrapper.py:641-648` dice che il client wrappato non genera il testo finale. La README pero' contiene ancora comportamento vecchio:

- `README.md:301`: `govern()` wraps any OpenAI-compatible client.
- `README.md:307-308`: NORMAL passa al client e SAFE chiama il client dopo synthetic user.
- `README.md:351-353`: poco dopo dice l'opposto, cioe' che il client non genera mai il delivery.
- `server/proxy.py:7`, `server/proxy.py:552-553`: docstring/commenti parlano ancora di forwarding upstream.
- `server/proxy.py:513`: version `0.5.0`, mentre `pyproject.toml:7` e' `0.6.1`.

### P1-2 - Latenza senza triage

Il risk estimator usa tre mini-estimator paralleli (`models/risk/estimator.py:713-721`, `models/risk/estimator.py:850-854`). Anche input palesemente benigni pagano governance LLM prima di arrivare alla generazione. Nei request proxy con contract opaque puo' aggiungersi anche il judge LLM del DCCL.

### P1-3 - Embedder OpenAI-only

`OpenAIEmbedder` e' l'unica implementazione concreta (`orchestration/embedder.py:100-107`), richiede `OPENAI_API_KEY` se non riceve client (`orchestration/embedder.py:133-140`) e chiama `embeddings.create` (`orchestration/embedder.py:169`). Il bootstrap lo usa per il ledger (`sdk/bootstrap.py:65-67`, `sdk/bootstrap.py:89-95`). Il ledger embeddizza sia in lookup sia in store (`orchestration/ledger.py:259`, `orchestration/ledger.py:342`).

### P1-4 - Revalidation scollegata

`orchestration/final_revalidation.py` contiene `has_developer_contract`, `emit_final_revalidation_skipped`, `record_upstream_final_generation` e `revalidate_final_output` (`orchestration/final_revalidation.py:54`, `orchestration/final_revalidation.py:64`, `orchestration/final_revalidation.py:97`, `orchestration/final_revalidation.py:148`). I call-site live usano solo `DEFAULT_POST_REVALIDATION_REFUSAL` (`server/proxy.py:46`, `orchestration/delivery.py:31`, `server/proxy.py:348`, `orchestration/delivery.py:103`).

### P1-5 - Benchmark non canonico

Gli artefatti benchmark riportano numeri diversi:

- `benchmark_outputs/benchmark_summary_20260529_174712_gpt-4o.json:10`: compliance `0.9880952380952381`
- `benchmark_outputs/benchmark_summary_20260609_155225_gpt-4o.json:10`: compliance `0.975609756097561`
- `benchmark_outputs/benchmark_summary_20260609_160323_gpt-4o.json:10`: compliance `0.975609756097561`
- `benchmark_outputs/benchmark_d86681a3-2420-4fc7-9322-7417c4f0597f.json:11955`: `moralstack_leakage_count` = 1
- `benchmark_outputs/benchmark_d86681a3-2420-4fc7-9322-7417c4f0597f.json:11990`: judge `gpt-5.2`

La README usa headline piu' favorevoli senza rendere canonico quale run sia quella ufficiale.

## Criteri di priorita'

Intervenire in questo ordine:

1. **P0 safety:** qualunque bug che permette a un fast-path di superare un hard-signal viene prima di tutto.
2. **Audit completeness:** se il sistema consegna risposte ma perde audit trail, non e' difendibile per compliance.
3. **Token/cost accounting:** ogni chiamata LLM deve essere attribuibile a request, modulo, fase, modello e token input/output; le stime devono essere marcate come tali.
4. **Isolamento multi-tenant e multi-turn:** evitare contaminazione di state e perdita di setup storico.
5. **Verita' pubblica e semantica prodotto:** docs e API devono dire una cosa sola.
6. **Costo e latenza:** ottimizzare solo dopo che le scorciatoie sono sicure e misurabili.
7. **Provider neutrality e pulizia wiring:** riducono costo, fragilita' e attrito.
8. **Manutenibilita':** rifattorizzare quando gli invarianti sono stabili, non prima.
## Piano di intervento ad alta priorita'

### A1 - Rendere non bypassabile il floor hard-signal

**Obiettivo.** Nessun `ComplianceDecision.MATCH`, ledger hit, conversational fast-path o altro early return puo' consegnare se risk estimation contiene hard semantic signals o operational risk alto.

**Codice da toccare.**

- `moralstack/orchestration/path_router.py`
- `moralstack/orchestration/controller.py`
- `moralstack/orchestration/orchestration_event_taxonomy.py`
- `tests/test_compliance_fast_path.py`
- `tests/governance_invariants/test_q17_hard_signal_invariant.py`

**Passi.**

1. In `path_router.py`, aggiungere un helper che non richiede una `Decision`, perche' nel ramo DCCL la `Decision` standard non esiste ancora.

   ```python
   def risk_estimation_has_hard_signal_floor(
       risk_estimation: RiskEstimationProtocol,
       op_risk: OperationalRisk,
   ) -> tuple[bool, tuple[str, ...]]:
       reasons: list[str] = []
       risk_signals = {
           str(sig).strip()
           for sig in (getattr(risk_estimation, "semantic_signals", None) or [])
           if str(sig).strip()
       }
       hard = risk_signals & _HARD_SEMANTIC_SIGNALS
       if hard:
           reasons.extend(sorted(hard))
       if op_risk == OperationalRisk.HIGH:
           reasons.append("operational_risk_high")
       policy_action = coerce_risk_policy_action(getattr(risk_estimation, "risk_policy_action", None))
       if policy_action == RiskPolicyAction.DENY:
           reasons.append("risk_policy_action_deny")
       return bool(reasons), tuple(reasons)
   ```

   Se si vuole essere piu' conservativi, iniziare solo con `_HARD_SEMANTIC_SIGNALS` e `OperationalRisk.HIGH`, poi includere `DENY` dopo test regressivi.

2. In `orchestration_event_taxonomy.py`, aggiungere una costante auditabile:

   ```python
   COMPLIANCE_MATCH_HARD_SIGNAL_BLOCKED = "COMPLIANCE_MATCH_HARD_SIGNAL_BLOCKED"
   ```

3. In `controller.py`, importare helper e costante. Subito dopo `cv = call_ctx.compliance_verdict`, prima del blocco `if cv is not None and cv.decision == ComplianceDecision.MATCH:`, calcolare:

   ```python
   op_risk_pre = getattr(risk_estimation, "operational_risk", OperationalRisk.NONE)
   hard_floor, hard_floor_reasons = risk_estimation_has_hard_signal_floor(risk_estimation, op_risk_pre)
   if cv is not None and cv.decision == ComplianceDecision.MATCH and hard_floor:
       self._events.emit_orchestration_event(
           request_id=request.request_id or "",
           stage="compliance_layer",
           component="dccl",
           event_type=COMPLIANCE_MATCH_HARD_SIGNAL_BLOCKED,
           decision="downgraded",
           status="ok",
           payload={
               "reason": "hard_signal_floor",
               "hard_floor_reasons": list(hard_floor_reasons),
               "matched_rule_id": (cv.matched_rule.rule_id if cv.matched_rule else None),
               "risk_score": getattr(risk_estimation, "score", None),
               "operational_risk": str(op_risk_pre),
           },
       )
   ```

   Poi cambiare la condizione del fast-path in:

   ```python
   if cv is not None and cv.decision == ComplianceDecision.MATCH and not hard_floor:
       ...
   ```

   Non mutare `cv` a `None`: conservare il verdict per audit. Semplicemente non usarlo come autorizzazione al fast-path.

4. Applicare lo stesso helper ai punti in cui si valuta ledger reuse e fast-path conversazionale. In `controller.py`, prima di applicare `_cached_lookup` come decision reuse (`controller.py:2352-2459`), bloccare il reuse se `hard_floor` e' true ed emettere un ledger event con outcome `hard_signal_floor_blocked`. Per il conversational fast-path, bloccare l'eleggibilita' se `hard_floor` e' true.

5. Aggiungere quick-check per DCCL `MATCH` free-form non enumerato. Usare `pipeline/output_contract.py:74` (`detect_enumerated_output`) per distinguere contract enumerati da free-form. Per free-form:
   - creare metodo privato in `controller.py`, per esempio `_quick_check_compliance_free_form(...)`;
   - riusare il pattern esistente di `critic.quick_check(request.prompt, draft, constitution)` da `deliberation_runner.py:974-977`;
   - ottenere la constitution con `get_constitution_safe(self.constitution_store, request.get_domain())`, come in `deliberation_runner.py:850-855`;
   - se quick check fallisce, non chiamare `_route_compliance_match`; proseguire nella pipeline standard.

**Test da aggiungere.**

1. `tests/test_compliance_fast_path.py`: DCCL `MATCH` + payload dannoso parafrasato che non matcha keyword -> non deve chiamare `_route_compliance_match`.
2. `tests/governance_invariants/test_q17_hard_signal_invariant.py`: q17 attivo + DCCL `MATCH` -> niente compliance fast-path.
3. Nuovo test: DCCL `MATCH` free-form non enumerato + critic quick_check fail -> downgrade a pipeline.
4. Test di regressione: DCCL `MATCH` enumerato benigno (`TRUE/FALSE`, `ALLOW/DENY`) resta fast-path.

**Criterio di accettazione.** Un hard-signal non puo' essere sovrascritto da contract, overlay o cache. L'evento di downgrade deve essere visibile in `orchestration_events`.

### A2 - Rendere la persistenza audit-grade sotto carico

**Obiettivo.** Eliminare scritture DB dal thread richiesta, usare un writer dedicato con connessione persistente e batching, contare ogni perdita, e garantire ordine parent->child.

**Codice da toccare.**

- `moralstack/observability/write_queue.py`
- `moralstack/observability/service.py`
- `moralstack/observability/router.py`
- `moralstack/observability/sinks/sqlite_sink.py`
- `moralstack/persistence/sink.py`
- `moralstack/persistence/write_queue.py`
- `moralstack/persistence/default.py`
- `tests/test_observability_service.py`
- `tests/test_observability_sqlite_sink.py`
- nuovo `tests/test_observability_concurrency.py`

**Passi.**

1. In `observability/write_queue.py`, smettere di trattare la coda come lista di callable generici. Aggiungere API envelope-native:

   ```python
   def submit_envelope(self, envelope: EventEnvelope) -> None: ...
   def submit_batch(self, envelopes: Sequence[EventEnvelope]) -> None: ...
   ```

   Conservare `submit(func, *args)` solo come compat legacy, ma far migrare il codice interno a `submit_envelope`.

2. Implementare micro-batching nel worker:
   - parametri: `batch_max_items` default 500, `batch_max_delay_ms` default 50;
   - il worker prende il primo evento bloccando, poi drena altri eventi disponibili fino a soglia o timeout;
   - chiama `router.route_batch(batch)` invece di `router.route(event)` per singolo evento.

3. Aggiungere contatori thread-safe nella queue:
   - `submitted_count`
   - `written_count`
   - `dropped_count`
   - `failed_count`
   - `last_error`

   Su `queue.Full`, non limitarsi a warning: incrementare `dropped_count` e generare un evento sintetico `debug.event` o una metrica leggibile. Se si sceglie backpressure, usare timeout breve configurabile invece di `put_nowait`.

4. In `observability/service.py`, cambiare:
   - `emit(envelope)` -> `self._queue.submit_envelope(envelope)`
   - `emit_batch(envelopes)` -> `self._queue.submit_batch(envelopes)`
   - `flush()` deve attendere che `submitted_count == written_count + failed_count + dropped_count` o timeout, non solo `queue.join()` opaco.

5. In `persistence/sink.py`, cambiare i `persist_*` sincroni: costruiscono ancora `EventEnvelope`, ma chiamano `get_obs().emit(envelope)` invece di `router.route(envelope)`. Aggiornare docstring: non sono piu' "SYNCHRONOUS". Se serve retrocompat test, introdurre `MORALSTACK_PERSIST_SYNC_FOR_TESTS=1`, ma non usarlo nel proxy.

6. In `persistence/write_queue.py`, sostituire `get_obs()._queue.submit(router.route, envelope)` con `get_obs().emit(envelope)`. Non accedere piu' a `_queue` dall'esterno.

7. In `persistence/default.py`, migrare `ensure_run_and_upsert_request()` e `update_request_domain()` verso eventi gia' esistenti:
   - `EVENT_RUN_STARTED`
   - `EVENT_REQUEST_UPSERTED`
   - `EVENT_REQUEST_DOMAIN_UPDATED`

   Gli event type esistono in `observability/events.py:13-30`; `sqlite_sink._dispatch` li gestisce in `observability/sinks/sqlite_sink.py:1312-1344`.

8. In `observability/sinks/sqlite_sink.py`, rendere il sink realmente connection-persistent:
   - aprire connessione in `SqliteEventSink.__init__` o lazy al primo write;
   - impostare una volta `PRAGMA busy_timeout=10000`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA synchronous=NORMAL`;
   - usare una lock interna attorno alla connessione se il writer puo' essere chiamato da piu' thread; idealmente solo il writer thread chiama il sink.

9. Sempre in `sqlite_sink.py`, migliorare `_dispatch_batch`:
   - processare prima `run.started` e `request.upserted`;
   - poi `request.domain_updated`, `request.response_updated`, `request.meta_updated`;
   - poi figli: `orchestration.event`, `llm.call`, `decision.trace`, `conversation.state_updated`, `ledger.*`, `session_store.*`, `proxy.request_finalized`;
   - usare una singola transazione per batch.

10. Per gli event type Step 13 che oggi vengono loopati individualmente (`sqlite_sink.py:1397-1402`), aggiungere batch reali o almeno eseguirli nella stessa connessione/transazione del batch corrente.

11. In `server/proxy.py`, lasciare lo shutdown drain (`server/proxy.py:516-529`) ma aggiungere log/metriche di queue counters. Non reintrodurre flush per request.

**Test da aggiungere.**

1. `tests/test_observability_concurrency.py`: creare 100 request/event bundle sintetici, emettere da thread multipli, `flush()`, verificare righe attese per `requests`, `orchestration_events`, `llm_calls`, `decision_traces`.
2. Test coda piena: maxsize piccolo, emissione > maxsize, verificare `dropped_count` e evento/metrica di drop.
3. Test ordine FK: batch con child prima del parent in input, sink deve scrivere parent prima e non perdere child.
4. Test `persistence/sink.py`: `persist_orchestration_event()` non chiama direttamente `router.route()`, ma `ObservabilityService.emit()`.

**Criterio di accettazione.** In carico sintetico, `events_emitted == events_persisted + events_dropped_counted`, con `events_dropped_counted == 0` nel caso normale. Nessuna scrittura SQLite deve avvenire sul thread request del proxy.


### A2b - Implementare token accounting canonico per request, modulo e modello

**Obiettivo.** Per ogni domanda/risposta deve essere possibile calcolare in modo affidabile:

- token input/output/total per singola chiamata LLM;
- token per modulo (`risk_intent`, `risk_signal`, `risk_operational`, `dccl`, `policy_generate`, `policy_rewrite`, `critic`, `simulator`, `perspectives`, `hindsight`, ecc.);
- token per modello effettivo;
- token totali della singola request (`run_id + request_id`), distinguendo valori esatti, stimati e mancanti.

**Codice da toccare.**

- nuovo `moralstack/observability/token_accounting.py`
- `moralstack/models/base.py`
- `moralstack/models/policy.py`
- `moralstack/orchestration/persistence_helpers.py`
- `moralstack/persistence/write_queue.py`
- `moralstack/persistence/sink.py`
- `moralstack/observability/events.py`
- `moralstack/observability/sinks/sqlite_sink.py`
- `moralstack/orchestration/types.py`
- `moralstack/orchestration/controller.py`
- `moralstack/server/proxy.py`
- `moralstack/sdk/response.py`
- `moralstack/observability/read_store.py`
- test nuovi: `tests/test_token_accounting.py`, `tests/test_token_usage_persistence.py`, `tests/test_proxy_usage_totals.py`

**Passi.**

1. Creare `observability/token_accounting.py` con una dataclass canonica:

   ```python
   @dataclass(frozen=True)
   class TokenUsageRecord:
       input_tokens: int | None = None
       output_tokens: int | None = None
       total_tokens: int | None = None
       cached_input_tokens: int | None = None
       reasoning_tokens: int | None = None
       accepted_prediction_tokens: int | None = None
       rejected_prediction_tokens: int | None = None
       source: Literal["provider_exact", "provider_total_only", "estimated_split", "local_estimate", "missing"] = "missing"
       raw_usage_json: str | None = None
   ```

   Aggiungere funzioni:
   - `from_provider_usage(usage: Any) -> TokenUsageRecord`
   - `from_token_usage_json(raw: str | None) -> TokenUsageRecord`
   - `to_json(record) -> str`
   - `merge_token_usage(records) -> TokenUsageRecord`

2. In `models/policy.py`, rimuovere la semantica implicita "70/30 = dato reale". Se il provider restituisce solo `total_tokens`, salvare `total_tokens` come esatto ma `input_tokens/output_tokens` come stimati o nulli con `source="provider_total_only"` o `source="estimated_split"`. Il `CostTracker` puo' ancora usare una stima, ma il dato persistito deve dire che e' stima.

3. In `models/base.py`, estendere `GenerationResult` per conservare una rappresentazione canonica:
   - mantenere `prompt_tokens`/`completion_tokens` per compatibilita';
   - aggiungere `token_usage: TokenUsageRecord | None` oppure far produrre a `token_usage_json()` uno schema v2 con `source`, `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens`.

4. In `persistence/write_queue.async_persist_llm_call`, normalizzare sempre `token_usage_json` prima di costruire l'envelope. Oltre al JSON raw, mettere nel payload campi top-level:
   - `input_tokens`
   - `output_tokens`
   - `total_tokens`
   - `cached_input_tokens`
   - `reasoning_tokens`
   - `accepted_prediction_tokens`
   - `rejected_prediction_tokens`
   - `token_source`
   - `token_usage_missing` boolean

5. In `observability/sinks/sqlite_sink.py`, migrare `llm_calls` aggiungendo colonne normalizzate:

   ```sql
   input_tokens INTEGER,
   output_tokens INTEGER,
   total_tokens INTEGER,
   cached_input_tokens INTEGER,
   reasoning_tokens INTEGER,
   accepted_prediction_tokens INTEGER,
   rejected_prediction_tokens INTEGER,
   token_source TEXT,
   token_usage_missing INTEGER DEFAULT 0
   ```

   Aggiornare insert single e batch (`_write_llm_call_single`, `_write_llm_call_batch`) per popolare le colonne. Mantenere `token_usage_json` per audit raw/backward compatibility.

6. Aggiungere una tabella o vista di aggregazione request-level. Scelta consigliata: tabella `request_token_usage` aggiornata a fine request, per evitare query costose e per poter esporre subito i totali nel proxy:

   ```sql
   CREATE TABLE IF NOT EXISTS request_token_usage (
       run_id TEXT NOT NULL,
       request_id TEXT NOT NULL,
       input_tokens INTEGER NOT NULL DEFAULT 0,
       output_tokens INTEGER NOT NULL DEFAULT 0,
       total_tokens INTEGER NOT NULL DEFAULT 0,
       cached_input_tokens INTEGER NOT NULL DEFAULT 0,
       reasoning_tokens INTEGER NOT NULL DEFAULT 0,
       llm_call_count INTEGER NOT NULL DEFAULT 0,
       missing_usage_count INTEGER NOT NULL DEFAULT 0,
       estimated_usage_count INTEGER NOT NULL DEFAULT 0,
       by_model_json TEXT,
       by_module_json TEXT,
       updated_at INTEGER NOT NULL,
       PRIMARY KEY (run_id, request_id)
   );
   ```

   Se si preferisce meno schema, creare una SQL view `request_token_usage_view` su `llm_calls`, ma il proxy non deve dipendere da una query async a DB per costruire `usage`.

7. Introdurre `TokenUsageAccumulator` request-local:
   - usare `contextvars` in `observability/token_accounting.py`;
   - inizializzarlo in `controller.process` all'inizio della request;
   - ogni `record_llm_call()` e `async_persist_llm_call()` deve chiamare `accumulator.add(module, phase, action, model, usage)` prima di accodare la persistenza;
   - alla fine della request, aggiungere i totali a `ResponseMetadata` e inviare un evento `request.token_usage_finalized` o `EVENT_REQUEST_META_UPDATED` con `meta.token_usage`.

8. Garantire copertura per tutti i call-site LLM:
   - risk mini-estimator: `models/risk/estimator.py:889-915` deve produrre 3 record distinti (`risk_intent`, `risk_signal`, `risk_operational`);
   - DCCL LLM: oltre all'orchestration event con `token_usage_json` (`compliance/dccl.py:536`), deve emettere un vero `llm.call` con modulo `dccl`;
   - policy generation/rewrite: tutti i `record_llm_call` in `orchestration/deliberation_runner.py` devono avere `model` e `token_usage_json` non null quando il provider lo fornisce;
   - critic/simulator/perspectives/hindsight: i result object hanno campi token (`runtime/modules/*`), ma `_token_usage_json_from_result` deve marcare `missing` quando non disponibili invece di sparire silenziosamente.

9. In `orchestration/types.py`, estendere `ResponseMetadata` con:

   ```python
   token_input_total: int = 0
   token_output_total: int = 0
   token_total: int = 0
   token_usage_by_model: dict[str, Any] = field(default_factory=dict)
   token_usage_by_module: dict[str, Any] = field(default_factory=dict)
   token_usage_missing_count: int = 0
   token_usage_estimated_count: int = 0
   ```

   Se si vuole evitare dict mutabili nella dataclass esistente, usare `Mapping[str, Any] | None` e normalizzare nel builder.

10. In `server/proxy.py`, cambiare `_build_chat_completion_response`: `usage` non deve piu' essere zero. Deve usare i totali in `result.response.metadata`:

   ```python
   "usage": {
       "prompt_tokens": metadata.token_input_total,
       "completion_tokens": metadata.token_output_total,
       "total_tokens": metadata.token_total,
   }
   ```

   Aggiungere eventualmente campi MoralStack nei headers o in `response_metadata` per breakdown by module/model, senza rompere compatibilita' OpenAI.

11. In `sdk/response.py`, esporre i totali governati anche quando non esiste `openai_response.usage`, perche' nel Plan 1 il delivery non viene dal client upstream.

12. In `observability/read_store.py` e UI/export, aggiungere query per:
   - token totali per request;
   - breakdown per modello;
   - breakdown per modulo/fase/action;
   - conteggio usage mancanti o stimati.

**Query target che devono funzionare.**

Totale per singola domanda/risposta:

```sql
SELECT input_tokens, output_tokens, total_tokens, llm_call_count,
       missing_usage_count, estimated_usage_count
FROM request_token_usage
WHERE run_id = ? AND request_id = ?;
```

Breakdown per modulo e modello:

```sql
SELECT module, phase, action, model,
       SUM(input_tokens) AS input_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(total_tokens) AS total_tokens,
       COUNT(*) AS calls,
       SUM(token_usage_missing) AS missing_usage
FROM llm_calls
WHERE run_id = ? AND request_id = ?
GROUP BY module, phase, action, model
ORDER BY module, phase, action, model;
```

**Test da aggiungere.**

1. `tests/test_token_accounting.py`: parser da usage provider completo, usage solo total, usage missing, usage con cached/reasoning tokens.
2. `tests/test_token_usage_persistence.py`: `async_persist_llm_call` scrive colonne normalizzate e mantiene `token_usage_json` raw.
3. `tests/test_proxy_usage_totals.py`: una request proxy con due call LLM fake ritorna `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens` uguali alla somma.
4. Test risk estimator: le tre mini-call producono tre record separati con modulo/fase distinti.
5. Test DCCL: il judge LLM produce un record `llm_calls` e non solo un orchestration event.
6. Test missing usage: una call senza usage incrementa `missing_usage_count` e non viene confusa con zero token.

**Criterio di accettazione.** Per ogni `run_id + request_id`, il totale token request-level deve essere calcolabile senza parsing ad hoc e senza confondere dati mancanti, stimati ed esatti. Il proxy deve restituire `usage` governato non nullo quando sono state effettuate chiamate LLM interne.
### A3 - Rendere correlation store bounded e tenant-aware

**Obiettivo.** Evitare collisioni cross-tenant e crescita memoria illimitata nel proxy.

**Codice da toccare.**

- `moralstack/server/conversation_correlation.py`
- `moralstack/server/proxy.py`
- `moralstack/server/headers.py` se si espone header diagnostico
- `tests/test_conversation_correlation.py`
- `tests/test_server_proxy.py`

**Passi.**

1. In `conversation_correlation.py`, sostituire `dict[str, str]` con `OrderedDict[str, tuple[str, float]]` o `cachetools.TTLCache`. Per evitare nuova dipendenza, usare `OrderedDict`.

2. Cambiare costruttore:

   ```python
   def __init__(self, *, max_entries: int = 10000, ttl_seconds: int = 3600) -> None:
       self._history_to_conversation: OrderedDict[str, tuple[str, float]] = OrderedDict()
   ```

3. Aggiungere metodi privati:
   - `_purge_expired(now)`
   - `_get(hash, now) -> str | None` che aggiorna LRU
   - `_put(hash, conversation_id, now)` che evicta `last=False`

4. Estendere `canonical_history_hash` o aggiungere wrapper:

   ```python
   def scoped_history_hash(messages, principal: str | None) -> str:
       base = canonical_history_hash(messages)
       scope = principal or "anonymous"
       return sha256(f"{scope}:{base}".encode()).hexdigest()
   ```

   Non includere valori sensibili grezzi; derivare `principal` da tenant id autenticato o fingerprint non reversibile della API key.

5. In `proxy.py`, estrarre principal:
   - ideale: header interno gia' autenticato, per esempio `X-Moralstack-Tenant-Id`;
   - fallback: se esiste Authorization, non loggare il token; usare solo hash HMAC con secret locale;
   - se nessun principal, usare `"anonymous"` e aggiungere warning diagnostico in debug.

6. Cambiare `_resolve_conversation_id_from_body_and_correlation(...)` per passare `principal` allo store. L'ordine resta: header `X-Moralstack-Conversation-Id`, extra body, lineage fallback.

7. Aggiungere metriche diagnostiche: size corrente, eviction count, expired count.

**Test da aggiungere.**

1. Due tenant diversi, stessa history, nessun explicit id -> conversation id diversi.
2. Stesso tenant, turni successivi lineage -> stesso conversation id.
3. Max entries piccolo -> eviction LRU.
4. TTL piccolo -> expiry.
5. Header esplicito continua a prevalere su lineage.

**Criterio di accettazione.** La correlation store non cresce oltre max_entries e non fonde conversazioni di tenant diversi con history identica.

### A4 - Rendere il multi-turn safety-aware oltre last-N

**Obiettivo.** Il risk estimator e i fast-path devono vedere un riassunto safety-relevant dei turni scartati, non solo gli ultimi tre turni.

**Codice da toccare.**

- `moralstack/orchestration/conversation_state.py`
- `moralstack/orchestration/controller.py`
- `moralstack/models/risk/estimator.py`
- `moralstack/runtime/modules/message_context.py`
- `moralstack/orchestration/conversation_context.py`
- `tests/test_cache_context_isolation.py`
- nuovo `tests/test_multiturn_safety_summary.py`

**Passi.**

1. In `conversation_state.py`, mantenere `conversation_safety_summary` ma renderlo operativo:
   - aggiungere campi strutturati, non solo stringa: `safety_signal_counts`, `recent_risk_scores`, `max_seen_operational_risk`, `sticky_posture_reason`;
   - se si vuole minimizzare diff, usare un dict serializzabile `safety_summary_payload`.

2. In `controller.py`, dentro `_extend_state_out_v04` (`controller.py:562-579`), aggiornare il summary a ogni turno:
   - aggiungere q-signal hard visti nel risk;
   - aggiungere `risk_category`, `operational_risk`, `risk_policy_action`;
   - mantenere massimo N eventi rilevanti, non tutto il transcript.

3. Aggiungere helper in nuovo file `moralstack/orchestration/safety_summary.py`:

   ```python
   def update_safety_summary(prev, risk_estimation, decision, request) -> dict: ...
   def render_safety_summary_for_risk(summary) -> str: ...
   def should_sticky_elevate(summary) -> tuple[bool, str | None]: ...
   ```

4. In `models/risk/estimator.py`, cambiare `_risk_context_messages` e `_risk_message_sections`:
   - continuare a includere ultimi tre turni;
   - prependere una sezione `Conversation safety summary` quando presente;
   - non tagliare il summary a 200 char come se fosse un turno qualunque.

5. In `runtime/modules/message_context.py`, fare la stessa cosa per critic/simulator/perspectives: ultimi tre turni piu' summary safety.

6. In `controller.py`, usare `should_sticky_elevate` prima di decidere cache/fast-path:
   - se trend crescente o hard signal storico, impostare posture `ELEVATED` o disabilitare fast-path;
   - se due SAFE_COMPLETE/REFUSE recenti o rischio crescente, niente ledger reuse benigno.

7. Aggiornare context-shape events (`controller.py:868-876`) per includere:
   - `safety_summary_included`
   - `safety_summary_signal_count`
   - `history_truncation`

**Test da aggiungere.**

1. Conversazione a 5 turni: setup safety nei turni 1-2, payload nel turno 5. Il risk prompt deve includere summary dei turni iniziali.
2. Ogni singolo turno sotto soglia, trend cumulativo crescente -> postura sticky e niente fast-path.
3. Conversazione lunga benignissima -> summary vuoto/non invasivo e nessuna regressione.

**Criterio di accettazione.** Il risk estimator non vede mai meno segnale safety della conversazione, anche quando vede meno testo.

### A5 - Allineare delivery, API e documentazione pubblica

**Obiettivo.** La repo deve raccontare una sola semantica: Plan 1 governed delivery, oppure modalita' upstream esplicita. Finche' non si implementa upstream-then-verify, la documentazione deve dichiarare governed-only senza ambiguita'.

**Codice/documenti da toccare.**

- `README.md`
- `moralstack/server/proxy.py`
- `moralstack/sdk/wrapper.py` solo se si vuole rinominare/rafforzare warning
- `docs/MORALSTACK_CODEBASE_INDEX.md`
- `docs/CODEBASE_FACTS.md`
- `docs/TRACES/openai_compatible_multiturn.md`
- `docs/TRACES/governance_decision_flow.md`
- `tests/test_server_proxy.py`
- `tests/test_sdk_wrapper.py`

**Passi.**

1. In `README.md`, rimuovere o riscrivere la tabella a `README.md:307-308`. Nuova tabella:
   - `NORMAL_COMPLETE`: delivered text from MoralStack governed pipeline.
   - `SAFE_COMPLETE`: governed safe answer from MoralStack policy pipeline.
   - `REFUSE`: governed refusal.

2. Nel paragrafo `govern()` a `README.md:301`, cambiare "wraps any OpenAI-compatible client" in:
   - "wraps the client object for API shape and non-chat passthrough; chat delivery is generated by MoralStack's governed pipeline."

3. In `server/proxy.py`, correggere docstring top-level `server/proxy.py:7` e commento `server/proxy.py:552-553`:
   - niente "forwards upstream";
   - "finalizes governed text and returns OpenAI-compatible response".

4. Aggiornare `server/proxy.py:513` a `version="0.6.1"` o derivarlo da package metadata per evitare drift futuro.

5. Aggiungere test che fallisce se il proxy chiama upstream client per delivery in NORMAL/SAFE. Se gia' esiste, rafforzarlo con assertion esplicita su mock call count zero.

6. Aggiornare docs trace e facts nella stessa PR, per rispettare `PROJECT_SPEC.md`.

**Criterio di accettazione.** README, SDK docstring, proxy docstring e test descrivono lo stesso comportamento. Nessun utente puo' credere che `model=` selezioni il generatore finale in Plan 1.

## Piano di intervento a priorita' media

### B1 - Decidere il futuro di `govern(client)` e di `final_revalidation.py`

**Obiettivo.** Scegliere tra governed-only esplicito e upstream-then-verify opt-in. Non lasciare codice morto che suggerisce garanzie non attive.

**Codice da toccare.**

- `moralstack/orchestration/final_revalidation.py`
- `moralstack/sdk/config.py`
- `moralstack/sdk/wrapper.py`
- `moralstack/server/proxy.py`
- `moralstack/orchestration/delivery.py`
- `tests/test_sdk_wrapper.py`
- nuovo `tests/test_final_revalidation.py`

**Opzione A: governed-only.**

1. Mantenere invarianti attuali.
2. Ridurre `final_revalidation.py` alla sola costante usata oppure spostare `DEFAULT_POST_REVALIDATION_REFUSAL` in `delivery.py`.
3. Rimuovere funzioni non chiamate: `revalidate_final_output`, `record_upstream_final_generation`, `emit_final_revalidation_skipped`, se non sono parte di una roadmap immediata.
4. Aggiornare docs: niente upstream generation.

**Opzione B: upstream-then-verify opt-in.**

1. In `sdk/config.py`, aggiungere:

   ```python
   generation_mode: Literal["governed", "upstream_then_verify"] = "governed"
   ```

2. In `sdk/wrapper.py`, se `generation_mode == "upstream_then_verify"` e final_action e' NORMAL/SAFE:
   - chiamare il client upstream con prompt governato;
   - registrare output con `record_upstream_final_generation`;
   - chiamare `revalidate_final_output`;
   - se critic hard violation o revalidation fail, consegnare governed refusal.

3. In `server/proxy.py`, supportare la modalita' solo dietro flag esplicito e documentato. Default resta governed.

4. Aggiornare `PROJECT_SPEC.md`/invarianti se si cambia la definizione di governed delivery. Questo non deve essere una patch silenziosa.

**Criterio di accettazione.** O il codice morto sparisce, o esiste una modalita' opt-in testata che lo usa end-to-end. Nessun comportamento implicito.

### B2 - Ridurre latenza con cascade conservativa

**Obiettivo.** Evitare 3 chiamate LLM di risk estimation sui casi ovviamente benigni o ovviamente rifiutabili, senza indebolire il worst-case.

**Codice da toccare.**

- nuovo `moralstack/models/risk/cascade.py`
- `moralstack/orchestration/controller.py`
- `moralstack/sdk/config.py`
- `moralstack/orchestration/types.py`
- `tests/test_risk_cascade.py`
- benchmark scripts in `scripts/`

**Passi.**

1. Aggiungere Tier 0 locale:
   - regex/euristiche per hard obvious refuse;
   - allowlist strettissima per benign trivial (`capital of`, arithmetic simple, formatting non-sensitive);
   - detector avversariali: encoding strani, jailbreak markers, policy override language -> defer.

2. Tier 0 deve restituire:

   ```python
   CascadeDecision(kind="allow"|"refuse"|"defer", confidence=float, reasons=list[str])
   ```

3. In `controller.process`, prima di `_estimate_risk`, chiamare Tier 0 solo se:
   - no prior elevated/sticky posture;
   - no developer contract opaque non fidato;
   - no conversation safety summary con segnali;
   - feature flag abilitato.

4. Se Tier 0 `refuse`: costruire refusal governata con audit.

5. Se Tier 0 `allow`: solo per confidenza altissima e contesto single-turn benigno; generare governed answer senza full risk estimator, ma campionare una frazione offline per audit.

6. Se `defer`: percorso attuale invariato.

7. Tier 1 futuro: singola LLM risk call economica per zona grigia. Non implementare prima di avere metriche Tier 0.

**Test.**

1. Benign trivial -> Tier 0 allow.
2. Payload hard obvious -> Tier 0 refuse.
3. Jailbreak/encoded/dual-use -> defer.
4. Multi-turn elevated -> defer sempre.
5. DCCL contract presente -> defer salvo contract enumerato molto chiuso.

**Criterio di accettazione.** Nessun caso hard adversarial esce come allow. La latenza mediana cala su benchmark benigno senza aumentare false negative.

### B3 - Rendere l'embedder locale di default e non duplicare embedding

**Obiettivo.** Togliere chiamata OpenAI dal critical path ledger e rimuovere doppio embedding lookup/store.

**Codice da toccare.**

- `moralstack/orchestration/embedder.py`
- `moralstack/sdk/bootstrap.py`
- `moralstack/orchestration/ledger.py`
- `tests/test_sdk_bootstrap.py`
- `tests/test_ledger.py` o test ledger esistenti

**Passi.**

1. In `embedder.py`, aggiungere implementazione locale:
   - opzione minima: `HashingEmbedder` deterministico per dev/test, zero rete;
   - opzione migliore: `FastEmbedEmbedder` dietro extra dependency.

2. Aggiungere factory:

   ```python
   def build_embedder(kind: str | None = None) -> EmbedderProtocol:
       kind = kind or os.getenv("MORALSTACK_EMBEDDER", "local")
   ```

3. In `sdk/bootstrap.py`, usare `build_embedder()` invece di `OpenAIEmbedder()` diretto. OpenAI diventa opt-in con `MORALSTACK_EMBEDDER=openai`.

4. In `ledger.py`, modificare lookup/store:
   - `lookup(...)` ritorna anche `query_embedding` nel result, oppure il controller conserva l'embedding in `call_ctx`;
   - `store(...)` accetta `prompt_embedding: list[float] | None`;
   - se presente, non richiama `self._embedder.embed(prompt)`.

5. Aggiornare tests per funzionare offline senza `OPENAI_API_KEY`.

**Criterio di accettazione.** Suite ledger/bootstrap passa senza rete e lo stesso prompt non viene embeddato due volte nel ciclo miss->store.

### B4 - Rendere benchmark e claim pubblici canonici

**Obiettivo.** Un solo set di numeri ufficiali per release, generato da artefatti riproducibili.

**Codice/documenti da toccare.**

- `benchmark_outputs/` naming/metadata
- script benchmark in `scripts/`
- `README.md`
- nuovo `docs/benchmark_canonical.md`
- eventuale `scripts/render_benchmark_readme.py`

**Passi.**

1. Scegliere criterio canonico: latest successful run per commit, non run piu' favorevole.

2. Aggiungere a ogni summary:
   - git commit;
   - dataset hash;
   - config hash;
   - model names;
   - judge model;
   - timestamp;
   - path raw logs.

3. Creare script che legge un summary canonico e genera snippet README. Vietare numeri scritti a mano.

4. Correggere README su compliance/leakage usando il run canonico. Se un artefatto riporta `moralstack_leakage_count=1`, non si puo' dichiarare leakage zero senza spiegare filtro o run.

5. Aggiungere test/script CI che fallisce se README benchmark non combacia con summary canonico.

**Criterio di accettazione.** Un reviewer puo' riprodurre ogni headline dalla repo senza scegliere manualmente tra piu' JSON.

### B5 - Chiudere wiring minori ma audit-relevant

**Obiettivo.** Eliminare dettagli falsi o inerti che erodono fiducia.

**Codice da toccare.**

- `moralstack/server/headers.py`
- `moralstack/orchestration/types.py`
- `moralstack/orchestration/controller.py`
- `moralstack/sdk/config.py`
- `moralstack/observability/config.py`
- `moralstack/models/risk/estimator.py`
- tests correlati

**Passi.**

1. Header cache:
   - scegliere se usare `cached_from_turn` o introdurre davvero `cached_from_decision_id`;
   - soluzione pragmatica: cambiare `headers.py:37` per leggere `cached_from_turn` quando disponibile e emettere `X-Moralstack-Cached-From-Turn`;
   - se si mantiene `X-Moralstack-Cached-From`, popolarlo in `ResponseMetadata` da `call_ctx.ledger_lookup.from_turn`.

2. `observability_mode`:
   - opzione A: rimuovere `"off"` da `sdk/config.py` con deprecation;
   - opzione B: aggiungere `"off"` a `ObservabilityMode` in `observability/config.py:23`, far tornare `"off"` da `get_observability_mode()`, e in `router.py` non dispatchare verso sink.
   - Aggiornare `tests/test_sdk_config.py` e `tests/test_observability_router.py`.

3. Docstring q-signal:
   - correggere `models/risk/estimator.py:713-717` da q1-q13 a q1-q17.
   - aggiungere test/commento per q17 se non gia' coperto.

**Criterio di accettazione.** Nessun campo pubblico documentato resta inerte o impossibile da valorizzare.

## Piano di intervento a priorita' bassa

### C1 - Refactor dei file orchestrali grandi

**Obiettivo.** Ridurre blast radius e rendere i contributi piu' sicuri, ma solo dopo aver stabilizzato P0 e audit.

**Codice da toccare.**

- `moralstack/orchestration/controller.py`
- `moralstack/orchestration/deliberation_runner.py`
- nuovi moduli sotto `moralstack/orchestration/`

**Passi.**

1. Estrarre senza cambiare comportamento:
   - `compliance_routing.py`: gating DCCL, hard floor, free-form quick-check.
   - `risk_context.py`: context shape, safety summary, transcript rendering.
   - `fast_paths.py`: ledger/conversational/DCCL/cascade preconditions.
   - `delivery_controller.py`: mapping final_action -> FinalResponse/metadata.
   - `observability_bridge.py`: emissione eventi orchestration/proxy.

2. Ogni estrazione deve essere una PR piccola:
   - spostare funzione;
   - mantenere firma;
   - aggiornare import;
   - nessun cambio logico.

3. Dopo ogni PR, eseguire subset test dell'area e full suite.

**Criterio di accettazione.** Diff comportamentale nullo, ma file principali piu' corti e responsabilita' isolate.

### C2 - Hardening deployment multi-worker

**Obiettivo.** Evitare deploy che rompono continuita' conversazionale.

**Codice da toccare.**

- `examples/server_quickstart.py`
- `moralstack/server/proxy.py`
- nuovo modulo config server se necessario

**Passi.**

1. Finche' state/correlation/locks sono process-local, rifiutare workers > 1 nei launcher ufficiali.

2. Aggiungere startup warning/readiness failure se config indica multi-worker senza backend shared.

3. Disegnare backend shared opzionale:
   - Redis per correlation/session/locks;
   - writer osservabilita' out-of-process;
   - tenant-aware keys.

**Criterio di accettazione.** Nessun quickstart ufficiale permette accidentalmente multi-worker unsafe.

## Sequenza consigliata di esecuzione

1. **PR 1:** A1 hard-signal floor DCCL + test avversariali.
2. **PR 2:** A1 esteso a ledger/conversational fast-path.
3. **PR 3:** A2 queue envelope-native + persistenza async per `persist_*`.
4. **PR 4:** A2 SQLite persistent writer + batching + stress test.
5. **PR 5:** A2b token accounting canonico per `llm_calls`, request totals e proxy usage.
6. **PR 6:** A3 correlation TTL/maxsize/principal salt.
7. **PR 7:** A4 safety summary e sticky posture.
8. **PR 8:** A5 docs delivery + proxy docstring/version drift.
9. **PR 9:** B5 wiring minori.
10. **PR 10:** B3 embedder locale + no doppia embedding.
11. **PR 11:** B1 decisione `final_revalidation`.
12. **PR 12:** B4 benchmark canonico.
13. **PR 13:** B2 cascade routing sperimentale dietro feature flag.
14. **PR 14+:** C1/C2 refactor e deploy hardening.

## Test minimi prima di dichiarare il piano completato

1. `python -m pytest tests/test_compliance_fast_path.py tests/governance_invariants/test_q17_hard_signal_invariant.py`
2. `python -m pytest tests/test_conversation_correlation.py tests/test_server_proxy.py`
3. `python -m pytest tests/test_observability_service.py tests/test_observability_sqlite_sink.py tests/test_observability_concurrency.py`
4. `python -m pytest tests/test_token_accounting.py tests/test_token_usage_persistence.py tests/test_proxy_usage_totals.py`
5. `python -m pytest tests/test_cache_context_isolation.py tests/test_multiturn_safety_summary.py`
6. `python -m pytest tests/test_sdk_wrapper.py tests/test_sdk_config.py`
7. `python -m pytest tests/test_output_contract.py tests/test_ledger.py tests/test_sdk_bootstrap.py`
8. Full suite: `python -m pytest`

## Criteri finali di accettazione

- Nessun fast-path consegna se e' attivo un hard signal.
- DCCL `MATCH` con q17, weapons, self-harm operational o op risk high viene downgradato alla pipeline standard.
- Audit trail sotto carico: eventi emessi = eventi persistiti + drop contati; nel caso normale drop = 0.
- Token accounting: ogni `llm.call` ha input/output/total normalizzati, modello, modulo, fase e source; ogni request ha totali aggregati e `usage` proxy non nullo quando applicabile.
- Correlation store bounded, TTL-based e tenant-aware.
- Risk estimator riceve safety summary cumulativo quando la history e' troncata.
- Docs pubbliche, SDK docstring e proxy docstring descrivono la stessa semantica.
- Benchmark README generato da un artefatto canonico.
- Il sistema funziona offline nei test ledger/bootstrap senza OpenAI embeddings.
- `final_revalidation.py` e' collegato a una modalita' opt-in o rimosso come codice morto.
- Refactor successivi non cambiano comportamento e sono coperti da test.

## Rischi residui e note operative

1. L'exploit end-to-end HTTP del bypass DCCL non e' stato ancora eseguito. La catena statica e' forte, ma serve un test rosso integrato.
2. La soglia reale di rottura della persistenza SQLite va misurata. L'analisi strutturale dice che il collo esiste; il numero esatto dipende da macchina, disco e workload.
3. Upstream-then-verify non e' una piccola feature: cambia la promessa di governed delivery e va trattata come modalita' separata.
4. La cascade di latenza deve arrivare dopo il floor P0. Nuovi fast-path prima di A1 sono regressione architetturale.
5. Ogni PR comportamentale deve aggiornare anche docs e trace, come richiesto da `PROJECT_SPEC.md`.
