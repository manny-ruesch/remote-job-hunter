import os
import sys
import json
import hmac
import subprocess
from pathlib import Path
from typing import Optional, Any

try:
    import psutil
except ImportError:
    psutil = None

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Add remote_job_hunter root to sys.path
HUNTER_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(HUNTER_DIR))

import cv_profiler

import db
import config
from rjh.profile import load_profile
from rjh.manual import ManualService, SqliteTicketStore
from rjh.web.manual_router import make_manual_router
from rjh.models import Job
from rjh.liveness import (
    check_job_liveness,
    compute_followup_status,
    detect_reposts_in_db,
    format_utc,
    parse_utc,
    run_liveness_batch,
    utc_now,
)
from rjh.market_radar import add_skill_to_profile, analyze_market_radar
from rjh.discovery import (
    discover_boards_from_db,
    import_linkedin_connections_csv,
    match_warm_intros_for_jobs,
    probe_company_ats,
    sync_discovered_boards_to_profile,
)

ACTIVE_HOME = Path(os.environ["RJH_HOME"]).expanduser() if os.environ.get("RJH_HOME") else HUNTER_DIR
PROFILE_PATH = ACTIVE_HOME / "profile.yaml" if (ACTIVE_HOME / "profile.yaml").exists() else HUNTER_DIR / "profile.yaml"
current_rjh_profile = load_profile(PROFILE_PATH if PROFILE_PATH.exists() else HUNTER_DIR / "profile.example.yaml")
ACTIVE_DB = os.environ.get("RJH_DB") or str((ACTIVE_HOME if (ACTIVE_HOME / "jobs.db").exists() else HUNTER_DIR) / "jobs.db")
manual_store = SqliteTicketStore(ACTIVE_DB)
manual_service = ManualService(manual_store, current_rjh_profile)


def _active_db_path() -> str:
    if os.environ.get("RJH_DB"):
        return os.environ["RJH_DB"]
    if os.environ.get("RJH_HOME"):
        return str(Path(os.environ["RJH_HOME"]).expanduser() / "jobs.db")
    return ACTIVE_DB


def _count_configured_sources() -> int:
    s = current_rjh_profile.sources
    board_sources = set(s.boards.keys()) & set(s.enabled)
    total_boards = sum(len(s.boards.get(b, [])) for b in board_sources)
    other_sources = len([name for name in s.enabled if name not in board_sources])
    return max(1, total_boards + other_sources)


def check_rjh_token(request: Request) -> None:
    expected = os.environ.get("RJH_TOKEN", "").strip()
    if not expected:
        return
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    candidate = bearer or request.headers.get("x-rjh-token", "") or request.query_params.get("token", "") or request.cookies.get("rjh_token", "")
    if not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(status_code=401, detail="Accès refusé : jeton RJH_TOKEN requis.")

def get_unscored_jobs(kind: str, limit: int) -> list[Job]:
    conn = db.get_conn()
    c = conn.cursor()
    if kind == "triage":
        c.execute("""
            SELECT id, title, company, location, url, description, salary, job_type 
            FROM jobs 
            WHERE (analysis_status = 'pending_manual' OR fit_score = 0.0) AND description IS NOT NULL AND length(description) > 30
            ORDER BY created_at DESC LIMIT ?
        """, (limit,))
        rows = c.fetchall()
        if not rows:
            c.execute("""
                SELECT id, title, company, location, url, description, salary, job_type 
                FROM jobs 
                WHERE description IS NOT NULL AND length(description) > 30
                ORDER BY created_at DESC LIMIT ?
            """, (min(limit, 10),))
            rows = c.fetchall()
    else:
        c.execute("""
            SELECT id, title, company, location, url, description, salary, job_type 
            FROM jobs 
            WHERE fit_score >= 7.5 AND (tailored_pitch IS NULL OR tailored_pitch = '')
            ORDER BY fit_score DESC LIMIT ?
        """, (limit,))
        rows = c.fetchall()
    conn.close()
    
    return [
        Job(
            source="db",
            source_id=r["id"],
            company=r["company"] or "",
            title=r["title"] or "",
            location=r["location"] or "",
            url=r["url"] or "",
            description=r["description"] or "",
            employment_type=r["job_type"] or "full_time",
            extra={"db_id": r["id"]}
        )
        for r in rows
    ]

def apply_manual_results(kind: str, results: dict[str, Any]):
    conn = db.get_conn()
    c = conn.cursor()
    hot_th = current_rjh_profile.scoring.hot_threshold
    for job_id, res in results.items():
        if kind == "triage":
            kws = ", ".join(getattr(res, "keywords", []) or [])
            c.execute("""
                UPDATE jobs 
                SET fit_score = ?,
                    rationale = ?,
                    gate = ?,
                    tech_match = CASE WHEN ? != '' THEN ? ELSE tech_match END,
                    pitch_hook = CASE WHEN pitch_hook IS NULL OR pitch_hook = '' THEN ? ELSE pitch_hook END,
                    status = CASE
                        WHEN status IN ('applied', 'interview', 'archived') THEN status
                        WHEN ? >= ? THEN 'hot'
                        ELSE 'screened'
                    END,
                    analysis_status = 'ok'
                WHERE id = ?
            """, (res.score, res.why, res.gate, kws, kws, res.why, res.score, hot_th, job_id))
        elif kind == "dossier":
            c.execute("""
                UPDATE jobs 
                SET pitch_hook = CASE WHEN ? != '' THEN ? ELSE pitch_hook END,
                    tailored_pitch = ?,
                    proof_of_work = ?,
                    cold_outreach = ?,
                    analysis_status = 'ok'
                WHERE id = ?
            """, (res.hook, res.hook, res.tailored_pitch, res.proof_of_work, res.cold_outreach, job_id))
    conn.commit()
    conn.close()

app = FastAPI(title="Remote Job Hunter — Command Center")


@app.middleware("http")
async def rjh_token_middleware(request: Request, call_next):
    expected = os.environ.get("RJH_TOKEN", "").strip()
    if expected and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        auth = request.headers.get("authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        candidate = (
            bearer
            or request.headers.get("x-rjh-token", "")
            or request.query_params.get("token", "")
            or request.cookies.get("rjh_token", "")
        )
        if not candidate or not hmac.compare_digest(candidate, expected):
            return JSONResponse(status_code=401, content={"detail": "Accès refusé : jeton RJH_TOKEN requis."})
    return await call_next(request)


app.include_router(make_manual_router(
    manual_service,
    pending_jobs=get_unscored_jobs,
    apply_results=apply_manual_results,
    require_auth=check_rjh_token,
))

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5070",
        "http://localhost:5070",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if not STATIC_DIR.exists():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

def is_hunter_running() -> tuple[bool, Optional[int]]:
    if psutil is None:
        return False, None
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if ("run_hunter.py" in cmd or ("rjh" in cmd and "scan" in cmd)) and "python" in p.info["name"].lower():
                return True, p.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False, None

def get_latest_log_lines(n: int = 50) -> list[str]:
    log_file = HUNTER_DIR / "hunter.log"
    if not log_file.exists():
        return []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return [line.rstrip() for line in lines[-n:]]
    except Exception:
        return []

@app.get("/api/stats")
async def get_stats():
    stats = db.get_pipeline_stats()
    is_running, pid = is_hunter_running()
    
    # Count applied, interviews, part-time, and archived
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'")
    applied_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'interview'")
    interview_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'archived'")
    archived_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM jobs WHERE job_type IN ('part_time', 'contract')")
    part_time_count = c.fetchone()[0]
    conn.close()

    return {
        "total_scouted": stats["total_jobs"],
        "hot_qualified": stats["hot_jobs"],
        "applied_count": applied_count,
        "interview_count": interview_count,
        "archived_count": archived_count,
        "part_time_count": part_time_count,
        "by_status": stats["by_status"],
        "by_source": stats["by_source"],
        "is_running": is_running,
        "running_pid": pid,
        "last_log_preview": get_latest_log_lines(5)
    }

@app.get("/api/jobs")
async def list_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    min_score: float = 0.0,
    search: Optional[str] = None,
    limit: int = 500
):
    conn = db.get_conn()
    c = conn.cursor()
    
    query = "SELECT * FROM jobs WHERE 1=1"
    params = []
    
    if status and status != "all":
        query += " AND status = ?"
        params.append(status)
        
    if job_type and job_type != "all":
        if job_type in ("part_time", "contract"):
            query += " AND (job_type = 'part_time' OR job_type = 'contract')"
        else:
            query += " AND job_type = ?"
            params.append(job_type)
        
    if min_score > 0:
        query += " AND fit_score >= ?"
        params.append(min_score)
        
    if search:
        query += " AND (company LIKE ? OR title LIKE ? OR tech_match LIKE ?)"
        s = f"%{search}%"
        params.extend([s, s, s])
        
    query += " ORDER BY fit_score DESC, created_at DESC LIMIT ?"
    params.append(limit)
    
    c.execute(query, tuple(params))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    # Parse proof_of_work & cold_outreach JSON if present + compute follow-up state
    now_dt = utc_now()
    for r in rows:
        pow_val = r.get("proof_of_work")
        if pow_val:
            try:
                r["proof_of_work"] = json.loads(pow_val) if isinstance(pow_val, str) else pow_val
            except Exception:
                pass
        cold_val = r.get("cold_outreach")
        if cold_val:
            try:
                r["cold_outreach"] = json.loads(cold_val) if isinstance(cold_val, str) else cold_val
            except Exception:
                pass
        r["followup"] = compute_followup_status(r, now=now_dt).to_dict()
                
    return rows

@app.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: str):
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    pow_val = job.get("proof_of_work")
    if pow_val:
        try:
            job["proof_of_work"] = json.loads(pow_val) if isinstance(pow_val, str) else pow_val
        except Exception:
            pass
            
    cold_val = job.get("cold_outreach")
    if cold_val:
        try:
            job["cold_outreach"] = json.loads(cold_val) if isinstance(cold_val, str) else cold_val
        except Exception:
            pass
    job["followup"] = compute_followup_status(job).to_dict()
    return job

@app.post("/api/jobs/{job_id}/status")
async def update_status(job_id: str, payload: dict[str, str]):
    new_status = payload.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="Missing status")
    db.update_job_status(job_id, new_status)
    updated = db.get_job_by_id(job_id)
    return {
        "success": True,
        "job_id": job_id,
        "new_status": new_status,
        "followup": compute_followup_status(updated).to_dict() if updated else None,
    }


@app.post("/api/jobs/{job_id}/check-liveness")
def check_single_job_liveness(job_id: str):
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    res = check_job_liveness(job.get("url") or "")
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE jobs SET liveness_status = ?, liveness_checked_at = ? WHERE id = ?",
            (res.status, res.checked_at, job_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "success": True,
        "job_id": job_id,
        **res.to_dict(),
    }


@app.post("/api/liveness/batch")
def check_batch_liveness(payload: Optional[dict[str, Any]] = None):
    body = payload or {}
    raw_limit = body.get("limit")
    raw_min_score = body.get("min_score")
    raw_ttl = body.get("ttl_hours")
    limit = int(raw_limit) if raw_limit is not None else 15
    min_score = float(raw_min_score) if raw_min_score is not None else 7.0
    ttl_hours = int(raw_ttl) if raw_ttl is not None else 6
    active_db = os.environ.get("RJH_DB") or (
        os.path.join(os.environ["RJH_HOME"], "jobs.db") if os.environ.get("RJH_HOME") else config.DB_PATH
    )
    summary = run_liveness_batch(
        active_db,
        limit=limit,
        min_score=min_score,
        ttl_hours=ttl_hours,
    )
    return {"success": True, **summary}


@app.post("/api/jobs/{job_id}/followup-sent")
def record_followup_sent(job_id: str):
    from datetime import timedelta
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    now_dt = utc_now()
    current_count = int(job.get("followup_count") or 0) + 1
    current_next = parse_utc(job.get("next_followup_at")) or now_dt
    anchor = max(now_dt, current_next)
    next_iso = format_utc(anchor + timedelta(days=7))
    applied_iso = job.get("applied_at") or format_utc(now_dt)

    conn = db.get_conn()
    try:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'applied',
                applied_at = COALESCE(applied_at, ?),
                followup_count = ?,
                next_followup_at = ?
            WHERE id = ?
            """,
            (applied_iso, current_count, next_iso, job_id),
        )
        conn.commit()
    finally:
        conn.close()

    updated = db.get_job_by_id(job_id) or job
    return {
        "success": True,
        "job_id": job_id,
        "followup": compute_followup_status(updated, now=now_dt).to_dict(),
    }

@app.post("/api/jobs/{job_id}/copy-pitch")
async def copy_pitch(job_id: str):
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    pitch = job.get("tailored_pitch", "")
    if not pitch:
        raise HTTPException(status_code=400, detail="No pitch available")
        
    # Copy to Windows clipboard via clip.exe
    try:
        p = subprocess.Popen(["clip.exe"], stdin=subprocess.PIPE, close_fds=True)
        p.communicate(input=pitch.encode("utf-8"))
    except Exception:
        pass
        
    return {"success": True, "pitch": pitch}

@app.post("/api/jobs/{job_id}/auto-fill")
async def trigger_autofill(job_id: str):
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    script_path = HUNTER_DIR / "auto_fill_engine.py"
    # Launch in independent visible process
    subprocess.Popen([sys.executable, str(script_path), job_id], cwd=str(HUNTER_DIR))
    return {"success": True, "message": f"Navigateur d'Auto-Fill lancé pour {job.get('company')} !"}

@app.post("/api/jobs/{job_id}/generate-cover-letter")
async def generate_cover_letter(job_id: str):
    import cover_letter_generator
    success, pdf_path, letter_text = cover_letter_generator.create_cover_letter_pdf(job_id)
    if not success:
        raise HTTPException(status_code=500, detail=pdf_path)
    return {
        "success": True,
        "pdf_path": pdf_path,
        "filename": Path(pdf_path).name,
        "download_url": f"/api/jobs/{job_id}/download-cover-letter",
        "text": letter_text
    }


@app.post("/api/jobs/{job_id}/generate-cv")
async def generate_tailored_cv(job_id: str):
    import cv_tailor
    success, payload = cv_tailor.create_tailored_cv_pdf(job_id, sync_active_cv=True)
    if not success:
        raise HTTPException(status_code=500, detail=payload.get("error", "CV compilation failed"))
    return payload


@app.get("/api/jobs/{job_id}/download-cv")
async def download_tailored_cv(job_id: str):
    import cv_tailor
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    safe_company = "".join(c for c in (job.get("company") or "Company") if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_") or "Company"
    pdf_path = cv_tailor.TAILORED_DIR / f"CV_Manny_Ruesch_{safe_company}.pdf"
    if not pdf_path.exists():
        ok, payload = cv_tailor.create_tailored_cv_pdf(job_id, sync_active_cv=True)
        if not ok:
            raise HTTPException(status_code=500, detail=payload.get("error", "CV compilation failed"))
        pdf_path = Path(payload["local_pdf_path"])
    return FileResponse(path=str(pdf_path), media_type="application/pdf", filename=pdf_path.name)


@app.get("/api/jobs/{job_id}/download-cover-letter")
async def download_cover_letter(job_id: str):
    import cover_letter_generator
    job = db.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    safe_company = "".join(c for c in (job.get("company") or "Company") if c.isalnum() or c in (" ", "-", "_")).strip()
    pdf_path = cover_letter_generator.DESKTOP_DIR / f"Cover_Letter_{safe_company}.pdf"
    if not pdf_path.exists():
        ok, out_path, _ = cover_letter_generator.create_cover_letter_pdf(job_id)
        if not ok:
            raise HTTPException(status_code=500, detail=out_path)
        pdf_path = Path(out_path)
    return FileResponse(path=str(pdf_path), media_type="application/pdf", filename=pdf_path.name)

def _run_hunter_worker():
    bat_path = HUNTER_DIR / "run_hunter.bat"
    subprocess.run(["cmd.exe", "/c", str(bat_path)], cwd=str(HUNTER_DIR))

@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "gemini_model": config.GEMINI_MODEL,
        "gemini_status": "operational" if bool(config.GOOGLE_API_KEY) else "manual_mode",
        "database": "sqlite_wal",
        "sources_count": _count_configured_sources(),
        "active_profile": cv_profiler.get_active_profile().get("target_role") or cv_profiler.get_active_profile().get("title", "AI Automation Builder")
    }

@app.get("/api/market-radar")
def get_market_radar(min_score: float = 0.0, limit: int = 500):
    global current_rjh_profile
    prof_file = PROFILE_PATH if PROFILE_PATH.exists() else (HUNTER_DIR / "profile.example.yaml")
    try:
        current_rjh_profile = load_profile(prof_file)
    except Exception:
        pass
    report = analyze_market_radar(
        db_file=ACTIVE_DB,
        profile=current_rjh_profile,
        candidate_json_path=cv_profiler.PROFILE_PATH,
        min_score=min_score,
        limit=limit,
    )
    return report.to_dict()


@app.post("/api/market-radar/add-keyword")
def add_market_radar_keyword(payload: dict[str, Any]):
    global current_rjh_profile
    skill = str(payload.get("skill") or "").strip()
    try:
        weight = float(payload.get("weight", 6.0))
    except (TypeError, ValueError):
        weight = 6.0
    if not skill:
        raise HTTPException(status_code=400, detail="Le paramètre 'skill' est requis.")
    prof_file = PROFILE_PATH if PROFILE_PATH.exists() else (HUNTER_DIR / "profile.yaml")
    try:
        res = add_skill_to_profile(
            skill=skill,
            profile_yaml_path=prof_file,
            candidate_json_path=cv_profiler.PROFILE_PATH,
            weight=weight,
        )
        if prof_file.exists():
            current_rjh_profile = load_profile(prof_file)
            manual_service.profile = current_rjh_profile
        return {"success": True, **res}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Impossible de mettre à jour le profil : {e}")


@app.get("/api/discovery/boards")
def get_discovered_boards():
    boards = discover_boards_from_db(_active_db_path(), current_rjh_profile)
    return {
        "success": True,
        "count": len(boards),
        "new_count": sum(1 for b in boards if not b.already_configured),
        "boards": [b.to_dict() for b in boards],
    }


@app.post("/api/discovery/probe")
async def probe_companies(request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    raw_companies = body.get("companies") or body.get("company") or []
    if isinstance(raw_companies, str):
        companies = [c.strip() for c in raw_companies.replace(";", ",").split(",") if c.strip()]
    elif isinstance(raw_companies, list):
        companies = [str(c).strip() for c in raw_companies if str(c).strip()]
    else:
        companies = []

    if not companies:
        raise HTTPException(status_code=400, detail="Veuillez fournir au moins un nom d'entreprise ou slug.")

    # Borne de sécurité anti-amplification (max 8 entreprises par appel HTTP)
    companies = companies[:8]
    discovered = []
    seen = set()
    for comp in companies:
        for b in probe_company_ats(comp, profile=current_rjh_profile, timeout=3.5):
            key = (b.provider, b.slug)
            if key not in seen:
                seen.add(key)
                discovered.append(b.to_dict())

    return {
        "success": True,
        "probed_companies": companies,
        "count": len(discovered),
        "boards": discovered,
    }


@app.post("/api/discovery/sync-boards")
async def sync_boards_endpoint(request: Request):
    global current_rjh_profile
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    boards = body.get("boards")
    prof_file = PROFILE_PATH if PROFILE_PATH.exists() else (HUNTER_DIR / "profile.yaml")

    if not boards:
        # Si aucun board n'est spécifié explicitement, synchroniser tous les nouveaux boards découverts en base
        db_boards = discover_boards_from_db(_active_db_path(), current_rjh_profile)
        boards = [b for b in db_boards if not b.already_configured]

    try:
        res = sync_discovered_boards_to_profile(boards, prof_file)
        if prof_file.exists():
            current_rjh_profile = load_profile(prof_file)
            manual_service.profile = current_rjh_profile
        return {
            "success": True,
            "configured_sources_count": _count_configured_sources(),
            **res,
        }
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Impossible de synchroniser profile.yaml : {e}")


@app.post("/api/referrals/import-csv")
async def import_referrals_csv(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Fichier CSV vide.")
    res = import_linkedin_connections_csv(content, _active_db_path())
    if res.get("imported", 0) == 0 and res.get("reason"):
        raise HTTPException(status_code=400, detail=res["reason"])
    matches = match_warm_intros_for_jobs(
        _active_db_path(),
        candidate_name=current_rjh_profile.identity.name,
        lang=current_rjh_profile.llm.language or "fr",
    )
    return {"success": True, "import": res, "matches": matches}


@app.get("/api/referrals/matches")
def get_referral_matches(limit: int = 50):
    matches = match_warm_intros_for_jobs(
        _active_db_path(),
        candidate_name=current_rjh_profile.identity.name,
        lang=current_rjh_profile.llm.language or "fr",
        limit=limit,
    )
    return {"success": True, **matches}


@app.get("/api/candidate-profile")
async def get_candidate_profile():
    return cv_profiler.get_active_profile()

@app.post("/api/upload-cv")
async def upload_cv(file: UploadFile = File(...)):
    fname = file.filename or ""
    if not fname.lower().endswith(".pdf"):
        raise HTTPException(400, "Seuls les fichiers PDF sont acceptés.")
        
    cv_dest = HUNTER_DIR / "uploaded_cv.pdf"
    with open(cv_dest, "wb") as f:
        content = await file.read()
        f.write(content)
        
    text = cv_profiler.extract_pdf_text(cv_dest)
    if not text:
        raise HTTPException(400, "Impossible d'extraire le texte du PDF.")
        
    profile = cv_profiler.profile_cv_with_gemini(text)
    if not profile:
        profile = cv_profiler.profile_cv_heuristic(text)
    cv_profiler.sync_profile_yaml(profile)
        
    return {
        "success": True,
        "filename": file.filename,
        "profile": profile,
        "message": f"Profil analysé : {profile.get('target_role', 'Candidat')} ({profile.get('domain', '')})"
    }

@app.post("/api/trigger-run")
async def trigger_run(background_tasks: BackgroundTasks):
    running, pid = is_hunter_running()
    if running:
        return {"success": False, "message": f"Hunter is already running (PID: {pid})"}
        
    background_tasks.add_task(_run_hunter_worker)
    return {"success": True, "message": "Remote Job Hunter run triggered in background!"}

@app.get("/api/logs")
async def get_logs(lines: int = 80):
    running, pid = is_hunter_running()
    return {
        "is_running": running,
        "pid": pid,
        "lines": get_latest_log_lines(lines)
    }

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        content = html_path.read_text(encoding="utf-8")
        return HTMLResponse(
            content=content,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    return "<h1>Remote Job Hunter Dashboard Loading...</h1>"

if __name__ == "__main__":
    import uvicorn
    print("🚀 Démarrage du Command Center Remote Job Hunter sur http://127.0.0.1:5070...")
    uvicorn.run("dashboard_server:app", host="127.0.0.1", port=5070, log_level="info")
