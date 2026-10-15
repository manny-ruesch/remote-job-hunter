"""We Work Remotely (WWR) : flux RSS publics 100 % télétravail, sans clé API."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

from ..http import HttpError, short_http_reason
from ..models import Job
from ..profile import Profile
from ..textutil import strip_html
from . import register
from .base import BaseJobSource, Prefilter, RateLimit, SourceResult

DEFAULT_CATEGORIES = (
    "remote-programming-jobs",
    "remote-devops-sysadmin-jobs",
    "all-other-remote-jobs",
)


@register
class WWRSource(BaseJobSource):
    name = "wwr"
    kind = "keyword"
    rate_limit = RateLimit(min_interval=0.4)
    BASE = "https://weworkremotely.com/categories"

    def _categories(self) -> list[str]:
        return list(self.config.get("categories") or DEFAULT_CATEGORIES)

    def _fetch_xml(self, url: str) -> bytes:
        self.http.limiter.wait()
        self.http.requests_made += 1
        hdrs = {"User-Agent": self.http.user_agent, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
        try:
            status, _, raw = self.http.transport("GET", url, hdrs, None, self.http.timeout)
        except Exception as e:
            raise HttpError(0, str(e), url) from e
        if not (200 <= status < 300):
            raise HttpError(status, raw.decode("utf-8", "replace"), url)
        return raw

    def ping(self, profile: Profile) -> tuple[str, str]:
        cat = (self._categories() or ["remote-programming-jobs"])[0]
        url = f"{self.BASE}/{cat}.rss"
        try:
            self._fetch_xml(url)
        except HttpError as e:
            kind, st = short_http_reason(e)
            return ("fail", kind if kind != "other" else f"other:{st}")
        return ("ok", "ok")

    def parse(self, payload: Any, **ctx: Any) -> list[Job]:
        if isinstance(payload, dict):
            return []
        raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload or b"")
        if not raw.strip().startswith(b"<"):
            return []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return []
        jobs: list[Job] = []
        for item in root.findall(".//item"):
            raw_title = (item.findtext("title") or "").strip()
            parts = raw_title.split(":", 1)
            company = parts[0].strip() if len(parts) > 1 else "Startup"
            title = parts[1].strip() if len(parts) > 1 else raw_title
            link = (item.findtext("link") or "").strip()
            region = (item.findtext("region") or "Full Remote").strip()
            desc = strip_html(item.findtext("description") or "")
            pub = (item.findtext("pubDate") or "").strip()
            if title and link:
                jobs.append(
                    Job(
                        source=self.name,
                        source_id=link,
                        company=company,
                        title=title,
                        url=link,
                        location=region or "Full Remote",
                        description=desc,
                        remote=True,
                        employment_type="full_time",
                        posted_at=pub,
                        extra={"category": ctx.get("category", "")},
                    )
                )
        return jobs

    def fetch(self, profile: Profile, prefilter: Prefilter | None = None) -> SourceResult:
        t0, n0 = time.monotonic(), self.http.requests_made
        res = SourceResult(self.name)
        seen: set[str] = set()
        for cat in self._categories():
            url = f"{self.BASE}/{cat}.rss"
            try:
                raw = self._fetch_xml(url)
            except HttpError as e:
                res.errors.append(f"{cat} : {e}")
                continue
            for job in self.parse(raw, category=cat):
                if job.id not in seen and (prefilter is None or prefilter(job)):
                    seen.add(job.id)
                    res.jobs.append(job)
        res.requests, res.duration = self.http.requests_made - n0, time.monotonic() - t0
        return res
