import re
from typing import Any

# Highly desirable roles & competencies for Manny
HIGH_BOOST_KEYWORDS = [
    "automation", "agent", "solutions engineer", "forward deployed", "solution architect",
    "revops", "revenue operations", "ai ops", "workflow", "integration", "implementation engineer"
]

TOOLING_KEYWORDS = [
    "n8n", "make.com", "zapier", "langchain", "crewai", "autogen", "multi-agent",
    "webhook", "rest api", "lead qualification", "crm automation", "hubspot", "airtable"
]

CORE_TECH_KEYWORDS = [
    "python", "llm", "llms", "openai", "anthropic", "gemini", "fastapi", "prompt engineering"
]

# Roles that waste time, tokens, or result in instant LeetCode/algorithmic rejection
PENALTY_ROLES = [
    "android", "ios", "react native", "swift", "kotlin", "flutter",
    "hardware", "firmware", "embedded", "kernel", "compiler",
    "c++", "rust", "golang", "java", "spring boot", "hvac",
    "hvac technician", "office manager", "designer", "recruiter",
    "account executive", "sales development", "sdr", "bdr", "legal counsel"
]

DEEP_BACKEND_PENALTIES = [
    "distributed systems", "database internals", "low-latency",
    "staff backend", "principal backend", "principal engineer", "infrastructure engineer",
    "site reliability", "sre", "kubernetes cluster"
]

HARD_DISQUALIFIERS = [
    "8+ years", "7+ years", "10+ years", "staff engineer", "principal engineer",
    "native german", "german native", "travel frequently", "travel 25-50%", "travel 50%"
]

def score_job_heuristically(job: dict[str, Any]) -> float:
    """
    Computes a fast lexical score (-50 to +100) based on title and description density.
    Runs in < 0.1ms per job with zero LLM/API cost.
    """
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()
    blob = f"{title} {desc}"
    
    score = 0.0
    
    # Hard disqualifiers in title or description
    for hd in HARD_DISQUALIFIERS:
        if hd in blob:
            score -= 40.0

    # 1. Disqualification checks in title
    for p in PENALTY_ROLES:
        if re.search(r'\b' + re.escape(p) + r'\b', title):
            score -= 30.0
            
    for p in DEEP_BACKEND_PENALTIES:
        if re.search(r'\b' + re.escape(p) + r'\b', title):
            score -= 20.0

    # 2. High boost roles in title
    for kw in HIGH_BOOST_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', title):
            score += 25.0
            
    # 3. Tooling mentions in title or description
    for kw in TOOLING_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', title):
            score += 30.0
        elif re.search(r'\b' + re.escape(kw) + r'\b', desc):
            score += 10.0
            
    # 4. Core stack boost
    for kw in CORE_TECH_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', title):
            score += 10.0
        elif re.search(r'\b' + re.escape(kw) + r'\b', desc):
            score += 2.0
            
    # 5. Remote / Location bonus
    loc = (job.get("location") or "").lower()
    if any(r in loc for r in ["remote", "europe", "emea", "worldwide", "anywhere", "berlin", "germany", "france"]):
        score += 5.0
    elif "us only" in loc or "must reside in the united states" in desc:
        score -= 15.0

    return score

def pre_rank_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Ranks a list of jobs descending by heuristic score.
    Injects '_heuristic_score' key into each job dict.
    """
    for j in jobs:
        j["_heuristic_score"] = score_job_heuristically(j)
        
    return sorted(jobs, key=lambda x: x["_heuristic_score"], reverse=True)
