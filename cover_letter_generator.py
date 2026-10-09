import os
import sys
import json
import urllib.request
import subprocess
from pathlib import Path
from typing import Optional, Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

import db
import config

EDGE_PATH = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
DESKTOP_DIR = Path("E:/Users/Manny/Desktop") if Path("E:/Users/Manny/Desktop").exists() else Path.home() / "Desktop"

COVER_LETTER_SYSTEM_PROMPT = f"""You are an elite executive career advisor writing a bespoke cover letter for {config.APPLICANT['name']}.
Applicant Profile:
- Name: {config.APPLICANT['name']}
- Title: {config.APPLICANT['title']}
- Location: {config.APPLICANT['location']}
- Email: {config.APPLICANT['email']} | Phone: {config.APPLICANT['phone']}
- Portfolio: {config.APPLICANT['portfolio']}
- Key Metrics: {config.APPLICANT['key_metrics']}
- Experience: {config.APPLICANT['experience_highlight']}

STRICT EDITORIAL STYLE GUIDELINES (NEVER VIOLATE THESE):
1. NO "CODER / TERMINAL" VIBE: Absolutely zero bulleted technical cards, monospace jargon tags, or ticket-like syntax.
2. EXECUTIVE EDITORIAL PROSE: Write in flowing, literary, confident, articulate paragraphs (similar to The Economist or Financial Times).
3. STRATEGIC POSITIONING: Speak as a high-leverage operational partner to leadership, not a task-executing programmer. Connect technical capabilities to revenue velocity, customer retention, data integrity, and eliminating operational drag.
4. PROVEN METRICS: Naturally weave in the 32+ production workflows, ~350 hours saved monthly, and ~720 operations automated at ADEN Immo GmbH without sounding braggy.
5. LENGTH: Exactly 4 to 5 concise, impactful paragraphs. Maximum 320 words total.
6. Return strictly the body text of the letter (paragraphs only, starting with 'Dear [Company / Team]...').
"""

def generate_cover_letter_text(company: str, title: str, description: str) -> str:
    model_name = config.GEMINI_MODEL if config.GEMINI_MODEL.startswith("gemini-") else f"gemini-{config.GEMINI_MODEL}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    prompt = f"""Write an executive editorial cover letter for:
COMPANY: {company}
ROLE: {title}
JOB DESCRIPTION:
{description[:6000]}
"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": COVER_LETTER_SYSTEM_PROMPT}]}
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": config.GOOGLE_API_KEY or ""
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "").strip()
    return ""

def create_cover_letter_pdf(job_id: str) -> tuple[bool, str, str]:
    job = db.get_job_by_id(job_id)
    if not job:
        return False, "Job not found", ""

    company = job.get("company", "Company")
    title = job.get("title", "Job Title")
    desc = job.get("description", "")

    # Generate bespoke text
    letter_text = generate_cover_letter_text(company, title, desc)
    if not letter_text:
        return False, "Failed to generate letter content", ""

    # Split into paragraphs
    paragraphs = [p.strip() for p in letter_text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [letter_text]

    first_para = paragraphs[0] if paragraphs else "Dear Team,"
    rest_paras = paragraphs[1:] if len(paragraphs) > 1 else []

    from datetime import datetime
    current_date = datetime.now().strftime("%B %Y")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Cover Letter — {config.APPLICANT['name']} for {company}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400;1,6..72,500&family=Plus+Jakarta+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
  <style>
    @page {{ size: A4 portrait; margin: 0; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #FAF8F5;
      color: #1A1715;
      font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
      font-size: 13.5px;
      line-height: 1.68;
      padding: 42px 56px;
      width: 210mm;
      min-height: 297mm;
      margin: 0 auto;
      -webkit-font-smoothing: antialiased;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      border-bottom: 1px solid rgba(140, 83, 50, 0.2);
      padding-bottom: 18px;
      margin-bottom: 24px;
    }}
    .candidate-name {{
      font-family: 'Newsreader', Georgia, serif;
      font-size: 28px;
      font-weight: 500;
      color: #1A1715;
      letter-spacing: -0.015em;
      line-height: 1;
    }}
    .candidate-title {{
      font-size: 11.5px;
      font-weight: 500;
      color: #8C5332;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-top: 5px;
    }}
    .contact-block {{
      text-align: right;
      font-size: 11.5px;
      color: #736B63;
      line-height: 1.5;
    }}
    .contact-block a {{
      color: #8C5332;
      text-decoration: none;
    }}
    .meta-row {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 22px;
      font-size: 12px;
      color: #736B63;
    }}
    .recipient-block strong {{
      font-family: 'Newsreader', Georgia, serif;
      font-size: 15.5px;
      font-weight: 500;
      color: #1A1715;
      display: block;
      margin-bottom: 2px;
    }}
    .subject-line {{
      font-family: 'Newsreader', Georgia, serif;
      font-size: 17.5px;
      font-weight: 500;
      font-style: italic;
      color: #8C5332;
      margin-bottom: 20px;
    }}
    p {{
      margin-bottom: 13px;
      color: #2D2825;
      text-align: justify;
      font-weight: 400;
    }}
    .lead-paragraph {{
      font-family: 'Newsreader', Georgia, serif;
      font-size: 15px;
      line-height: 1.6;
      color: #1A1715;
      margin-bottom: 15px;
    }}
    .sign-off {{
      margin-top: 26px;
      font-family: 'Newsreader', Georgia, serif;
      font-size: 14.5px;
      color: #1A1715;
    }}
    .signature-name {{
      font-family: 'Newsreader', Georgia, serif;
      font-size: 21px;
      font-weight: 500;
      color: #8C5332;
      margin-top: 5px;
      letter-spacing: -0.01em;
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <div class="candidate-name">{config.APPLICANT['name']}</div>
      <div class="candidate-title">{config.APPLICANT['title']}</div>
    </div>
    <div class="contact-block">
      {config.APPLICANT['location']}<br>
      {config.APPLICANT['phone']} • <a href="mailto:{config.APPLICANT['email']}">{config.APPLICANT['email']}</a><br>
      <a href="{config.APPLICANT['portfolio']}" target="_blank">{config.APPLICANT['portfolio'].replace('https://', '')}</a>
    </div>
  </header>

  <div class="meta-row">
    <div class="recipient-block">
      <strong>{company}</strong>
      Hiring & GTM Systems Team
    </div>
    <div>{current_date}</div>
  </div>

  <div class="subject-line">
    Application: {title}
  </div>

  <p class="lead-paragraph">{first_para}</p>
"""
    for p in rest_paras:
        html_content += f"  <p>{p}</p>\n"

    html_content += f"""
  <div class="sign-off">
    Warm regards,<br>
    <div class="signature-name">{config.APPLICANT['name']}</div>
  </div>
</body>
</html>"""

    # Safe filename
    safe_company = "".join(c for c in company if c.isalnum() or c in (" ", "-", "_")).strip()
    out_html = BASE_DIR / f"temp_cover_letter_{safe_company}.html"
    out_pdf = DESKTOP_DIR / f"Cover_Letter_{safe_company}.pdf"

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Compile via headless Edge
    if EDGE_PATH.exists():
        cmd = [
            str(EDGE_PATH),
            "--headless",
            "--disable-gpu",
            f"--print-to-pdf={str(out_pdf)}",
            "--no-margins",
            f"file:///{str(out_html).replace(os.sep, '/')}"
        ]
        subprocess.run(cmd, check=True)
        if out_html.exists():
            out_html.unlink()
        return True, str(out_pdf), letter_text

    return False, "Microsoft Edge not found for PDF rendering", letter_text

if __name__ == "__main__":
    if len(sys.argv) > 1:
        success, path, text = create_cover_letter_pdf(sys.argv[1])
        print("Success:", success)
        print("Path:", path)
    else:
        print("Usage: python cover_letter_generator.py <job_id>")
