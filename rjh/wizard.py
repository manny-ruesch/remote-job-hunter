"""`rjh init` : 4 questions simples -> un profile.yaml VALIDE (il est relu par le chargeur strict avant d'être écrit).

Principes pour un public non technique :
  * une réponse par défaut partout (Entrée) ; des menus numérotés ; jamais de YAML à écrire soi-même
  * la clé d'API est saisie MASQUÉE, rangée dans un .env en droits 600, et n'est jamais écrite dans le profil
  * le mode « copier-coller » est proposé en premier : on peut démarrer sans aucune clé
  * tout est injectable (entrées, sorties, listage des modèles) -> testable sans terminal ni réseau
"""
from __future__ import annotations

import getpass
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .messages import t
from .paths import env_path, profile_path, write_env_key
from .profile import load_profile

GEMINI_VAR = "GOOGLE_API_KEY"
PROVIDERS: dict[str, tuple[str, bool]] = {     # fournisseur -> (variable de la clé, clé requise)
    "openai": ("OPENAI_API_KEY", True), "anthropic": ("ANTHROPIC_API_KEY", True), "openrouter": ("OPENROUTER_API_KEY", True),
    "groq": ("GROQ_API_KEY", True), "mistral": ("MISTRAL_API_KEY", True), "ollama": ("", False),
}
# Termes de titres à éviter selon le niveau. « lead » seul est volontairement absent : il écarterait « Lead generation ».
_SENIOR_TERMS = ["senior", "staff", "principal", "head of", "director", "vp", "team lead", "tech lead"]
LEVELS: dict[str, dict[str, Any]] = {
    "junior": {"years": 1, "max_years": 3, "avoid": _SENIOR_TERMS, "exclude": []},
    "mid": {"years": 5, "max_years": 7, "avoid": ["head of", "director", "vp", "principal", "staff"], "exclude": ["intern", "stagiaire"]},
    "senior": {"years": 10, "max_years": None, "avoid": [], "exclude": ["intern", "stagiaire"]},
}
MODES = {"1": "remote", "2": "preferred", "3": "onsite"}
LEVEL_KEYS = {"1": "junior", "2": "mid", "3": "senior"}
ADZUNA_COUNTRY = {"UK": "gb"}                  # Adzuna dit « gb » ; nos régions disent « UK »


@dataclass
class IO:
    inp: Callable[[str], str] = input
    out: Callable[[str], None] = print
    secret: Callable[[str], str] = getpass.getpass


@dataclass
class Answers:
    titles: list[str]
    mode: str                       # remote | preferred | onsite
    country: str                    # ISO2 majuscule
    city: str = ""
    level: str = "mid"
    ai: str = "manual"              # manual | gemini | <fournisseur>
    model: str = ""
    key_var: str = ""
    lang: str = "fr"


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------ pièces pures -----------

def parse_titles(text: str) -> list[str]:
    """« infirmier, aide-soignant ; IDE » -> liste propre, sans doublon, 6 maximum."""
    seen, out = set(), []
    for part in re.split(r"[,;\n]| / ", text or ""):
        title = " ".join(part.strip().strip("\"'.").split())
        if title and title.casefold() not in seen:
            seen.add(title.casefold())
            out.append(title)
    return out[:6]


def normalize_country(raw: str) -> str | None:
    c = (raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", c):
        return None
    return "UK" if c == "GB" else c


def pick_gemini_model(models: list[str]) -> str | None:
    """Le modèle « flash » stable le plus récent parmi ceux que la clé peut utiliser (jamais un nom écrit en dur :
    les noms de modèles meurent vite)."""
    names = [m.removeprefix("models/") for m in models]
    versioned = []
    for n in names:
        m = re.fullmatch(r"gemini-(\d+(?:\.\d+)*)-flash", n)
        if m:
            versioned.append((tuple(int(x) for x in m.group(1).split(".")), n))
    if versioned:
        return max(versioned)[1]
    fallback = [n for n in names if "gemini" in n and "flash" in n and not any(x in n for x in ("lite", "image", "tts", "live", "audio", "preview", "exp"))]
    return sorted(fallback)[-1] if fallback else None


def build_profile_dict(a: Answers, *, adzuna_available: bool = False) -> dict[str, Any]:
    lv = LEVELS[a.level]
    remote_mode = {"remote": "required", "preferred": "preferred", "onsite": "any"}[a.mode]
    adz_country = ADZUNA_COUNTRY.get(a.country, a.country.lower())
    enabled = {"remote": ["remotive"], "preferred": ["remotive"], "onsite": []}[a.mode]
    if a.mode == "onsite" or adzuna_available:
        enabled = enabled + ["adzuna"]
    seniority: dict[str, Any] = {"level": a.level, "years_experience": lv["years"], "avoid_title_terms": lv["avoid"]}
    if lv["max_years"] is not None:
        seniority["max_years_required"] = lv["max_years"]
    adzuna_opts: dict[str, Any] = {"countries": [adz_country], "max_results": 150, "max_age_days": 14}
    if a.city:
        adzuna_opts["where"] = a.city
    llm: dict[str, Any] = {"provider": a.ai, "model": a.model, "api_key_env": a.key_var, "language": a.lang, "max_jobs_per_run": 15}
    return {
        "version": 1,
        "identity": {"city": a.city} if a.city else {},
        "search": {
            "titles": {"primary": a.titles, "exclude": lv["exclude"]},
            "seniority": seniority,
            "work_mode": {"remote": remote_mode, "cities": [a.city] if a.city and a.mode != "remote" else [], "hybrid_ok": a.mode != "remote"},
            "location": {"eligible_regions": [a.country], "countries": [adz_country]},
            "languages": {"spoken": {a.lang: "native"}},
        },
        "scoring": {"hot_threshold": 7.5, "heuristic_min": 15},
        "sources": {"enabled": enabled, "options": {"remotive": {"max_titles": 3}, "adzuna": adzuna_opts}},
        "llm": llm,
        "candidate": {"summary": ""},
    }


_SECTION_COMMENTS = {
    "fr": {"identity": "Vos coordonnées (jamais envoyées à l'IA).", "search": "Ce que vous cherchez. Les pays comme FR incluent l'Union européenne.",
           "scoring": "Seuils : une offre notée 7,5/10 ou plus est « HOT ».", "sources": "D'où viennent les offres (rjh doctor teste chaque source).",
           "llm": "L'IA : seul le NOM de la variable d'environnement de la clé est écrit ici, jamais la clé.",
           "candidate": "Ajoutez 2-3 phrases sur vous : les textes générés n'en seront que meilleurs."},
    "en": {"identity": "Your contact details (never sent to the AI).", "search": "What you are looking for. Countries like FR include the European Union.",
           "scoring": "Thresholds: a posting scored 7.5/10 or more is 'HOT'.", "sources": "Where postings come from (rjh doctor tests each source).",
           "llm": "The AI: only the NAME of the key's environment variable is written here, never the key.",
           "candidate": "Add 2-3 sentences about yourself: generated texts will be better."},
}


def render_yaml(d: Mapping[str, Any], lang: str = "fr") -> str:
    """YAML commenté. Chaque section est sérialisée par PyYAML (guillemets et accents corrects), jamais assemblée à la main."""
    comments = _SECTION_COMMENTS.get(lang, _SECTION_COMMENTS["en"])
    head = {"fr": "# Profil de recherche : modifiable à la main à tout moment.\n# Vérifiez avec : rjh doctor\n",
            "en": "# Search profile: edit by hand at any time.\n# Check with: rjh doctor\n"}.get(lang, "")
    parts = [head, "version: 1\n"]
    for key, value in d.items():
        if key == "version":
            continue
        parts.append(f"\n# {comments.get(key, '')}\n" + yaml.safe_dump({key: value}, allow_unicode=True, sort_keys=False, default_flow_style=False))
    return "".join(parts)


# ------------------------------------------------------------------ l'assistant ------------

def _choose(io: IO, lang: str, key: str, valid: tuple[str, ...], default: str) -> str:
    io.out(t(lang, key))
    while True:
        raw = io.inp(t(lang, "choice", default=default)).strip() or default
        if raw in valid:
            return raw
        io.out(t(lang, "choice_bad"))


def _default_list_models(provider: str, key: str) -> list[str]:
    from .http import HttpClient, RateLimiter
    from .llm import GeminiProvider, LLMConfig
    try:
        http = HttpClient(timeout=5, max_retries=0, limiter=RateLimiter(0))
        return GeminiProvider(LLMConfig(provider="gemini", api_key=key), http).list_models()
    except Exception:                                   # réseau, clé invalide… : l'assistant continue, doctor expliquera
        return []


def run_wizard(io: IO, *, lang: str, home: Path, force: bool = False, preset: Mapping[str, Any] | None = None,
               environ: Mapping[str, str] | None = None,
               list_models: Callable[[str, str], list[str]] | None = None) -> Path | None:
    """Retourne le chemin du profil écrit, ou None si l'utilisateur a renoncé. `preset` = réponses déjà connues
    (mode non interactif : title, mode, country, city, level, ai, model, key)."""
    env = dict(environ or {})
    list_models = list_models or _default_list_models
    pre = dict(preset or {})
    interactive = not pre.get("yes")
    target = profile_path(home)

    if target.exists() and not force:
        if not interactive:
            io.out(t(lang, "cancelled"))
            return None
        if io.inp(t(lang, "exists", path=target)).strip().lower() not in ("o", "oui", "y", "yes"):
            io.out(t(lang, "cancelled"))
            return None
    if interactive:
        io.out(t(lang, "intro") + "\n")

    # 1/4 ---------------------------------------------------------------------------- poste
    titles = parse_titles(pre["title"]) if pre.get("title") else []
    while not titles:
        if not interactive:
            raise ValueError(t(lang, "need_args"))
        io.out(t(lang, "q1"))
        titles = parse_titles(io.inp("> "))
        if not titles:
            io.out(t(lang, "q1_empty"))

    # 2/4 ---------------------------------------------------------------------------- lieu
    mode = pre.get("mode") if pre.get("mode") in MODES.values() else None
    if mode is None:
        mode = MODES[_choose(io, lang, "q2", tuple(MODES), "1")] if interactive else "remote"
    country = normalize_country(pre.get("country", "")) if pre.get("country") else None
    while country is None:
        if not interactive:
            raise ValueError(t(lang, "need_args"))
        country = normalize_country(io.inp(t(lang, "q2_country") + " "))
        if country is None:
            io.out(t(lang, "q2_country_bad"))
    city = (pre.get("city") or "").strip()
    if not city and interactive and mode != "remote":
        while True:
            city = io.inp(t(lang, "q2_city" if mode == "onsite" else "q2_city_opt") + " ").strip()
            if city or mode == "preferred":
                break

    # 3/4 ---------------------------------------------------------------------------- niveau
    level = pre.get("level") if pre.get("level") in LEVELS else None
    if level is None:
        level = LEVEL_KEYS[_choose(io, lang, "q3", tuple(LEVEL_KEYS), "2")] if interactive else "mid"

    # 4/4 ---------------------------------------------------------------------------- IA
    ai, model, key_var = pre.get("ai") or None, pre.get("model", ""), ""
    if ai is None:
        ai = {"1": "manual", "2": "gemini", "3": "other"}[_choose(io, lang, "q4", ("1", "2", "3"), "1")] if interactive else "manual"
    if ai == "other":
        while True:
            io.out(t(lang, "q4_provider"))
            ai = io.inp("> ").strip().lower()
            if ai in PROVIDERS:
                break
            io.out(t(lang, "q4_provider_bad"))
        if not model:
            io.out(t(lang, "q4_model"))
            model = io.inp("> ").strip()
    if ai not in ("manual", "gemini") and ai not in PROVIDERS:
        raise ValueError(f"fournisseur inconnu : {ai}")
    if ai != "manual":
        var, needs_key = (GEMINI_VAR, True) if ai == "gemini" else PROVIDERS[ai]
        key_var = var
        if needs_key:
            key, from_env = pre.get("key") or "", False
            if not key and env.get(var):
                key, from_env = env[var], True
                if interactive:
                    io.out(t(lang, "key_found", var=var))
            if not key and interactive:
                key = io.secret(t(lang, "key_prompt")).strip()
            if key and not from_env:                        # saisie ou fournie : rangée dans le .env (600), jamais dans le profil
                write_env_key(env_path(home), var, key)
                io.out(t(lang, "key_saved", path=env_path(home)))
            elif not key:
                io.out(t(lang, "key_later", var=var, path=env_path(home)))
            if ai == "gemini" and key and not model:
                picked = pick_gemini_model(list_models("gemini", key))
                if picked:
                    model = picked
                    io.out(t(lang, "model_picked", model=model))
                else:
                    io.out(t(lang, "model_unknown"))

    answers = Answers(titles=titles, mode=mode, country=country, city=city, level=level, ai=ai, model=model, key_var=key_var, lang=lang)
    adzuna_ok = bool(env.get("ADZUNA_APP_ID") and env.get("ADZUNA_APP_KEY"))
    data = build_profile_dict(answers, adzuna_available=adzuna_ok)
    load_profile(data)                                   # garde-fou : on n'écrit jamais un profil que le chargeur refuserait

    home.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    target.write_text(render_yaml(data, lang), encoding="utf-8")

    io.out(t(lang, "done", path=target))
    where = f" ({city}, {country})" if city else f" ({country})"
    ai_label = t(lang, "ai_manual") if ai == "manual" else f"{ai} {model}".strip()
    io.out(t(lang, "summary", titles=", ".join(titles), mode=t(lang, f"mode_{mode}"), where=where,
             level=t(lang, f"level_{level}"), ai=ai_label))
    if mode == "onsite" and not adzuna_ok:
        io.out(t(lang, "adzuna_hint"))
    io.out(t(lang, "next", path=target))
    return target
