"""Welcome to the Jungle (WTTJ) : recherche publique Algolia (scaleups FR/EU)."""
from __future__ import annotations

import time
from typing import Any

from ..http import HttpError, short_http_reason
from ..models import Job
from ..profile import Profile
from . import register
from .base import BaseJobSource, Prefilter, RateLimit, SourceResult

_CONTRACT_MAP = {
    "full_time": "full_time",
    "cdi": "full_time",
    "part_time": "part_time",
    "freelance": "freelance",
    "contract": "contract",
    "cdd": "contract",
    "internship": "internship",
    "stage": "internship",
}


@register
class WTTJSource(BaseJobSource):
    name = "wttj"
    kind = "keyword"
    rate_limit = RateLimit(min_interval=0.4)
    URL = "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries"
    HEADERS = {
        "x-algolia-application-id": "CSEKHVMS53",
        "x-algolia-api-key": "4bd8f6215d0cc52b26430765769e65a0",
        "Referer": "https://www.welcometothejungle.com/",
        "Origin": "https://www.welcometothejungle.com",
    }

    def _queries(self, profile: Profile) -> list[str]:
        s = profile.search
        titles = (s.titles.primary + s.titles.related)[: int(self.config.get("max_titles", 4))]
        boosts = list(s.keywords.boost)[:2]
        return list(dict.fromkeys(titles + boosts)) or ["automation"]

    def ping(self, profile: Profile) -> tuple[str, str]:
        body = {"requests": [{"indexName": "wk_cms_jobs_production", "params": "query=automation&hitsPerPage=1&page=0"}]}
        try:
            self.http.post_json(self.URL, headers=self.HEADERS, json_body=body)
        except HttpError as e:
            kind, st = short_http_reason(e)
            return ("fail", kind if kind != "other" else f"other:{st}")
        return ("ok", "ok")

    def parse(self, payload: Any, **ctx: Any) -> list[Job]:
        jobs: list[Job] = []
        seen_urls: set[str] = set()
        for batch in (payload or {}).get("results", []):
            for hit in batch.get("hits", []):
                title = (hit.get("name") or "").strip()
                org = hit.get("organization") or {}
                company = (org.get("name") or "").strip()
                org_slug = org.get("slug") or ""
                job_slug = hit.get("slug") or ""
                if not (title and org_slug and job_slug):
                    continue
                url = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{job_slug}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                office = hit.get("office") or {}
                city = office.get("city") or "Remote"
                country = office.get("country") or "Europe"
                loc = f"{city}, {country}"
                raw_ct = str(hit.get("contract_type") or "").lower()
                emp = _CONTRACT_MAP.get(raw_ct, "full_time")
                rem_val = str(hit.get("remote") or "").lower()
                remote = True if rem_val == "fulltime" else (False if rem_val == "no" else None)
                summary = (hit.get("summary") or hit.get("profile") or "").strip()
                desc = summary or f"{title} at {company} ({loc}). Contract: {emp}. Remote: {rem_val or 'fulltime'}."
                jobs.append(
                    Job(
                        source=self.name,
                        source_id=f"{org_slug}/{job_slug}",
                        company=company,
                        title=title,
                        url=url,
                        location=loc,
                        description=desc,
                        remote=remote,
                        employment_type=emp,
                        posted_at=str(hit.get("published_at") or ""),
                    )
                )
        return jobs

    def fetch(self, profile: Profile, prefilter: Prefilter | None = None) -> SourceResult:
        t0, n0 = time.monotonic(), self.http.requests_made
        res = SourceResult(self.name)
        remote_only = profile.search.work_mode.remote == "required"
        reqs = []
        for q in self._queries(profile):
            flt = "&filters=remote:fulltime" if remote_only else ""
            reqs.append({"indexName": "wk_cms_jobs_production", "params": f"query={q}&hitsPerPage=15&page=0{flt}"})
            if remote_only:
                reqs.append({
                    "indexName": "wk_cms_jobs_production",
                    "params": f"query={q}&hitsPerPage=10&page=0&filters=remote:fulltime AND (contract_type:freelance OR contract_type:part_time)",
                })
        try:
            payload = self.http.post_json(self.URL, headers=self.HEADERS, json_body={"requests": reqs})
        except HttpError as e:
            res.errors.append(str(e))
            res.requests, res.duration = self.http.requests_made - n0, time.monotonic() - t0
            return res
        seen: set[str] = set()
        for job in self.parse(payload):
            if job.id not in seen and (prefilter is None or prefilter(job)):
                seen.add(job.id)
                res.jobs.append(job)
        res.requests, res.duration = self.http.requests_made - n0, time.monotonic() - t0
        return res
