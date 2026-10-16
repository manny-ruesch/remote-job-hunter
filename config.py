import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def load_env_file(filepath: Path):
    if not filepath.exists():
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

# Check RJH_HOME / local .env first, then fallback paths
ACTIVE_HOME = Path(os.environ["RJH_HOME"]).expanduser() if os.environ.get("RJH_HOME") else BASE_DIR
load_env_file(ACTIVE_HOME / ".env")
load_env_file(BASE_DIR / ".env")
load_env_file(Path("C:/Users/Manny/AppData/Local/hermes/.env"))
load_env_file(Path.home() / ".hermes/.env")

DB_PATH = os.environ.get("RJH_DB") or os.path.join(str(ACTIVE_HOME), "jobs.db")
DIGEST_PATH = os.path.join(str(ACTIVE_HOME), "HOT_OPPORTUNITIES.md")

# Load profile.yaml fallback values for APPLICANT when env vars are not set
_prof_identity: dict = {}
_prof_candidate: dict = {}
_prof_search: dict = {}
_prof_llm: dict = {}
_prof_scoring: dict = {}
try:
    import yaml
    _pfile = ACTIVE_HOME / "profile.yaml"
    if not _pfile.exists():
        _pfile = BASE_DIR / "profile.yaml"
    if _pfile.exists():
        _pdata = yaml.safe_load(_pfile.read_text(encoding="utf-8")) or {}
        _prof_identity = _pdata.get("identity") or {}
        _prof_candidate = _pdata.get("candidate") or {}
        _prof_search = _pdata.get("search") or {}
        _prof_llm = _pdata.get("llm") or {}
        _prof_scoring = _pdata.get("scoring") or {}
except Exception:
    pass

_links = _prof_identity.get("links") or {}
_primary_titles = (_prof_search.get("titles") or {}).get("primary") or []
_boost_keys = list(((_prof_search.get("keywords") or {}).get("boost") or {}).keys())
_facts = _prof_candidate.get("facts") or []

# Applicant Profile (hydrated from env vars first, then profile.yaml, then safe defaults)
APPLICANT = {
    "name": os.getenv("APPLICANT_NAME") or _prof_identity.get("name") or "AI Automation Builder",
    "location": os.getenv("APPLICANT_LOCATION") or _prof_identity.get("city") or "Remote (EU / US timezone overlap)",
    "email": os.getenv("APPLICANT_EMAIL") or _prof_identity.get("email") or "",
    "phone": os.getenv("APPLICANT_PHONE") or _prof_identity.get("phone") or "",
    "portfolio": os.getenv("APPLICANT_PORTFOLIO") or _links.get("portfolio") or "https://manny-ruesch.github.io",
    "github": os.getenv("APPLICANT_GITHUB") or _links.get("github") or "",
    "linkedin": os.getenv("APPLICANT_LINKEDIN") or _links.get("linkedin") or "",
    "title": os.getenv("APPLICANT_TITLE") or (_primary_titles[0].title() if _primary_titles else "AI & Automation Builder · Operations, Growth & Systems"),
    "key_metrics": os.getenv("APPLICANT_KEY_METRICS") or (_facts[0] if _facts else "32+ production n8n workflows, ~350h/mo saved, ~720 ops/mo automated"),
    "stack": os.getenv("APPLICANT_STACK") or (", ".join(_boost_keys) if _boost_keys else "n8n, Python, LLMs, REST APIs, Webhooks, PostgreSQL, HubSpot"),
    "languages": os.getenv("APPLICANT_LANGUAGES", "French (Native), English (C1 Full Professional)"),
    "experience_highlight": os.getenv("APPLICANT_HIGHLIGHT") or (_prof_candidate.get("summary") or "").strip() or "Builder of multimodal AI automation pipelines and autonomous CRM/lead enrichment engines."
}

# Keywords to prioritize
KEYWORD_FILTERS = [
    "automation", "n8n", "ai", "llm", "agent", "workflow", 
    "revops", "growth", "operations", "python", "prompt", "pipeline",
    "solutions", "forward deployed", "integration"
]

# Roles specifically targeted (Junior to Mid sweet-spot for Manny's hybrid tech/ops profile)
TARGET_ROLES = [
    "ai automation", "automation specialist", "automation engineer",
    "workflow automation", "revops", "revenue operations", "growth operations",
    "solutions engineer", "associate solutions engineer", "junior solutions engineer",
    "ai ops", "ai implementation", "technical operations", "integration specialist",
    "ai agent specialist", "no-code engineer", "low-code"
]

# Roles and Seniority to disqualify (Strictly avoid Senior, Staff, Principal, LeetCode CS, Research)
EXCLUDED_ROLES = [
    "senior", "staff", "principal", "lead", "director", "head of", "architect",
    "backend engineer", "back-end engineer", "front-end engineer", "frontend engineer", 
    "full-stack engineer", "fullstack engineer", "compiler", "kernel", 
    "distributed systems", "infrastructure engineer", "site reliability", "sre", 
    "research scientist", "research engineer", "account executive", "recruiter", 
    "sales development", "account manager", "legal", "payroll"
]

# Discord Webhook & Bot
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", None)

# Google Gemini API Key
def get_google_api_key():
    candidates = [
        os.getenv("GOOGLE_API_KEY"),
        "C:/Users/Manny/AppData/Local/hermes/.env",
        os.path.expanduser("~/.hermes/.env"),
        str(BASE_DIR / ".env")
    ]
    if os.getenv("GOOGLE_API_KEY"):
        return os.getenv("GOOGLE_API_KEY")
    for env_path in candidates[1:]:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GOOGLE_API_KEY="):
                        return line.split("=", 1)[1].strip("\"'")
    return ""

GOOGLE_API_KEY = get_google_api_key()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# Scoring threshold
MIN_SCORE_THRESHOLD = float(os.getenv("MIN_SCORE_THRESHOLD", "7.5"))

# Server Config
COMMAND_CENTER_PORT = int(os.getenv("COMMAND_CENTER_PORT", "5070"))
COMMAND_CENTER_HOST = os.getenv("COMMAND_CENTER_HOST", "127.0.0.1")
