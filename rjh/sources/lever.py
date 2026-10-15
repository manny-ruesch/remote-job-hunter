"""Lever : source ATS directe (BoardSource), 100 % gratuite et sans clé API."""
from __future__ import annotations

from ..models import Job
from ..textutil import strip_html
from . import register
from .base import BoardSource, RateLimit


def _map_commitment(raw: str) -> str:
    low = (raw or "").lower()
    if "part" in low:
        return "part_time"
    if "contract" in low or "freelance" in low:
        return "contract"
    if "intern" in low:
        return "internship"
    return "full_time"


@register
class LeverSource(BoardSource):
    name = "lever"
    rate_limit = RateLimit(min_interval=0.2)
    max_workers = 4
    BASE = "https://api.lever.co/v0/postings"

    def board_request(self, slug: str):
        return f"{self.BASE}/{slug}", {"mode": "json"}, {}

    def parse(self, payload, **ctx):
        company = ctx.get("company", "")
        items = payload if isinstance(payload, list) else (payload or {}).get("postings", [])
        jobs = []
        for j in items:
            if not isinstance(j, dict):
                continue
            cats = j.get("categories") or {}
            loc = str(cats.get("location") or "")
            wp = str(j.get("workplaceType") or "").lower()
            remote = True if (wp == "remote" or "remote" in loc.lower()) else (False if wp == "onsite" else None)
            desc = j.get("descriptionPlain") or strip_html(j.get("description") or "")
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(j.get("id") or ""),
                    company=company,
                    title=j.get("text", ""),
                    url=j.get("applyUrl") or j.get("hostedUrl") or "",
                    location=loc,
                    description=desc,
                    remote=remote,
                    employment_type=_map_commitment(str(cats.get("commitment") or "")),
                    posted_at=str(j.get("createdAt") or ""),
                    extra={"board": ctx.get("slug", "")},
                )
            )
        return jobs
