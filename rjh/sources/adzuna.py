"""Adzuna : source PAR MOTS-CLÉS multi-pays, avec clés (ADZUNA_APP_ID / ADZUNA_APP_KEY).
Montre : credentials, pays, pagination, secrets masqués dans les erreurs (la clé voyage en query string
chez Adzuna : HttpError ne l'affiche jamais). Les descriptions renvoyées sont des EXTRAITS."""
from __future__ import annotations

from ..models import Job
from ..textutil import strip_html
from . import register
from .base import KeywordSource, RateLimit, SearchQuery


@register
class AdzunaSource(KeywordSource):
    name = "adzuna"
    requires_env = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    rate_limit = RateLimit(min_interval=1.0)
    BASE = "https://api.adzuna.com/v1/api/jobs"

    def request(self, q: SearchQuery):
        country = (q.country or self.config.get("country") or "gb").lower()
        params = {
            "app_id": self.env["ADZUNA_APP_ID"], "app_key": self.env["ADZUNA_APP_KEY"],
            "what": q.text, "results_per_page": self.page_size, "content-type": "application/json",
            "where": q.where or None, "max_days_old": q.max_age_days,
        }
        return f"{self.BASE}/{country}/search/{q.page}", params, {}

    def has_more(self, payload, q: SearchQuery) -> bool:
        return q.page * self.page_size < int((payload or {}).get("count", 0))

    def parse(self, payload, **ctx):
        jobs = []
        for r in (payload or {}).get("results", []):
            etype = "contract" if r.get("contract_type") == "contract" else (r.get("contract_time") or "")
            jobs.append(Job(
                source=self.name, source_id=str(r.get("id", "")),
                company=(r.get("company") or {}).get("display_name", ""), title=strip_html(r.get("title", "")),
                url=r.get("redirect_url", ""), location=(r.get("location") or {}).get("display_name", ""),
                description=strip_html(r.get("description", "")), employment_type=etype, posted_at=r.get("created", ""),
                salary_min=r.get("salary_min"), salary_max=r.get("salary_max"),
                extra={"description_is_snippet": True},
            ))
        return jobs
