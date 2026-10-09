import os
import sys
import json
import time
import subprocess
from pathlib import Path
from typing import Optional, Any

import psutil
from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Add remote_job_hunter root to sys.path
HUNTER_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(HUNTER_DIR))

import cv_profiler

import db
import config

app = FastAPI(title="Remote Job Hunter — Command Center")

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
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "run_hunter.py" in cmd and "python" in p.info["name"].lower():
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
    
    # Parse proof_of_work & cold_outreach JSON if present
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
    return job

@app.post("/api/jobs/{job_id}/status")
async def update_status(job_id: str, payload: dict[str, str]):
    new_status = payload.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="Missing status")
    db.update_job_status(job_id, new_status)
    return {"success": True, "job_id": job_id, "new_status": new_status}

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
    except Exception as e:
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
        "text": letter_text
    }

def _run_hunter_worker():
    bat_path = HUNTER_DIR / "run_hunter.bat"
    subprocess.run(["cmd.exe", "/c", str(bat_path)], cwd=str(HUNTER_DIR))

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
        raise HTTPException(500, "Erreur lors du profilage IA par Gemini.")
        
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
