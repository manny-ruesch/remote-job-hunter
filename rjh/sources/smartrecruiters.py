"""SmartRecruiters : source ATS directe (BoardSource), 100 % gratuite et sans clé API.

L'API publique de liste (`/v1/companies/{slug}/postings`) retourne les métadonnées
structurées de chaque offre (titre, localisation, département, fonction, niveau
d'expérience, télétravail). On synthétise une description enrichie d'au moins
80 caractères afin que le triage heuristique et les filtres du dashboard disposent
de tout le contexte métier sans provoquer de tempête de requêtes N+1.
"""
from __future__ import annotations

from typing import Any

from ..models import Job
from ..textutil import strip_html
from . import register
from .base import BoardSource, RateLimit

_EMP_MAP = {
    "full_time": "full_time",
    "permanent": "full_time",
    "regular": "full_time",
    "part_time": "part_time",
    "contract": "contract",
    "temporary": "contract",
    "freelance": "freelance",
    "internship": "internship",
    "intern": "internship",
    "trainee": "internship",
}


def _safe_label(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("label") or obj.get("name") or obj.get("id") or "").strip()
    return str(obj or "").strip()


@register
class SmartRecruitersSource(BoardSource):
    name = "smartrecruiters"
    rate_limit = RateLimit(min_interval=0.25)
    max_workers = 4
    BASE = "https://api.smartrecruiters.com/v1/companies"

    def board_request(self, slug: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        return f"{self.BASE}/{slug}/postings", {"limit": "100"}, {}

    def parse(self, payload: Any, **ctx: Any) -> list[Job]:
        slug = str(ctx.get("slug") or "").strip()
        fallback_company = str(ctx.get("company") or slug).strip()
        items = (payload or {}).get("content") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return []

        jobs: list[Job] = []
        for j in items:
            if not isinstance(j, dict):
                continue
            job_id = str(j.get("id") or j.get("uuid") or "").strip()
            title = str(j.get("name") or j.get("title") or "").strip()
            if not (job_id and title):
                continue

            raw_comp = j.get("company")
            comp_obj: dict[str, Any] = raw_comp if isinstance(raw_comp, dict) else {}
            company = str(comp_obj.get("name") or fallback_company).strip()
            comp_id = str(comp_obj.get("identifier") or slug or company).strip()

            raw_loc = j.get("location")
            loc_obj: dict[str, Any] = raw_loc if isinstance(raw_loc, dict) else {}
            city = str(loc_obj.get("city") or "").strip()
            region = str(loc_obj.get("region") or "").strip()
            country = str(loc_obj.get("country") or "").strip().upper()
            is_remote = loc_obj.get("remote")
            loc_parts = [p for p in (city, region, country) if p]
            if is_remote is True and "remote" not in " ".join(loc_parts).lower():
                loc_parts.insert(0, "Remote")
            location = ", ".join(loc_parts) or ("Remote" if is_remote else "")

            emp_raw = _safe_label(j.get("typeOfEmployment")).lower().replace("-", "_").replace(" ", "_")
            emp = _EMP_MAP.get(emp_raw, "full_time")

            dept = _safe_label(j.get("department"))
            func = _safe_label(j.get("function"))
            ind = _safe_label(j.get("industry"))
            exp = _safe_label(j.get("experienceLevel"))

            # Si le détail complet (jobAd) est présent dans le payload, on l'extrait
            raw_desc = ""
            raw_job_ad = j.get("jobAd")
            job_ad: dict[str, Any] = raw_job_ad if isinstance(raw_job_ad, dict) else {}
            raw_sections = job_ad.get("sections")
            sections: dict[str, Any] = raw_sections if isinstance(raw_sections, dict) else {}
            if sections:
                sec_texts = []
                for sec_key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
                    sec = sections.get(sec_key)
                    if isinstance(sec, dict) and sec.get("text"):
                        sec_texts.append(strip_html(str(sec.get("text"))))
                raw_desc = "\n\n".join(t for t in sec_texts if t)

            if not raw_desc:
                meta_bits = [
                    f"Role: {title} at {company}.",
                    f"Department: {dept}." if dept else "",
                    f"Function: {func}." if func else "",
                    f"Industry: {ind}." if ind else "",
                    f"Experience: {exp}." if exp else "",
                    f"Location: {location or 'Flexible'} ({'Remote' if is_remote else 'Hybrid/On-site'}).",
                ]
                raw_desc = " ".join(b for b in meta_bits if b)

            url = str(j.get("applyUrl") or j.get("postingUrl") or f"https://jobs.smartrecruiters.com/{comp_id}/{job_id}")
            jobs.append(
                Job(
                    source=self.name,
                    source_id=job_id,
                    company=company,
                    title=title,
                    url=url,
                    location=location,
                    description=raw_desc,
                    remote=bool(is_remote) if isinstance(is_remote, bool) else None,
                    employment_type=emp,
                    posted_at=str(j.get("releasedDate") or ""),
                    extra={"board": slug},
                )
            )
        return jobs
