"""Market Skill Gap Analyzer & Filter/Keyword Audit (Pack 2 — inspiré de career-ops).

Analyse en temps réel :
1. Les compétences les plus demandées dans le corpus d'offres collectées (avec support
   universel : Tech/IA/Automation, Data/Cloud, Produit/Gestion, Santé/Médical,
   Finance/Compta, Marketing/Sales) via l'automate ``TermSet`` + ``norm()`` (gérant
   correctement ``C++``, ``C#``, ``.NET``, ``Node.js``, les accents et pluriels).
2. Le Skill Gap : compétences à forte demande absentes du profil candidat vs compétences
   déjà couvertes par le profil.
3. L'audit des filtres et gates (``GateTriggerStat``) : combien d'offres sont rejetées
   (``reject``) ou pénalisées (``penalize``) par chaque règle du profil.
4. Les mots-clés de boost "morts" (0 correspondance sur le corpus analysé).
5. La synchronisation atomique en 1 clic d'une compétence manquante vers ``profile.yaml``
   et ``candidate_profile.json`` (idempotente, protégée par verrou et validée par
   ``load_profile`` avant remplacement atomique).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
from typing import Any

import yaml

from .heuristic import ProfileScorer, TermSet
from .models import Job
from .profile import Profile, ProfileError, load_profile
from .textutil import norm, strip_html

_PROFILE_MUTATION_LOCK = threading.Lock()

# Table d'alias canoniques (normalisée via norm()) pour regrouper les variantes
SKILL_ALIASES: dict[str, str] = {
    "nodejs": "node.js",
    "node js": "node.js",
    "reactjs": "react",
    "react.js": "react",
    "vuejs": "vue.js",
    "vue": "vue.js",
    "nextjs": "next.js",
    "next js": "next.js",
    "postgres": "postgresql",
    "golang": "go",
    "k8s": "kubernetes",
    "py": "python",
    "ts": "typescript",
    "js": "javascript",
    "genai": "generative ai",
    "ia generative": "generative ai",
    "llms": "llm",
    "rag": "rag",
    "make.com": "make",
    "integromat": "make",
    "powerbi": "power bi",
    "ga4": "google analytics",
    "sea": "google ads",
    "ehpad": "geriatrie",
}

# Catalogue multi-secteurs de compétences (universel : Tech & Non-Tech)
SKILL_TAXONOMY: dict[str, tuple[str, ...]] = {
    "IA & Automation": (
        "n8n",
        "make",
        "zapier",
        "llm",
        "langchain",
        "langgraph",
        "rag",
        "openai",
        "claude",
        "anthropic",
        "gemini",
        "prompt engineering",
        "ai agents",
        "generative ai",
        "mcp",
        "ollama",
        "vllm",
        "airtable",
        "notion",
        "webhooks",
    ),
    "Software & Dev": (
        "python",
        "typescript",
        "javascript",
        "node.js",
        "react",
        "next.js",
        "vue.js",
        "fastapi",
        "django",
        "flask",
        "go",
        "rust",
        "java",
        "c#",
        "c++",
        ".net",
        "php",
        "ruby",
        "graphql",
        "rest api",
        "sql",
        "postgresql",
        "mongodb",
        "redis",
        "playwright",
        "selenium",
    ),
    "Cloud, Data & DevOps": (
        "docker",
        "kubernetes",
        "aws",
        "gcp",
        "azure",
        "terraform",
        "ci/cd",
        "github actions",
        "linux",
        "snowflake",
        "dbt",
        "airflow",
        "spark",
        "bigquery",
        "tableau",
        "power bi",
        "looker",
        "pandas",
    ),
    "Marketing, Growth & Sales": (
        "hubspot",
        "salesforce",
        "crm",
        "seo",
        "google ads",
        "meta ads",
        "google analytics",
        "copywriting",
        "cold email",
        "outbound",
        "lead generation",
        "growth",
        "revops",
        "gtm",
        "klaviyo",
        "stripe",
        "shopify",
    ),
    "Produit, Design & Gestion": (
        "product management",
        "scrum",
        "agile",
        "jira",
        "figma",
        "ux",
        "ui",
        "roadmap",
        "okr",
        "user research",
        "customer success",
        "onboarding",
        "support client",
    ),
    "Finance, RH & Juridique": (
        "comptabilite",
        "audit",
        "controle de gestion",
        "ifrs",
        "sap",
        "sage",
        "excel",
        "paie",
        "recrutement",
        "sourcing",
        "rgpd",
        "droit social",
        "droit des contrats",
        "facturation",
    ),
    "Santé, Médical & Social": (
        "soins infirmiers",
        "geriatrie",
        "urgences",
        "reanimation",
        "pediatrie",
        "bloc operatoire",
        "had",
        "ssr",
        "telemedecine",
        "pharmacovigilance",
        "recherche clinique",
        "éducation specialisee",
        "accompagnement social",
    ),
}

# Mapping inverse : terme normalisé -> (label canonique, catégorie)
_CANONICAL_SKILL_MAP: dict[str, tuple[str, str]] = {}
for _cat, _skills in SKILL_TAXONOMY.items():
    for _sk in _skills:
        _ns = norm(_sk)
        if _ns:
            _CANONICAL_SKILL_MAP[_ns] = (_sk, _cat)

for _alias, _target in SKILL_ALIASES.items():
    _na = norm(_alias)
    _nt = norm(_target)
    if _na and _nt in _CANONICAL_SKILL_MAP:
        _CANONICAL_SKILL_MAP[_na] = _CANONICAL_SKILL_MAP[_nt]

_TAXONOMY_TERMSET = TermSet(list(_CANONICAL_SKILL_MAP.keys()))


def canonicalize_skill(raw: str) -> tuple[str, str]:
    """Retourne ``(canonical_name, category)`` pour un nom de compétence."""
    cleaned = norm(raw)
    if not cleaned:
        return ("", "Autre")
    if cleaned in _CANONICAL_SKILL_MAP:
        return _CANONICAL_SKILL_MAP[cleaned]
    aliased = SKILL_ALIASES.get(cleaned)
    if aliased and norm(aliased) in _CANONICAL_SKILL_MAP:
        return _CANONICAL_SKILL_MAP[norm(aliased)]
    return (cleaned, "Compétence métier")


@dataclass
class SkillMarketItem:
    skill: str
    category: str
    job_count: int
    pct_of_jobs: float
    avg_fit_score: float
    in_profile: bool
    status: str  # "covered" | "missing_high_demand" | "emerging"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateTriggerStat:
    gate_id: str
    action: str  # "reject" | "penalize" | "title_prefilter"
    rejected_count: int
    penalized_count: int
    pct_of_corpus: float
    sample_titles: list[str] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketRadarReport:
    total_jobs_analyzed: int
    deduped_jobs_count: int
    viable_jobs_count: int
    profile_coverage_pct: float
    top_skills: list[SkillMarketItem] = field(default_factory=list)
    missing_skills: list[SkillMarketItem] = field(default_factory=list)
    covered_skills: list[SkillMarketItem] = field(default_factory=list)
    gate_stats: list[GateTriggerStat] = field(default_factory=list)
    dead_boost_keywords: list[str] = field(default_factory=list)
    active_boost_hits: dict[str, int] = field(default_factory=dict)
    profile_recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_jobs_analyzed": self.total_jobs_analyzed,
            "deduped_jobs_count": self.deduped_jobs_count,
            "viable_jobs_count": self.viable_jobs_count,
            "profile_coverage_pct": self.profile_coverage_pct,
            "top_skills": [s.to_dict() for s in self.top_skills],
            "missing_skills": [s.to_dict() for s in self.missing_skills],
            "covered_skills": [s.to_dict() for s in self.covered_skills],
            "gate_stats": [g.to_dict() for g in self.gate_stats],
            "dead_boost_keywords": list(self.dead_boost_keywords),
            "active_boost_hits": dict(self.active_boost_hits),
            "profile_recommendations": list(self.profile_recommendations),
        }


def _collect_owned_skills(profile: Profile, candidate_json: dict[str, Any] | None = None) -> set[str]:
    """Construit l'ensemble normalisé des compétences détenues par le candidat."""
    raw_terms: list[str] = []
    raw_terms.extend(profile.search.keywords.boost.keys())
    raw_terms.extend(profile.search.keywords.must_any)
    raw_terms.extend(profile.search.titles.primary)
    raw_terms.extend(profile.search.titles.related)
    if candidate_json and isinstance(candidate_json, dict):
        for key in ("core_stack", "positive_keywords"):
            val = candidate_json.get(key)
            if isinstance(val, list):
                raw_terms.extend(str(x) for x in val if x)

    owned: set[str] = set()
    for term in raw_terms:
        n = norm(str(term))
        if not n:
            continue
        owned.add(n)
        canon, _ = canonicalize_skill(n)
        if canon:
            owned.add(norm(canon))
    # Également scanner le résumé et les faits du candidat avec le TermSet
    bio_blob = norm(f"{profile.candidate.summary} {' '.join(profile.candidate.facts)}")
    if bio_blob:
        for matched in _TAXONOMY_TERMSET.find(bio_blob):
            canon, _ = canonicalize_skill(matched)
            owned.add(norm(canon))
    return owned


def _row_to_job(row: sqlite3.Row | dict[str, Any]) -> Job:
    r = dict(row)
    jt = str(r.get("job_type") or "").lower().strip()
    emp = ""
    if jt in ("part_time", "part-time", "parttime"):
        emp = "part_time"
    elif jt in ("freelance", "contract", "contractor"):
        emp = "contract"
    elif jt in ("full_time", "full-time", "fulltime"):
        emp = "full_time"
    elif jt in ("internship", "intern", "stage"):
        emp = "internship"
    url_str = str(r.get("url") or "")
    return Job(
        source=str(r.get("source") or "db"),
        source_id=str(r.get("id") or url_str or "0"),
        company=str(r.get("company") or ""),
        title=str(r.get("title") or ""),
        location=str(r.get("location") or "Remote"),
        url=url_str,
        description=strip_html(str(r.get("description") or "")),
        employment_type=emp,
    )


def _split_tech_match(raw_tech_match: str) -> list[str]:
    if not raw_tech_match:
        return []
    parts = re.split(r"[,;/|•]+", str(raw_tech_match))
    out: list[str] = []
    for p in parts:
        cleaned = p.strip()
        if 2 <= len(cleaned) <= 45:
            out.append(cleaned)
    return out


def analyze_market_radar(
    db_file: str | Path,
    profile: Profile | str | Path,
    candidate_json_path: str | Path | None = None,
    *,
    min_score: float = 0.0,
    limit: int = 500,
) -> MarketRadarReport:
    """Analyse le marché sur les offres de ``db_file`` face au ``profile``."""
    prof_obj = load_profile(profile) if not isinstance(profile, Profile) else profile
    cand_data: dict[str, Any] = {}
    if candidate_json_path and Path(candidate_json_path).exists():
        try:
            cand_data = json.loads(Path(candidate_json_path).read_text(encoding="utf-8"))
        except Exception:
            cand_data = {}

    owned_skills = _collect_owned_skills(prof_obj, cand_data)
    owned_termset = TermSet(list(owned_skills))
    scorer = ProfileScorer(prof_obj)

    db_path = Path(db_file)
    if not db_path.exists():
        return MarketRadarReport(
            total_jobs_analyzed=0,
            deduped_jobs_count=0,
            viable_jobs_count=0,
            profile_coverage_pct=0.0,
        )

    con = sqlite3.connect(str(db_path), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT id, source, company, title, location, url, salary,
                   description, fit_score, tech_match, job_type, status,
                   analysis_status, rationale, gate, dedupe_key
            FROM jobs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 2000)),),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()

    total_rows = len(rows)
    if total_rows == 0:
        return MarketRadarReport(
            total_jobs_analyzed=0,
            deduped_jobs_count=0,
            viable_jobs_count=0,
            profile_coverage_pct=0.0,
        )

    # 1. Déduplication par dedupe_key (évite qu'une offre multi-sources pèse 3x)
    deduped_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for r in rows:
        d = dict(r)
        job_obj = _row_to_job(d)
        dkey = str(d.get("dedupe_key") or "").strip() or job_obj.dedupe_key
        if dkey in seen_keys:
            continue
        seen_keys.add(dkey)
        d["_job_obj"] = job_obj
        deduped_rows.append(d)

    deduped_count = len(deduped_rows)

    # 2. Audit des Gates & sélection du corpus pertinent (viable / in-domain)
    gate_rejects: dict[str, int] = {}
    gate_penalties: dict[str, int] = {}
    gate_samples: dict[str, list[str]] = {}
    gate_actions: dict[str, str] = {}

    for gid, g_action, _g_penalty, _g_pred in scorer.gates:
        gate_rejects[gid] = 0
        gate_penalties[gid] = 0
        gate_samples[gid] = []
        gate_actions[gid] = g_action

    gate_rejects["title_prefilter"] = 0
    gate_penalties["title_prefilter"] = 0
    gate_samples["title_prefilter"] = []
    gate_actions["title_prefilter"] = "reject"

    # Compteur d'apparitions des mots-clés de boost du profil
    boost_hits: dict[str, int] = {norm(k): 0 for k in prof_obj.search.keywords.boost.keys() if norm(k)}
    boost_raw_labels: dict[str, str] = {norm(k): k for k in prof_obj.search.keywords.boost.keys() if norm(k)}

    viable_rows: list[dict[str, Any]] = []

    for d in deduped_rows:
        job_obj: Job = d["_job_obj"]
        blob_norm = norm(f"{job_obj.title} {job_obj.description} {d.get('tech_match') or ''}")
        d["_blob_norm"] = blob_norm

        # Compter les hits des mots-clés de boost sur l'ensemble du corpus dédupliqué
        if scorer.boost_set:
            for hit in scorer.boost_set.find(blob_norm):
                nh = norm(hit)
                if nh in boost_hits:
                    boost_hits[nh] += 1

        # Vérifier le pré-filtre de titre
        if not scorer.title_prefilter(job_obj):
            gate_rejects["title_prefilter"] += 1
            if len(gate_samples["title_prefilter"]) < 3 and job_obj.title:
                gate_samples["title_prefilter"].append(f"{job_obj.company} — {job_obj.title}")
            continue

        # Évaluer chaque gate individuellement (sans masquage early-return)
        job_facts = scorer.facts(job_obj)
        rejected_by_gate = False
        for gid, g_action, _g_penalty, g_pred in scorer.gates:
            if g_pred(job_facts):
                if g_action == "reject":
                    gate_rejects[gid] = gate_rejects.get(gid, 0) + 1
                    rejected_by_gate = True
                else:
                    gate_penalties[gid] = gate_penalties.get(gid, 0) + 1
                if len(gate_samples.setdefault(gid, [])) < 3 and job_obj.title:
                    gate_samples[gid].append(f"{job_obj.company} — {job_obj.title}")

        # Également prendre en compte une gate enregistrée en base (ex: issue du LLM)
        db_gate = str(d.get("gate") or "").strip()
        if db_gate and db_gate not in ("null", "None", ""):
            if db_gate not in gate_rejects:
                gate_rejects[db_gate] = 0
                gate_penalties[db_gate] = 0
                gate_samples[db_gate] = []
                gate_actions[db_gate] = "reject"
            if not rejected_by_gate:
                gate_rejects[db_gate] += 1
                if len(gate_samples[db_gate]) < 3 and job_obj.title:
                    gate_samples[db_gate].append(f"{job_obj.company} — {job_obj.title}")
                rejected_by_gate = True

        fit_val = float(d.get("fit_score") or 0.0)
        if not rejected_by_gate and fit_val >= min_score:
            viable_rows.append(d)

    # Si toutes les offres ont été filtrées (ex: base très petite), replier sur deduped_rows
    skill_corpus = viable_rows if viable_rows else deduped_rows
    corpus_size = len(skill_corpus)

    # 3. Extraction et agrégation des compétences sur le corpus pertinent
    skill_jobs: dict[str, set[str]] = {}
    skill_scores: dict[str, list[float]] = {}
    skill_meta: dict[str, tuple[str, str]] = {}  # norm_key -> (display_name, category)

    for idx, d in enumerate(skill_corpus):
        row_key = str(d.get("id") or idx)
        blob_norm = d["_blob_norm"]
        fit_val = float(d.get("fit_score") or 0.0)

        matched_in_job: set[str] = set()

        # a) Extraction via l'automate TermSet du catalogue multi-secteurs
        for m in _TAXONOMY_TERMSET.find(blob_norm):
            canon_name, cat = canonicalize_skill(m)
            nk = norm(canon_name)
            if nk:
                matched_in_job.add(nk)
                skill_meta.setdefault(nk, (canon_name, cat))

        # b) Extraction depuis la colonne tech_match (compétences remontées par l'IA ou l'heuristique)
        for raw_tm in _split_tech_match(str(d.get("tech_match") or "")):
            canon_name, cat = canonicalize_skill(raw_tm)
            nk = norm(canon_name)
            if nk and len(nk) >= 2:
                matched_in_job.add(nk)
                skill_meta.setdefault(nk, (canon_name, cat))

        for nk in matched_in_job:
            skill_jobs.setdefault(nk, set()).add(row_key)
            if fit_val > 0:
                skill_scores.setdefault(nk, []).append(fit_val)

    # 4. Construction des SkillMarketItem avec seuils statistiques robustes
    min_high_demand_count = max(2, min(4, max(1, int(round(0.10 * corpus_size)))))
    all_items: list[SkillMarketItem] = []

    for nk, job_ids in skill_jobs.items():
        count = len(job_ids)
        pct = round((count / max(1, corpus_size)) * 100.0, 1)
        scores = skill_scores.get(nk) or []
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
        display_name, cat = skill_meta.get(nk, (nk, "Compétence métier"))

        # Vérifier si la compétence est dans le profil du candidat
        in_prof = (nk in owned_skills) or bool(owned_termset.find(nk))
        if in_prof:
            status = "covered"
        elif pct >= 15.0 and count >= min_high_demand_count:
            status = "missing_high_demand"
        else:
            status = "emerging"

        all_items.append(
            SkillMarketItem(
                skill=display_name,
                category=cat,
                job_count=count,
                pct_of_jobs=pct,
                avg_fit_score=avg_score,
                in_profile=in_prof,
                status=status,
            )
        )

    # Tri par nombre d'offres décroissant, puis score moyen décroissant, puis ordre alphabétique
    all_items.sort(key=lambda x: (-x.job_count, -x.avg_fit_score, x.skill))

    top_skills = all_items[:24]
    missing_skills = [it for it in all_items if not it.in_profile][:15]
    covered_skills = [it for it in all_items if it.in_profile][:15]

    # Couverture du profil sur le Top 10 des compétences demandées par le marché
    top10 = all_items[:10]
    if top10:
        total_weight = sum(it.job_count for it in top10)
        covered_weight = sum(it.job_count for it in top10 if it.in_profile)
        coverage_pct = round((covered_weight / max(1, total_weight)) * 100.0, 1)
    else:
        coverage_pct = 100.0 if owned_skills else 0.0

    # 5. Mots-clés de boost morts (0 hit sur l'ensemble des offres dédupliquées)
    dead_boosts = [
        boost_raw_labels[nk]
        for nk, cnt in boost_hits.items()
        if cnt == 0
    ]
    active_boosts = {
        boost_raw_labels[nk]: cnt
        for nk, cnt in sorted(boost_hits.items(), key=lambda kv: (-kv[1], kv[0]))
        if cnt > 0
    }

    # 6. Construction des GateTriggerStat
    gate_stats: list[GateTriggerStat] = []
    for gid in list(gate_actions.keys()):
        rej = gate_rejects.get(gid, 0)
        pen = gate_penalties.get(gid, 0)
        total_trig = rej + pen
        if total_trig == 0:
            continue
        pct_corp = round((total_trig / max(1, deduped_count)) * 100.0, 1)
        act = gate_actions.get(gid, "reject")
        if pct_corp >= 35.0 and act == "reject":
            rec = (
                f"La règle « {gid} » rejette {pct_corp}% des offres collectées. "
                "Vérifiez si vos exclusions de titre ou de localisation ne sont pas trop strictes."
            )
        elif pct_corp >= 25.0 and act == "penalize":
            rec = (
                f"La règle « {gid} » pénalise {pct_corp}% du flux. "
                "Assurez-vous que la pénalité correspond bien à un critère secondaire."
            )
        else:
            rec = "Filtrage sain et proportionné."

        gate_stats.append(
            GateTriggerStat(
                gate_id=gid,
                action=act,
                rejected_count=rej,
                penalized_count=pen,
                pct_of_corpus=pct_corp,
                sample_titles=gate_samples.get(gid, []),
                recommendation=rec,
            )
        )
    gate_stats.sort(key=lambda g: (-(g.rejected_count + g.penalized_count), g.gate_id))

    # 7. Synthèse de recommandations actionnables
    recommendations: list[str] = []
    high_missing = [it for it in missing_skills if it.status == "missing_high_demand"]
    if high_missing:
        names = ", ".join(f"{it.skill} ({it.pct_of_jobs:.0f}%)" for it in high_missing[:4])
        recommendations.append(
            f"Compétences très demandées absentes de votre profil : {names}. "
            "Si vous les maîtrisez, ajoutez-les en 1 clic pour augmenter votre score d'adéquation."
        )
    if dead_boosts and deduped_count >= 5:
        sample_dead = ", ".join(dead_boosts[:5])
        recommendations.append(
            f"Mots-clés de boost sans aucune occurrence dans les {deduped_count} offres analysées : {sample_dead}. "
            "Envisagez de les remplacer par des termes plus fréquents dans les annonces."
        )
    for gs in gate_stats:
        if gs.action == "reject" and gs.pct_of_corpus >= 35.0:
            recommendations.append(gs.recommendation)
    if not recommendations:
        recommendations.append(
            "Votre profil est bien aligné avec les compétences dominantes des offres collectées."
        )

    return MarketRadarReport(
        total_jobs_analyzed=total_rows,
        deduped_jobs_count=deduped_count,
        viable_jobs_count=len(viable_rows),
        profile_coverage_pct=coverage_pct,
        top_skills=top_skills,
        missing_skills=missing_skills,
        covered_skills=covered_skills,
        gate_stats=gate_stats,
        dead_boost_keywords=dead_boosts,
        active_boost_hits=active_boosts,
        profile_recommendations=recommendations,
    )


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _surgically_insert_boost_in_yaml(raw_yaml: str, skill_key: str, weight: float) -> str | None:
    """Insère ou met à jour ``skill_key: weight`` sous ``search -> keywords -> boost``
    en préservant l'intégralité des commentaires et du formatage de ``profile.yaml``."""
    lines = raw_yaml.splitlines()
    in_search = False
    in_keywords = False
    in_boost = False
    boost_indent = -1
    entry_indent = "      "
    last_boost_entry_idx = -1

    # Safely format YAML key if it contains special chars like #, :, ., +
    if re.fullmatch(r"[a-zA-Z0-9_-]+", skill_key):
        formatted_key = skill_key
    else:
        escaped = skill_key.replace('"', '\\"')
        formatted_key = f'"{escaped}"'

    weight_str = str(int(weight)) if float(weight).is_integer() else f"{weight:.1f}"

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))

        if indent == 0:
            in_search = stripped.startswith("search:")
            in_keywords = False
            if in_boost:
                break
            continue

        if in_search and indent == 2:
            if stripped.startswith("keywords:"):
                in_keywords = True
                continue
            elif in_boost:
                break
            else:
                in_keywords = False

        if in_search and in_keywords and indent == 4:
            if stripped.startswith("boost:"):
                in_boost = True
                boost_indent = indent
                last_boost_entry_idx = idx
                continue
            elif in_boost:
                break

        if in_boost and indent > boost_indent:
            entry_indent = " " * indent
            # Check if this line already defines the skill_key
            key_part, sep, rest = stripped.partition(":")
            if sep:
                clean_k = norm(key_part.strip().strip("'\""))
                if clean_k == norm(skill_key):
                    # Parse existing numeric value before any comment
                    val_part, comment_sep, comment_part = rest.partition("#")
                    try:
                        old_w = float(val_part.strip())
                    except ValueError:
                        old_w = 0.0
                    new_w = max(old_w, float(weight))
                    new_w_str = str(int(new_w)) if new_w.is_integer() else f"{new_w:.1f}"
                    suffix = f"  #{comment_part}" if comment_sep else ""
                    lines[idx] = f"{entry_indent}{formatted_key}: {new_w_str}{suffix}"
                    return "\n".join(lines) + ("\n" if raw_yaml.endswith("\n") else "")
            last_boost_entry_idx = idx

    if last_boost_entry_idx >= 0:
        new_line = f"{entry_indent}{formatted_key}: {weight_str}"
        lines.insert(last_boost_entry_idx + 1, new_line)
        return "\n".join(lines) + ("\n" if raw_yaml.endswith("\n") else "")
    return None


def add_skill_to_profile(
    skill: str,
    profile_yaml_path: str | Path,
    candidate_json_path: str | Path | None = None,
    *,
    weight: float = 6.0,
) -> dict[str, Any]:
    """Ajoute de manière atomique, idempotente et validée une compétence dans
    ``profile.yaml`` (``search.keywords.boost``) et ``candidate_profile.json``."""
    raw_skill = (skill or "").strip()
    if not raw_skill or len(raw_skill) > 60 or any(ord(c) < 32 for c in raw_skill):
        raise ValueError("Nom de compétence invalide (doit contenir entre 1 et 60 caractères imprimables).")

    canon_name, category = canonicalize_skill(raw_skill)
    clean_key = norm(canon_name or raw_skill)
    if not clean_key:
        raise ValueError("Nom de compétence vide après normalisation.")

    clamped_weight = max(1.0, min(float(weight), 20.0))
    yaml_path = Path(profile_yaml_path)

    with _PROFILE_MUTATION_LOCK:
        updated_yaml = False
        final_weight = clamped_weight

        if yaml_path.exists():
            raw_yaml = yaml_path.read_text(encoding="utf-8")
            # Nettoyage préventif si cv_profiler avait injecté name/headline dans candidate
            parsed = yaml.safe_load(raw_yaml) or {}
            if isinstance(parsed, dict) and isinstance(parsed.get("candidate"), dict):
                parsed["candidate"].pop("name", None)
                parsed["candidate"].pop("headline", None)

            surgical = _surgically_insert_boost_in_yaml(raw_yaml, clean_key, clamped_weight)
            if surgical is not None:
                try:
                    validated_prof = load_profile(yaml.safe_load(surgical))
                    final_weight = float(validated_prof.search.keywords.boost.get(clean_key, clamped_weight))
                    _atomic_write_text(yaml_path, surgical)
                    updated_yaml = True
                except (ProfileError, yaml.YAMLError):
                    surgical = None

            if not updated_yaml and isinstance(parsed, dict):
                search_sec = parsed.setdefault("search", {})
                kw_sec = search_sec.setdefault("keywords", {})
                boost_sec = kw_sec.setdefault("boost", {})
                existing_w = 0.0
                for k, v in list(boost_sec.items()):
                    if norm(str(k)) == clean_key:
                        try:
                            existing_w = float(v)
                        except (TypeError, ValueError):
                            existing_w = 0.0
                        boost_sec.pop(k, None)
                final_weight = max(existing_w, clamped_weight)
                boost_sec[clean_key] = int(final_weight) if final_weight.is_integer() else round(final_weight, 1)
                # Valider avec load_profile avant d'écrire sur disque
                load_profile(parsed)
                dumped = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=True)
                _atomic_write_text(yaml_path, dumped)
                updated_yaml = True

        updated_json = False
        if candidate_json_path:
            json_path = Path(candidate_json_path)
            cand_obj: dict[str, Any] = {}
            if json_path.exists():
                try:
                    loaded = json.loads(json_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        cand_obj = loaded
                except Exception:
                    cand_obj = {}

            display_label = canon_name if canon_name else raw_skill
            for field_name in ("core_stack", "positive_keywords"):
                lst = cand_obj.get(field_name)
                if not isinstance(lst, list):
                    lst = []
                existing_norms = {norm(str(x)) for x in lst if x}
                if clean_key not in existing_norms:
                    lst.append(display_label)
                cand_obj[field_name] = lst

            _atomic_write_text(
                json_path,
                json.dumps(cand_obj, indent=2, ensure_ascii=False) + "\n",
            )
            updated_json = True

    return {
        "status": "ok",
        "skill": canon_name or raw_skill,
        "normalized_key": clean_key,
        "category": category,
        "weight": final_weight,
        "updated_profile_yaml": updated_yaml,
        "updated_candidate_json": updated_json,
    }
