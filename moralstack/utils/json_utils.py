"""
Utilità per il parsing JSON robusto in MoralStack.

Gestisce casi comuni di output LLM malformato:
- JSON troncato (stringhe/array/oggetti non chiusi)
- Testo prima/dopo il JSON
- Escape sequences problematiche
- Blocchi markdown
"""

from __future__ import annotations

import json
import re
from typing import Any


class JSONParseError(Exception):
    """Errore nel parsing JSON."""

    pass


def _preprocess_text(text: str) -> str:
    """Pre-processa il testo per rimuovere problemi comuni."""
    # Rimuovi trailing comma in oggetti e array
    # Pattern: , seguito da } o ] con opzionale whitespace nel mezzo
    text = re.sub(r",\s*\}", "}", text)
    text = re.sub(r",\s*\]", "]", text)

    # Rimuovi backslash spurii prima di underscore (errore comune LLM)
    text = text.replace("\\_", "_")

    # Rimuovi caratteri di controllo problematici (eccetto newline e tab)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    # Normalizza newline
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    return text


def _try_complete_truncated_json(text: str) -> str | None:
    """
    Tenta di completare un JSON troncato.

    Casi gestiti:
    - Stringhe non chiuse: "value → "value"
    - Array non chiusi: ["a", "b → ["a", "b"]
    - Oggetti non chiusi: {"key": "value → {"key": "value"}
    """
    if not text.strip().startswith("{"):
        return None

    # Conta le parentesi aperte/chiuse
    open_braces = text.count("{")
    close_braces = text.count("}")
    open_brackets = text.count("[")
    close_brackets = text.count("]")

    # Se già bilanciato, non serve completare
    if open_braces == close_braces and open_brackets == close_brackets:
        return text

    completed = text.rstrip()

    # Rimuovi trailing comma se presente
    completed = re.sub(r",\s*$", "", completed)

    # Se finisce con una stringa non chiusa, chiudila
    # Pattern: "...[^"] alla fine senza " di chiusura
    if re.search(r':\s*"[^"]*$', completed):
        completed += '"'

    # Chiudi array aperti
    while open_brackets > close_brackets:
        completed += "]"
        close_brackets += 1

    # Chiudi oggetti aperti
    while open_braces > close_braces:
        completed += "}"
        close_braces += 1

    return completed


def _extract_json_object(text: str) -> str | None:
    """Estrae il primo oggetto JSON completo dal testo."""
    start_idx = text.find("{")
    if start_idx == -1:
        return None

    # Cerca la parentesi di chiusura corrispondente
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start_idx, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == "\\":
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx : i + 1]

    # Se arriviamo qui, l'oggetto è troncato
    return text[start_idx:]


def extract_json(text: str) -> dict[str, Any]:
    """
    Estrae e parsa JSON da testo in modo robusto.
    Supporta blocchi markdown, testo prima/dopo, JSON troncato, escape problematici.
    text: stringa che può contenere JSON.
    Ritorna: dizionario parsato.
    Solleva JSONParseError se non riesce a estrarre JSON valido.
    """
    original_text = text
    text = _preprocess_text(text.strip())

    # Tentativo 1: Parsing diretto (caso ideale)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Tentativo 2: Estrazione da blocchi markdown ```json ... ``` o ``` ... ```
    code_patterns = [
        r"```json\s*(.*?)\s*```",
        r"```\s*(.*?)\s*```",
    ]
    for pattern in code_patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                # Prova a riparare il contenuto
                pass

    # Tentativo 3: Estrai l'oggetto JSON dal testo
    json_text = _extract_json_object(text)
    if json_text:
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            pass

        # Tentativo 4: Prova a completare JSON troncato
        completed = _try_complete_truncated_json(json_text)
        if completed:
            try:
                return json.loads(completed)
            except json.JSONDecodeError:
                pass

    # Tentativo 5: Cerca JSON tra primo { e ultimo }
    start_idx = text.find("{")
    end_idx = text.rfind("}")

    if start_idx != -1 and end_idx > start_idx:
        json_content = text[start_idx : end_idx + 1]
        try:
            return json.loads(json_content)
        except json.JSONDecodeError:
            # Tentativo 6: Riparazione newline nelle stringhe
            try:
                repaired = re.sub(
                    r':\s*"([^"]*)"',
                    lambda m: ': "' + m.group(1).replace("\n", " ").replace("\r", "") + '"',
                    json_content,
                )
                return json.loads(repaired)
            except Exception:
                pass

            # Tentativo 7: Completa se troncato
            completed = _try_complete_truncated_json(json_content)
            if completed and completed != json_content:
                try:
                    return json.loads(completed)
                except json.JSONDecodeError:
                    pass

    # Tentativo 8: Se c'è solo { senza }, prova a completare
    if "{" in text and "}" not in text:
        start_idx = text.find("{")
        truncated = text[start_idx:]
        completed = _try_complete_truncated_json(truncated)
        if completed:
            try:
                return json.loads(completed)
            except json.JSONDecodeError:
                pass

    raise JSONParseError(f"Impossibile estrarre JSON valido dal testo: {original_text[:150]}...")
