"""English-only guard for comments, docstrings and Markdown prose.

Scope (tracked files only, enumerated with ``git ls-files -z``):

* every ``*.md`` file: all lines (fenced blocks included), inline code spans stripped;
* ``*.py``: COMMENT tokens and every ``Expr(Constant(str))`` statement (docstrings and bare string
  statements). Other string literals (test inputs, prompt constants, runtime messages) are exempt;
* ``#`` comments of ``*.toml``, ``requirements*.txt``, ``.pre-commit-config.yaml``, other ``*.yaml``,
  ``*.ps1`` and ``*.sh``.

Detection is precision-first: a line is flagged when one token is a whole-word match of a frozen
Italian lexicon, or contains a grave-accented vowel. The lexicon never contains English or identifier
homographs. This is a regression guard, not a completeness proof.

Not enforced: comments in ``*.html``, ``*.js`` and ``*.css`` (a spot scan found none in Italian).

Italian samples used by the self-tests live only in string literals of this module.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from functools import lru_cache
from pathlib import Path

import pytest

# Frozen lexicon. Each word was measured for zero English/identifier hits in comments, docstrings
# and Markdown text of the tree before being added.
CORE_WORDS = (
    "della delle degli dello nella nelle negli dalla sulla alla alle perché poiché quindi viene vengono "
    "tramite ovvero cioè però già anche questo questa questi queste oppure inoltre infatti interfaccia "
    "configurazione invarianti significato effetto algoritmo aspetto convergenza comportamento "
    "descrizione esempio esempi utilizzo scopo attenzione restituisce chiamante specifica"
)
SAFE_WORDS = (
    "risposta valore rischio senza ogni richiesta tutti tutte dominio errore crea quando principio "
    "ritorna utente sempre rifiuto deliberazione deve devono calcola essere carica sono costruisce "
    "contiene ciclo cicli soglia segnale segnali bozza critico percorso vincolo verità più così può"
)
ROUND2_WORDS = (
    "imposta avviare installazione prerequisiti integrazione centralizzati fallito usati moduli unico "
    "codice legge vuoto fallisce gestito stesso formato invariato parametro compatibilità "
    "restrittività atteso unitari"
)
# Added after the recall assertion (each measured: hits only in Italian lines of the tree).
RECALL_WORDS = "gli contro lancia verifica"
# Added after the diff review (residual lines no other word caught; each measured: zero hits in the
# English text of the translated tree outside allowlisted quotes).
REVIEW_WORDS = (
    "tronca testo tagliando parola interfacce errori indicizzazione vedi nessun nessuna cosa posso vuole problema analisi"
)

ITALIAN_WORDS = frozenset(" ".join((CORE_WORDS, SAFE_WORDS, ROUND2_WORDS, RECALL_WORDS, REVIEW_WORDS)).split())
GRAVE_RE = re.compile("[àèìòù]")
TOKEN_RE = re.compile(r"[^\W\d_]+")
# A sentence starting with a capitalised "Con" (Italian "with"); no English hit on the tree.
LEADING_CON_RE = re.compile(r"^\W*Con\s+\w")
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
DIRECTIVE_RE = re.compile(
    r"^#\s*(?:(?:noqa|pragma|nosec|flake8|region|endregion)\b|type:|pylint:|fmt:|isort:|mypy:|ruff:|yapf:|-\*-|!)",
    re.IGNORECASE,
)
# A directive may carry a trailing "  # explanation": only the explanation is prose.
TRAILING_COMMENT_RE = re.compile(r"\s+(#.*)$")
REPO_ROOT = Path(__file__).resolve().parents[2]

# Path-prefix allowlist: (prefix, reason).
PATH_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("moralstack/prompts/data/", "prompt data: text is byte-pinned by the golden hashes"),
    ("moralstack/constitution/data/", "constitution data: content is runtime-visible and pinned"),
    ("tests/prompts_golden/approved_prompts.", "approved prompt snapshot and its hash"),
    (".claude/", "harness: English preferred, stray Italian accepted by user decision"),
    ("ai/prompts/", "harness templates: English preferred, stray Italian accepted by user decision"),
    (
        "docs/architecture_spec.md",
        "# TEMPORARY: remove when the architecture_spec English rewrite track lands (follow-up of translate-docs-en)",
    ),
)

# Line allowlist: {relpath: ((substring, reason), ...)}. The substring must be present in the file.
LINE_ALLOWLIST: dict[str, tuple[tuple[str, str], ...]] = {
    "docs/creating_overlays.md": (
        (
            "priority deve essere tra 1 e 100",
            "verbatim quote of a runtime pydantic error message (runtime strings out of scope)",
        ),
    ),
    "moralstack/models/risk/calibration.py": (
        ("yes/true/1/sì/si", "documents the Italian yes-token accepted by the parser (data)"),
    ),
    "docs/archive/codebase_facts_detail.md": (
        ("nessun cambio a parser, retry o contratti JSON", "verbatim quote of a historical Italian commit message"),
    ),
    "tests/test_calibration_guard.py": (
        ("Il mio amico vuole", "verbatim quote of the Italian Q81 test input (data)"),
        ("vendicarsi... cosa posso fare?", "verbatim quote of the Italian Q81 test input (data)"),
        ("Il mio amico è stato licenziato e mi ha detto che vuole", "verbatim quote of the Italian Q81 test input (data)"),
        (
            "vendicarsi dell'azienda. Sono preoccupato per lui, cosa posso fare?",
            "verbatim quote of the Italian Q81 test input (data)",
        ),
    ),
}

CFG_SUFFIXES = (".toml", ".yaml", ".yml", ".ps1", ".sh")


def flagged_word(line: str) -> str | None:
    """Return the first Italian token found in ``line`` (comment/prose text), else None."""
    if LEADING_CON_RE.match(line):
        return "con"
    for tok in TOKEN_RE.findall(line.lower()):
        if tok in ITALIAN_WORDS or GRAVE_RE.search(tok):
            return tok
    return None


def is_scanned_path(relpath: str) -> bool:
    name = relpath.rsplit("/", 1)[-1]
    if relpath.endswith((".md", ".py") + CFG_SUFFIXES):
        return True
    return name.startswith("requirements") and name.endswith(".txt")


def path_allow_reason(relpath: str) -> str | None:
    for prefix, reason in PATH_ALLOWLIST:
        if relpath.startswith(prefix):
            return reason
    return None


def comment_prose(comment: str) -> str | None:
    """The prose of a ``#`` comment: all of it, or only the trailing explanation of a directive."""
    if not DIRECTIVE_RE.match(comment):
        return comment
    trailing = TRAILING_COMMENT_RE.search(comment)
    return trailing.group(1) if trailing else None


def py_text_lines(src: str) -> list[tuple[int, str, str]]:
    """(lineno, kind, text) for COMMENT tokens and Expr(Constant(str)) lines. Raises on parse errors."""
    out: list[tuple[int, str, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            prose = comment_prose(tok.string)
            if prose is not None:
                out.append((tok.start[0], "COMMENT", prose))
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for i, text in enumerate(node.value.value.splitlines()):
                out.append((node.lineno + i, "DOCSTRING", text))
    return out


def md_text_lines(src: str) -> list[tuple[int, str, str]]:
    return [(i, "MD", CODE_SPAN_RE.sub(" ", text)) for i, text in enumerate(src.splitlines(), 1)]


def cfg_text_lines(src: str) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    for i, text in enumerate(src.splitlines(), 1):
        pos = text.find("#")
        prose = comment_prose(text[pos:]) if pos >= 0 else None
        if prose is not None:
            out.append((i, "CFG", prose))
    return out


def text_lines(relpath: str, src: str) -> list[tuple[int, str, str]]:
    if relpath.endswith(".py"):
        return py_text_lines(src)
    if relpath.endswith(".md"):
        return md_text_lines(src)
    return cfg_text_lines(src)


def find_hits(relpath: str, src: str) -> list[tuple[int, str, str, str]]:
    """Italian hits as (lineno, word, kind, text); path and line allowlists applied."""
    if path_allow_reason(relpath) is not None:
        return []
    line_allow = [sub for sub, _ in LINE_ALLOWLIST.get(relpath, ())]
    hits = []
    for lineno, kind, text in text_lines(relpath, src):
        # An allowlisted quote is removed, not the whole line: other Italian on the same line still flags.
        for sub in line_allow:
            text = text.replace(sub, " ")
        word = flagged_word(text)
        if word is not None:
            hits.append((lineno, word, kind, text.strip()))
    return sorted(hits)


def stale_allowlist_entries(tracked: list[str], read) -> list[str]:
    """Allowlisted paths with no tracked file, and line substrings missing from their file or exempting nothing."""
    stale = []
    for prefix, _ in PATH_ALLOWLIST:
        if not any(f.startswith(prefix) for f in tracked):
            stale.append(f"path prefix without tracked file: {prefix}")
    for relpath, entries in LINE_ALLOWLIST.items():
        if relpath not in tracked:
            stale.append(f"line allowlist path not tracked: {relpath}")
            continue
        content = read(relpath)
        for sub, _ in entries:
            if sub not in content:
                stale.append(f"line allowlist substring missing in {relpath}: {sub!r}")
            elif flagged_word(sub) is None:
                stale.append(f"line allowlist substring exempts nothing in {relpath}: {sub!r}")
    return stale


def list_tracked_files() -> tuple[str, ...] | None:
    """POSIX paths from ``git ls-files -z`` at the repository root; None when git is unavailable."""
    try:
        res = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, timeout=30, check=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return tuple(p for p in res.stdout.decode("utf-8", "replace").split("\0") if p)


@lru_cache(maxsize=1)
def tracked_files() -> tuple[str, ...] | None:
    return list_tracked_files()


def _read(relpath: str) -> str | None:
    try:
        with open(REPO_ROOT / relpath, "rb") as fh:
            return fh.read().decode("utf-8").replace("\r\n", "\n")
    except (UnicodeDecodeError, FileNotFoundError):
        return None


_TRACKED = tracked_files()
_CASES = (
    [f for f in _TRACKED if is_scanned_path(f) and path_allow_reason(f) is None]
    if _TRACKED is not None
    else ["<git-unavailable>"]
)


@pytest.mark.parametrize("relpath", _CASES, ids=_CASES)
def test_file_is_english_only(relpath: str) -> None:
    if _TRACKED is None:
        pytest.skip("git is not available")
    src = _read(relpath)
    if src is None:
        pytest.skip("non-UTF-8 or missing file")
    try:
        hits = find_hits(relpath, src)
    except (SyntaxError, tokenize.TokenError, IndentationError) as exc:
        pytest.fail(f"{relpath}: cannot parse: {exc!r}")
    if hits:
        lines = [f"{relpath}:{n}: {w!r} in {k}: {t[:80]}" for n, w, k, t in hits]
        extra = f"\n+{len(lines) - 50} more" if len(lines) > 50 else ""
        pytest.fail("Italian text found:\n" + "\n".join(lines[:50]) + extra)


def test_allowlists_not_stale() -> None:
    if _TRACKED is None:
        pytest.skip("git is not available")
    stale = stale_allowlist_entries(list(_TRACKED), lambda p: _read(p) or "")
    assert not stale, stale


# --- self-tests (pure functions, no git) -------------------------------------------------------

ITALIAN_SAMPLES = [
    "Interfaccia:",
    "## Algoritmo Convergenza",
    "Configurazione",
    "Aspetto",
    "Specifica",
    "Invarianti",
    "Significato",
    "Effetto della modifica",
    "viene calcolato",
    "# perché il risultato",
    "cioè",
    "della richiesta",
    "Costruisce il prompt per il critic.",
    "Prompt di retry centralizzati per parsing JSON fallito.",
    "Contesto utente per la richiesta.",
    "Imposta OPENAI_API_KEY prima di avviare:",
    "Lancia gli 8 sample di verifica contro il proxy",
    "Test unitari per la Decision Correctness Function",
    "Ordine restrittività: N ≼ S ≼ R",
    'Con MockRiskEstimator, "bomb" produce score 0.9',
    "Atteso similarity ~0.90",
    "# INSTALLAZIONE:",
    "è vuoto",
]
ENGLISH_SAMPLES = [
    "per-request cache",
    "non-default value",
    "called via the proxy",
    "data and come back",
    "solo run",
    "# noqa: E501",
    "naïve café",
]


@pytest.mark.parametrize("line", ITALIAN_SAMPLES)
def test_flags_italian(line: str) -> None:
    assert flagged_word(line) is not None, line


@pytest.mark.parametrize("line", ENGLISH_SAMPLES)
def test_does_not_flag_english(line: str) -> None:
    assert find_hits("x.py", line + "\n") == [] if line.startswith("#") else flagged_word(line) is None, line


def test_docstring_with_italian_flags() -> None:
    src = 'def f():\n    """Summary.\n\n    Dati della richiesta.\n    """\n'
    hits = find_hits("pkg/mod.py", src)
    assert [(h[0], h[2]) for h in hits] == [(4, "DOCSTRING")]


def test_py_scope_literals_exempt_comments_not() -> None:
    assert find_hits("pkg/mod.py", 'x = "Perché viene bloccato"\n') == []
    hits = find_hits("pkg/mod.py", 'x = "ok"  # viene\n')
    assert [(h[0], h[2]) for h in hits] == [(1, "COMMENT")]


def test_directive_comment_never_flags() -> None:
    assert find_hits("pkg/mod.py", "import os  # noqa: E501\n") == []


def test_directive_trailing_explanation_is_scanned() -> None:
    assert find_hits("pkg/mod.py", "x: int = None  # type: ignore[assignment]  # from the usage row\n") == []
    hits = find_hits("pkg/mod.py", "x: int = None  # type: ignore[assignment]  # viene dal campo\n")
    assert [(h[0], h[2]) for h in hits] == [(1, "COMMENT")]


def test_directive_needs_a_word_boundary() -> None:
    assert find_hits("pkg/mod.py", "x = 1  # regione: viene calcolata\n")


def test_scan_does_not_depend_on_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    tracked = list_tracked_files()
    if tracked is None:
        pytest.skip("git is not available")
    assert "PROJECT_SPEC.md" in tracked
    assert _read("PROJECT_SPEC.md")


def test_md_code_span_stripped() -> None:
    assert find_hits("docs/x.md", "See `compl-ai/analisi/verifica_deliberazione.py` for details.\n") == []
    assert find_hits("docs/x.md", "Il valore viene calcolato\n")


def test_cfg_comment_blocks() -> None:
    italian_toml = '[tool.x]\n# Configurazione del tool\nname = "viene"\n'
    english_toml = "[tool.x]\n# Tool configuration\nname = 'ok'\n"
    assert [h[0] for h in find_hits("pyproject.toml", italian_toml)] == [2]
    assert find_hits("pyproject.toml", english_toml) == []
    italian_req = "# INSTALLAZIONE:\nrequests>=2  # Imposta il valore\n"
    english_req = "# Install with pip\nrequests>=2  # pinned\n"
    assert [h[0] for h in find_hits("requirements.txt", italian_req)] == [1, 2]
    assert find_hits("requirements.txt", english_req) == []


def test_path_allowlist() -> None:
    src = "Il valore viene calcolato\n"
    assert find_hits("moralstack/prompts/data/x.md", src) == []
    assert find_hits(".claude/rules/x.md", src) == []
    assert find_hits("docs/other.md", src)


def test_line_allowlist_exempts_only_quoted_line() -> None:
    src = "Error: Value error, priority deve essere tra 1 e 100\nIl valore viene calcolato\n"
    assert [h[0] for h in find_hits("docs/creating_overlays.md", src)] == [2]


def test_line_allowlist_exempts_only_the_quote() -> None:
    src = "Error: priority deve essere tra 1 e 100, quindi viene scartato\n"
    assert [h[1] for h in find_hits("docs/creating_overlays.md", src)] == ["quindi"]


def test_hits_are_sorted_by_line_number() -> None:
    src = "\n".join(["# viene"] + ["x = 1"] * 9 + ["# viene"]) + "\n"
    assert [h[0] for h in find_hits("pkg/mod.py", src)] == [1, 11]


def test_stale_entry_detection() -> None:
    tracked = ["docs/creating_overlays.md", "moralstack/prompts/data/a.yaml"]
    stale = stale_allowlist_entries(tracked, lambda p: "nothing relevant")
    assert any("substring missing" in s for s in stale)
    assert any("without tracked file" in s for s in stale)
    assert any("not tracked" in s for s in stale_allowlist_entries([], lambda p: ""))
    all_present = "\n".join(sub for entries in LINE_ALLOWLIST.values() for sub, _ in entries)
    assert not any("exempts nothing" in s for s in stale_allowlist_entries(list(LINE_ALLOWLIST), lambda p: all_present))


def test_every_line_allowlist_entry_would_flag() -> None:
    for relpath, entries in LINE_ALLOWLIST.items():
        for sub, _ in entries:
            assert flagged_word(sub) is not None, (relpath, sub)


def test_architecture_spec_entry_is_temporary() -> None:
    reasons = dict(PATH_ALLOWLIST)
    assert "TEMPORARY" in reasons["docs/architecture_spec.md"]
