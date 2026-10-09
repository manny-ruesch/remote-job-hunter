import json
import urllib.request
from pathlib import Path
from typing import Any
import pypdf
import config

PROFILE_PATH = Path(__file__).resolve().parent / "candidate_profile.json"

PROFILER_SYSTEM_PROMPT = """You are an expert technical career assessor. 
Your goal is to parse a candidate's resume/CV text and extract an exhaustive, structured profile for an autonomous job hunting agent.
You must return a valid JSON object matching this schema:
{
  "name": "Candidate Full Name",
  "target_role": "Target Job Title",
  "domain": "Domain Name",
  "location": "Location / Timezone",
  "email": "Email if found",
  "phone": "Phone if found",
  "linkedin": "LinkedIn URL if found",
  "portfolio": "Portfolio/Website if found",
  "core_stack": ["tool1", "tool2", "tool3"],
  "positive_keywords": ["keyword1", "keyword2", "keyword3"],
  "negative_keywords": ["unwanted_tech1", "unwanted_role1"],
  "experience_summary": "Two sentences highlighting measurable achievements and technical impact.",
  "key_metrics": "Key metric summary",
  "languages": "Languages spoken"
}
Ensure keywords are lower-case, concise, and production-relevant.
"""

def extract_pdf_text(pdf_path: Path) -> str:
    if not pdf_path.exists():
        return ""
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages_text).strip()
    except Exception as e:
        print(f"⚠️ Error extracting PDF text: {e}")
        return ""

def profile_cv_with_gemini(cv_text: str) -> dict[str, Any]:
    if not cv_text:
        return {}

    model_name = config.GEMINI_MODEL if config.GEMINI_MODEL.startswith("gemini-") else f"gemini-{config.GEMINI_MODEL}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

    prompt = f"Analyze this resume text and output the structured candidate JSON profile:\n\n{cv_text[:8000]}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": PROFILER_SYSTEM_PROMPT}]},
        "generationConfig": {"responseMimeType": "application/json"}
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": config.GOOGLE_API_KEY or ""
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    profile = json.loads(parts[0]["text"])
                    # Save to candidate_profile.json
                    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
                        json.dump(profile, f, indent=2, ensure_ascii=False)
                    return profile
    except Exception as e:
        print(f"⚠️ Gemini profiling error: {e}")

    return {}

def get_active_profile() -> dict[str, Any]:
    if PROFILE_PATH.exists():
        try:
            with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return config.APPLICANT
