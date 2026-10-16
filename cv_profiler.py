import json
import re
import urllib.request
from pathlib import Path
from typing import Any
import pypdf
import yaml
import config

PROFILE_PATH = Path(__file__).resolve().parent / "candidate_profile.json"
YAML_PROFILE_PATH = Path(__file__).resolve().parent / "profile.yaml"

PROFILER_SYSTEM_PROMPT = """You are an expert universal career advisor and resume parser across ALL industries (healthcare, finance, accounting, sales, marketing, HR, logistics, education, hospitality, legal, operations, and software/engineering).
Your goal is to parse a candidate's resume/CV text and extract an exhaustive, structured profile for an autonomous job hunting assistant.
You must return a valid JSON object matching this schema:
{
  "name": "Candidate Full Name",
  "target_role": "Target Job Title (in the resume's primary language)",
  "domain": "Industry / Domain Name (e.g. Healthcare, Accounting & Finance, Sales, Operations, Software Engineering)",
  "location": "City, Country or Remote / Timezone",
  "work_mode": "preferred | required | any (use 'any' for on-site roles like nursing/retail/hospitality, 'preferred' for hybrid/office, 'required' for 100% remote)",
  "email": "Email if found",
  "phone": "Phone if found",
  "linkedin": "LinkedIn URL if found",
  "portfolio": "Portfolio/Website if found",
  "core_stack": ["skill_or_certification_1", "skill_or_tool_2", "skill_3"],
  "positive_keywords": ["keyword1", "keyword2", "keyword3"],
  "negative_keywords": ["unwanted_domain_1", "unwanted_role_1"],
  "experience_summary": "Two sentences highlighting measurable achievements and professional impact.",
  "key_metrics": "Key measurable achievements or qualifications summary",
  "languages": "Languages spoken"
}
Ensure keywords are lower-case, concise, and relevant to the candidate's actual profession.
"""


def profile_cv_heuristic(cv_text: str) -> dict[str, Any]:
    """Repli déterministe hors-ligne (Zero-API-Key) pour extraire le profil de n'importe quel CV PDF."""
    lines = [ln.strip() for ln in cv_text.splitlines() if ln.strip()]
    name = lines[0][:60] if lines else "Candidat"
    target_role = lines[1][:80] if len(lines) > 1 else "Professionnel"

    email_m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", cv_text)
    phone_m = re.search(r"(\+?\d[\d\s().-]{7,}\d)", cv_text)
    linkedin_m = re.search(r"(linkedin\.com/in/[A-Za-z0-9_-]+)", cv_text, re.I)

    # Extract frequent capitalized or domain terms as skills
    words = re.findall(r"\b[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9+#.-]{2,24}\b", cv_text)
    stop = {"les", "des", "dans", "pour", "avec", "sur", "une", "par", "aux", "est", "sont", "the", "and", "for", "with", "from", "that", "this", "experience", "formation", "education", "skills", "competences"}
    seen: list[str] = []
    for w in words:
        wl = w.lower()
        if wl not in stop and not wl.isdigit() and wl not in [s.lower() for s in seen]:
            seen.append(w)
        if len(seen) >= 12:
            break

    profile = {
        "name": name,
        "target_role": target_role,
        "domain": "Tous secteurs",
        "location": "France / Europe",
        "work_mode": "preferred",
        "email": email_m.group(0) if email_m else "",
        "phone": phone_m.group(0).strip() if phone_m else "",
        "linkedin": linkedin_m.group(0) if linkedin_m else "",
        "portfolio": "",
        "core_stack": seen[:8] or ["Gestion de projet", "Communication", "Analyse"],
        "positive_keywords": [s.lower() for s in seen[:10]],
        "negative_keywords": [],
        "experience_summary": " ".join(lines[2:5])[:280] if len(lines) > 2 else "",
        "key_metrics": "",
        "languages": "Français, Anglais",
    }
    try:
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    return profile


def sync_profile_yaml(profile: dict[str, Any], yaml_path: Path | None = None) -> None:
    """Synchronise le profil extrait du CV avec profile.yaml uniquement si le candidat change de métier."""
    target = yaml_path or YAML_PROFILE_PATH
    if not target.exists():
        return
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return
        cand = data.setdefault("candidate", {})
        if profile.get("name"):
            cand["name"] = profile["name"]
        if profile.get("target_role"):
            cand["headline"] = profile["target_role"]
        if profile.get("experience_summary"):
            cand["summary"] = profile["experience_summary"]
        target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except Exception:
        pass

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
