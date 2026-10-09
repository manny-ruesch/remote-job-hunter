import json
import time
import urllib.request
import urllib.error
from typing import Optional, Any
from config import GOOGLE_API_KEY, GEMINI_MODEL, APPLICANT

SYSTEM_PROMPT = f"""You are an elite Tech Career Agent representing Manny Ruesch.
Manny's profile:
- Title: {APPLICANT['title']}
- Location: {APPLICANT['location']}
- Languages: {APPLICANT['languages']}
- Core Stack: {APPLICANT['stack']}
- Proven Impact: {APPLICANT['key_metrics']}
- Key Highlights: {APPLICANT['experience_highlight']}
- Portfolio: {APPLICANT['portfolio']} | GitHub: {APPLICANT['github']}

Your task is to analyze a remote job posting and evaluate its relevance for Manny.

Scoring criteria (BE RIGOROUS, PRAGMATIC AND ADVERSARIAL):
- 9.0 - 10.0: IDEAL SWEET-SPOT (Junior to Mid Builder, Full-Time OR Part-Time/Contract). AI Automation Specialist, Solutions Engineer (Associate/Junior/Mid), Operations Automation Builder, RevOps Specialist, or Integration Specialist at a PME, startup, agency, or scale-up where the primary mission is connecting APIs, building n8n/Python pipelines, prompt chaining, and automating business workflows. Note: Part-Time (10-25h/week), Fractional, or Freelance contract roles in automation are highly valued!
- 7.5 - 8.9: STRONG FIT. SMB / Scale-up roles in Operations, Growth, or Customer Technical Solutions where Manny's ADEN Immo experience (saving 350h/mo with 32+ production n8n workflows) provides immediate, provable ROI from week 1.
- < 5.0: AUTOMATIC DISQUALIFICATION (HARD GATES):
  * Seniority Mismatch: Any posting requiring 4+ or 5+ years of experience, or explicitly titled Senior / Staff / Principal / Lead / Director / Architect. Manny has ~2 years of high-velocity automation/building experience.
  * Frontier AI Lab Core Engineering (Anthropic, OpenAI, Cohere, DeepMind) demanding heavy computer science, distributed infrastructure, or model evaluations.
  * Language Mismatch: If the job strictly requires "Native German" or "Fluent German" (Manny is French native, English C1, German basic).
  * Travel / On-Site Requirement: If the job requires frequent travel (> 10%) or is not true 100% full-remote.
  * Pure "Software Engineer" / "Backend Engineer" focused on distributed systems, compilers, or heavy CS algorithms/LeetCode.
  * Research Scientist / Deep Learning Researcher.
  * Non-technical Sales / SDR / HR / Recruiter roles.

Output strictly valid JSON with these keys:
{{
  "fit_score": 8.5,
  "rationale": "One-line explanation of why this matches Manny's skills.",
  "tech_match": ["n8n", "Python", "LLMs", "REST APIs"],
  "pitch_hook": "2 punchy sentences connecting Manny's real production work to this specific job's biggest need.",
  "tailored_pitch": "A complete, high-converting 120-150 word application pitch written in professional English, citing the 32+ workflows in production, hours saved, and linking to his portfolio.",
  "proof_of_work": {{
    "problem_diagnosis": "Specific operational or technical bottleneck the company is hiring to solve.",
    "solution_architecture": "3 concrete technical implementation steps (Ingestion, LLM Validation/Guardrails, Action Delivery) using n8n, Python, and modern APIs.",
    "mermaid_diagram": "graph LR\\n  A[Inbound Lead/Event] --> B[n8n / FastAPI Router]\\n  B --> C[LLM Structured Parse & Guardrails]\\n  C --> D[CRM / Webhook Sync]"
  }},
  "cold_outreach": {{
    "target_role": "Title of the hiring manager / decision maker (e.g. Head of Solutions Engineering, VP Ops, CTO)",
    "subject": "Punchy message subject line under 50 chars",
    "dm_text": "A concise 50-70 word cold message to the decision maker bypassing the ATS, referencing their bottleneck, Manny's 32+ workflows / 350h saved, and linking to his portfolio.",
    "linkedin_search_url": "https://www.google.com/search?q=site%3Alinkedin.com%2Fin+%22Company%22+%22Target+Role%22"
  }}
}}
Note: If fit_score < 7.5, set proof_of_work and cold_outreach to null to conserve tokens.
"""

def analyze_job(title: str, company: str, location: str, description: str) -> dict[str, Any]:
    model_name = GEMINI_MODEL if GEMINI_MODEL.startswith("gemini-") else f"gemini-{GEMINI_MODEL}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    
    prompt = f"""Evaluate this job posting for candidate {APPLICANT.get('name', 'Builder')}:

COMPANY: {company}
JOB TITLE: {title}
LOCATION: {location}
DESCRIPTION:
{description[:8000]}
"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GOOGLE_API_KEY or ""
        }
    )
    
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        raw_text = parts[0]["text"]
                        res = json.loads(raw_text)
                        res["analysis_status"] = "success"
                        return res
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(4 * (attempt + 1))
                continue
            err_msg = e.read().decode("utf-8")
            print(f"⚠️ Gemini API error [{e.code}]: {err_msg[:200]}")
            break
        except Exception as e:
            print(f"⚠️ Analysis error: {e}")
            time.sleep(2)
            
    return {
        "fit_score": None,
        "analysis_status": "failed",
        "rationale": "Analysis failed",
        "tech_match": [],
        "pitch_hook": "",
        "tailored_pitch": ""
    }
