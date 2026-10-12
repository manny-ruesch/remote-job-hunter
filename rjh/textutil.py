"""Utilitaires texte partagés : normalisation, nettoyage HTML, estimation de tokens."""
from __future__ import annotations

import html
import math
import re
import unicodedata

_WS = re.compile(r"\s+")
_BLOCK = re.compile(r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|section|article)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def norm(s: str) -> str:
    """Minuscules, sans accents, espaces normalisés. Base de TOUTES les comparaisons
    (« infirmière » == « infirmiere », « Straße » == « strasse »)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("\u00a0", " ").replace("\u2019", "'").replace("\u2018", "'")
    return _WS.sub(" ", s.casefold()).strip()


def strip_html(s: str) -> str:
    """HTML (éventuellement échappé, comme chez Greenhouse) -> texte brut.
    Le résultat est du TEXTE : l'afficher côté web avec textContent / un échappement."""
    if not s:
        return ""
    s = html.unescape(s)
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    lines = (_WS.sub(" ", ln).strip() for ln in s.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def est_tokens(text: str) -> int:
    """Estimation prudente (~3,6 caractères/token pour du FR/EN mélangé)."""
    return math.ceil(len(text) / 3.6)


def truncate(text: str, max_chars: int) -> str:
    text = text or ""
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"
