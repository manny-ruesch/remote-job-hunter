"""Ashby : source ATS directe (BoardSource), 100 % gratuite et sans clé API."""
from __future__ import annotations

from ..models import Job
from ..textutil import strip_html
from . import register
from .base import BoardSource, RateLimit

_EMP_MAP = {
    "fulltime": "full_time",
    "full_time": "full_time",
    "parttime": "part_time",
    "part_time": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "freelance": "freelance",
    "intern": "internship",
    "internship": "internship",
}


@register
class AshbySource(BoardSource):
    name = "ashby"
    rate_limit = RateLimit(min_interval=0.15)
    max_workers = 8
    BASE = "https://api.ashbyhq.com/posting-api/job-board"

    def board_request(self, slug: str):
        return f"{self.BASE}/{slug}", {}, {}

    def parse(self, payload, **ctx):
        company = ctx.get("company", "")
        jobs = []
        for j in (payload or {}).get("jobs", []):
            emp_raw = str(j.get("employmentType") or "").lower().replace("-", "").replace(" ", "")
            emp = _EMP_MAP.get(emp_raw, "full_time")
            is_rem = j.get("isRemote")
            desc = strip_html(j.get("descriptionHtml") or j.get("descriptionPlain") or "")
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(j.get("id") or j.get("jobUrl") or ""),
                    company=company,
                    title=j.get("title", ""),
                    url=j.get("jobUrl", ""),
                    location=j.get("location", ""),
                    description=desc,
                    remote=bool(is_rem) if is_rem is not None else None,
                    employment_type=emp,
                    posted_at=str(j.get("publishedAt") or ""),
                    extra={"board": ctx.get("slug", "")},
                )
            )
        return jobs
