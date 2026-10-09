import os
import sys
import time
import json

# Force UTF-8 stdout/stderr on Windows redirected streams
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from db import init_db, is_job_seen, save_job, get_hot_jobs, get_pipeline_stats
from fetcher import collect_all_jobs
from heuristic_scorer import pre_rank_jobs
from analyzer import analyze_job
from notifier import send_discord_alert, append_to_markdown_digest
from scrapling_enricher import enrich_job_description
from config import MIN_SCORE_THRESHOLD

def show_stats():
    stats = get_pipeline_stats()
    print("="*65)
    print("📊 REMOTE JOB HUNTER — PIPELINE STATS & MINI-CRM")
    print("="*65)
    print(f"Total jobs scouted   : {stats['total_jobs']}")
    print(f"Hot qualified jobs   : {stats['hot_jobs']}")
    print("\nBreakdown by Status:")
    for status, count in stats.get("by_status", {}).items():
        print(f"  • {status.capitalize():<12} : {count}")
    print("\nTop Sources:")
    for source, count in sorted(stats.get("by_source", {}).items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  • {source:<30} : {count}")
    print("="*65)

def run():
    if len(sys.argv) > 1 and sys.argv[1] in ("--stats", "-s", "stats"):
        show_stats()
        return

    print("="*65)
    print("🚀 STARTING AUTONOMOUS REMOTE JOB HUNTER (V2 - OPTIMIZED)")
    print(f"🎯 Target Profile: Manny Ruesch (AI & Automation Builder)")
    print(f"📊 Alert Threshold: Score >= {MIN_SCORE_THRESHOLD}/10")
    print("="*65)
    
    init_db()
    
    # 1. Collect candidate jobs
    raw_jobs = collect_all_jobs()
    new_jobs = [j for j in raw_jobs if not is_job_seen(j["id"])]
    print(f"\n📊 Total collected: {len(raw_jobs)} | Already seen: {len(raw_jobs) - len(new_jobs)} | New to evaluate: {len(new_jobs)}")
    
    if not new_jobs:
        print("✨ No new jobs to analyze right now. Up to date!")
        return

    # 2. Heuristic Pre-Ranking: rank jobs by lexical density to prioritize sweet-spots
    ranked_jobs = pre_rank_jobs(new_jobs)
    
    # Filter candidates: prioritize positive heuristic scores (automation, agents, workflows, solutions)
    viable_jobs = [j for j in ranked_jobs if j.get("_heuristic_score", 0) > 0]
    to_evaluate = viable_jobs[:15] if viable_jobs else ranked_jobs[:10]
    
    print(f"🎯 Filtered & Pre-ranked: {len(viable_jobs)} high-density candidates identified.")
    print(f"🧠 Analyzing Top {len(to_evaluate)} priority candidates with Gemini AI...\n")
    
    hot_count = 0
    for i, job in enumerate(to_evaluate, 1):
        h_score = job.get("_heuristic_score", 0)
        print(f"[{i}/{len(to_evaluate)}] [{h_score:+.0f} pts] Evaluating: {job['company']} — {job['title']}...")
        
        # Auto-enrich with Scrapling if description is brief/truncated (< 250 chars)
        desc = job.get("description", "")
        if len(desc) < 250 and job.get("url"):
            print(f"    🔍 Enriching full JD via Scrapling StealthyFetcher...")
            enriched = enrich_job_description(job["url"])
            if enriched:
                desc = enriched
                job["description"] = enriched

        analysis = analyze_job(
            title=job["title"],
            company=job["company"],
            location=job["location"],
            description=desc
        )
        
        score = analysis.get("fit_score", 0.0)
        tech = analysis.get("tech_match", [])
        hook = analysis.get("pitch_hook", "")
        pitch = analysis.get("tailored_pitch", "")
        pow_data = analysis.get("proof_of_work")
        cold_data = analysis.get("cold_outreach")
        
        flags = []
        if pow_data: flags.append("🏗️ PoW")
        if cold_data: flags.append("🎯 Cold Outreach")
        flag_str = f" | {' + '.join(flags)}" if flags else ""
        print(f"    ⭐ Score: {score:.1f}/10 | Tech: {', '.join(tech[:3]) if tech else 'None'}{flag_str}")
        
        job_data = {
            **job,
            "fit_score": score,
            "tech_match": tech,
            "pitch_hook": hook,
            "tailored_pitch": pitch,
            "proof_of_work": pow_data,
            "cold_outreach": cold_data
        }
        
        # Save to SQLite with full PoW architecture & Cold Outreach
        save_job(
            job_id=job["id"],
            source=job["source"],
            company=job["company"],
            title=job["title"],
            location=job["location"],
            url=job["url"],
            salary=job.get("salary", ""),
            description=job["description"],
            fit_score=score,
            tech_match=", ".join(tech),
            pitch_hook=hook,
            tailored_pitch=pitch,
            proof_of_work=json.dumps(pow_data) if pow_data else "",
            cold_outreach=json.dumps(cold_data) if cold_data else "",
            job_type=job.get("job_type", "full_time"),
            status="hot" if score >= MIN_SCORE_THRESHOLD else "screened"
        )
        
        # If hot match, trigger alerts
        if score >= MIN_SCORE_THRESHOLD:
            hot_count += 1
            print(f"    🔥 QUALIFIED! Triggering Interactive Discord Alert & Digest...")
            send_discord_alert(job_data)
            append_to_markdown_digest(job_data)
            
        time.sleep(1) # respectful spacing
        
    print("\n" + "="*65)
    print(f"🏁 RUN COMPLETE: {len(to_evaluate)} jobs evaluated | {hot_count} HOT matches dispatched!")
    print(f"📁 Local Digest: C:\\Users\\Manny\\remote_job_hunter\\HOT_OPPORTUNITIES.md")
    print("="*65)

if __name__ == "__main__":
    run()
