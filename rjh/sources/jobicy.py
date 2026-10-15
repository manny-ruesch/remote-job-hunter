"""Jobicy : flux JSON public d'offres 100 % télétravail, sans clé API."""
from __future__ import annotations

from ..models import Job
from ..profile import Profile
from ..textutil import strip_html
from . import register
from .base import KeywordSource, RateLimit, SearchQuery


def _map_type(raw: str) -> str:
    low = (raw or "").lower()
    if "part" in low:
        return "part_time"
    if "contract" in low or "freelance" in low:
        return "contract"
    if "intern" in low:
        return "internship"
    return "full_time"


@register
class JobicySource(KeywordSource):
    name = "jobicy"
    rate_limit = RateLimit(min_interval=1.0)
    max_pages = 1
    URL = "https://jobicy.com/api/v2/remote-jobs"

    def build_queries(self, profile: Profile) -> list[SearchQuery]:
        return [SearchQuery(text="")]

    def request(self, q: SearchQuery):
        params: dict[str, int | str] = {"count": int(self.config.get("count", 50))}
        if q.text:
            params["tag"] = q.text
        return self.URL, params, {}

    def parse(self, payload, **ctx):
        jobs = []
        for j in (payload or {}).get("jobs", []):
            sal_min = j.get("annualSalaryMin")
            sal_max = j.get("annualSalaryMax")
            try:
                s_min = float(sal_min) if sal_min else None
            except (TypeError, ValueError):
                s_min = None
            try:
                s_max = float(sal_max) if sal_max else None
            except (TypeError, ValueError):
                s_max = None
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(j.get("id") or j.get("url") or ""),
                    company=j.get("companyName", ""),
                    title=j.get("jobTitle", ""),
                    url=j.get("url", ""),
                    location=j.get("jobGeo", "Worldwide"),
                    description=strip_html(j.get("jobDescription", "")),
                    remote=True,
                    employment_type=_map_type(str(j.get("jobType") or "")),
                    posted_at=str(j.get("pubDate") or ""),
                    salary_min=s_min,
                    salary_max=s_max,
                    salary_currency=str(j.get("salaryCurrency") or ""),
                )
            )
        return jobs
