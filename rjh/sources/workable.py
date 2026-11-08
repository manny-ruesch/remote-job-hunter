"""Workable : source ATS directe (BoardSource), 100 % gratuite et sans clé API.

Utilise l'endpoint JSON public `GET https://www.workable.com/api/accounts/{slug}?details=true`
qui est 100 % compatible avec `BoardSource.get_json()` et renvoie la description HTML
complète de chaque offre lorsque `details=true` est passé.
"""
from __future__ import annotations

from typing import Any

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
    "temporary": "contract",
    "freelance": "freelance",
    "internship": "internship",
    "intern": "internship",
}


@register
class WorkableSource(BoardSource):
    name = "workable"
    rate_limit = RateLimit(min_interval=0.25)
    max_workers = 4
    BASE = "https://www.workable.com/api/accounts"

    def board_request(self, slug: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        return f"{self.BASE}/{slug}", {"details": "true"}, {}

    def parse(self, payload: Any, **ctx: Any) -> list[Job]:
        if not isinstance(payload, dict):
            return []
        slug = str(ctx.get("slug") or "").strip()
        company = str(payload.get("name") or ctx.get("company") or slug).strip()
        raw_jobs_val = payload.get("jobs")
        if isinstance(raw_jobs_val, list):
            raw_jobs: list[Any] = raw_jobs_val
        else:
            raw_res = payload.get("results")
            raw_jobs = raw_res if isinstance(raw_res, list) else []

        jobs: list[Job] = []
        for j in raw_jobs:
            if not isinstance(j, dict):
                continue
            shortcode = str(j.get("shortcode") or j.get("code") or j.get("id") or "").strip()
            title = str(j.get("title") or "").strip()
            if not (shortcode and title):
                continue

            city = str(j.get("city") or "").strip()
            state = str(j.get("state") or j.get("region") or "").strip()
            country = str(j.get("country") or "").strip()
            raw_loc = j.get("location")
            loc_obj: dict[str, Any] = raw_loc if isinstance(raw_loc, dict) else {}
            if not city and loc_obj:
                city = str(loc_obj.get("city") or "").strip()
                state = str(loc_obj.get("region") or "").strip()
                country = str(loc_obj.get("country") or "").strip()

            telecommuting = j.get("telecommuting")
            workplace = str(j.get("workplace") or j.get("workplace_type") or "").lower()
            is_remote = True if (telecommuting is True or workplace == "remote") else (False if workplace == "onsite" else None)

            loc_parts = [p for p in (city, state, country) if p]
            if is_remote is True and "remote" not in " ".join(loc_parts).lower():
                loc_parts.insert(0, "Remote")
            location = ", ".join(loc_parts) or ("Remote" if is_remote else "")

            emp_raw = str(j.get("employment_type") or "").lower().replace("-", "").replace(" ", "")
            emp = _EMP_MAP.get(emp_raw, "full_time")

            desc_parts = [
                strip_html(str(j.get("description") or "")),
                strip_html(str(j.get("requirements") or "")),
                strip_html(str(j.get("benefits") or "")),
            ]
            desc = "\n\n".join(p for p in desc_parts if p)
            if not desc:
                dept = str(j.get("department") or "").strip()
                desc = f"Role: {title} at {company}. Department: {dept or 'General'}. Location: {location or 'Flexible'}."

            url = str(j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{slug}/j/{shortcode}/")
            jobs.append(
                Job(
                    source=self.name,
                    source_id=shortcode,
                    company=company,
                    title=title,
                    url=url,
                    location=location,
                    description=desc,
                    remote=is_remote,
                    employment_type=emp,
                    posted_at=str(j.get("published_on") or j.get("created_at") or ""),
                    extra={"board": slug},
                )
            )
        return jobs
