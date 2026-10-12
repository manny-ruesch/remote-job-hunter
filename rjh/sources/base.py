"""Contrat des sources d'offres.

Ajouter une source = une classe de ~30 lignes :

    @register
    class MaSource(KeywordSource):                 # ou BoardSource
        name = "masource"
        def request(self, q): return URL, {"q": q.text}, {}
        def parse(self, payload, **ctx): return [Job(...) for x in payload["items"]]

Deux familles, car elles ne se pilotent pas pareil :

  KeywordSource  « je cherche CE rôle »      le serveur filtre ; on lui envoie des requêtes déduites du profil
                                              (titres cibles x pays) ; pagination ; ex. Adzuna, Remotive
  BoardSource    « j'ai CES entreprises »     le serveur renvoie TOUT ; on interroge une liste d'entreprises
                                              (sources.boards.<nom>), puis on filtre côté client ; ex. Greenhouse, Lever
"""
from __future__ import annotations

import abc
import dataclasses
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Mapping

from ..http import HttpClient, HttpError, RateLimiter
from ..models import Job
from ..profile import Profile

Prefilter = Callable[[Job], bool]


class SourceConfigError(RuntimeError):
    """Source mal configurée (variable d'environnement absente…) : signalée, jamais avalée."""


@dataclass(frozen=True)
class RateLimit:
    min_interval: float = 0.5        # secondes entre deux requêtes (partagé entre threads)
    max_retries: int = 3             # sur 429 / 5xx, avec Retry-After respecté


@dataclass(frozen=True)
class SearchQuery:
    text: str
    country: str = ""                # ISO2 minuscule : "fr"
    where: str = ""                  # ville / région libre
    page: int = 1
    max_age_days: int | None = None


@dataclass
class SourceResult:
    """Rapport d'exécution d'UNE source : c'est ce qui alimente le bandeau « santé » du dashboard.
    Une source qui échoue n'est plus invisible (l'ancien code avalait 24 erreurs avec `except: pass`)."""
    source: str
    jobs: list[Job] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requests: int = 0
    duration: float = 0.0
    per_board: dict[str, int] = field(default_factory=dict)      # BoardSource : offres gardées par entreprise

    @property
    def ok(self) -> bool:
        return not self.errors


class BaseJobSource(abc.ABC):
    name: ClassVar[str] = ""
    kind: ClassVar[str] = ""                                     # "keyword" | "board"
    requires_env: ClassVar[tuple[str, ...]] = ()                 # ex. ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    rate_limit: ClassVar[RateLimit] = RateLimit()

    def __init__(self, config: Mapping[str, Any] | None = None, *, http: HttpClient | None = None,
                 env: Mapping[str, str] | None = None):
        self.config = dict(config or {})
        self.env = os.environ if env is None else env
        missing = [v for v in self.requires_env if not self.env.get(v)]
        if missing:
            raise SourceConfigError(f"source « {self.name} » : variable(s) d'environnement manquante(s) : {', '.join(missing)}")
        self.http = http or HttpClient(limiter=RateLimiter(self.rate_limit.min_interval),
                                       max_retries=self.rate_limit.max_retries)

    @abc.abstractmethod
    def fetch(self, profile: Profile, prefilter: Prefilter | None = None) -> SourceResult:
        """Point d'entrée unique. Ne lève pas pour une panne réseau : renseigne SourceResult.errors."""

    @abc.abstractmethod
    def parse(self, payload: Any, **ctx: Any) -> list[Job]:
        """Fonction PURE (payload brut -> offres). Testable sur un fichier JSON enregistré, sans réseau."""

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.http.get_json(url, **kw)


# ------------------------------------------------------------------ par mots-clés ----------

class KeywordSource(BaseJobSource):
    kind = "keyword"
    max_pages: ClassVar[int] = 2
    page_size: ClassVar[int] = 50

    def build_queries(self, profile: Profile) -> list[SearchQuery]:
        """Requêtes déduites du profil : (titres cibles + proches) x pays. Surchargeable."""
        s = profile.search
        titles = (s.titles.primary + s.titles.related)[: int(self.config.get("max_titles", 4))]
        countries = list(self.config.get("countries") or s.location.countries or [""])
        where, age = self.config.get("where", ""), self.config.get("max_age_days")
        queries = [SearchQuery(text=t, country=c.lower(), where=where, max_age_days=age) for t in titles for c in countries]
        return queries[: int(self.config.get("max_queries", 8))]

    @abc.abstractmethod
    def request(self, q: SearchQuery) -> tuple[str, dict[str, Any], dict[str, str]]:
        """(url, params, headers) pour une requête et une page."""

    def has_more(self, payload: Any, q: SearchQuery) -> bool:
        return False

    def fetch(self, profile: Profile, prefilter: Prefilter | None = None) -> SourceResult:
        t0, n0 = time.monotonic(), self.http.requests_made
        res, seen = SourceResult(self.name), set()
        limit = int(self.config.get("max_results", 200))
        for q in self.build_queries(profile):
            for page in range(1, self.max_pages + 1):
                qp = dataclasses.replace(q, page=page)
                try:
                    url, params, headers = self.request(qp)
                    payload = self.get_json(url, params=params, headers=headers)
                except HttpError as e:
                    res.errors.append(f"« {q.text} » {q.country or '*'} page {page} : {e}")
                    break
                for job in self.parse(payload, query=qp):
                    if job.id not in seen and (prefilter is None or prefilter(job)):
                        seen.add(job.id)
                        res.jobs.append(job)
                if len(res.jobs) >= limit or not self.has_more(payload, qp):
                    break
            if len(res.jobs) >= limit:
                break
        res.requests, res.duration = self.http.requests_made - n0, time.monotonic() - t0
        return res


# ------------------------------------------------------------------ ATS d'entreprises ------

class BoardSource(BaseJobSource):
    kind = "board"
    max_workers: ClassVar[int] = 4

    def boards(self, profile: Profile) -> list[str]:
        return list(profile.sources.boards.get(self.name) or self.config.get("boards") or [])

    @abc.abstractmethod
    def board_request(self, slug: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        """(url, params, headers) pour lister TOUTES les offres d'une entreprise."""

    def company_name(self, slug: str, payload: Any) -> str:
        return slug.replace("-", " ").replace("_", " ").title()

    def fetch(self, profile: Profile, prefilter: Prefilter | None = None) -> SourceResult:
        t0, n0 = time.monotonic(), self.http.requests_made
        res = SourceResult(self.name)
        slugs = self.boards(profile)
        if not slugs:
            res.errors.append(f"aucune entreprise configurée (sources.boards.{self.name})")
            return res

        def one(slug: str) -> list[Job]:
            url, params, headers = self.board_request(slug)
            payload = self.get_json(url, params=params, headers=headers)
            return self.parse(payload, company=self.company_name(slug, payload), slug=slug)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {slug: pool.submit(one, slug) for slug in slugs}
        seen: set[str] = set()
        for slug in slugs:                                            # ordre du profil : résultat déterministe
            try:
                jobs = futures[slug].result()
            except HttpError as e:                                    # un board renommé (404) est VISIBLE
                res.errors.append(f"{slug} : HTTP {e.status}" if e.status else f"{slug} : {e}")
                continue
            kept = [j for j in jobs if j.id not in seen and (prefilter is None or prefilter(j))]
            seen.update(j.id for j in kept)
            res.jobs.extend(kept)
            res.per_board[slug] = len(kept)
        res.requests, res.duration = self.http.requests_made - n0, time.monotonic() - t0
        return res
