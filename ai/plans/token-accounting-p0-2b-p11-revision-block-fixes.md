# Revisione piano — token-accounting-p0-2b-p11 (fix ai 5 BLOCKING di Codex)

> Sezioni pronte per essere incollate a sostituzione delle sezioni corrispondenti in
> `ai/plans/token-accounting-p0-2b-p11.md`. Non sostituire "Tests to add / modify"
> (verra rifatta da un test-strategist separato con questo design come input).
>
> Tutte le evidenze `path:line` sotto sono state ri-verificate leggendo il codice reale
> a `main` in questa sessione (non solo riportate da Codex). Dove i numeri di riga di
> Codex erano leggermente stale, sono stati corretti.

---

## 0. Scoperta non presente nel piano originale ne nel report di Codex

Durante la ri-verifica di BLOCKING 1 e emerso un **bug pre-esistente e indipendente**
che rende il fix di BLOCKING 1 un prerequisito piu stretto di quanto Codex descriva:

`SpeculativeOverlapHandle.abandon()` (`moralstack/orchestration/speculative_overlap.py:127-170`)
avvia un thread daemon **grezzo** (`threading.Thread(target=_bg, daemon=True)`, riga 170)
senza `contextvars.copy_context()`. Il dict `merged` passato a `self._events.emit_llm_call(**merged)`
dentro `_bg` (righe 161-163) **non contiene mai `run_id`/`request_id` espliciti**
(verificato: `persist_kwargs` costruito in `_speculative_generate`,
`controller.py:973-999`, non include quelle chiavi). `emit_llm_call` ->
`record_llm_call(None, None, kwargs)` (`default_event_emitter.py:17-18`) ->
`async_persist_llm_call(**kwargs)` (`persistence/write_queue.py:36-41`) fa
`run_id = kwargs.get("run_id") or get_current_run_id()`. `get_current_run_id()`/
`get_current_request_id()` leggono `contextvars.ContextVar` (`observability/context.py:12-13`,
default `None`); un thread creato senza `copy_context()` **non eredita il contesto del
chiamante** — parte con `ContextVar` ai valori di default. Risultato verificato per
lettura del codice: **nel thread `_bg`, `run_id`/`request_id` sono sempre `None`**, quindi
`async_persist_llm_call` ritorna subito (`if not run_id or not request_id: return`)
e **l'evento `EVENT_LLM_CALL` della speculativa scartata non viene mai emesso oggi**,
non semplicemente "in ritardo".

Questo bug e indipendente dal token accounting (esiste gia oggi, causa un buco di audit
sulle speculative scartate) ma e un **prerequisito bloccante** per BLOCKING 1: senza
correggerlo, il meccanismo di riconciliazione descritto sotto non ha nulla da riconciliare
perche la riga non arriva mai al DB. Va corretto nello stesso PR di BLOCKING 1 (stesso file,
stesso metodo), non come task separato.

---

## 1. Design aggiornato per i 5 BLOCKING

### BLOCKING 1 — Race di finalizzazione con le speculative call

**Fix in due parti, stesso file `moralstack/orchestration/speculative_overlap.py`:**

**Parte A — correggere la perdita di contesto (prerequisito, sezione 0).**
In `abandon()`, catturare `run_id`/`request_id` (e, per coerenza dell'intera riga
persistita, anche `session_id`/`turn_number`/`cycle`) **nel thread chiamante** (dove il
contesto e corretto), prima di avviare `_bg`, e iniettarli esplicitamente in `merged`:

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
    if captured_run_id and captured_request_id:
        mark_speculative_pending(captured_run_id, captured_request_id)

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
        finally:
            if captured_run_id and captured_request_id:
                mark_speculative_resolved(captured_run_id, captured_request_id)

    threading.Thread(target=_bg, daemon=True, name="speculative-abandon").start()
```
`setdefault` (non sovrascrittura incondizionata) mantiene retro-compatibilita se in
futuro `_speculative_generate` iniziasse a popolare quelle chiavi da se.

**Parte B — contatore di outcome pendenti, sincrono e non bloccante, nello stesso
accumulatore di `observability/request_token_accumulator.py` (BLOCKING 5 la rende
gia keyata `(run_id, request_id)`; qui si aggiunge un contatore nella stessa entry,
sotto lo stesso lock — evita un secondo dict/lock):**

```python
@dataclass
class RequestTokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    missing_usage_count: int = 0
    estimated_usage_count: int = 0
    pending_speculative_discards: int = 0   # NEW - bookkeeping only, mai persistito
    deferred_finalize_requested: bool = False  # NEW - bookkeeping only

def mark_speculative_pending(run_id: str, request_id: str) -> None:
    """Increment the in-flight speculative-discard counter for this request. Never raises."""

def mark_speculative_resolved(run_id: str, request_id: str) -> None:
    """Decrement the counter; if it reaches 0 AND a deferred finalize was requested,
    perform the pop+persist now (runs in the daemon thread - off the request's hot
    path, so a small amount of work here is acceptable). Never raises."""

def has_pending_speculative_discard(run_id: str, request_id: str) -> bool: ...

def peek_request_token_usage(run_id: str, request_id: str) -> RequestTokenTotals | None:
    """Non-destructive read of current totals (does not pop). Used for the synchronous
    proxy-facing snapshot when finalization must be deferred."""
```
**Punto di lettura in `controller.py::_finalize_token_accounting`** (stessa funzione
del design originale, agganciata a `_attach_trace_and_return`, righe 312-320), logica
estesa:

```python
def _finalize_token_accounting(self, result, request) -> None:
    try:
        run_id = get_current_run_id()
        request_id = request.request_id
        if not run_id or not request_id:
            return
        if has_pending_speculative_discard(run_id, request_id):
            totals = peek_request_token_usage(run_id, request_id)
            request_deferred_finalize(run_id, request_id, timeout_s=_SPECULATIVE_FINALIZE_TIMEOUT_S)
        else:
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

`request_deferred_finalize(run_id, request_id, timeout_s)`: imposta
`entry.deferred_finalize_requested = True` e schedula un
`threading.Timer(timeout_s, _timeout_finalize, args=(run_id, request_id))`
(timer daemon) come rete di sicurezza nel caso l'executor si blocchi e
`mark_speculative_resolved` non porti mai il contatore pendente a 0.

Default `_SPECULATIVE_FINALIZE_TIMEOUT_S = 10.0`, esposto come costante di modulo
patchabile via `monkeypatch`.

**Copertura esatta rispetto ai call site citati da Codex** (ri-verificati in sessione):
`controller.py:2527-2528` (`route == "refuse"`), `:2552-2553` (`route == "safe_complete"`),
`:2592-2594` (`constrained_generation` nel ramo deliberativo).

**Limite residuo, esplicitamente accettato e documentato:**
`SpeculativeOverlapHandle.shutdown_executor()` (`speculative_overlap.py:51-58`)
chiama `abandon()` come fallback solo se il route non lo ha gia fatto.
**Alternative considerate per BLOCKING 1 (scartate):**
- **Attendere sincronamente `_spec_future` dentro `abandon()` con timeout breve**:
  scartata — reintroduce esattamente la latenza che la speculative overlap doveva
  eliminare sui path refuse/safe_complete (i piu sensibili alla latenza, essendo i
  path "veloci" per definizione).
- **Rendere `abandon()` sempre sincrono se `spec_future.done()`**: valutata come
  ottimizzazione compatibile e a basso rischio (nessuna race possibile se il future e
  gia risolto al momento dell'abbandono), ma non e necessaria per la correttezza del
  design sopra (il contatore gestisce comunque il caso). Lasciata come possibile
  micro-ottimizzazione per l'implementatore, non obbligatoria.
- **`ContextVar` copiato via `copy_context()` anche per `_bg`**: risolverebbe la Parte A
  in modo piu idiomatico, ma non basta da sola — servirebbe comunque il contatore di
  Parte B per chiudere la race di finalizzazione. Si mantiene la cattura esplicita
  (Parte A) perche e piu leggibile e non richiede portare l'intero contesto
  attraverso un `copy_context()` opaco.

---

### BLOCKING 2 — `llm.call` non e un proxy affidabile di "chiamata al provider"

Nuovo campo esplicito **`billable_provider_call: bool`**, non riuso del `Literal`
`TokenUsageSource` (che resta a 4 valori come da design originale — tenere le due
dimensioni separate: qualita della provenienza vs "questa riga ha consumato token
fatturabili"). Default `True` ovunque (nessuna riga esistente cambia comportamento);
`False` esplicito solo nei 3 call site diagnostici verificati in sessione:

1. `deliberation_runner.py:2616-2639` — `record_llm_call(..., {"action": "generate
   (speculative-reuse)", ..., "call_kind": "speculative_reuse", ...})`: nessuna
   seconda chiamata LLM (il draft speculativo viene riusato). Aggiungere
   `"billable_provider_call": False` al dict `persist_kwargs`.
2. `deliberation_runner.py:2651-2674` — `record_llm_call(..., {"action": "rewrite
   (SKIPPED_EMPTY_GUIDANCE)", ...})`, **scoperto in questa sessione, non citato da
   Codex**: la funzione ritorna a riga 2675 prima di chiamare `self.policy.rewrite()`
   — riga diagnostica "abbiamo saltato la rewrite", zero chiamate LLM. Stesso fix:
   `"billable_provider_call": False`.
3. `deliberation_runner.py:2716-2742` — blocco `if protection_result.had_leakage:`,
   `action="leakage_detected"`, `module="output_protection"`: post-processing sul
   testo gia generato (leakage cleanup), non una chiamata LLM. Stesso fix.

Le righe realmente billable — inclusa `call_outcome="discarded"` delle speculative
scartate (BLOCKING 1) — restano `billable_provider_call=True` per costruzione
(default), coerente con la Decisione prodotto 1: hanno davvero consumato token, vanno
contati anche se scartate.

**Perche un booleano esplicito e non una derivazione da `call_kind`/`call_outcome`
esistenti** (gia presenti nello schema, verificato: colonne TEXT esistenti,
`write_queue.py:67-68`, `sqlite_sink.py:699-700`): scartato — `call_kind` e testo
libero con valori eterogenei (`"speculative"`, `"speculative_reuse"`,
`"final_revalidation_refusal"`, `None`, ecc.) usato anche per scopi diagnostici non
legati alla fatturabilita; derivare `billable_provider_call` da un elenco crescente
di stringhe `call_kind` da whitelistare/blacklistare e fragile (ogni nuovo
`call_kind` futuro richiederebbe ricordarsi di aggiornare la lista) — un booleano
esplicito e sempre presente e un filtro stabile per tutte le query di aggregazione,
presente e futuro.

**Plumbing:**
- `moralstack/persistence/write_queue.py::async_persist_llm_call` (righe 51-71):
  aggiungere `"billable_provider_call": kwargs.get("billable_provider_call", True)`
  al `payload` dict.
- `moralstack/observability/sinks/sqlite_sink.py`: nuova colonna
  `llm_calls.billable_provider_call INTEGER` (nullable, stesso pattern additivo delle
  altre — righe 698-702), scritta come 1/0 da `_write_llm_call_single`/
  `_write_llm_call_batch`. Righe storiche restano NULL; tutte le query di
  aggregazione (breakdown, ricostruzione, e l'hook dell'accumulatore) devono leggere
  `COALESCE(billable_provider_call, 1) = 1` cosi le righe pre-migrazione (tutte reali
  chiamate provider, per costruzione) restano correttamente conteggiate come
  billable.
- `moralstack/observability/service.py::emit()`/`emit_batch()`: il hook
  dell'accumulatore (Decisione 4 del design originale) deve saltare
  `record_llm_call_usage(...)` quando `envelope.payload.get("billable_provider_call")
  is False`, altrimenti il totale sincrono esposto al proxy diverge dal totale
  ricostruito via SQL (che filtra sulla colonna). Applicare lo stesso filtro per
  singolo envelope dentro il ciclo di `emit_batch()`, non una volta per batch (un
  batch dei mini-estimatori di rischio puo contenere righe miste).

---

### BLOCKING 3 — Provenienza token assente nei dataclass runtime

**Verificato**: `moralstack.runtime.modules.*` non e sotto l'override
`mypy --strict` di `pyproject.toml:139-141` (solo `moralstack.orchestration.*` lo e)
— quindi un `Protocol` condiviso non e necessario per la tipizzazione strict; e
sufficiente un campo additivo per dataclass (diff minimo, nessuna gerarchia comune da
introdurre tra 4 moduli che oggi non ne hanno una).

**Nuovo helper in `observability/token_usage.py`** (stesso file della Decisione 1
originale):

```python
@classmethod
def from_generation_result(cls, result: Any) -> "TokenUsage":
    """Build TokenUsage from a GenerationResult-like object via getattr (tolerant
    of mocks/legacy callers lacking token_usage_source)."""
    tokens_used = int(getattr(result, "tokens_used", 0) or 0)
    prompt_tokens = getattr(result, "prompt_tokens", None)
    completion_tokens = getattr(result, "completion_tokens", None)
    source = getattr(result, "token_usage_source", None)
    if source not in ("exact", "estimated", "missing", "unknown"):
        source = "missing" if (tokens_used == 0 and prompt_tokens is None and completion_tokens is None) else "unknown"
    return cls(int(prompt_tokens or 0), int(completion_tokens or 0), tokens_used, source)

@classmethod
def combine(cls, usages: Sequence["TokenUsage"]) -> "TokenUsage":
    """Sum counts; combined source = least certain among the inputs
    (missing < unknown < estimated < exact). Empty input -> TokenUsage(0,0,0,'missing')."""
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

**Campo additivo `token_usage_source: TokenUsageSource = "unknown"` su:**

- `runtime/modules/critic_module.py::CriticReport` (dataclass righe 70-90) —
  popolato al costrutto di riga 500-515: aggiungere
  `token_usage_source=TokenUsage.from_generation_result(result).source`.
- `runtime/modules/simulator_module.py::SimulationResult` (dataclass righe 111-124) —
  popolato al costrutto raggiunto da riga 448-459 (via `self._build_result(...)`, che va
  esteso per accettare/propagare il nuovo campo).
- `runtime/modules/hindsight_module.py::HindsightResult` (dataclass righe 267-284) —
  popolato al costrutto di riga 780-789.
- `runtime/modules/perspective_module.py::PerspectiveResult` (dataclass righe 60-88) —
  popolato a riga 731-737 (`pr.token_usage_source = TokenUsage.from_generation_result(result).source`,
  stesso stile mutabile gia usato per `pr.tokens_used`/`pr.prompt_tokens` in quel blocco:
  `PerspectiveResult` non e frozen).
- `runtime/modules/perspective_module.py::EnsembleResult` (dataclass righe 155-176,
  aggregato): 2 siti di costruzione verificati (`perspective_module.py:624-634` e
  `:674-684`), entrambi sommano gia `tokens_used`/`prompt_tokens`/`completion_tokens`
  su `results: list[PerspectiveResult]`. Aggiungere
  `token_usage_source=TokenUsage.combine([TokenUsage(r.prompt_tokens or 0, r.completion_tokens or 0, r.tokens_used, r.token_usage_source) for r in results]).source`
  in entrambi i siti.

**`deliberation_runner.py::_token_usage_json_from_result`** (righe 379-392) riscritta
per usare `TokenUsage` invece di costruire il JSON a mano, leggendo il nuovo campo:

```python
def _token_usage_json_from_result(result: Any) -> str | None:
    """Build token usage json from result-like objects used by deliberative modules."""
    usage = TokenUsage.from_generation_result(result)
    return usage.to_json()  # None only when usage.source == "missing" (unchanged contract)
```

Questo mantiene il comportamento-locking del test esistente
`test_token_usage_json_builder_from_deliberative_result`
(`tests/test_orchestrator.py:1630-1642`): l'oggetto mock senza attributo `source` non
solleva `AttributeError` — `from_generation_result` gestisce l'assenza con
`getattr(..., None)` e un default esplicito (`missing`/`unknown`), esattamente come
richiesto dal test-locking gia presente nel piano originale.

**Alternativa scartata**: `Protocol HasTokenUsage` con proprieta `token_usage:
TokenUsage` comune ai 4 moduli. Scartata perche: (a) nessun beneficio di
tipizzazione dato che `runtime.modules.*` non e sotto mypy strict; (b) richiederebbe
rifattorizzare 4 dataclass non correlate per implementare esplicitamente il
protocollo (o affidarsi a duck typing strutturale, che e gia quello che `getattr()`
fa oggi — zero valore aggiunto); (c) va contro il vincolo di diff minimo
(PROJECT_SPEC paragrafo 6) per un task che non lo richiede esplicitamente.

---

### BLOCKING 4 — Refusal generation fuori scope

**Causa radice verificata**: `_llm_refusal_call` (`safe_refusal_generator.py:540-556`)
chiama `llm_client.generate(...)`, ottiene un `GenerationResult` reale (stesso tipo
usato da `policy.py`), estrae solo `.text` (riga 549) e scarta il resto — incluso
`prompt_tokens`/`completion_tokens`/`token_usage_source` che il `GenerationResult`
porta gia (dopo il fix PR2 del piano originale). `RefusalGenerationResult`
(`safe_refusal_generator.py:19-43`, frozen) non ha campi token. I 2 call site che
persistono (`response_assembler.py:304-322`, `final_revalidation.py:577-597`) non
passano mai `token_usage_json` — queste sono chiamate LLM reali (1 o 2 per
generazione di rifiuto, contate in `attempts`), quindi il gap e un vero
sotto-conteggio di token realmente consumati, non un caso "not applicable" come
BLOCKING 2.

**Fix — 3 file, stesso pattern di cattura usato per `policy.py` nel PR2 originale:**

1. `safe_refusal_generator.py::_llm_refusal_call` — cambiare il tipo di ritorno per
   portare anche l'usage del singolo round-trip:
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
2. `RefusalGenerationResult` (righe 19-43): aggiungere campo
   `token_usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0, 0, "missing"))`.
   `generate_llm_safe_refusal_detailed` (righe 559-662) accumula un `TokenUsage` per
   ogni chiamata effettivamente emessa (1 senza retry, 2 con anti-leak retry, righe
   607-639) in una lista e imposta `token_usage=TokenUsage.combine([...])` su tutti e
   3 i punti di ritorno (`llm_client is None` -> `TokenUsage(0,0,0,"missing")`, righe
   599-605; output troppo corto -> righe 646-653; successo -> righe 655-662). Un solo
   audit row per generazione di rifiuto resta il pattern esistente (`attempts` gia
   descrive quante chiamate reali sono avvenute) — non si introduce una riga
   `llm_calls` per attempt, per restare coerenti con la granularita gia scelta dal
   codice esistente per questo modulo.
3. `response_assembler.py:304-322` e `final_revalidation.py:577-597`: aggiungere
   `"token_usage_json": refusal_result.token_usage.to_json()` al dict
   `persist_kwargs` passato a `record_llm_call` in entrambi i call site.

**Nota**: nessun invariante di governance e toccato — `_llm_refusal_call` genera solo
la forma linguistica del rifiuto dopo che REFUSE e gia stato deciso da segnali
strutturati (commento esplicito in cima al file, righe 1-6); questo fix aggiunge solo
contabilita dei token, non cambia cosa il LLM riceve o decide.

---

### BLOCKING 5 — Chiave dell'accumulatore non sicura

**Verificato**: `ProcessedRequest` (`orchestration/types.py:194-207`) e un
`@dataclass` non frozen, `request_id: str = field(default_factory=lambda:
str(uuid.uuid4()))` — un default generato, ma un campo mutabile assegnabile dal
chiamante (nessun controllo di unicita a livello di processo). Lo schema SQLite usa
`(run_id, request_id)` come chiave composita: `requests` ha `PRIMARY KEY (run_id,
request_id)` (`sqlite_sink.py:123-128`), `llm_calls` referenzia `FOREIGN KEY (run_id,
request_id) REFERENCES requests (run_id, request_id)` (`sqlite_sink.py:207-216`).

**Fix**: il dict interno di `request_token_accumulator.py` e keyato dalla tupla
`(run_id, request_id)`, non da `request_id` da solo, su tutte le funzioni:

```python
_KeyT = tuple[str, str]  # (run_id, request_id)
_entries: dict[_KeyT, RequestTokenTotals] = {}
_lock = threading.Lock()

def record_llm_call_usage(run_id: str, request_id: str, token_usage_json: str | None) -> None:
    key: _KeyT = (run_id, request_id)
    ...

def pop_request_token_usage(run_id: str, request_id: str) -> RequestTokenTotals | None:
    key: _KeyT = (run_id, request_id)
    with _lock:
        return _entries.pop(key, None)

def peek_request_token_usage(run_id: str, request_id: str) -> RequestTokenTotals | None: ...
def finalize_and_persist(run_id: str, request_id: str) -> RequestTokenTotals | None: ...
def mark_speculative_pending(run_id: str, request_id: str) -> None: ...
def mark_speculative_resolved(run_id: str, request_id: str) -> None: ...
def has_pending_speculative_discard(run_id: str, request_id: str) -> bool: ...
```

Le firme pubbliche restano identiche a quelle del design originale (gia prendevano
entrambi i parametri) — cambia solo l'implementazione interna della chiave del dict.
Il cap FIFO difensivo (design originale, R2) evince/rimuove per tupla, non per
`request_id` isolato.

---

## 2. Files to modify — aggiornato (delta rispetto al piano originale)

Oltre ai file gia elencati nel piano originale, aggiungere:

- `moralstack/orchestration/safe_refusal_generator.py` — BLOCKING 4:
  `_llm_refusal_call` a `tuple[str, TokenUsage]`, `RefusalGenerationResult` +campo
  `token_usage`, propagazione nei 3 punti di ritorno di
  `generate_llm_safe_refusal_detailed`.
- `moralstack/orchestration/response_assembler.py` — BLOCKING 4: call site righe
  304-322, aggiungere `token_usage_json` al payload.
- `moralstack/orchestration/final_revalidation.py` — BLOCKING 4: call site righe
  577-597, aggiungere `token_usage_json` al payload.
- `moralstack/runtime/modules/critic_module.py` — BLOCKING 3: `CriticReport` +campo
  `token_usage_source`, popolato a riga 500-515.
- `moralstack/runtime/modules/simulator_module.py` — BLOCKING 3: `SimulationResult`
  +campo `token_usage_source`, popolato a riga 448-459 (via `_build_result`).
- `moralstack/runtime/modules/hindsight_module.py` — BLOCKING 3: `HindsightResult`
  +campo `token_usage_source`, popolato a riga 780-789.
- `moralstack/runtime/modules/perspective_module.py` — BLOCKING 3: `PerspectiveResult`
  +campo `token_usage_source` (popolato a riga 731-737), `EnsembleResult` +campo
  `token_usage_source` aggregato (2 siti: righe 624-634, 674-684).
- `moralstack/orchestration/speculative_overlap.py` — BLOCKING 1 (+fix prerequisito
  sezione 0): `abandon()` cattura esplicitamente `run_id`/`request_id`/`session_id`/
  `turn_number`/`cycle` prima di avviare `_bg`; chiamate a
  `mark_speculative_pending`/`mark_speculative_resolved`.
- `moralstack/persistence/write_queue.py` — BLOCKING 2: `async_persist_llm_call`
  propaga `billable_provider_call` nel payload (righe 51-71).
- `moralstack/orchestration/deliberation_runner.py` — BLOCKING 2 (oltre alle
  modifiche gia previste per `_token_usage_json_from_result`, righe 379-392):
  aggiungere `"billable_provider_call": False` ai 3 call site righe 2616-2639,
  2651-2674, 2716-2742.
- `moralstack/observability/read_store.py` — invariato nello scope, ma le query di
  `get_token_usage_totals`/`get_token_usage_breakdown` (design originale, Decisione 2)
  devono ora filtrare `WHERE COALESCE(billable_provider_call, 1) = 1`.
- `moralstack/persistence/db.py` — suggerimento non bloccante di Codex, a basso
  costo: `get_llm_calls_for_request` e gia specchiato qui (righe 76-77, delega a
  `_rs`); se si aggiungono `get_token_usage_totals`/`get_token_usage_breakdown` a
  `read_store.py`, specchiarli anche qui con lo stesso pattern per coerenza con
  l'API di lettura esistente.

Nessun file tra quelli gia elencati nel piano originale viene rimosso dallo scope.

---

## 3. Risks — aggiornato (aggiunte, il resto invariato)

- **R8 — timing speculative/finalizzazione (BLOCKING 1).** Il meccanismo a contatore
  copre correttamente i 3 path normali citati da Codex (`controller.py:2527-2528,
  2552-2553, 2592-2594`). Rimane un gap residuo, esplicitamente accettato, sui path
  eccezione di `process()` (`:2613-2678`) dove `shutdown_executor()` chiama
  `abandon()` in `finally:` dopo che `_finalize_token_accounting` e gia girato —
  mitigato solo dalla query di ricostruzione via `llm_calls`, non eliminato. Blast
  radius: solo richieste che terminano in timeout/errore dell'orchestratore (gia un
  path degradato); nessun impatto sul valore di `usage` restituito al client in quei
  casi (resta `{0,0,0}` o parziale, mai un crash).
- **R9 — discriminatore billable/non-billable disallineato tra accumulatore
  in-process e query SQL (BLOCKING 2).** Se il filtro `billable_provider_call` viene
  applicato in un solo posto (es. solo nella query SQL ma non nell'hook di
  `emit()`), il totale sincrono esposto al proxy diverge silenziosamente dal totale
  ricostruibile offline. Mitigazione: stesso predicato (`COALESCE(billable_provider_call,
  1) = 1` lato SQL, controllo equivalente su `envelope.payload.get("billable_provider_call",
  True)` lato accumulatore) applicato nei due punti nello stesso PR, con un test di
  parita esplicito (vedi acceptance criteria).
- **R10 — propagazione incompleta di `token_usage_source` nei moduli runtime
  (BLOCKING 3).** Rischio che un quinto sito di costruzione (oggi non individuato) di
  uno dei 4 dataclass rimanga scoperto. Mitigazione: `TokenUsage.from_generation_result`
  e centralizzato in un solo helper (non ri-duplicato per modulo, stesso principio
  DRY gia applicato al fallback 70/30 nel design originale) — un audit grep su
  `getattr(result, "tokens_used"` nei 4 file individua ogni sito residuo in modo
  meccanico prima del merge.
- **R11 — performance dell'accumulatore sotto carico dei mini-estimatori di rischio
  (non bloccante da Codex, incorporato).** `models/risk/estimator.py::_persist_mini_llm_calls_batch`
  (righe 703-710) invia batch multi-envelope via `_obs_route_batch` (righe 71-75) ->
  `ObservabilityService.emit_batch()` (`service.py:50-57`). L'hook dell'accumulatore
  deve iterare ogni envelope del batch e fare un parsing JSON breve per ciascuno
  sotto lock — costo O(N) per batch, non O(1) come nel caso `emit()` singolo.
  Mitigazione: lock a grana fine (solo increment di contatori, mai I/O dentro la
  sezione critica), validare con un test di carico sintetico (N=50-100 envelope per
  batch) prima del merge, non solo in produzione.
- **Correzione al framing del rischio proxy/governed-delivery (suggerimento Codex,
  incorporato).** Il rischio reale di questo intero piano e audit correctness (il
  campo `usage` riportato al client/agli export di analytics puo essere impreciso),
  non "delivered-answer provenance": il proxy non chiama mai l'upstream per generare
  la risposta consegnata (`.claude/rules/governed-delivery.md`,
  `orchestration/delivery.py::finalize_delivery`) — cambia solo il valore del campo
  `usage` nel payload sintetico gia esistente. Nessun conflitto con l'invariante
  Governed-delivery-only in nessuno dei 5 fix sopra.

---

## 4. Assumptions — aggiornato

- **A2 (corretta — era falsa come scritta nel piano originale).** L'affermazione
  originale ("`request_id` e generato una volta per chiamata a `.process()` ed e
  univoco a livello di processo") non regge: `ProcessedRequest.request_id`
  (`orchestration/types.py:194,198`) e un campo mutabile con un default generato
  (`uuid.uuid4()`), non garantito univoco da solo — un chiamante puo fornire un
  `request_id` esplicito (es. correlazione con un ID esterno), e lo schema SQLite usa
  `(run_id, request_id)` come chiave composita (`sqlite_sink.py:99-136, 207-216`),
  non `request_id` da solo. Per questo l'accumulatore usa la tupla `(run_id,
  request_id)` come chiave (BLOCKING 5), non `request_id` isolato.
- **A4 (corretta — vera solo come garanzia best-effort, non assoluta).**
  L'affermazione originale ("nel path proxy il contesto di persistenza
  (`proxy_run_id`) e sempre attivo") non e assoluta:
  `_initialize_observability_run()` (`server/proxy.py:617-654`) ritorna stringa
  vuota quando l'osservabilita non e configurata (nessun `db_path` e modalita
  diversa da `file_only`/`db_only`/`dual`, righe 647-649) — in quel caso
  `set_current_run_id()` (riga 650) non viene mai chiamato. `_ensure_request_row`
  (righe 657-681) ritorna immediatamente senza persistere nulla quando
  `proxy_run_id` e vuoto (righe 680-681). Il design del token accounting deve
  restare corretto (nessun crash, campo `usage` a `{0,0,0}` di default) anche in
  questa configurazione — gia garantito dai guardrail `if not run_id or not
  request_id: return` presenti in tutte le funzioni dell'accumulatore, ma va
  esplicitato come assunzione verificata, non presunta assoluta.

Tutte le altre assunzioni (A1, A3, A5) restano invariate rispetto al piano originale.

---

## 5. Acceptance criteria — aggiornato (aggiunte, il resto invariato)

- [ ] Una richiesta che imbocca il path refuse/safe_complete/deliberativo con
      `constrained_generation=True` e che ha una speculative draft ancora in corso al
      momento del routing produce, dopo il completamento in background, una riga
      `llm_calls` con `call_outcome="discarded"` e quella riga e inclusa nel totale
      di `request_token_usage` per quella richiesta (non richiede una seconda
      richiesta/turno per apparire — verificato con `threading.Barrier` o `Future`
      controllato nel test, non `time.sleep`).
- [ ] Le righe sintetiche/diagnostiche (speculative-reuse, SKIPPED_EMPTY_GUIDANCE,
      output_protection/leakage_detected) compaiono in `llm_calls` con
      `billable_provider_call=0` e sono escluse sia dal totale sincrono
      (`ResponseMetadata`/`usage` del proxy) sia dalla query di breakdown per
      modulo/modello.
- [ ] Il breakdown per modulo (`get_token_usage_breakdown`) riporta
      `token_usage_source` corretta e non-default per critic, simulator,
      perspectives, hindsight su un run di prova con mock che restituiscono
      esplicitamente `token_usage_source="exact"`/`"estimated"`.
- [ ] Una generazione di rifiuto (REFUSE) — sia dal path deliberativo
      (`response_assembler.py`) sia dal path di final revalidation
      (`final_revalidation.py`) — produce una riga `llm_calls` con
      `token_usage_json` non-NULL quando il policy LLM e disponibile; con retry
      anti-leak (2 chiamate), il totale riportato e la somma delle due chiamate, non
      solo dell'ultima.
- [ ] Test di parita esplicito: per una richiesta con un mix di righe billable e
      non-billable, `SUM(...) FROM llm_calls WHERE COALESCE(billable_provider_call,1)=1`
      e uguale al totale sincrono esposto in `ResponseMetadata`/`usage` del proxy per
      la stessa richiesta.
- [ ] Nessuna riga storica (pre-migrazione, `billable_provider_call IS NULL`) viene
      esclusa per errore dai totali ricostruiti (verificato con `COALESCE(...,1)`).

Tutti gli acceptance criteria del piano originale restano validi e non sostituiti.

---

## Nota metodologica

Ogni riferimento path:line in questa revisione e stato ri-verificato leggendo il
file reale in questa sessione (non solo riportato da Codex); dove i numeri di riga
del report Codex erano leggermente stale rispetto a `main`, sono stati corretti. La
scoperta di sezione 0 (perdita di `run_id`/`request_id` nel thread daemon di
`abandon()`) non era presente ne nel piano originale ne nel report di Codex — va
aggiunta come nota metodologica anche nel documento principale, coerente con
PROJECT_SPEC paragrafo 9 (documentare un difetto scoperto fuori scope invece di
correggerlo silenziosamente altrove).
