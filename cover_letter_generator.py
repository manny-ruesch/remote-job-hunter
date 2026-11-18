from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

import config
import db
from rjh.profile import Profile, load_profile

EDGE_CANDIDATES = [
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
]
DESKTOP_DIR = Path.home() / "Desktop"

DEFAULT_APPLICANT: dict[str, Any] = {
    "name": "Alex Dupont",
    "title": "Automation & Systems Engineer · Operations & Data Workflows",
    "location": "Remote (EU / CET) · Paris, France",
    "email": "alex.dupont@example.org",
    "phone": "+33 6 00 00 00 00",
    "portfolio": "https://alex-dupont.example.org",
    "linkedin": "https://linkedin.com/in/alex-dupont",
    "github": "https://github.com/alex-dupont",
    "key_metrics": "25+ automated workflows in production at Tech Solutions, ~200h/mo saved across operations",
    "experience_highlight": (
        "Specialist in process automation, API integrations, and production data pipelines "
        "at Tech Solutions."
    ),
    "facts": [
        "Designed and deployed 25+ automated workflows in production at Tech Solutions",
        "Reduced manual operational coordination by ~200 hours per month",
        "Engineered reliable CRM and webhook integrations with an 80% latency reduction",
    ],
}


def _resolve_default_profile_path() -> Path:
    rjh_home = os.environ.get("RJH_HOME")
    if rjh_home:
        candidate = Path(rjh_home).expanduser() / "profile.yaml"
        if candidate.exists():
            return candidate
    return BASE_DIR / "profile.yaml"


def load_applicant_profile(profile_source: "str | Path | Profile | dict[str, Any] | None" = None) -> dict[str, Any]:
    """Charge les informations du candidat depuis profile.yaml (ou un objet Profile), avec repli générique."""
    prof: Profile | None = None
    if isinstance(profile_source, Profile):
        prof = profile_source
    elif isinstance(profile_source, dict):
        try:
            prof = load_profile(profile_source)
        except Exception:
            prof = None
    else:
        target_path = Path(profile_source) if profile_source is not None else _resolve_default_profile_path()
        if target_path.exists():
            try:
                prof = load_profile(target_path)
            except Exception:
                prof = None

    if prof is None:
        return dict(DEFAULT_APPLICANT)

    ident = prof.identity
    cand = prof.candidate
    search = prof.search

    primary_titles = [t.strip() for t in (search.titles.primary or []) if t.strip()]
    title = (
        primary_titles[0].title()
        if primary_titles
        else DEFAULT_APPLICANT["title"]
    )
    facts = [f.strip() for f in (cand.facts or []) if f.strip()]
    if not facts:
        facts = list(DEFAULT_APPLICANT["facts"])

    links = ident.links or {}
    portfolio = (
        links.get("portfolio")
        or links.get("website")
        or links.get("github")
        or links.get("linkedin")
        or DEFAULT_APPLICANT["portfolio"]
    )

    return {
        "name": (ident.name or "").strip() or DEFAULT_APPLICANT["name"],
        "title": title,
        "location": (ident.city or "").strip() or DEFAULT_APPLICANT["location"],
        "email": (ident.email or "").strip() or DEFAULT_APPLICANT["email"],
        "phone": (ident.phone or "").strip() or DEFAULT_APPLICANT["phone"],
        "portfolio": portfolio,
        "linkedin": links.get("linkedin") or DEFAULT_APPLICANT["linkedin"],
        "github": links.get("github") or DEFAULT_APPLICANT["github"],
        "key_metrics": "; ".join(facts[:3]),
        "experience_highlight": (cand.summary or "").strip() or DEFAULT_APPLICANT["experience_highlight"],
        "facts": facts,
    }


def build_cover_letter_system_prompt(applicant: dict[str, Any] | None = None) -> str:
    app = applicant or load_applicant_profile()
    facts_list = app.get("facts") or [app.get("key_metrics", DEFAULT_APPLICANT["key_metrics"])]
    facts_bullets = "\n".join(f"   - {f}" for f in facts_list[:5])

    return f"""You are an elite executive career advisor writing a bespoke cover letter for {app['name']}.
Applicant Profile:
- Name: {app['name']}
- Title: {app['title']}
- Location: {app['location']}
- Email: {app['email']} | Phone: {app['phone']}
- Portfolio: {app['portfolio']}
- Key Metrics: {app['key_metrics']}
- Experience: {app['experience_highlight']}

STRICT EDITORIAL STYLE GUIDELINES (NEVER VIOLATE THESE):
1. NO "CODER / TERMINAL" VIBE: Absolutely zero bulleted technical cards, monospace jargon tags, or ticket-like syntax.
2. EXECUTIVE EDITORIAL PROSE: Write in flowing, literary, confident, articulate paragraphs (similar to The Economist or Financial Times).
3. STRATEGIC POSITIONING: Speak as a high-leverage operational partner to leadership, not a task-executing programmer. Connect capabilities to measurable business outcomes, operational velocity, and reliability.
4. PROVEN METRICS: Naturally weave in the candidate's verified achievements without sounding braggy:
{facts_bullets}
5. LENGTH: Exactly 4 to 5 concise, impactful paragraphs. Maximum 320 words total.
6. Return strictly the body text of the letter (paragraphs only, starting with 'Dear [Company / Team]...').
"""


COVER_LETTER_SYSTEM_PROMPT = build_cover_letter_system_prompt(DEFAULT_APPLICANT)


def generate_cover_letter_text(
    company: str,
    title: str,
    description: str,
    applicant: dict[str, Any] | None = None,
) -> str:
    app_data = applicant or load_applicant_profile()
    sys_prompt = build_cover_letter_system_prompt(app_data)
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
        "systemInstruction": {"parts": [{"text": sys_prompt}]}
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


def create_cover_letter_pdf(
    job_id: str,
    profile_source: "str | Path | Profile | dict[str, Any] | None" = None,
) -> tuple[bool, str, str]:
    job = db.get_job_by_id(job_id)
    if not job:
        return False, "Job not found", ""

    applicant = load_applicant_profile(profile_source)
    company = job.get("company", "Company")
    title = job.get("title", "Job Title")
    desc = job.get("description", "")

    # Generate bespoke text
    letter_text = generate_cover_letter_text(company, title, desc, applicant=applicant)
    if not letter_text:
        return False, "Failed to generate letter content", ""

    # Split into paragraphs
    paragraphs = [p.strip() for p in letter_text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [letter_text]

    first_para = html.escape(paragraphs[0]) if paragraphs else "Dear Team,"
    rest_paras = [html.escape(p) for p in paragraphs[1:]] if len(paragraphs) > 1 else []
    safe_comp_html = html.escape(company)
    safe_title_html = html.escape(title)
    safe_name_html = html.escape(applicant['name'])
    safe_role_html = html.escape(applicant['title'])
    safe_loc_html = html.escape(applicant['location'])
    safe_phone_html = html.escape(applicant['phone'])
    safe_email_html = html.escape(applicant['email'])
    safe_port_html = html.escape(applicant['portfolio'])

    current_date = datetime.now().strftime("%B %Y")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Cover Letter — {safe_name_html} for {safe_comp_html}</title>
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
      <div class="candidate-name">{safe_name_html}</div>
      <div class="candidate-title">{safe_role_html}</div>
    </div>
    <div class="contact-block">
      {safe_loc_html}<br>
      {safe_phone_html} • <a href="mailto:{safe_email_html}">{safe_email_html}</a><br>
      <a href="{safe_port_html}" target="_blank">{safe_port_html.replace('https://', '')}</a>
    </div>
  </header>

  <div class="meta-row">
    <div class="recipient-block">
      <strong>{safe_comp_html}</strong>
      Hiring & GTM Systems Team
    </div>
    <div>{current_date}</div>
  </div>

  <div class="subject-line">
    Application: {safe_title_html}
  </div>

  <p class="lead-paragraph">{first_para}</p>
"""
    for p in rest_paras:
        html_content += f"  <p>{p}</p>\n"

    html_content += f"""
  <div class="sign-off">
    Warm regards,<br>
    <div class="signature-name">{safe_name_html}</div>
  </div>
</body>
</html>"""

    # Safe filename
    safe_company = "".join(c for c in company if c.isalnum() or c in (" ", "-", "_")).strip()
    out_html = BASE_DIR / f"temp_cover_letter_{safe_company}.html"
    out_pdf = DESKTOP_DIR / f"Cover_Letter_{safe_company}.pdf"

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Strategy 1: Compile via headless Edge
    edge_bin = next((p for p in EDGE_CANDIDATES if p.exists()), None)
    if edge_bin:
        cmd = [
            str(edge_bin),
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

    # Strategy 2: Cross-platform Playwright Chromium PDF fallback
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file:///{str(out_html).replace(os.sep, '/')}", wait_until="networkidle")
            page.pdf(path=str(out_pdf), format="A4", print_background=True)
            browser.close()
        if out_html.exists():
            out_html.unlink()
        return True, str(out_pdf), letter_text
    except Exception:
        pass

    return False, "No PDF renderer available (Edge or Playwright Chromium required)", letter_text

if __name__ == "__main__":
    if len(sys.argv) > 1:
        success, path, text = create_cover_letter_pdf(sys.argv[1])
        print("Success:", success)
        print("Path:", path)
    else:
        print("Usage: python cover_letter_generator.py <job_id>")
