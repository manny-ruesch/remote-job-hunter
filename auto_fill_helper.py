import sys
import subprocess
import json
from db import get_conn
from config import APPLICANT

def copy_to_clipboard(text: str):
    try:
        p = subprocess.Popen(['clip.exe'], stdin=subprocess.PIPE, close_fds=True)
        p.communicate(input=text.encode('utf-8'))
        print("📋 Tailored pitch copied to clipboard!")
    except Exception as e:
        print(f"Could not copy to clipboard: {e}")

def open_job(job_id_or_url: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM jobs WHERE id = ? OR url = ?", (job_id_or_url, job_id_or_url))
    row = c.fetchone()
    conn.close()
    
    if not row:
        print(f"❌ Job '{job_id_or_url}' not found in database.")
        return
        
    job = dict(row)
    url = job.get("url")
    pitch = job.get("tailored_pitch", "")
    
    print("\n" + "="*60)
    print(f"🎯 Preparing application for: {job.get('company')} — {job.get('title')}")
    print(f"🔗 URL: {url}")
    print("="*60)
    
    if pitch:
        copy_to_clipboard(pitch)
        print("\n📝 PITCH READY TO PASTE:\n")
        print(pitch)
        print("\n" + "-"*60)
        
    pow_raw = job.get("proof_of_work")
    if pow_raw:
        try:
            pow_data = json.loads(pow_raw) if isinstance(pow_raw, str) else pow_raw
            print("\n🏗️ PROOF-OF-WORK ARCHITECTURE:\n")
            if pow_data.get("problem_diagnosis"):
                print(f"• Bottleneck: {pow_data['problem_diagnosis']}")
            if pow_data.get("solution_architecture"):
                print(f"• Plan: {pow_data['solution_architecture']}")
            if pow_data.get("mermaid_diagram"):
                print(f"\n```mermaid\n{pow_data['mermaid_diagram']}\n```")
            print("\n" + "-"*60)
        except Exception:
            pass

    import webbrowser
    from urllib.parse import urlparse

    print("\n🚀 Opening application in your browser...")
    if url:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            webbrowser.open(url)
        else:
            print(f"⚠️ Invalid URL scheme: {url}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        open_job(sys.argv[1])
    else:
        print("Usage: python auto_fill_helper.py <job_id_or_url>")
