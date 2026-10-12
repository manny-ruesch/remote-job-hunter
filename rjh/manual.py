"""Mode « copier-coller » : la logique métier, sans web (le routeur FastAPI n'en est qu'une fine couche).

Cycle de vie d'un ticket :
    open  --(l'utilisateur colle la réponse)-->  done      toutes les offres du lot sont lues
                                          \\->  partial   certaines manquent : un NOUVEAU ticket (parent_id) est créé
                                                          automatiquement avec UNIQUEMENT les offres manquantes
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .analysis import KINDS, Analyzer, BatchBudget, ParseReport, parse_batch
from .llm import LLMConfig, ManualProvider
from .models import Job
from .profile import Profile


@dataclass
class ManualTicket:
    id: str
    kind: str                                   # "triage" | "dossier"
    status: str                                 # open | done | partial
    prompt: str                                 # le texte EXACT à coller dans le chat
    id_map: dict[str, str]                      # id local (« j3 ») -> job.id stable
    jobs: list[dict]                            # offres sérialisées : permet de reconstruire un prompt pour les manquantes
    created_at: float = 0.0
    parent_id: str | None = None
    report: dict = field(default_factory=dict)


class SqliteTicketStore:
    """Persistant (fichier) ou éphémère (« :memory: »). Sûr entre threads."""

    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS manual_tickets (id TEXT PRIMARY KEY, kind TEXT, status TEXT, prompt TEXT,"
                " id_map TEXT, jobs TEXT, created_at REAL, parent_id TEXT, response TEXT, report TEXT)"
            )
            self._db.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> ManualTicket:
        return ManualTicket(r["id"], r["kind"], r["status"], r["prompt"], json.loads(r["id_map"]), json.loads(r["jobs"]),
                            r["created_at"], r["parent_id"], json.loads(r["report"] or "{}"))

    def create(self, kind: str, prompt: str, items: list[tuple[str, Job]], parent_id: str | None = None) -> ManualTicket:
        t = ManualTicket(
            id=uuid.uuid4().hex[:10], kind=kind, status="open", prompt=prompt,
            id_map={local: job.id for local, job in items},
            jobs=[dataclasses.asdict(job) for _, job in items], created_at=time.time(), parent_id=parent_id,
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO manual_tickets (id,kind,status,prompt,id_map,jobs,created_at,parent_id) VALUES (?,?,?,?,?,?,?,?)",
                (t.id, t.kind, t.status, t.prompt, json.dumps(t.id_map), json.dumps(t.jobs), t.created_at, t.parent_id),
            )
            self._db.commit()
        return t

    def get(self, ticket_id: str) -> ManualTicket | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM manual_tickets WHERE id=?", (ticket_id,)).fetchone()
        return self._row(r) if r else None

    def close(self, ticket_id: str, status: str, response: str, report: dict) -> None:
        with self._lock:
            self._db.execute("UPDATE manual_tickets SET status=?, response=?, report=? WHERE id=?",
                             (status, response, json.dumps(report), ticket_id))
            self._db.commit()

    def list_open(self) -> list[ManualTicket]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM manual_tickets WHERE status='open' ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]


@dataclass
class ImportReport:
    ticket_id: str
    status: str                                 # done | partial
    results: dict[str, Any]                     # job.id -> TriageResult | DossierResult
    parse: ParseReport
    followup: ManualTicket | None = None        # nouveau ticket pour les offres manquantes
    already_closed: bool = False

    def summary(self) -> dict:
        """Forme JSON pour l'API / l'interface."""
        return {
            "ticket_id": self.ticket_id, "status": self.status,
            "accepted": len(self.results), "missing": len(self.parse.missing),
            "invalid": self.parse.invalid, "unexpected": self.parse.unexpected,
            "warnings": self.parse.warnings, "truncated": self.parse.truncated,
            "followup_ticket": self.followup.id if self.followup else None,
        }


class ManualService:
    def __init__(self, store: SqliteTicketStore, profile: Profile, budget: BatchBudget | None = None):
        self.store, self.profile, self.budget = store, profile, budget

    def create_batches(self, jobs: list[Job], kind: str = "triage") -> list[ManualTicket]:
        """Découpe `jobs` en lots et crée un ticket (= un prompt à coller) par lot."""
        analyzer = Analyzer(ManualProvider(LLMConfig(provider="manual")), self.profile, budget=self.budget, store=self.store)
        return analyzer.run(kind, jobs).pending

    def import_response(self, ticket_id: str, text: str) -> ImportReport:
        t = self.store.get(ticket_id)
        if t is None:
            raise KeyError(f"ticket inconnu : {ticket_id}")
        kind = KINDS[t.kind]
        if t.status != "open":
            return ImportReport(t.id, t.status, {}, ParseReport(warnings=["ticket déjà traité"]), already_closed=True)
        rep = parse_batch(text, list(t.id_map), kind.validator)
        results = {t.id_map[local]: obj for local, obj in rep.results.items()}
        followup = None
        if rep.missing:
            # Re-file automatique : on ne redemande QUE les offres manquantes, avec les mêmes contrats.
            items = []
            for n, local in enumerate(rep.missing, 1):
                d = t.jobs[list(t.id_map).index(local)]
                items.append((f"j{n}", Job(**d)))
            b = self.budget or BatchBudget.for_kind(t.kind)
            system, user = kind.builder(self.profile, items, b.desc_chars)
            followup = self.store.create(t.kind, f"{system}\n\n{user}", items, parent_id=t.id)
        status = "done" if not rep.missing else "partial"
        self.store.close(t.id, status, text, {"accepted": len(results), "missing": rep.missing, "warnings": rep.warnings})
        return ImportReport(t.id, status, results, rep, followup)
