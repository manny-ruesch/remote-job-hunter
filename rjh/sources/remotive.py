"""Remotive : source PAR MOTS-CLÉS, sans clé d'API. Exemple minimal d'une KeywordSource.
Vérifiez les conditions d'usage de l'API (fréquence, attribution) avant un usage intensif."""
from __future__ import annotations

from ..models import Job
from ..textutil import strip_html
from . import register
from .base import KeywordSource, RateLimit, SearchQuery

_ETYPE = {"full_time": "full_time", "part_time": "part_time", "contract": "contract", "freelance": "freelance", "internship": "internship"}


@register
class RemotiveSource(KeywordSource):
    name = "remotive"
    rate_limit = RateLimit(min_interval=2.0)
    max_pages = 1
    URL = "https://remotive.com/api/remote-jobs"

    def request(self, q: SearchQuery):
        return self.URL, {"search": q.text, "limit": self.page_size}, {}

    def parse(self, payload, **ctx):
        jobs = []
        for j in (payload or {}).get("jobs", []):
            jobs.append(Job(
                source=self.name, source_id=str(j.get("id", "")), company=j.get("company_name", ""),
                title=j.get("title", ""), url=j.get("url", ""), location=j.get("candidate_required_location", ""),
                description=strip_html(j.get("description", "")), remote=True,
                employment_type=_ETYPE.get(str(j.get("job_type", "")).lower(), ""),
                posted_at=j.get("publication_date", ""),
                extra={"category": j.get("category", ""), "salary_text": j.get("salary", "")},
            ))
        return jobs
