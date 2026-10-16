"""Moteur de scan unifié (`rjh scan`) piloté par `profile.yaml`.

Orchestre le cycle complet :
  1. Collecte parallèle multi-sources (`rjh.sources.collect`)
  2. Dédoublonnage SQLite WAL (`id`, `dedupe_key` et URL canonique)
  3. Pré-tri lexical heuristique (`ProfileScorer`)
  4. Évaluation IA en 2 passes (`triage` puis `dossier` sur les offres HOT)
     AVEC bascule automatique vers les tickets Mode Manuel (`SqliteTicketStore`)
     si aucune clé API n'est configurée ou si `provider: manual`.
  5. Sauvegarde non-destructive dans `jobs.db` + alertes Discord / Digest Markdown.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .analysis import Analyzer, DossierResult, TriageResult
from .heuristic import ProfileScorer, ScoreDetail
from .http import HttpClient
from .llm import LLMAuthError, LLMConfig, LLMProvider, ManualProvider, ModelUnavailableError, create_provider
from .manual import ManualService, ManualTicket, SqliteTicketStore
from .messages import t
from .models import Job
from .paths import db_path, profile_path
from .profile import load_profile
from .sources import collect


_SOURCE_LABELS = {
    "ashby": "Ashby",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "wttj": "WTTJ",
    "wwr": "We Work Remotely",
    "remotive": "Remotive",
    "jobicy": "Jobicy",
    "hn": "Hacker News",
    "adzuna": "Adzuna",
}


def format_source_label(job: Job) -> str:
    base = _SOURCE_LABELS.get(job.source.lower(), job.source.capitalize())
    board = job.extra.get("board")
    if board:
        return f"{base} · {str(board).capitalize()}"
    if job.company and "·" not in base:
        return f"{base} · {job.company}"
    return base


def _clean_url(url: str) -> str:
    return (url or "").split("?")[0].rstrip("/").lower()


def init_jobs_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10.0)
    try:
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA busy_timeout = 5000;")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                source TEXT,
                company TEXT,
                title TEXT,
                location TEXT,
                url TEXT,
                salary TEXT,
                description TEXT,
                fit_score REAL DEFAULT 0.0,
                tech_match TEXT,
                pitch_hook TEXT,
                tailored_pitch TEXT,
                proof_of_work TEXT,
                cold_outreach TEXT,
                job_type TEXT DEFAULT 'full_time',
                status TEXT DEFAULT 'new',
                analysis_status TEXT DEFAULT 'ok',
                rationale TEXT,
                gate TEXT,
                profile_fp TEXT,
                dedupe_key TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
        migrations = {
            "proof_of_work": "TEXT",
            "cold_outreach": "TEXT",
            "job_type": "TEXT DEFAULT 'full_time'",
            "analysis_status": "TEXT DEFAULT 'ok'",
            "rationale": "TEXT",
            "gate": "TEXT",
            "profile_fp": "TEXT",
            "dedupe_key": "TEXT",
        }
        for col, spec in migrations.items():
            if col not in cols:
                con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {spec}")
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_fit ON jobs(status, fit_score);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_type_fit ON jobs(job_type, fit_score);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_analysis_status ON jobs(analysis_status);")
        con.commit()
    finally:
        con.close()


def filter_unseen_jobs(db_file: Path, jobs: list[Job]) -> list[Job]:
    """Écarte les offres déjà évaluées (`analysis_status='ok'` et score > 0 ou statut traité).
    Si une offre existe déjà avec `analysis_status IN ('failed', 'pending_manual')`, conserve son `db_id`."""
    if not db_file.exists():
        return list(jobs)
    con = sqlite3.connect(db_file, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, dedupe_key, url, status, fit_score, analysis_status FROM jobs"
        ).fetchall()
    except sqlite3.Error:
        con.close()
        return list(jobs)
    finally:
        con.close()

    done_ids: set[str] = set()
    done_dedupe: set[str] = set()
    done_urls: set[str] = set()
    retry_by_key: dict[str, str] = {}

    for r in rows:
        jid = r["id"] or ""
        dkey = r["dedupe_key"] or ""
        curl = _clean_url(r["url"] or "")
        a_status = (r["analysis_status"] or "ok").lower()
        status = (r["status"] or "new").lower()
        score = float(r["fit_score"] or 0.0)

        is_evaluated = (
            a_status == "ok"
            and (score > 0.0 or status in ("screened", "hot", "applied", "interview", "archived"))
        )
        if is_evaluated:
            if jid:
                done_ids.add(jid)
            if dkey:
                done_dedupe.add(dkey)
            if curl:
                done_urls.add(curl)
        else:
            if jid:
                retry_by_key[jid] = jid
            if dkey:
                retry_by_key[dkey] = jid
            if curl:
                retry_by_key[curl] = jid

    unseen: list[Job] = []
    for job in jobs:
        curl = _clean_url(job.url)
        if job.id in done_ids or job.dedupe_key in done_dedupe or (curl and curl in done_urls):
            continue
        existing_id = retry_by_key.get(job.id) or retry_by_key.get(job.dedupe_key) or (retry_by_key.get(curl) if curl else None)
        if existing_id:
            job.extra["db_id"] = existing_id
        unseen.append(job)
    return unseen


def _format_salary(job: Job) -> str:
    if job.extra.get("salary_text"):
        return str(job.extra["salary_text"])
    if job.salary_min and job.salary_max:
        return f"{job.salary_min:,.0f} - {job.salary_max:,.0f} {job.salary_currency}".strip()
    if job.salary_min:
        return f"{job.salary_min:,.0f} {job.salary_currency}".strip()
    return ""


def upsert_job_record(
    db_file: Path,
    job: Job,
    *,
    fit_score: float = 0.0,
    tech_match: str = "",
    pitch_hook: str = "",
    tailored_pitch: str = "",
    proof_of_work: str = "",
    cold_outreach: str = "",
    status: str = "new",
    analysis_status: str = "ok",
    rationale: str = "",
    gate: str | None = None,
    profile_fp: str = "",
) -> None:
    con = sqlite3.connect(db_file, timeout=10.0)
    try:
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA busy_timeout = 5000;")
        emp = job.employment_type or "full_time"
        if emp == "freelance":
            emp = "contract"
        con.execute(
            """
            INSERT INTO jobs (
                id, source, company, title, location, url, salary, description,
                fit_score, tech_match, pitch_hook, tailored_pitch, proof_of_work, cold_outreach,
                job_type, status, analysis_status, rationale, gate, profile_fp, dedupe_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source = excluded.source,
                company = excluded.company,
                title = excluded.title,
                location = excluded.location,
                url = excluded.url,
                salary = CASE WHEN excluded.salary != '' THEN excluded.salary ELSE jobs.salary END,
                description = CASE WHEN length(excluded.description) > length(jobs.description) THEN excluded.description ELSE jobs.description END,
                fit_score = CASE WHEN excluded.fit_score > 0 THEN excluded.fit_score ELSE jobs.fit_score END,
                tech_match = CASE WHEN excluded.tech_match != '' THEN excluded.tech_match ELSE jobs.tech_match END,
                pitch_hook = CASE WHEN excluded.pitch_hook != '' THEN excluded.pitch_hook ELSE jobs.pitch_hook END,
                tailored_pitch = CASE WHEN excluded.tailored_pitch != '' THEN excluded.tailored_pitch ELSE jobs.tailored_pitch END,
                proof_of_work = CASE WHEN excluded.proof_of_work != '' THEN excluded.proof_of_work ELSE jobs.proof_of_work END,
                cold_outreach = CASE WHEN excluded.cold_outreach != '' THEN excluded.cold_outreach ELSE jobs.cold_outreach END,
                job_type = excluded.job_type,
                analysis_status = excluded.analysis_status,
                rationale = CASE WHEN excluded.rationale != '' THEN excluded.rationale ELSE jobs.rationale END,
                gate = COALESCE(excluded.gate, jobs.gate),
                profile_fp = excluded.profile_fp,
                dedupe_key = excluded.dedupe_key,
                status = CASE
                    WHEN jobs.status IN ('applied', 'interview', 'archived') THEN jobs.status
                    ELSE excluded.status
                END
            """,
            (
                job.id,
                format_source_label(job),
                job.company,
                job.title,
                job.location or "Remote",
                job.url,
                _format_salary(job),
                job.description,
                fit_score,
                tech_match,
                pitch_hook,
                tailored_pitch,
                proof_of_work,
                cold_outreach,
                emp,
                status,
                analysis_status,
                rationale,
                gate,
                profile_fp,
                job.dedupe_key,
            ),
        )
        con.commit()
    finally:
        con.close()


@dataclass
class ScanReport:
    collected: int = 0
    unseen: int = 0
    viable: int = 0
    selected: int = 0
    evaluated: int = 0
    hot: int = 0
    failed: int = 0
    mode: str = "api"                       # "api" | "manual" | "manual_fallback" | "dry_run"
    tickets: list[str] = field(default_factory=list)
    source_reports: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _default_notify(job_dict: dict[str, Any]) -> None:
    try:
        import notifier
        notifier.send_discord_alert(job_dict)
        notifier.append_to_markdown_digest(job_dict)
    except Exception:
        pass


def run_scan(
    home: Path,
    *,
    lang: str = "fr",
    max_jobs: int | None = None,
    manual_override: bool = False,
    notify: bool = True,
    dry_run: bool = False,
    sources: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
    http: HttpClient | None = None,
    provider: LLMProvider | None = None,
    notify_fn: Callable[[dict[str, Any]], None] | None = None,
    out: Callable[[str], None] | None = None,
) -> ScanReport:
    t0 = time.monotonic()
    env = os.environ if environ is None else environ
    emit = out or (lambda _msg: None)
    prof_file = profile_path(home)
    profile = load_profile(prof_file)
    db_file = db_path(home, env)
    init_jobs_db(db_file)
    scorer = ProfileScorer(profile)
    fp = profile.fingerprint()

    emit(t(lang, "sc_start", n_sources=len(sources or profile.sources.enabled), hot=profile.scoring.hot_threshold))

    # 1. Collecte multi-sources
    raw_jobs, src_reports = collect(
        profile,
        prefilter=scorer.title_prefilter,
        names=sources,
        http=http,
        env=env,
    )
    report = ScanReport(collected=len(raw_jobs))
    for sr in src_reports:
        report.source_reports.append({
            "source": sr.source,
            "ok": sr.ok,
            "jobs": len(sr.jobs),
            "errors": sr.errors,
            "duration_s": round(sr.duration, 2),
        })
        if sr.errors:
            for err in sr.errors:
                report.warnings.append(f"{sr.source}: {err}")

    # 2. Dédoublonnage SQLite
    unseen = filter_unseen_jobs(db_file, raw_jobs)
    report.unseen = len(unseen)

    # 3. Pré-tri heuristique ProfileScorer
    ranked: list[tuple[Job, ScoreDetail]] = scorer.rank(unseen)
    viable: list[tuple[Job, ScoreDetail]] = [(j, d) for j, d in ranked if scorer.viable(d)]
    report.viable = len(viable)

    limit = max_jobs if max_jobs is not None else profile.llm.max_jobs_per_run
    chosen_pairs = viable[:limit] if viable else ranked[: min(limit, 10)]
    to_evaluate = [j for j, _ in chosen_pairs]
    report.selected = len(to_evaluate)

    emit(t(lang, "sc_collected", collected=report.collected, unseen=report.unseen, viable=report.viable, selected=report.selected))

    if not to_evaluate:
        emit(t(lang, "sc_nothing"))
        report.elapsed_s = round(time.monotonic() - t0, 2)
        return report

    if dry_run:
        report.mode = "dry_run"
        for job, detail in chosen_pairs:
            emit(f"  [{detail.score:+.0f} pts] {job.company} — {job.title} ({job.location or 'Remote'})")
        report.elapsed_s = round(time.monotonic() - t0, 2)
        return report

    # 4. Résolution du fournisseur IA ou bascule automatique vers le Mode Manuel
    active_provider = provider
    fallback_manual = False
    if active_provider is None:
        if manual_override or profile.llm.provider.lower() == "manual":
            active_provider = ManualProvider(LLMConfig(provider="manual"), http=http)
            report.mode = "manual"
        else:
            try:
                active_provider = create_provider(profile.llm, env=env, http=http)
                report.mode = "api"
            except LLMAuthError:
                active_provider = ManualProvider(LLMConfig(provider="manual"), http=http)
                report.mode = "manual_fallback"
                fallback_manual = True

    def _enqueue_manual(reason_msg: str) -> ScanReport:
        emit(reason_msg)
        for job in to_evaluate:
            upsert_job_record(
                db_file,
                job,
                status="new",
                analysis_status="pending_manual",
                profile_fp=fp,
            )
        store = SqliteTicketStore(str(db_file))
        service = ManualService(store, profile)
        tickets: list[ManualTicket] = service.create_batches(to_evaluate, kind="triage")
        report.tickets = [tk.id for tk in tickets]
        emit(t(lang, "sc_tickets_created", n=len(tickets), ids=", ".join(report.tickets)))
        report.elapsed_s = round(time.monotonic() - t0, 2)
        return report

    if active_provider.interactive:
        msg = (
            t(lang, "sc_fallback_manual", provider=profile.llm.provider, var=profile.llm.api_key_env or "API_KEY")
            if fallback_manual
            else t(lang, "sc_manual_mode")
        )
        return _enqueue_manual(msg)

    # 5. Mode API : Triage + Dossier pour les offres HOT (avec repli automatique si clé refusée / modèle retiré)
    analyzer = Analyzer(active_provider, profile)
    try:
        triage_out = analyzer.run("triage", to_evaluate)
    except (LLMAuthError, ModelUnavailableError) as e:
        report.mode = "manual_fallback"
        report.warnings.append(str(e))
        return _enqueue_manual(
            t(lang, "sc_fallback_manual", provider=profile.llm.provider, var=profile.llm.api_key_env or "API_KEY")
        )
    report.warnings.extend(triage_out.warnings)
    report.failed = len(triage_out.failed)

    hot_jobs: list[Job] = []
    for job in to_evaluate:
        tr: TriageResult | None = triage_out.results.get(job.id)
        if tr is None:
            # Ne jamais enregistrer un échec à 0.0 comme « évalué » (Pitfall #52)
            upsert_job_record(
                db_file,
                job,
                status="new",
                analysis_status="failed",
                profile_fp=fp,
            )
            continue
        report.evaluated += 1
        if tr.score >= profile.scoring.hot_threshold:
            hot_jobs.append(job)

    dossier_results: dict[str, DossierResult] = {}
    if hot_jobs:
        dossier_out = analyzer.run("dossier", hot_jobs)
        report.warnings.extend(dossier_out.warnings)
        dossier_results = dossier_out.results

    notifier_cb = notify_fn or (_default_notify if notify else (lambda _j: None))

    for job in to_evaluate:
        tr = triage_out.results.get(job.id)
        if tr is None:
            continue
        dr = dossier_results.get(job.id)
        if dr is not None:
            dr.company = job.company
            dr.title = job.title
        is_hot = tr.score >= profile.scoring.hot_threshold
        status = "hot" if is_hot else "screened"
        hook = dr.hook if dr else tr.why
        pitch = dr.pitch if dr else ""
        pow_str = dr.proof_of_work if dr else ""
        cold_str = dr.cold_outreach if dr else ""

        upsert_job_record(
            db_file,
            job,
            fit_score=tr.score,
            tech_match=", ".join(tr.keywords),
            pitch_hook=hook,
            tailored_pitch=pitch,
            proof_of_work=pow_str,
            cold_outreach=cold_str,
            status=status,
            analysis_status="ok",
            rationale=tr.why,
            gate=tr.gate,
            profile_fp=fp,
        )
        if is_hot:
            report.hot += 1
            if notify:
                notifier_cb({
                    "id": job.id,
                    "source": format_source_label(job),
                    "company": job.company,
                    "title": job.title,
                    "location": job.location or "Remote",
                    "url": job.url,
                    "salary": _format_salary(job),
                    "job_type": job.employment_type or "full_time",
                    "fit_score": tr.score,
                    "tech_match": tr.keywords,
                    "pitch_hook": hook,
                    "tailored_pitch": pitch,
                    "proof_of_work": json.loads(pow_str) if pow_str else None,
                    "cold_outreach": json.loads(cold_str) if cold_str else None,
                })

    emit(t(lang, "sc_done", evaluated=report.evaluated, hot=report.hot, failed=report.failed))
    report.elapsed_s = round(time.monotonic() - t0, 2)
    return report
