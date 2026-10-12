"""Greenhouse : source ATS (BoardSource). `?content=true` renvoie la description COMPLÈTE dans la liste :
plus besoin d'ouvrir chaque offre dans un navigateur furtif pour la lire."""
from __future__ import annotations

from ..models import Job
from ..textutil import strip_html
from . import register
from .base import BoardSource, RateLimit


@register
class GreenhouseSource(BoardSource):
    name = "greenhouse"
    rate_limit = RateLimit(min_interval=0.3)
    BASE = "https://boards-api.greenhouse.io/v1/boards"

    def board_request(self, slug: str):
        return f"{self.BASE}/{slug}/jobs", {"content": "true"}, {}

    def parse(self, payload, **ctx):
        company = ctx.get("company", "")
        return [
            Job(
                source=self.name, source_id=str(j.get("id", "")), company=j.get("company_name") or company,
                title=j.get("title", ""), url=j.get("absolute_url", ""),
                location=(j.get("location") or {}).get("name", ""),
                description=strip_html(j.get("content", "")), posted_at=j.get("updated_at", ""),
                extra={"board": ctx.get("slug", "")},
            )
            for j in (payload or {}).get("jobs", [])
        ]
