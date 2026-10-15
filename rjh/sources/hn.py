"""Hacker News « Who is hiring? » : API officielle Firebase (fondateurs & CTOs en direct)."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..http import HttpError
from ..models import Job
from ..profile import Profile
from ..textutil import strip_html
from . import register
from .base import BaseJobSource, Prefilter, RateLimit, SourceResult

_URL_RX = re.compile(r'https?://[^\s<>"\'\)]+')


def _detect_type(text: str) -> str:
    low = (text or "").lower()
    if any(k in low for k in ("part-time", "part time", "part_time", "10-20h", "20h/week")):
        return "part_time"
    if any(k in low for k in ("contract", "freelance", "fractional", "contractor")):
        return "contract"
    return "full_time"


@register
class HackerNewsSource(BaseJobSource):
    name = "hn"
    kind = "keyword"
    rate_limit = RateLimit(min_interval=0.0)
    USER_URL = "https://hacker-news.firebaseio.com/v0/user/whoishiring.json"
    ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

    def ping(self, profile: Profile) -> tuple[str, str]:
        return self._ping_request(self.USER_URL, {}, {})

    def parse(self, payload: Any, **ctx: Any) -> list[Job]:
        items = payload if isinstance(payload, list) else [payload]
        jobs: list[Job] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw = item.get("text") or ""
            cid = str(item.get("id") or "")
            if not raw or not cid:
                continue
            text = strip_html(raw)
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            if not lines or "|" not in lines[0]:
                continue
            parts = [p.strip() for p in lines[0].split("|")]
            company = parts[0][:60] if parts else "Startup"
            title = parts[1][:80] if len(parts) > 1 else "Software / Automation Engineer"
            location = parts[2][:60] if len(parts) > 2 else "Remote"
            header_low = lines[0].lower()
            remote = True if "remote" in header_low else (False if "onsite" in header_low or "on-site" in header_low else None)
            links = _URL_RX.findall(raw)
            url = links[0] if links else f"https://news.ycombinator.com/item?id={cid}"
            jobs.append(
                Job(
                    source=self.name,
                    source_id=cid,
                    company=company,
                    title=title,
                    url=url,
                    location=location,
                    description=text[:3500],
                    remote=remote,
                    employment_type=_detect_type(lines[0]),
                )
            )
        return jobs

    def fetch(self, profile: Profile, prefilter: Prefilter | None = None) -> SourceResult:
        t0, n0 = time.monotonic(), self.http.requests_made
        res = SourceResult(self.name)
        max_comments = int(self.config.get("max_comments", 60))
        try:
            user_data = self.get_json(self.USER_URL) or {}
            submitted = user_data.get("submitted") or []
            if not submitted:
                return res
            story = self.get_json(self.ITEM_URL.format(id=submitted[0])) or {}
            comment_ids = (story.get("kids") or [])[:max_comments]
        except HttpError as e:
            res.errors.append(str(e))
            res.requests, res.duration = self.http.requests_made - n0, time.monotonic() - t0
            return res

        def fetch_one(cid: Any) -> dict | None:
            try:
                return self.get_json(self.ITEM_URL.format(id=cid))
            except HttpError:
                return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            comments = [c for c in pool.map(fetch_one, comment_ids) if c]

        seen: set[str] = set()
        for job in self.parse(comments):
            if job.id not in seen and (prefilter is None or prefilter(job)):
                seen.add(job.id)
                res.jobs.append(job)
        res.requests, res.duration = self.http.requests_made - n0, time.monotonic() - t0
        return res
