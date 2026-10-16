import urllib.request
import urllib.error
import json
import os
import sys
from typing import Any
from config import DISCORD_WEBHOOK_URL, DIGEST_PATH

# Attempt to load bot token for interactive buttons
DISCORD_BOT_TOKEN = None
try:
    sys.path.append(r"C:\Users\Manny\gumroad_pipeline")
    import config as gp_config
    DISCORD_BOT_TOKEN = getattr(gp_config, "DISCORD_BOT_TOKEN", None)
except Exception:
    pass

REMOTE_JOBS_CHANNEL_ID = "1554959776373280869"

def send_discord_alert(job: dict[str, Any]) -> bool:
    score = job.get("fit_score", 0.0)
    company = job.get("company", "Company")
    title = job.get("title", "Job Title")
    url = job.get("url", "")
    location = job.get("location", "Remote")
    salary = job.get("salary", "")
    tech = ", ".join(job.get("tech_match", [])) or "Automation / AI"
    pitch_hook = job.get("pitch_hook", "")
    tailored_pitch = job.get("tailored_pitch", "")
    pow_data = job.get("proof_of_work")
    job_id = job.get("id", "")
    j_type = job.get("job_type", "full_time")
    if j_type == "part_time":
        format_label = "⏱️ Part-Time"
    elif j_type == "contract":
        format_label = "⚡ Freelance / Contract"
    else:
        format_label = "💼 Full-Time"
    
    # Bronze/Gold for top scores (>8.5), emerald green for >7.5
    color = 0xD97706 if score >= 8.5 else 0x10B981
    
    fields = [
        {
            "name": "🌍 Localisation & Format",
            "value": f"📍 **{location}** • `{format_label}`" + (f" | 💰 `{salary}`" if salary else ""),
            "inline": False
        },
        {
            "name": "🛠️ Stack & Correspondance",
            "value": f"`{tech}`",
            "inline": False
        }
    ]
    
    if pitch_hook:
        fields.append({
            "name": "💡 Angle d'attaque / Hook",
            "value": pitch_hook,
            "inline": False
        })
        
    if tailored_pitch:
        fields.append({
            "name": "📝 Pitch personnalisé (Extrait)",
            "value": f"```{tailored_pitch[:600]}...```",
            "inline": False
        })
        
    if pow_data and isinstance(pow_data, dict):
        diag = pow_data.get("problem_diagnosis", "")
        if diag:
            fields.append({
                "name": "🏗️ Diagnostic Architecture & PoW",
                "value": f"> *{diag[:280]}...*",
                "inline": False
            })
            
    embed = {
        "title": f"🎯 [{score:.1f}/10] {company} · {title}",
        "description": "Nouvelle opportunité remote qualifiée par l'IA pour ton profil.",
        "url": url,
        "color": color,
        "fields": fields,
        "footer": {
            "text": f"Source: {job.get('source', 'Web')} • Remote Job Hunter"
        }
    }
    
    # Build Interactive Buttons
    components = []
    buttons = []
    if url:
        buttons.append({
            "type": 2,
            "style": 5, # Link
            "label": "🚀 Ouvrir la Candidature",
            "url": url
        })
        
    if job_id:
        buttons.append({
            "type": 2,
            "style": 1, # Primary
            "label": "📋 Copier le Pitch",
            "custom_id": f"pitch_{job_id[:40]}"
        })
        if pow_data:
            buttons.append({
                "type": 2,
                "style": 2, # Secondary
                "label": "🏗️ Architecture PoW",
                "custom_id": f"pow_{job_id[:40]}"
            })
            
    cold_data = job.get("cold_outreach")
    if job_id and cold_data:
        buttons.append({
            "type": 2,
            "style": 2, # Secondary
            "label": "🎯 DM Décideur",
            "custom_id": f"dm_{job_id[:40]}"
        })
            
    if buttons:
        components.append({"type": 1, "components": buttons[:5]})

    # Strategy 1: Post via Discord Bot API with full interactive components
    if DISCORD_BOT_TOKEN:
        try:
            bot_endpoint = f"https://discord.com/api/v10/channels/{REMOTE_JOBS_CHANNEL_ID}/messages"
            req = urllib.request.Request(
                bot_endpoint,
                data=json.dumps({"embeds": [embed], "components": components}).encode("utf-8"),
                headers={
                    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                    "Content-Type": "application/json",
                    "User-Agent": "DiscordBot (https://github.com, 1.0)"
                }
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status in (200, 204):
                    return True
        except Exception:
            # Fall back to webhook below
            pass

    # Strategy 2: Webhook Fallback
    if DISCORD_WEBHOOK_URL:
        payload = {
            "username": "AI Job Hunter",
            "avatar_url": "https://raw.githubusercontent.com/manny-ruesch/manny-ruesch.github.io/main/favicon-32.png",
            "embeds": [embed]
        }
        try:
            req = urllib.request.Request(
                DISCORD_WEBHOOK_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status in (200, 204)
        except Exception:
            return False

    return False

def append_to_markdown_digest(job: dict[str, Any]):
    exists = os.path.exists(DIGEST_PATH)
    with open(DIGEST_PATH, "a", encoding="utf-8") as f:
        if not exists:
            f.write("# 🎯 Hot Remote Opportunities for Manny Ruesch\n\n")
            f.write("*Auto-scouted and qualified by the Remote Job Hunter AI pipeline.*\n\n---\n\n")
            
        f.write(f"## [{job.get('fit_score', 0):.1f}/10] {job.get('company')} — {job.get('title')}\n\n")
        f.write(f"- **Location:** {job.get('location', 'Remote')}\n")
        if job.get('salary'):
            f.write(f"- **Salary:** {job.get('salary')}\n")
        f.write(f"- **Source:** {job.get('source')} | [Direct Job Link]({job.get('url')})\n")
        f.write(f"- **Stack Match:** `{', '.join(job.get('tech_match', []))}`\n\n")
        
        if job.get('pitch_hook'):
            f.write(f"> **Hook:** {job.get('pitch_hook')}\n\n")
            
        if job.get('tailored_pitch'):
            f.write("**Tailored Application Pitch:**\n\n")
            f.write(f"```text\n{job.get('tailored_pitch')}\n```\n\n")
            
        pow_data = job.get('proof_of_work')
        if pow_data and isinstance(pow_data, dict):
            f.write("### 🏗️ Proof-of-Work Architecture Proposal\n\n")
            if pow_data.get('problem_diagnosis'):
                f.write(f"**Identified Bottleneck:** {pow_data.get('problem_diagnosis')}\n\n")
            if pow_data.get('solution_architecture'):
                f.write(f"**3-Step Implementation Plan:**\n{pow_data.get('solution_architecture')}\n\n")
            if pow_data.get('mermaid_diagram'):
                f.write("```mermaid\n")
                f.write(f"{pow_data.get('mermaid_diagram')}\n")
                f.write("```\n\n")
                
        cold_data = job.get('cold_outreach')
        if cold_data and isinstance(cold_data, dict):
            f.write("### 🎯 Cold Outreach Décideur (Court-circuit ATS)\n\n")
            if cold_data.get('target_role'):
                f.write(f"- **Cible :** `{cold_data.get('target_role')}`\n")
            if cold_data.get('subject'):
                f.write(f"- **Objet :** *{cold_data.get('subject')}*\n")
            if cold_data.get('dm_text'):
                f.write(f"\n```text\n{cold_data.get('dm_text')}\n```\n\n")
            if cold_data.get('linkedin_search_url'):
                f.write(f"👉 [Trouver le profil sur LinkedIn]({cold_data.get('linkedin_search_url')})\n\n")

        f.write("---\n\n")
