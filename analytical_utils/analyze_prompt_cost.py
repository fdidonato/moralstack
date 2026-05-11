#!/usr/bin/env python3
"""
analyze_prompt_costs.py — Misura il costo per token (statico vs dinamico) di
ogni modulo della pipeline MoralStack, leggendo dal DB SQLite di observability.

USO:

    # Sull'ultimo run di tipo benchmark
    python scripts/analyze_prompt_costs.py

    # Su un run specifico
    python scripts/analyze_prompt_costs.py --run-id <run_id>

    # Lista i run disponibili
    python scripts/analyze_prompt_costs.py --list-runs

    # Specifica un DB diverso da $MORALSTACK_OBSERVABILITY_DB_PATH
    python scripts/analyze_prompt_costs.py --db /path/to/moralstack.db

    # Output in JSON invece che tabella
    python scripts/analyze_prompt_costs.py --json

OUTPUT:

    Tabella per modulo + phase con:
      - n_calls       : numero di chiamate LLM
      - tok_sys_avg   : token medi system_prompt
      - tok_usr_avg   : token medi user prompt (= dinamico, l'interessante)
      - tok_in_total  : input totali nella colonna (sys + usr) × n_calls
      - tok_out_avg   : token output medi (raw_response)
      - duration_avg  : durata media in ms
      - retries_total : retry accumulati
      - cost_share    : % del costo input totale di tutto il run

OBIETTIVO:

    Identificare dove la pipeline spende davvero token.
    Aspettative dall'analisi statica:
      - risk_estimation 3 mini-call ~6.800 tok/query (post v3 prompts)
      - critic, hindsight, perspective: prompt statici piccoli ma il
        DRAFT replicato fa il volume — questo lo script lo misura.

PRINCIPIO:

    system_prompt è lo statico. user prompt è dinamico (request, draft,
    constitution_context, risk_signals iniettati).
    La differenza media usr - sys riflette il "draft replication cost".
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ────────────────────────────────────────────────────────────────────────────
# Token counting (preferisce token_usage_json reale, fallback tiktoken,
# ultimo fallback chars/4)
# ────────────────────────────────────────────────────────────────────────────

_TIKTOKEN_ENC: Any = None


def _get_tiktoken_encoder() -> Any:
    """Carica tiktoken pigramente. Nessun errore se mancante."""
    global _TIKTOKEN_ENC
    if _TIKTOKEN_ENC is not None:
        return _TIKTOKEN_ENC
    try:
        import tiktoken  # type: ignore[import-not-found]

        _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _TIKTOKEN_ENC = False  # marker: unavailable
    return _TIKTOKEN_ENC


def count_tokens(text: str | None) -> int:
    """Conta token con tiktoken; fallback chars/4."""
    if not text:
        return 0
    enc = _get_tiktoken_encoder()
    if enc and enc is not False:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


# ────────────────────────────────────────────────────────────────────────────
# DB resolution
# ────────────────────────────────────────────────────────────────────────────


def resolve_db_path(explicit: str | None) -> Path:
    """Risolve il path del DB con la stessa precedenza dell'osservability."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            sys.exit(f"DB non trovato: {p}")
        return p

    for env in ("MORALSTACK_OBSERVABILITY_DB_PATH", "MORALSTACK_DB_PATH"):
        v = os.getenv(env, "").strip()
        if v:
            p = Path(v).expanduser()
            if p.exists():
                return p
            sys.exit(f"DB indicato in ${env} non esiste: {p}")

    # Fallback ai path comuni
    for candidate in (
        Path.cwd() / "moralstack.db",
        Path.cwd() / "data" / "moralstack.db",
        Path.cwd() / "logs" / "observability" / "moralstack.db",
    ):
        if candidate.exists():
            return candidate

    sys.exit("DB non trovato. Specifica con --db o imposta " "MORALSTACK_OBSERVABILITY_DB_PATH.")


# ────────────────────────────────────────────────────────────────────────────
# Run selection
# ────────────────────────────────────────────────────────────────────────────


def list_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("""
        SELECT
            r.run_id, r.run_type, r.started_at, r.ended_at, r.status,
            (SELECT COUNT(*) FROM requests   WHERE run_id = r.run_id) AS n_requests,
            (SELECT COUNT(*) FROM llm_calls  WHERE run_id = r.run_id) AS n_llm_calls
        FROM runs r
        ORDER BY r.started_at DESC
            LIMIT 30
        """)
    return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]


def latest_benchmark_run(conn: sqlite3.Connection) -> str | None:
    """Trova l'ultimo run di tipo benchmark (o l'ultimo qualsiasi se niente benchmark)."""
    cur = conn.execute("""
        SELECT run_id FROM runs
        WHERE LOWER(run_type) LIKE '%benchmark%'
        ORDER BY started_at DESC LIMIT 1
        """)
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


# ────────────────────────────────────────────────────────────────────────────
# Core analysis
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class CallStats:
    """Statistiche aggregate per un modulo+phase+action."""

    module: str
    phase: str
    action: str
    n_calls: int = 0
    sys_tokens: list[int] = None  # type: ignore[assignment]
    usr_tokens: list[int] = None  # type: ignore[assignment]
    out_tokens: list[int] = None  # type: ignore[assignment]
    in_tokens_real: list[int] = None  # type: ignore[assignment]  # da token_usage_json
    out_tokens_real: list[int] = None  # type: ignore[assignment]
    durations: list[float] = None  # type: ignore[assignment]
    retries: int = 0
    errors: int = 0

    def __post_init__(self) -> None:
        for f in ("sys_tokens", "usr_tokens", "out_tokens", "in_tokens_real", "out_tokens_real", "durations"):
            if getattr(self, f) is None:
                setattr(self, f, [])

    @staticmethod
    def _avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def avg_sys(self) -> float:
        return self._avg(self.sys_tokens)

    def avg_usr(self) -> float:
        return self._avg(self.usr_tokens)

    def avg_out(self) -> float:
        return self._avg(self.out_tokens)

    def avg_in_real(self) -> float:
        return self._avg(self.in_tokens_real)

    def avg_out_real(self) -> float:
        return self._avg(self.out_tokens_real)

    def avg_dur(self) -> float:
        return self._avg(self.durations)

    def total_in(self) -> float:
        return (self.avg_sys() + self.avg_usr()) * self.n_calls

    def total_out(self) -> float:
        return self.avg_out() * self.n_calls


def _extract_real_tokens(tu_json: str | None) -> tuple[int | None, int | None]:
    """Estrae (input_tokens, output_tokens) dal token_usage_json di OpenAI."""
    if not tu_json:
        return None, None
    try:
        d = json.loads(tu_json)
    except (ValueError, TypeError):
        return None, None
    # Diversi shape supportati: dict OpenAI standard {prompt_tokens, completion_tokens}
    # o {input_tokens, output_tokens} di altri provider.
    in_tok = d.get("prompt_tokens") or d.get("input_tokens")
    out_tok = d.get("completion_tokens") or d.get("output_tokens")
    return (
        int(in_tok) if isinstance(in_tok, (int, float)) else None,
        int(out_tok) if isinstance(out_tok, (int, float)) else None,
    )


def analyze_run(conn: sqlite3.Connection, run_id: str) -> dict[str, CallStats]:
    """Aggrega tutte le llm_calls per (module, phase, action)."""
    cur = conn.execute(
        """
        SELECT module, phase, action, system_prompt, prompt, raw_response,
            token_usage_json, duration_ms, attempts, error
        FROM llm_calls
        WHERE run_id = ?
        ORDER BY started_at
        """,
        (run_id,),
    )

    buckets: dict[str, CallStats] = {}
    for row in cur.fetchall():
        module, phase, action, sys_p, usr_p, raw, tu, dur, attempts, err = row
        # Bucketing key: module+phase+action
        # action distingue i 3 mini-estimator (intent/signals/operational)
        # quando il phase è "risk_estimation"
        key = f"{module}/{phase}/{action}"
        b = buckets.setdefault(key, CallStats(module=module, phase=phase, action=action))
        b.n_calls += 1
        b.sys_tokens.append(count_tokens(sys_p))
        b.usr_tokens.append(count_tokens(usr_p))
        b.out_tokens.append(count_tokens(raw))
        if dur is not None:
            b.durations.append(float(dur))
        if attempts and attempts > 1:
            b.retries += int(attempts) - 1
        if err:
            b.errors += 1
        # Token reali da OpenAI quando disponibili
        in_real, out_real = _extract_real_tokens(tu)
        if in_real is not None:
            b.in_tokens_real.append(in_real)
        if out_real is not None:
            b.out_tokens_real.append(out_real)

    return buckets


# ────────────────────────────────────────────────────────────────────────────
# Reporting
# ────────────────────────────────────────────────────────────────────────────


def request_count(conn: sqlite3.Connection, run_id: str) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM requests WHERE run_id = ?", (run_id,))
    return int(cur.fetchone()[0])


def print_table(buckets: dict[str, CallStats], n_requests: int) -> None:
    rows = sorted(buckets.values(), key=lambda b: -b.total_in())
    grand_in = sum(b.total_in() for b in rows) or 1.0
    grand_in_real = sum(b.avg_in_real() * b.n_calls for b in rows if b.in_tokens_real)

    # real_label = " (real)" if grand_in_real > 0 else ""
    print()
    print(f"  Requests in this run : {n_requests}")
    print(f"  LLM calls total       : {sum(b.n_calls for b in rows)}")
    print(f"  Calls per request avg : {sum(b.n_calls for b in rows) / max(1, n_requests):.1f}")
    print(f"  Token estimator       : {'tiktoken (cl100k_base)' if _get_tiktoken_encoder() else 'chars/4 fallback'}")
    if grand_in_real > 0:
        print(f"  Real input tokens     : {grand_in_real:,.0f} (from token_usage_json)")
    print()
    print("─" * 122)
    print(
        f"{'MODULE / PHASE / ACTION':<48}"
        f"{'calls':>7}"
        f"{'sys_avg':>9}"
        f"{'usr_avg':>9}"
        f"{'out_avg':>9}"
        f"{'in_total':>10}"
        f"{'%share':>9}"
        f"{'dur_avg':>10}"
        f"{'retry':>7}"
    )
    print("─" * 122)

    for b in rows:
        share = (b.total_in() / grand_in) * 100.0
        label = f"{b.module}/{b.phase}"
        if b.action and b.action not in ("estimate", "generate", ""):
            label += f"/{b.action}"
        if len(label) > 47:
            label = label[:44] + "..."
        print(
            f"{label:<48}"
            f"{b.n_calls:>7}"
            f"{b.avg_sys():>9.0f}"
            f"{b.avg_usr():>9.0f}"
            f"{b.avg_out():>9.0f}"
            f"{b.total_in():>10,.0f}"
            f"{share:>8.1f}%"
            f"{b.avg_dur():>9.0f}ms"
            f"{b.retries:>7}"
        )

    print("─" * 122)
    total_in = sum(b.total_in() for b in rows)
    total_out = sum(b.total_out() for b in rows)
    total_calls = sum(b.n_calls for b in rows)
    total_dur = sum(b.avg_dur() * b.n_calls for b in rows)
    print(
        f"{'TOTAL':<48}"
        f"{total_calls:>7}"
        f"{'':>9}{'':>9}{'':>9}"
        f"{total_in:>10,.0f}"
        f"{'':>9}"
        f"{total_dur:>9,.0f}ms"
    )
    print()
    print(f"  Estimated input  / request : {total_in / max(1, n_requests):>10,.0f} tok")
    print(f"  Estimated output / request : {total_out / max(1, n_requests):>10,.0f} tok")
    print(f"  Estimated wall-time / req  : {total_dur / max(1, n_requests):>10,.0f} ms")
    print()


def print_replication_analysis(buckets: dict[str, CallStats], n_requests: int) -> None:
    """Sezione speciale: quanto pesa la replicazione del draft tra moduli post-policy."""
    # Assumo che il "draft" sia ciò che pasa per critic, hindsight, perspectives
    # La sua dimensione si stima dal raw_response medio di policy_generate
    policy_out_tokens = []
    for b in buckets.values():
        if b.phase in ("policy_generate", "policy_rewrite", "speculative_generate"):
            policy_out_tokens.extend(b.out_tokens)
    if not policy_out_tokens:
        return
    avg_draft = sum(policy_out_tokens) / len(policy_out_tokens)

    downstream_calls = 0
    downstream_in_tokens = 0
    for b in buckets.values():
        if b.phase in ("critic", "hindsight", "perspectives"):
            downstream_calls += b.n_calls
            downstream_in_tokens += int(b.total_in())
    if downstream_calls == 0:
        return

    # Calls per request medio
    calls_per_req = downstream_calls / max(1, n_requests)
    replicated_share = (avg_draft * downstream_calls / max(1, downstream_in_tokens)) * 100.0
    # Saving = draft × (calls - 1) per query, perché 1 occorrenza è inevitabile (shared system)
    saving_per_query = avg_draft * max(0, calls_per_req - 1)
    saving_total = saving_per_query * n_requests

    print("  ── DRAFT REPLICATION ANALYSIS ──")
    print(f"    Avg draft size (policy output)         : ~{avg_draft:>8,.0f} tok")
    print(f"    Downstream calls per request           : {calls_per_req:>9.1f}  (critic+hindsight+perspective)")
    print(f"    Draft-replication share of their input : ~{replicated_share:>8.0f} %")
    print(f"    Theoretical saving per query if shared : ~{saving_per_query:>8,.0f} tok/query")
    print(f"    Theoretical saving for full run        : ~{saving_total:>8,.0f} tok across {n_requests} requests")
    print()


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze MoralStack DB for prompt costs per module.")
    parser.add_argument("--db", help="Override DB path")
    parser.add_argument("--run-id", help="Specific run_id (default: latest benchmark)")
    parser.add_argument("--list-runs", action="store_true", help="List available runs")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if args.list_runs:
            runs = list_runs(conn)
            if args.json:
                print(json.dumps(runs, indent=2, default=str))
            else:
                print(f"\n  Last {len(runs)} runs in {db_path}:\n")
                print(f"  {'run_id':<40}{'type':<14}{'requests':>10}{'llm_calls':>11}{'status':>10}")
                print("  " + "─" * 85)
                for r in runs:
                    rid = (r["run_id"] or "")[:38]
                    print(
                        f"  {rid:<40}"
                        f"{(r['run_type'] or '')[:13]:<14}"
                        f"{r['n_requests']:>10}"
                        f"{r['n_llm_calls']:>11}"
                        f"{(r['status'] or '')[:9]:>10}"
                    )
                print()
            return 0

        run_id = args.run_id or latest_benchmark_run(conn)
        if not run_id:
            sys.exit("Nessun run trovato. Esegui un benchmark prima.")

        buckets = analyze_run(conn, run_id)
        if not buckets:
            sys.exit(f"Run {run_id} non ha llm_calls registrate.")

        n_req = request_count(conn, run_id)

        if args.json:
            out = {
                "db_path": str(db_path),
                "run_id": run_id,
                "n_requests": n_req,
                "buckets": [
                    {
                        "module": b.module,
                        "phase": b.phase,
                        "action": b.action,
                        "n_calls": b.n_calls,
                        "sys_tok_avg": round(b.avg_sys(), 1),
                        "usr_tok_avg": round(b.avg_usr(), 1),
                        "out_tok_avg": round(b.avg_out(), 1),
                        "in_tok_real_avg": round(b.avg_in_real(), 1) if b.in_tokens_real else None,
                        "out_tok_real_avg": round(b.avg_out_real(), 1) if b.out_tokens_real else None,
                        "duration_ms_avg": round(b.avg_dur(), 1),
                        "retries": b.retries,
                        "errors": b.errors,
                        "in_tok_total": int(b.total_in()),
                    }
                    for b in sorted(buckets.values(), key=lambda x: -x.total_in())
                ],
            }
            print(json.dumps(out, indent=2))
            return 0

        print()
        print(f"  DB                : {db_path}")
        print(f"  Run id            : {run_id}")
        print_table(buckets, n_req)
        print_replication_analysis(buckets, n_req)

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
