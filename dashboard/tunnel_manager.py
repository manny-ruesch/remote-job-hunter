import re
import sys
import subprocess
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

import config

CLOUDFLARED_EXE = Path("C:/Program Files (x86)/cloudflared/cloudflared.exe")
TUNNEL_URL_FILE = BASE_DIR / "dashboard" / "tunnel_url.txt"
LOG_FILE = BASE_DIR / "dashboard" / "tunnel.log"

def notify_discord(tunnel_url: str):
    try:
        content = (
            f"🎯 **Remote Job Hunter Command Center — Accès Mobile Actif**\n\n"
            f"Pilote tes candidatures, consulte les diagrammes Mermaid et copie tes pitchs depuis ton téléphone :\n"
            f"👉 <{tunnel_url}>\n\n"
            f"*(Lien temporaire chiffré via Cloudflare Tunnel)*"
        )
        payload = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(
            config.DISCORD_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        urllib.request.urlopen(req, timeout=10)
        print("Discord notification sent to #remote-jobs!")
    except Exception as e:
        print(f"Error sending Discord notification: {e}")

def run_tunnel():
    print("Starting cloudflared tunnel for http://localhost:5070...")
    cmd = [str(CLOUDFLARED_EXE), "tunnel", "--url", "http://localhost:5070"]
    
    with open(LOG_FILE, "w", encoding="utf-8") as log_out:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace"
        )
        
        tunnel_url = None
        if proc.stdout:
            for line in proc.stdout:
                log_out.write(line)
                log_out.flush()
                
                # Check for trycloudflare.com URL
                if not tunnel_url:
                    m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                    if m:
                        tunnel_url = m.group(0)
                        print(f"✅ Tunnel Cloudflare actif : {tunnel_url}")
                        with open(TUNNEL_URL_FILE, "w", encoding="utf-8") as f:
                            f.write(tunnel_url)
                        notify_discord(tunnel_url)
                        
        proc.wait()

if __name__ == "__main__":
    import json
    run_tunnel()
