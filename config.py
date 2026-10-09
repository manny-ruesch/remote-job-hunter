import os
import sys
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

# Check local .env first, then fallback paths
load_env_file(BASE_DIR / ".env")
load_env_file(Path("C:/Users/Manny/AppData/Local/hermes/.env"))
load_env_file(Path.home() / ".hermes/.env")

DB_PATH = os.path.join(str(BASE_DIR), "jobs.db")
DIGEST_PATH = os.path.join(str(BASE_DIR), "HOT_OPPORTUNITIES.md")

# Applicant Profile
APPLICANT = {
    "name": os.getenv("APPLICANT_NAME", "AI Automation Builder"),
    "location": os.getenv("APPLICANT_LOCATION", "Remote (EU / US timezone overlap)"),
    "email": os.getenv("APPLICANT_EMAIL", ""),
    "phone": os.getenv("APPLICANT_PHONE", ""),
    "portfolio": os.getenv("APPLICANT_PORTFOLIO", ""),
    "github": os.getenv("APPLICANT_GITHUB", ""),
    "linkedin": os.getenv("APPLICANT_LINKEDIN", ""),
    "title": os.getenv("APPLICANT_TITLE", "AI & Automation Builder · Operations, Growth & Systems"),
    "key_metrics": os.getenv("APPLICANT_KEY_METRICS", "32+ production n8n workflows, ~350h/mo saved, ~720 ops/mo automated"),
    "stack": os.getenv("APPLICANT_STACK", "n8n, Python, LLMs (Gemini, Claude, OpenAI), Prompt Chaining, Multi-Agent Systems, REST APIs, Webhooks, PostgreSQL, Docker, HubSpot"),
    "languages": os.getenv("APPLICANT_LANGUAGES", "French (Native), English (C1 Full Professional)"),
    "experience_highlight": os.getenv("APPLICANT_HIGHLIGHT", "Builder of multimodal AI automation pipelines and autonomous CRM/lead enrichment engines.")
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
