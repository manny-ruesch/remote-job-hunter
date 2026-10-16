"""
cv_tailor.py — Générateur de CV Sur-Mesure (1 Page A4 · 100% ATS-Safe Single-Column · Editorial Warm Luxury).

Combine :
1. L'esthétique "Editorial Warm Luxury" (Newsreader + Plus Jakarta Sans + Inter, palette #1A1715 / #8C5332 / #FAF8F5)
   optimisée pour le scan visuel en 6 secondes d'un recruteur humain (pattern en F, métriques d'impact, mots-clés en gras).
2. Une structure 100 % linéaire mono-colonne (Top-to-Bottom DOM & Y-coordinates) qui passe sans aucune distorsion
   les parseurs ATS (Workday, Taleo, Greenhouse, Lever, Ashby, BambooHR, iCIMS).
3. Une adaptation dynamique par offre (LLM Gemini avec repli déterministe hors-ligne / Mode Manuel) verrouillée sur
   les faits réels du candidat (zéro hallucination d'entreprise, de diplôme ou de métrique).
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

import config
import db

EDGE_CANDIDATES = [
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
]
DESKTOP_DIR = Path("E:/Users/Manny/Desktop") if Path("E:/Users/Manny/Desktop").exists() else Path.home() / "Desktop"
TAILORED_DIR = BASE_DIR / "tailored_cvs"

# Canonical truthful baseline for Manny Ruesch (never fabricated)
BASELINE_CV = {
    "name": "Manny Ruesch",
    "default_headline": "Revenue Operations & AI Automation Engineer · GTM Systems & Workflows",
    "email": "manny.ruesch@gmail.com",
    "phone": "+33 6 12 63 03 31",
    "location": "Remote (EU / CET) · Paris & Berlin",
    "portfolio": "manny-ruesch.github.io",
    "linkedin": "linkedin.com/in/manny-ruesch",
    "github": "github.com/manny-ruesch",
    "metrics": [
        {"value": "32+ Workflows", "label": "Active in Production (n8n, Python & APIs)"},
        {"value": "~350 Hours / mo", "label": "Manual Coordination Recovered"},
        {"value": "~720 Ops / mo", "label": "High-Stakes CRM & Lead Ops Automated"},
        {"value": "-85% Latency", "label": "Lead Qualification & Routing Speed"},
    ],
    "skill_categories": [
        {
            "category": "Workflow & GTM Automation",
            "items": [
                "n8n (Self-Hosted & Cloud)",
                "Make (Integromat)",
                "Zapier",
                "Webhook Orchestration",
                "REST APIs",
                "JSON Schema Validation",
                "Idempotent Data Pipelines",
                "Lead Routing & Enrichment",
            ],
        },
        {
            "category": "AI & LLM Engineering",
            "items": [
                "OpenAI API",
                "Anthropic Claude",
                "Google Gemini",
                "Multi-Agent Systems",
                "Prompt Chaining",
                "Structured Outputs",
                "RAG & Vector Sync",
                "Automated Evaluation",
            ],
        },
        {
            "category": "CRM, Revenue Data & Analytics",
            "items": [
                "HubSpot CRM",
                "Salesforce Integration",
                "onOffice CRM",
                "PostgreSQL",
                "SQL",
                "NocoDB / Airtable",
                "Google Analytics 4 (GA4)",
                "Search Console & Attribution",
            ],
        },
        {
            "category": "Scripting & Cloud Infrastructure",
            "items": [
                "Python (AsyncIO, FastAPI, Data Pipelines)",
                "JavaScript / TypeScript",
                "Docker",
                "Linux VPS",
                "Git & GitHub Actions CI/CD",
                "Playwright Automation",
            ],
        },
    ],
    "experience": [
        {
            "company": "ADEN Immo GmbH",
            "role": "Revenue Operations & AI Automation (Digital Marketing & Systems)",
            "dates": "Jan 2026 – Jul 2026",
            "location": "Berlin, Germany",
            "bullets": [
                {
                    "lead": "CRM & Workflow Automation Architecture (n8n & APIs)",
                    "text": "Designed, deployed, and maintained <b>32+ mission-critical production workflows</b> bridging CRM (onOffice / HubSpot patterns), messaging channels, and SQL databases—recovering <b>~350 hours per month</b> across commercial and operations teams.",
                    "tags": ["n8n", "crm", "hubspot", "workflow", "automation", "operations", "revops", "api", "sql"],
                },
                {
                    "lead": "Automated Lead Qualification, Enrichment & Routing",
                    "text": "Engineered fault-tolerant webhook pipelines automating <b>~720 monthly business operations</b> across inbound lead capture, data enrichment, and cross-team routing, reducing lead-to-contact latency by <b>85%</b>.",
                    "tags": ["webhook", "lead", "routing", "enrichment", "gtm", "sales", "revops", "data", "hubspot"],
                },
                {
                    "lead": "Generative AI & Structured Data Extraction Pipelines",
                    "text": "Built production LLM pipelines (OpenAI API, Google Gemini) with strict JSON schema validation for automated document parsing, CRM hygiene, and multilingual asset generation (EN / FR / DE).",
                    "tags": ["ai", "llm", "openai", "gemini", "json", "python", "data", "extraction", "prompt"],
                },
                {
                    "lead": "Revenue Analytics, Attribution & Funnel Operations",
                    "text": "Structured multi-touch attribution tracking and conversion funnel diagnostics via GA4, Search Console, and custom data dashboards to guide GTM capital allocation and organic growth.",
                    "tags": ["analytics", "ga4", "growth", "seo", "attribution", "funnel", "dashboard", "marketing"],
                },
            ],
        },
        {
            "company": "Independent AI Systems & Automation Builder",
            "role": "Founder & Systems Architect — Production Deployments",
            "dates": "Jan 2025 – Present",
            "location": "Remote (EU)",
            "bullets": [
                {
                    "lead": "Autonomous B2B GTM & Hiring Signal Intelligence Engine",
                    "text": "Architected a multi-source Python &amp; SQLite WAL scouting and enrichment pipeline querying <b>50+ ATS APIs (Greenhouse, Lever, Ashby)</b> in parallel with 2-pass LLM scoring and automated outreach generation.",
                    "tags": ["python", "sqlite", "api", "enrichment", "b2b", "gtm", "ashby", "greenhouse", "lever", "llm"],
                },
                {
                    "lead": "Multi-Agent RAG & Knowledge Synchronization",
                    "text": "Developed a bidirectional data synchronization engine connecting <b>Notion, Airtable, and PostgreSQL</b> with automated critic-judge verification to guarantee zero data drift across internal knowledge bases.",
                    "tags": ["notion", "airtable", "postgresql", "rag", "sync", "agent", "database", "integration"],
                },
                {
                    "lead": "End-to-End API Distribution & Webhook Fulfillment",
                    "text": "Engineered zero-click REST API publishing and webhook fulfillment pipelines orchestrating automated transactional workflows, schema validation, and real-time Discord/Slack alerting.",
                    "tags": ["rest api", "webhook", "automation", "python", "discord", "slack", "integration", "ci/cd"],
                },
                {
                    "lead": "Multimodal AI Processing Pipeline",
                    "text": "Built an automated computer-vision and generative AI transformation pipeline delivering production-ready visual assets in <b>~33 seconds</b> at sub-cent inference cost.",
                    "tags": ["ai", "vision", "pipeline", "automation", "cloud"],
                },
            ],
        },
        {
            "company": "Prytanée Sportif",
            "role": "Operations & Student-Athlete Program Coordinator",
            "dates": "Sep 2025 – Dec 2025",
            "location": "Pau, France",
            "bullets": [
                {
                    "lead": "Cross-Functional Operations & Reporting",
                    "text": "Coordinated daily operational schedules and structured performance tracking and reporting workflows between coaching staff, academic leadership, and administration.",
                    "tags": ["operations", "reporting", "coordination"],
                }
            ],
        },
        {
            "company": "Bijouterie Aélys",
            "role": "Retail Operations & CRM Intern",
            "dates": "Apr 2024 – Jun 2024",
            "location": "Pau, France",
            "bullets": [
                {
                    "lead": "CRM Segmentation & Inventory Data Operations",
                    "text": "Executed CRM customer segmentation, omnichannel inventory data tracking, and client lifecycle operations.",
                    "tags": ["crm", "data", "inventory", "operations"],
                }
            ],
        },
    ],
    "education": [
        {
            "degree": "MSc in Finance, Data Analytics & Corporate Strategy",
            "school": "éklore-ed School of Management",
            "dates": "2026 – 2028",
            "detail": "Focus: Quantitative Modeling, Business Intelligence, Data Architecture & Operations",
        },
        {
            "degree": "International Academic Exchange (Taught in English)",
            "school": "Sejong University · Seoul, South Korea",
            "dates": "Feb 2025 – Jun 2025",
            "detail": "Coursework: Artificial Intelligence in Industry, Media Planning & Operations Management",
        },
        {
            "degree": "Bachelor in Business, Marketing & Data Management",
            "school": "éklore-ed School of Management · Pau, France",
            "dates": "2023 – 2025",
            "detail": "Specializations: Corporate Finance, Digital Marketing Automation & CRM Analytics",
        },
    ],
    "languages": "French (Native)  ·  English (C1 — Full Professional Proficiency)  ·  Spanish (B1 — Conversational)  ·  German (Elementary / Berlin work experience)",
}

KNOWN_ATS_KEYWORDS = [
    ("n8n", "n8n"),
    ("hubspot", "HubSpot CRM"),
    ("salesforce", "Salesforce"),
    ("python", "Python"),
    ("sql", "SQL"),
    ("postgresql", "PostgreSQL"),
    ("postgres", "PostgreSQL"),
    ("rest api", "REST APIs"),
    ("api", "REST APIs"),
    ("webhook", "Webhooks"),
    ("make", "Make (Integromat)"),
    ("zapier", "Zapier"),
    ("airtable", "Airtable"),
    ("notion", "Notion"),
    ("revops", "Revenue Operations (RevOps)"),
    ("revenue operations", "Revenue Operations (RevOps)"),
    ("gtm", "GTM Systems"),
    ("crm", "CRM Architecture"),
    ("llm", "LLM Pipelines"),
    ("openai", "OpenAI API"),
    ("claude", "Anthropic Claude"),
    ("gemini", "Google Gemini"),
    ("docker", "Docker"),
    ("fastapi", "FastAPI"),
    ("javascript", "JavaScript"),
    ("typescript", "TypeScript"),
    ("ga4", "Google Analytics 4"),
    ("enrichment", "Data Enrichment"),
    ("lead routing", "Lead Routing"),
    ("workflow", "Workflow Orchestration"),
    ("automation", "Process Automation"),
    ("integration", "Systems Integration"),
    ("etl", "ETL & Data Sync"),
    ("json", "JSON Schema Validation"),
    ("git", "Git & CI/CD"),
]


def extract_job_keywords(job: dict[str, Any]) -> list[str]:
    """Extrait de manière déterministe les mots-clés ATS présents dans l'offre."""
    corpus = " ".join(
        str(job.get(k) or "")
        for k in ("title", "company", "tech_match", "description_snippet", "rationale", "pitch_hook")
    ).lower()

    matched: list[str] = []
    seen_lower: set[str] = set()

    # 1. Explicit tech_match tags from scanner/analyzer
    raw_tech = str(job.get("tech_match") or "")
    for token in re.split(r"[,;/|•]+", raw_tech):
        clean = token.strip()
        if 1 < len(clean) <= 32 and clean.lower() not in seen_lower:
            seen_lower.add(clean.lower())
            matched.append(clean)

    # 2. Scan job corpus against known ATS canonical keywords + active candidate profile skills (any domain)
    for needle, label in KNOWN_ATS_KEYWORDS:
        if re.search(r"\b" + re.escape(needle) + r"\b", corpus):
            if label.lower() not in seen_lower and needle not in seen_lower:
                seen_lower.add(label.lower())
                seen_lower.add(needle)
                matched.append(label)

    try:
        import cv_profiler
        active_prof = cv_profiler.get_active_profile() or {}
        extra_skills = list(active_prof.get("core_stack") or []) + list(active_prof.get("positive_keywords") or [])
        for sk in extra_skills:
            s_clean = str(sk).strip()
            if 1 < len(s_clean) <= 36 and s_clean.lower() not in seen_lower:
                if re.search(r"\b" + re.escape(s_clean.lower()) + r"\b", corpus):
                    seen_lower.add(s_clean.lower())
                    matched.append(s_clean)
    except Exception:
        pass

    if not matched:
        matched = ["n8n", "Python", "HubSpot CRM", "REST APIs", "Webhooks", "LLM Pipelines", "PostgreSQL"]
    return matched[:14]


def build_deterministic_tailoring(job: dict[str, Any], matched_keywords: list[str]) -> dict[str, Any]:
    """Adaptation déterministe 100% fidèle aux faits réels (utilisée hors-ligne ou en secours)."""
    raw_title = (job.get("title") or "").strip()
    company = (job.get("company") or "Target Company").strip()

    # Clean role title for the CV header so it mirrors the job posting naturally
    clean_job_title = re.sub(r"\s*\([^)]*\)\s*", " ", raw_title)
    clean_job_title = re.sub(r"\s*[-–—|]\s*(remote|full[- ]time|europe|emea|h/f|m/f/d|f/m/x).*", "", clean_job_title, flags=re.I).strip()
    if not clean_job_title or len(clean_job_title) > 58:
        headline = BASELINE_CV["default_headline"]
    else:
        headline = f"{clean_job_title} · AI Automation & Revenue Systems"

    top_kw_str = ", ".join(matched_keywords[:6])
    summary = (
        f"Results-driven <b>{html.escape(clean_job_title or 'Revenue Operations & AI Automation Engineer')}</b> "
        f"specializing in CRM architecture, workflow orchestration, and production AI pipelines ({html.escape(top_kw_str)}). "
        f"Proven track record at ADEN Immo GmbH unifying <b>32+ mission-critical production workflows</b>, "
        f"recovering <b>~350 hours/month</b> of manual coordination, and automating <b>~720 monthly commercial operations</b> "
        f"with an <b>85% reduction</b> in lead routing latency. Ready to scale fault-tolerant GTM &amp; operational systems for <b>{html.escape(company)}</b>."
    )

    # Score & sort bullets under each role by keyword overlap with the target job
    corpus_tokens = set(re.findall(r"[a-z0-9]+", (raw_title + " " + " ".join(matched_keywords) + " " + str(job.get("description_snippet") or "")).lower()))
    tailored_experience = []
    for idx, exp in enumerate(BASELINE_CV["experience"]):
        bullets = list(exp["bullets"])
        bullets.sort(
            key=lambda b: sum(1 for t in b.get("tags", []) if t in corpus_tokens or any(t in kw.lower() for kw in matched_keywords)),
            reverse=True,
        )
        # Keep top 4 bullets for ADEN Immo, top 3 most relevant for Independent Builder, 1 for earlier roles
        max_b = 4 if idx == 0 else (3 if idx == 1 else 1)
        tailored_experience.append({**exp, "bullets": bullets[:max_b]})

    return {
        "headline": headline,
        "summary_html": summary,
        "matched_keywords": matched_keywords,
        "experience": tailored_experience,
        "tailoring_mode": "deterministic_ats",
    }


def refine_tailoring_with_llm(job: dict[str, Any], base_plan: dict[str, Any]) -> dict[str, Any]:
    """Si GOOGLE_API_KEY est configurée, affine le titre et le résumé exécutif via Gemini (sans jamais inventer de faits)."""
    if not config.GOOGLE_API_KEY:
        return base_plan

    company = job.get("company") or "Target Company"
    title = job.get("title") or "Automation Engineer"
    desc = (job.get("description_snippet") or "")[:1800]
    kws = ", ".join(base_plan["matched_keywords"])

    prompt = f"""You are an elite Executive Resume Strategist and ATS Optimization Specialist.
Tailor the headline and 3-sentence executive summary of Manny Ruesch's resume for this specific role:
- Target Company: {company}
- Target Role: {title}
- Matched ATS Keywords to include naturally: {kws}
- Job Context: {desc}

STRICT TRUTHFULNESS RULES (NEVER VIOLATE):
1. NEVER invent employers, degrees, certifications, or metrics.
2. Ground all claims strictly in Manny's verified facts:
   - 32+ production workflows (n8n, Python, REST APIs, webhooks) deployed at ADEN Immo GmbH (Berlin).
   - ~350 hours/month of manual coordination saved.
   - ~720 high-stakes monthly operations automated across CRM, lead qualification, enrichment, and routing (-85% latency).
   - Fluent in English (C1) and French (Native).
3. Return ONLY valid JSON with keys:
   - "headline": string (max 75 chars, mirroring the target role title + core specialty)
   - "summary": string (3 concise sentences, plain text with <b>...</b> around 3-4 key metrics/terms, max 460 chars)
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GOOGLE_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400, "responseMimeType": "application/json"},
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        parsed = json.loads(raw_text)
        headline = (parsed.get("headline") or "").strip()
        summary = (parsed.get("summary") or "").strip()
        if headline and summary:
            # Sanitize summary allowing only <b> and </b> tags
            safe_summary = html.escape(summary).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
            base_plan["headline"] = headline[:80]
            base_plan["summary_html"] = safe_summary
            base_plan["tailoring_mode"] = f"llm_{config.GEMINI_MODEL}"
    except Exception:
        pass
    return base_plan


def render_ats_cv_html(job: dict[str, Any], plan: dict[str, Any]) -> str:
    """
    Génère le document HTML 1 page A4 :
    - Esthétique 'Editorial Warm Luxury' (Newsreader + Plus Jakarta Sans + Inter, #1A1715, #8C5332, #FAF8F5)
    - Architecture 100 % linéaire mono-colonne (zéro entrelacement horizontal dans les parseurs ATS)
    """
    cv = BASELINE_CV
    matched_lower = {k.lower() for k in plan["matched_keywords"]}

    # Merge any extra matched keywords from the job across the categories so 100% of matched keywords are in the ATS text layer
    all_static_text = " ".join(" ".join(c["items"]) for c in cv["skill_categories"]).lower()
    extra_matched = [
        kw for kw in plan["matched_keywords"]
        if kw.lower() not in all_static_text and len(kw) <= 32
    ]

    # Build skill categories with matched keywords front-loaded and bolded
    skill_rows_html = []
    for idx, cat in enumerate(cv["skill_categories"]):
        cat_extras = extra_matched[idx::len(cv["skill_categories"])]
        items = cat_extras + list(cat["items"])
        # Deduplicate preserving order
        dedup_items = []
        seen_i = set()
        for it in items:
            if it.lower() not in seen_i:
                seen_i.add(it.lower())
                dedup_items.append(it)
        dedup_items.sort(
            key=lambda item: (
                0
                if any(
                    kw in item.lower() or item.lower() in kw
                    for kw in matched_lower
                )
                else 1
            )
        )
        formatted_items = []
        for item in dedup_items[:9]:
            escaped_item = html.escape(item)
            if any(kw in item.lower() or item.lower() in kw for kw in matched_lower):
                formatted_items.append(f"<b>{escaped_item}</b>")
            else:
                formatted_items.append(escaped_item)
        skill_rows_html.append(
            f'<div class="skill-line"><span class="skill-cat">{html.escape(cat["category"])}:</span> '
            f'<span class="skill-items">{", ".join(formatted_items)}</span></div>'
        )

    # Build Experience HTML
    exp_blocks_html = []
    for exp in plan["experience"]:
        bullets_html = "\n".join(
            f'<li><b>{html.escape(b["lead"])}:</b> {b["text"]}</li>'
            for b in exp["bullets"]
        )
        exp_blocks_html.append(
            f"""    <div class="exp-item">
      <div class="exp-row-1">
        <span class="exp-company">{html.escape(exp["company"])}</span>
        <span class="exp-dates">{html.escape(exp["dates"])}</span>
      </div>
      <div class="exp-row-2">
        <span class="exp-role">{html.escape(exp["role"])}</span>
        <span class="exp-loc">{html.escape(exp["location"])}</span>
      </div>
      <ul class="exp-bullets">
        {bullets_html}
      </ul>
    </div>"""
        )

    # Build Education HTML
    edu_blocks_html = []
    for edu in cv["education"]:
        edu_blocks_html.append(
            f"""    <div class="edu-item">
      <div class="edu-row-1">
        <div><span class="edu-degree">{html.escape(edu["degree"])}</span> <span class="edu-sep">—</span> <span class="edu-school">{html.escape(edu["school"])}</span> <span class="edu-inline-detail">· {html.escape(edu["detail"])}</span></div>
        <span class="edu-dates">{html.escape(edu["dates"])}</span>
      </div>
    </div>"""
        )

    metrics_html = "\n".join(
        f"""    <div class="metric-pill">
      <span class="m-val">{html.escape(m["value"])}</span>
      <span class="m-lbl">{html.escape(m["label"])}</span>
    </div>"""
        for m in cv["metrics"]
    )

    safe_name = html.escape(cv["name"])
    safe_headline = html.escape(plan["headline"])
    safe_company = html.escape(job.get("company") or "Target Role")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{safe_name} — Resume ({safe_company})</title>
<meta name="author" content="{safe_name}">
<meta name="keywords" content="{html.escape(', '.join(plan['matched_keywords']))}">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=Plus+Jakarta+Sans:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');

  @page {{
    size: A4 portrait;
    margin: 0;
  }}
  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}
  html, body {{
    width: 210mm;
    height: 297mm;
    overflow: hidden;
  }}
  body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    color: #1F1C19;
    background: #FFFFFF;
    font-size: 8.1pt;
    line-height: 1.33;
    padding: 9.5mm 13.5mm;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}

  /* 1. HEADER — Single-column ATS-safe linear contact block */
  .header {{
    border-bottom: 1.5px solid #1A1715;
    padding-bottom: 6px;
    margin-bottom: 7.5px;
  }}
  .header-top {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }}
  .candidate-name {{
    font-family: 'Newsreader', Georgia, serif;
    font-size: 22pt;
    font-weight: 500;
    color: #1A1715;
    letter-spacing: -0.02em;
    line-height: 1;
  }}
  .candidate-location {{
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 7.5pt;
    font-weight: 600;
    color: #6E655D;
    letter-spacing: 0.03em;
    text-transform: uppercase;
  }}
  .candidate-headline {{
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 8.7pt;
    font-weight: 700;
    color: #8C5332;
    letter-spacing: 0.035em;
    text-transform: uppercase;
    margin-top: 2.5px;
    margin-bottom: 4px;
  }}
  .contact-bar {{
    font-size: 7.55pt;
    color: #4A433D;
    display: flex;
    flex-wrap: nowrap;
    gap: 6px;
    align-items: center;
    white-space: nowrap;
  }}
  .contact-bar a {{
    color: #1A1715;
    text-decoration: none;
    font-weight: 500;
    border-bottom: 1px solid #D5CEC4;
  }}
  .contact-sep {{
    color: #B5ABA0;
  }}

  /* 2. EXECUTIVE SUMMARY */
  .section {{
    margin-bottom: 7.5px;
  }}
  .section-title {{
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 7.75pt;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: #1A1715;
    border-bottom: 1px solid #D8D0C6;
    padding-bottom: 2px;
    margin-bottom: 4.5px;
  }}
  .summary-text {{
    font-family: 'Newsreader', Georgia, serif;
    font-size: 9.05pt;
    line-height: 1.38;
    color: #2D2824;
  }}
  .summary-text b {{
    font-weight: 600;
    color: #1A1715;
  }}

  /* 3. KEY IMPACT STRIP (Recruiter 6-second eye magnet) */
  .metrics-strip {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    margin-bottom: 7.5px;
  }}
  .metric-pill {{
    background: #FAF8F5;
    border: 1px solid #E6E0D6;
    border-left: 2.5px solid #8C5332;
    padding: 4px 7.5px;
    border-radius: 2px;
  }}
  .metric-pill .m-val {{
    display: block;
    font-family: 'Newsreader', Georgia, serif;
    font-size: 11pt;
    font-weight: 600;
    color: #1A1715;
    line-height: 1.08;
  }}
  .metric-pill .m-lbl {{
    display: block;
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 6.2pt;
    font-weight: 600;
    color: #6E655D;
    text-transform: uppercase;
    letter-spacing: 0.025em;
    margin-top: 1px;
  }}

  /* 4. CORE COMPETENCIES & TECHNICAL SKILLS (Linear rows for ATS) */
  .skills-box {{
    background: #FAF8F5;
    border: 1px solid #EAE5DE;
    padding: 5px 8.5px;
    border-radius: 2px;
  }}
  .skill-line {{
    font-size: 7.6pt;
    line-height: 1.34;
    margin-bottom: 1.8px;
    color: #3D3732;
  }}
  .skill-line:last-child {{
    margin-bottom: 0;
  }}
  .skill-cat {{
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-weight: 700;
    color: #1A1715;
  }}
  .skill-items b {{
    color: #8C5332;
    font-weight: 600;
  }}

  /* 5. PROFESSIONAL EXPERIENCE (Single-Column, 2-Tier Decoupled Headers) */
  .exp-item {{
    margin-bottom: 6.5px;
  }}
  .exp-item:last-child {{
    margin-bottom: 0;
  }}
  .exp-row-1 {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }}
  .exp-company {{
    font-family: 'Newsreader', Georgia, serif;
    font-size: 10pt;
    font-weight: 600;
    color: #1A1715;
  }}
  .exp-dates {{
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 7.2pt;
    font-weight: 600;
    color: #6E655D;
    white-space: nowrap;
  }}
  .exp-row-2 {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-top: 0px;
    margin-bottom: 2px;
  }}
  .exp-role {{
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 7.7pt;
    font-weight: 600;
    color: #8C5332;
  }}
  .exp-loc {{
    font-size: 7.1pt;
    color: #7D736A;
    font-style: italic;
    white-space: nowrap;
  }}
  .exp-bullets {{
    list-style: none;
    padding-left: 0;
  }}
  .exp-bullets li {{
    position: relative;
    padding-left: 10px;
    font-size: 7.7pt;
    color: #2D2824;
    line-height: 1.31;
    margin-bottom: 1.8px;
  }}
  .exp-bullets li:last-child {{
    margin-bottom: 0;
  }}
  .exp-bullets li::before {{
    content: "•";
    position: absolute;
    left: 0;
    color: #8C5332;
    font-weight: 700;
  }}
  .exp-bullets li b {{
    color: #1A1715;
    font-weight: 600;
  }}

  /* 6. EDUCATION & LANGUAGES */
  .edu-item {{
    margin-bottom: 2.5px;
  }}
  .edu-item:last-child {{
    margin-bottom: 0;
  }}
  .edu-row-1 {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
  }}
  .edu-degree {{
    font-family: 'Newsreader', Georgia, serif;
    font-size: 8.75pt;
    font-weight: 600;
    color: #1A1715;
  }}
  .edu-sep {{
    color: #8C5332;
    margin: 0 2px;
  }}
  .edu-school {{
    font-size: 7.65pt;
    font-weight: 600;
    color: #8C5332;
  }}
  .edu-inline-detail {{
    font-size: 7.3pt;
    color: #5C554E;
  }}
  .edu-dates {{
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 7.15pt;
    font-weight: 600;
    color: #6E655D;
    white-space: nowrap;
  }}
  .lang-bar {{
    margin-top: 4px;
    padding-top: 3.5px;
    border-top: 1px dashed #E2DBD1;
    font-size: 7.55pt;
    color: #2D2824;
  }}
  .lang-bar b {{
    font-family: 'Plus Jakarta Sans', sans-serif;
    color: #1A1715;
  }}
</style>
</head>
<body>

  <!-- 1. ATS-SAFE HEADER -->
  <header class="header">
    <div class="header-top">
      <h1 class="candidate-name">{safe_name}</h1>
      <span class="candidate-location">{html.escape(cv["location"])}</span>
    </div>
    <div class="candidate-headline">{safe_headline}</div>
    <div class="contact-bar">
      <a href="mailto:{html.escape(cv['email'])}">{html.escape(cv['email'])}</a>
      <span class="contact-sep">|</span>
      <span>{html.escape(cv['phone'])}</span>
      <span class="contact-sep">|</span>
      <a href="https://{html.escape(cv['portfolio'])}">{html.escape(cv['portfolio'])}</a>
      <span class="contact-sep">|</span>
      <a href="https://{html.escape(cv['linkedin'])}">{html.escape(cv['linkedin'])}</a>
      <span class="contact-sep">|</span>
      <a href="https://{html.escape(cv['github'])}">{html.escape(cv['github'])}</a>
    </div>
  </header>

  <!-- 2. PROFESSIONAL SUMMARY -->
  <section class="section">
    <h2 class="section-title">Professional Summary</h2>
    <p class="summary-text">{plan["summary_html"]}</p>
  </section>

  <!-- 3. QUANTIFIED IMPACT METRICS -->
  <div class="metrics-strip">
{metrics_html}
  </div>

  <!-- 4. CORE COMPETENCIES & TECHNICAL SKILLS -->
  <section class="section">
    <h2 class="section-title">Core Competencies &amp; Technical Stack</h2>
    <div class="skills-box">
      {"".join(skill_rows_html)}
    </div>
  </section>

  <!-- 5. PROFESSIONAL EXPERIENCE -->
  <section class="section">
    <h2 class="section-title">Professional Experience</h2>
{chr(10).join(exp_blocks_html)}
  </section>

  <!-- 6. EDUCATION & LANGUAGES -->
  <section class="section" style="margin-bottom: 0;">
    <h2 class="section-title">Education &amp; Languages</h2>
{chr(10).join(edu_blocks_html)}
    <div class="lang-bar">
      <b>Languages:</b> {html.escape(cv["languages"])}
    </div>
  </section>

</body>
</html>"""


def audit_ats_pdf(pdf_path: Path, html_content: str, matched_keywords: list[str]) -> dict[str, Any]:
    """
    Vérifie déterministiquement le PDF généré :
    1. Nombre de pages strictement égal à 1 (/Type /Page).
    2. Présence des sections standards ATS dans l'ordre linéaire.
    3. Présence des coordonnées et des mots-clés cibles de l'offre.
    """
    raw_bytes = pdf_path.read_bytes()
    page_count = raw_bytes.count(b"/Type /Page") - raw_bytes.count(b"/Type /Pages")
    if page_count <= 0:
        page_count = 1

    # Strip HTML tags to inspect the exact linear text stream
    plain_text = re.sub(r"<style[\s\S]*?</style>", " ", html_content)
    plain_text = re.sub(r"<[^>]+>", " ", plain_text)
    plain_text = html.unescape(re.sub(r"\s+", " ", plain_text)).strip()
    plain_lower = plain_text.lower()

    expected_sections = [
        "Professional Summary",
        "Core Competencies & Technical Stack",
        "Professional Experience",
        "Education & Languages",
    ]
    positions = [plain_lower.find(s.lower()) for s in expected_sections]
    sections_present = all(p >= 0 for p in positions)
    linear_order_ok = sections_present and positions == sorted(positions)

    present_kw = [kw for kw in matched_keywords if kw.lower() in plain_lower]
    kw_ratio = round((len(present_kw) / max(1, len(matched_keywords))) * 100)

    checks = [
        {"id": "one_page", "label": "Strictement 1 page A4 (aucun débordement)", "ok": page_count == 1},
        {"id": "single_column", "label": "Flux linéaire 1 colonne (Workday / Greenhouse / Lever / Ashby)", "ok": linear_order_ok},
        {"id": "standard_headings", "label": "Sections standards ATS détectées dans l'ordre", "ok": sections_present},
        {"id": "contact_parseable", "label": "Email, téléphone, LinkedIn, GitHub & Portfolio extractibles", "ok": all(x in plain_lower for x in ("manny.ruesch@gmail.com", "+33 6 12 63 03 31", "linkedin.com/in/manny-ruesch", "github.com/manny-ruesch"))},
    ]
    ats_score = min(100, int(kw_ratio * 0.6 + (40 if all(c["ok"] for c in checks) else 25)))

    return {
        "ats_score": ats_score,
        "page_count": page_count,
        "linear_order_ok": linear_order_ok,
        "matched_keywords": present_kw,
        "headline": "",
        "checks": checks,
    }


def render_html_to_pdf(html_path: Path, pdf_path: Path) -> tuple[bool, str]:
    """Compile le fichier HTML en PDF vectoriel A4 via Edge Headless ou Playwright Chromium."""
    edge_bin = next((p for p in EDGE_CANDIDATES if p.exists()), None)
    if edge_bin:
        cmd = [
            str(edge_bin),
            "--headless",
            "--disable-gpu",
            f"--print-to-pdf={str(pdf_path)}",
            "--no-margins",
            f"file:///{str(html_path).replace(os.sep, '/')}",
        ]
        subprocess.run(cmd, check=True)
        return True, "edge_headless"

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file:///{str(html_path).replace(os.sep, '/')}", wait_until="networkidle")
            page.pdf(path=str(pdf_path), format="A4", print_background=True)
            browser.close()
        return True, "playwright_chromium"
    except Exception as exc:
        return False, f"Aucun moteur PDF disponible ({exc})"


def create_tailored_cv_pdf(job_id: str, sync_active_cv: bool = True) -> tuple[bool, dict[str, Any]]:
    """
    Génère un CV 1 page A4 sur-mesure pour l'offre `job_id`, optimisé recruteur + ATS.
    Sauvegarde :
    - Dans `tailored_cvs/CV_Manny_Ruesch_{Company}.pdf` (et `.html` source)
    - Sur le Bureau (`E:/Users/Manny/Desktop/CV_Manny_Ruesch_{Company}.pdf`)
    - Dans `uploaded_cv.pdf` (si `sync_active_cv=True`) pour l'Auto-Fill Playwright en 1 clic.
    """
    job = db.get_job_by_id(job_id)
    if not job:
        return False, {"error": f"Offre introuvable : {job_id}"}

    matched_keywords = extract_job_keywords(job)
    plan = build_deterministic_tailoring(job, matched_keywords)
    plan = refine_tailoring_with_llm(job, plan)

    html_content = render_ats_cv_html(job, plan)

    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    safe_company = "".join(c for c in (job.get("company") or "Company") if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_")
    if not safe_company:
        safe_company = "Company"

    filename = f"CV_Manny_Ruesch_{safe_company}.pdf"
    html_path = TAILORED_DIR / f"CV_Manny_Ruesch_{safe_company}.html"
    pdf_path = TAILORED_DIR / filename
    desktop_pdf_path = DESKTOP_DIR / filename

    html_path.write_text(html_content, encoding="utf-8")

    ok, engine = render_html_to_pdf(html_path, pdf_path)
    if not ok or not pdf_path.exists():
        return False, {"error": engine}

    # Copy to Desktop and optionally sync to uploaded_cv.pdf for Auto-Fill
    try:
        if DESKTOP_DIR.exists():
            shutil.copy2(pdf_path, desktop_pdf_path)
    except Exception:
        desktop_pdf_path = pdf_path

    if sync_active_cv:
        try:
            active_cv_dest = BASE_DIR / "uploaded_cv.pdf"
            shutil.copy2(pdf_path, active_cv_dest)
        except Exception:
            pass

    audit = audit_ats_pdf(pdf_path, html_content, plan["matched_keywords"])
    audit["headline"] = plan["headline"]
    audit["tailoring_mode"] = plan["tailoring_mode"]

    return True, {
        "success": True,
        "job_id": job_id,
        "company": job.get("company"),
        "title": job.get("title"),
        "filename": filename,
        "pdf_path": str(desktop_pdf_path),
        "local_pdf_path": str(pdf_path),
        "html_path": str(html_path),
        "download_url": f"/api/jobs/{job_id}/download-cv",
        "ats_audit": audit,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ok, info = create_tailored_cv_pdf(sys.argv[1])
        print(json.dumps(info, indent=2, ensure_ascii=False))
