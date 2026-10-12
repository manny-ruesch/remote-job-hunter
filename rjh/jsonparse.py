"""Extraction JSON tolérante pour des réponses de LLM collées à la main (ou renvoyées par une API).

Deux niveaux :
  * parse_json_loose  : récupère UN document JSON complet malgré fences, texte autour,
                        guillemets typographiques, virgules traînantes.
  * salvage_items     : récupère les objets COMPLETS d'une liste même si la sortie a été COUPÉE
                        en plein milieu (le cas n°1 des chats : limite de longueur de réponse).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


class JsonParseError(ValueError):
    pass


_FENCE = re.compile(r"```[a-zA-Z0-9_-]*[ \t]*\n?(.*?)(?:```|\Z)", re.S)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
# Un chat qui rend le JSON en texte « joli » remplace volontiers les guillemets droits.
_SMART = {
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2018": "'", "\u2019": "'", "\u00a0": " ", "\u200b": "", "\ufeff": "",
}


def _repair(s: str) -> str:
    for k, v in _SMART.items():
        s = s.replace(k, v)
    return _TRAILING_COMMA.sub(r"\1", s)


def _fenced_blocks(text: str) -> list[str]:
    blocks = [m.group(1).strip() for m in _FENCE.finditer(text)]
    return [b for b in blocks if b and b[0] in "{["]


def _first_brace_slice(text: str) -> str:
    idx = [i for i in (text.find("{"), text.find("[")) if i != -1]
    return text[min(idx):] if idx else text


def _candidates(text: str) -> list[str]:
    text = (text or "").strip()
    out = _fenced_blocks(text) + [text]
    i = min([x for x in (text.find("{"), text.find("[")) if x != -1], default=-1)
    j = max(text.rfind("}"), text.rfind("]"))
    if i != -1 and j > i:
        out.append(text[i : j + 1])
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def parse_json_loose(text: str) -> Any:
    """Un document JSON complet, ou JsonParseError."""
    for cand in _candidates(text):
        for variant in (cand, _repair(cand)):
            try:
                return json.loads(variant)
            except (json.JSONDecodeError, ValueError):
                continue
    raise JsonParseError("aucun JSON valide trouvé dans la réponse")


@dataclass
class Salvage:
    items: list[dict]
    invalid: int          # fragments {...} fermés mais non parsables
    truncated: bool       # structure non fermée à la fin : la sortie a été coupée


def _try_loads(chunk: str) -> Any:
    for variant in (chunk, _repair(chunk)):
        try:
            return json.loads(variant)
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _scan(src: str) -> Salvage:
    stack: list[tuple[str, int]] = []
    in_str = esc = False
    items: list[dict] = []
    invalid = 0
    for i, ch in enumerate(src):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append((ch, i))
        elif ch in "}]":
            if not stack:
                continue
            open_ch, start = stack.pop()
            # On ne retient que les objets-ENFANTS directs d'une liste « de résultats » :
            # liste racine (index 0) ou liste dans l'objet enveloppe {"results":[...]} (index 1).
            if ch == "}" and open_ch == "{" and stack and stack[-1][0] == "[" and len(stack) - 1 in (0, 1):
                parsed = _try_loads(src[start : i + 1])
                if isinstance(parsed, dict):
                    items.append(parsed)
                else:
                    invalid += 1
    return Salvage(items=items, invalid=invalid, truncated=bool(stack) or in_str)


def salvage_items(text: str) -> Salvage:
    """Objets complets d'une liste de résultats, même dans une sortie tronquée."""
    text = text or ""
    fenced = _fenced_blocks(text)
    src = fenced[0] if fenced else _first_brace_slice(text)
    best = _scan(src)
    if not best.items:                       # guillemets typographiques ? on retente réparé
        alt = _scan(_repair(src))
        if alt.items:
            best = alt
    return best
