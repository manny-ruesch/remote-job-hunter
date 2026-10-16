import sys

# Force UTF-8 stdout/stderr on Windows redirected streams
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from pathlib import Path
from db import get_pipeline_stats
from rjh.cli import main as rjh_main

HUNTER_DIR = Path(__file__).resolve().parent

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
        return 0
    return rjh_main(["--home", str(HUNTER_DIR), "scan", *sys.argv[1:]])

if __name__ == "__main__":
    sys.exit(run())
