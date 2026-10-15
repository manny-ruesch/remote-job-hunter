import os
import sqlite3
import datetime
from config import DB_PATH
from typing import Optional, Any

def get_conn():
    active_db = os.environ.get("RJH_DB") or (
        os.path.join(os.environ["RJH_HOME"], "jobs.db") if os.environ.get("RJH_HOME") else DB_PATH
    )
    conn = sqlite3.connect(active_db, timeout=10.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
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
        status TEXT DEFAULT 'new',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # Check if proof_of_work and cold_outreach columns exist for existing databases
    c.execute("PRAGMA table_info(jobs)")
    cols = [col["name"] for col in c.fetchall()]
    if "proof_of_work" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN proof_of_work TEXT")
    if "cold_outreach" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN cold_outreach TEXT")
    if "job_type" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN job_type TEXT DEFAULT 'full_time'")
    if "analysis_status" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN analysis_status TEXT DEFAULT 'ok'")
    if "rationale" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN rationale TEXT")
    if "gate" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN gate TEXT")
    if "profile_fp" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN profile_fp TEXT")
    if "dedupe_key" not in cols:
        c.execute("ALTER TABLE jobs ADD COLUMN dedupe_key TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_fit ON jobs(status, fit_score);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_type_fit ON jobs(job_type, fit_score);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_analysis_status ON jobs(analysis_status);")
    conn.commit()
    conn.close()

def is_job_seen(job_id: str) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def save_job(
    job_id: str,
    source: str,
    company: str,
    title: str,
    location: str,
    url: str,
    salary: str,
    description: str,
    fit_score: float = 0.0,
    tech_match: str = "",
    pitch_hook: str = "",
    tailored_pitch: str = "",
    proof_of_work: str = "",
    cold_outreach: str = "",
    job_type: str = "full_time",
    status: str = "new"
):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO jobs 
    (id, source, company, title, location, url, salary, description, fit_score, tech_match, pitch_hook, tailored_pitch, proof_of_work, cold_outreach, job_type, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        source = excluded.source,
        company = excluded.company,
        title = excluded.title,
        location = excluded.location,
        url = excluded.url,
        salary = excluded.salary,
        description = excluded.description,
        fit_score = CASE WHEN excluded.fit_score > 0 THEN excluded.fit_score ELSE jobs.fit_score END,
        tech_match = CASE WHEN excluded.tech_match != '' THEN excluded.tech_match ELSE jobs.tech_match END,
        pitch_hook = CASE WHEN excluded.pitch_hook != '' THEN excluded.pitch_hook ELSE jobs.pitch_hook END,
        tailored_pitch = CASE WHEN excluded.tailored_pitch != '' THEN excluded.tailored_pitch ELSE jobs.tailored_pitch END,
        proof_of_work = CASE WHEN excluded.proof_of_work IS NOT NULL AND excluded.proof_of_work != '' THEN excluded.proof_of_work ELSE jobs.proof_of_work END,
        cold_outreach = CASE WHEN excluded.cold_outreach IS NOT NULL AND excluded.cold_outreach != '' THEN excluded.cold_outreach ELSE jobs.cold_outreach END,
        job_type = excluded.job_type,
        status = CASE 
            WHEN jobs.status IN ('applied', 'interview', 'archived') THEN jobs.status 
            ELSE excluded.status 
        END
    """, (
        job_id, source, company, title, location, url, salary, description,
        fit_score, tech_match, pitch_hook, tailored_pitch, proof_of_work, cold_outreach, job_type, status
    ))
    conn.commit()
    conn.close()

def get_job_by_id(job_id: str) -> Optional[dict[str, Any]]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def update_job_status(job_id: str, status: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    conn.commit()
    conn.close()

def get_hot_jobs(min_score: float = 7.5) -> list[dict[str, Any]]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    SELECT * FROM jobs 
    WHERE fit_score >= ? 
    ORDER BY fit_score DESC, created_at DESC
    """, (min_score,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_pipeline_stats() -> dict[str, Any]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM jobs")
    total = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM jobs WHERE fit_score >= 7.5")
    hot = c.fetchone()[0]
    
    c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
    by_status = dict(c.fetchall())
    
    c.execute("SELECT source, COUNT(*) FROM jobs GROUP BY source")
    by_source = dict(c.fetchall())
    
    conn.close()
    return {
        "total_jobs": total,
        "hot_jobs": hot,
        "by_status": by_status,
        "by_source": by_source
    }

init_db()
