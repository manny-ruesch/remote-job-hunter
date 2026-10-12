"""Modèles de données partagés par les sources, le pré-tri et l'analyse."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .textutil import norm

# « (m/f/d) », « (h/f) », « - H/F »… : marqueurs de genre qui rendent deux titres identiques « différents ».
_GENDER = re.compile(r"\(?\b(?:m\s*/\s*[fwd]|[fhw]\s*/\s*[hfmd]|m\s*/\s*f\s*/\s*[dx])\b\)?(?:\s*/\s*[dx])?", re.I)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _NON_ALNUM.sub(" ", norm(_GENDER.sub(" ", s))).strip()


@dataclass
class Job:
    source: str                       # "greenhouse", "adzuna", ...
    source_id: str                    # identifiant côté source (ou URL à défaut)
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""             # TEXTE brut (strip_html déjà appliqué)
    remote: bool | None = None        # None = inconnu
    employment_type: str = ""         # full_time | part_time | contract | freelance | internship | ""
    posted_at: str = ""               # ISO 8601 si connue
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Identifiant stable PAR SOURCE."""
        if "db_id" in self.extra:
            return str(self.extra["db_id"])
        return hashlib.sha1(f"{self.source}|{self.source_id or self.url}".encode()).hexdigest()[:16]

    @property
    def dedupe_key(self) -> str:
        """Identique pour la MÊME offre vue sur deux sources (LinkedIn + Ashby…) :
        société + titre sans marqueurs de genre + première partie du lieu."""
        city = _slug(self.location.split(",")[0].replace("remote", ""))
        return hashlib.sha1(f"{_slug(self.company)}|{_slug(self.title)}|{city}".encode()).hexdigest()[:16]
