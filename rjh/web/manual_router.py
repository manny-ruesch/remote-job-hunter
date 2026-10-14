"""Routeur FastAPI du mode copier-coller. Couche volontairement mince : la logique est dans rjh.manual.

Branchement dans dashboard_server.py (3 lignes) :

    from rjh.manual import ManualService, SqliteTicketStore
    from rjh.web.manual_router import make_manual_router
    service = ManualService(SqliteTicketStore("jobs.db"), profile)
    app.include_router(make_manual_router(service, pending_jobs=get_unscored_jobs, apply_results=save_results,
                                          require_auth=check_token))

`pending_jobs(kind, limit)` -> list[Job] : offres à analyser (vous les lisez dans votre base).
`apply_results(kind, {job.id: TriageResult|DossierResult})` : vous les écrivez dans votre base.
`require_auth` : OBLIGATOIRE dès que le dashboard est exposé (tunnel) : ce routeur envoie vos prompts.
"""
from __future__ import annotations

from typing import Any, Callable
from pydantic import BaseModel

from ..manual import ManualService
from ..models import Job
from ..textutil import est_tokens

MAX_PASTE_CHARS = 200_000


class NewBatches(BaseModel):
    kind: str = "triage"
    limit: int = 30


class ImportBody(BaseModel):
    text: str


def make_manual_router(service: ManualService, *, pending_jobs: Callable[[str, int], list[Job]],
                       apply_results: Callable[[str, dict[str, Any]], None],
                       require_auth: Callable[..., Any] | None = None):
    from fastapi import APIRouter, Depends, HTTPException     # import tardif : dépendance du seul dashboard

    router = APIRouter(prefix="/api/manual", dependencies=[Depends(require_auth)] if require_auth else [])

    def brief(t) -> dict:
        return {"id": t.id, "kind": t.kind, "status": t.status, "n_jobs": len(t.id_map),
                "est_tokens": est_tokens(t.prompt), "parent_id": t.parent_id}

    @router.post("/batches")
    def create_batches(body: NewBatches):
        if body.kind not in ("triage", "dossier"):
            raise HTTPException(400, "kind doit valoir triage ou dossier")
        jobs = pending_jobs(body.kind, max(1, min(body.limit, 200)))
        return {"tickets": [brief(t) for t in service.create_batches(jobs, body.kind)]}

    @router.get("/tickets")
    def open_tickets():
        return {"tickets": [brief(t) for t in service.store.list_open()]}

    @router.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: str):
        t = service.store.get(ticket_id)
        if t is None:
            raise HTTPException(404, "ticket inconnu")
        return {**brief(t), "prompt": t.prompt}

    @router.post("/tickets/{ticket_id}/import")
    def import_response(ticket_id: str, body: ImportBody):
        if len(body.text) > MAX_PASTE_CHARS:
            raise HTTPException(413, "réponse trop longue")
        try:
            report = service.import_response(ticket_id, body.text)
        except KeyError:
            raise HTTPException(404, "ticket inconnu")
        if report.results:
            apply_results(service.store.get(ticket_id).kind, report.results)
        return report.summary()

    return router
