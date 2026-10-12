"""Profil de recherche universel (profile.yaml).

Remplace TOUS les dictionnaires de mots-clés codés en dur (rôles cibles, exclusions, stack,
régions, langues…). Le fichier est validé strictement : une clé inconnue (« seniorty ») est une
erreur avec suggestion, pas un champ silencieusement ignoré.
"""
from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Union

import yaml


class ProfileError(ValueError):
    pass


# ---------------------------------------------------------------- schéma -----------------

@dataclass
class Identity:
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    links: dict[str, str] = field(default_factory=dict)
    resume_path: str = ""


@dataclass
class Titles:
    primary: list[str] = field(default_factory=list)      # rôles cherchés (poids fort)
    related: list[str] = field(default_factory=list)      # rôles acceptables (poids moyen)
    exclude: list[str] = field(default_factory=list)      # titres à écarter


@dataclass
class Keywords:
    must_any: list[str] = field(default_factory=list)     # au moins un doit apparaître
    boost: dict[str, float] = field(default_factory=dict)      # terme -> points (+)
    penalize: dict[str, float] = field(default_factory=dict)   # terme -> points (-)


@dataclass
class Seniority:
    level: Literal["intern", "junior", "mid", "senior", "lead", "any"] = "any"
    years_experience: float | None = None
    max_years_required: float | None = None               # au-delà : pénalité forte
    avoid_title_terms: list[str] = field(default_factory=list)   # ex. senior, staff, head of


@dataclass
class Contract:
    types: list[str] = field(default_factory=list)        # full_time, part_time, contract, freelance, internship


@dataclass
class WorkMode:
    remote: Literal["required", "preferred", "any"] = "any"
    cities: list[str] = field(default_factory=list)       # utile si remote != required
    radius_km: int | None = None
    hybrid_ok: bool = True


@dataclass
class Location:
    eligible_regions: list[str] = field(default_factory=list)   # ex. ["FR","EU","WORLDWIDE"]
    excluded_regions: list[str] = field(default_factory=list)   # ex. ["US","CA","IN"]
    countries: list[str] = field(default_factory=list)          # pays pour les API par pays (adzuna) : ["fr"]


@dataclass
class Languages:
    spoken: dict[str, str] = field(default_factory=dict)        # {"fr": "native", "en": "C1"}
    reject_if_required: list[str] = field(default_factory=list)  # codes ISO : ["de"]


@dataclass
class Compensation:
    currency: str = "EUR"
    min_annual: float | None = None
    unknown_ok: bool = True


@dataclass
class Companies:
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)


@dataclass
class Search:
    titles: Titles = field(default_factory=Titles)
    keywords: Keywords = field(default_factory=Keywords)
    seniority: Seniority = field(default_factory=Seniority)
    contract: Contract = field(default_factory=Contract)
    work_mode: WorkMode = field(default_factory=WorkMode)
    location: Location = field(default_factory=Location)
    languages: Languages = field(default_factory=Languages)
    compensation: Compensation = field(default_factory=Compensation)
    companies: Companies = field(default_factory=Companies)


# Vocabulaire FERMÉ des conditions de gate (jamais d'eval / de code libre dans un YAML).
GATE_CONDITIONS = {
    "title_matches": list, "description_matches": list, "years_required_gte": (int, float),
    "location_ineligible": bool, "company_blacklisted": bool, "language_required_in": list,
    "salary_below": (int, float), "remote": bool,
}


@dataclass
class Gate:
    id: str = ""
    when: dict[str, Any] = field(default_factory=dict)    # toutes les conditions doivent être vraies
    action: Literal["reject", "penalize"] = "penalize"
    penalty: float = 30.0


@dataclass
class Scoring:
    hot_threshold: float = 7.5          # score LLM (0-10) à partir duquel une offre est « HOT »
    heuristic_min: float = 15.0         # score heuristique (0-100) minimal pour passer au LLM
    gates: list[Gate] = field(default_factory=list)       # gates personnalisées (en plus des implicites)


@dataclass
class Sources:
    enabled: list[str] = field(default_factory=list)
    boards: dict[str, list[str]] = field(default_factory=dict)   # {"greenhouse": ["stripe", ...]}
    options: dict[str, dict[str, Any]] = field(default_factory=dict)  # options par source


@dataclass
class LLMSettings:
    provider: str = "manual"            # gemini | openai | ollama | anthropic | manual | ...
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""               # NOM de la variable d'environnement, jamais la clé elle-même
    temperature: float = 0.2
    max_jobs_per_run: int = 15
    language: str = "fr"                # langue des explications et des textes générés


@dataclass
class Candidate:
    summary: str = ""                                     # 3-5 lignes injectées dans les prompts
    facts: list[str] = field(default_factory=list)        # preuves VÉRIFIABLES : le LLM n'a le droit de citer que celles-là


@dataclass
class Profile:
    version: int = 1
    identity: Identity = field(default_factory=Identity)
    search: Search = field(default_factory=Search)
    scoring: Scoring = field(default_factory=Scoring)
    sources: Sources = field(default_factory=Sources)
    llm: LLMSettings = field(default_factory=LLMSettings)
    candidate: Candidate = field(default_factory=Candidate)

    def fingerprint(self) -> str:
        """Empreinte des sections qui influencent les scores : clé de cache des analyses LLM
        et déclencheur de « ré-évaluer » quand le profil change."""
        payload = {
            "search": dataclasses.asdict(self.search),
            "scoring": dataclasses.asdict(self.scoring),
            "candidate": dataclasses.asdict(self.candidate),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- chargeur ---------------

def _coerce(tp: Any, value: Any, path: str) -> Any:
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if tp is Any:
        return value
    if origin in (Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if value is None:
            if len(non_none) == len(args):
                raise ProfileError(f"{path} : une valeur est requise")
            return None
        return _coerce(non_none[0], value, path)
    if origin is Literal:
        if value not in args:
            raise ProfileError(f"{path} : {value!r} invalide, attendu l'un de {', '.join(map(repr, args))}")
        return value
    if origin is list:
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            value = [value]                      # « fr » au lieu de [« fr »] : toléré
        if value is None:
            return []
        if not isinstance(value, list):
            raise ProfileError(f"{path} : une liste est attendue")
        return [_coerce(args[0], v, f"{path}[{i}]") for i, v in enumerate(value)]
    if origin is dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ProfileError(f"{path} : un dictionnaire est attendu")
        return {(str(k) if args[0] is str else k): _coerce(args[1], v, f"{path}.{k}") for k, v in value.items()}
    if dataclasses.is_dataclass(tp):
        return _build(tp, value, path)
    if tp is bool:
        if not isinstance(value, bool):
            raise ProfileError(f"{path} : true ou false attendu")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ProfileError(f"{path} : un entier est attendu")
        return int(value)
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProfileError(f"{path} : un nombre est attendu")
        return float(value)
    if tp is str:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ProfileError(f"{path} : du texte est attendu")
        return str(value)
    return value


def _build(cls: type, data: Any, path: str) -> Any:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ProfileError(f"{path} : un objet (clé: valeur) est attendu")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = [k for k in data if k not in known]
    if unknown:
        msgs = []
        for k in unknown:
            close = difflib.get_close_matches(str(k), known, n=1)
            msgs.append(f"« {k} »" + (f" (vouliez-vous dire « {close[0]} » ?)" if close else ""))
        raise ProfileError(f"{path or 'profil'} : clé(s) inconnue(s) {', '.join(msgs)}. Clés valides : {', '.join(sorted(known))}")
    hints = typing.get_type_hints(cls)
    kwargs = {name: _coerce(hints[name], value, f"{path}.{name}" if path else name) for name, value in data.items()}
    return cls(**kwargs)


def validate_profile(p: Profile) -> list[str]:
    """Erreurs bloquantes -> ProfileError ; retourne les AVERTISSEMENTS (non bloquants)."""
    warnings: list[str] = []
    if not 0 <= p.scoring.hot_threshold <= 10:
        raise ProfileError("scoring.hot_threshold doit être entre 0 et 10")
    seen = set()
    for g in p.scoring.gates:
        if not g.id:
            raise ProfileError("scoring.gates : chaque gate doit avoir un « id »")
        if g.id in seen:
            raise ProfileError(f"scoring.gates : id « {g.id} » en double")
        seen.add(g.id)
        if not g.when:
            raise ProfileError(f"gate « {g.id} » : « when » est vide")
        for key, value in g.when.items():
            if key not in GATE_CONDITIONS:
                close = difflib.get_close_matches(key, GATE_CONDITIONS, n=1)
                raise ProfileError(
                    f"gate « {g.id} » : condition inconnue « {key} »"
                    + (f" (vouliez-vous dire « {close[0]} » ?)" if close else "")
                    + f". Conditions : {', '.join(GATE_CONDITIONS)}"
                )
            if not isinstance(value, GATE_CONDITIONS[key]) or (isinstance(value, bool) and GATE_CONDITIONS[key] is not bool):
                raise ProfileError(f"gate « {g.id} » : « {key} » attend {GATE_CONDITIONS[key]}")
    s = p.search
    if not (s.titles.primary or s.titles.related or s.keywords.must_any):
        warnings.append("aucun rôle cible ni mot-clé obligatoire : le pré-tri n'aura rien pour classer les offres")
    if s.work_mode.remote == "required" and not (s.location.eligible_regions or s.location.excluded_regions):
        warnings.append("remote obligatoire mais aucune région éligible : impossible d'écarter les offres « Remote - US »")
    if p.llm.provider not in ("manual", "ollama", "lmstudio") and not p.llm.api_key_env:
        warnings.append(f"llm.provider = {p.llm.provider} sans api_key_env : aucune clé ne sera lue")
    if not p.sources.enabled:
        warnings.append("sources.enabled est vide : rien ne sera collecté")
    return warnings


def load_profile(source: "str | Path | dict") -> Profile:
    """Charge un profil depuis un chemin YAML ou un dict déjà parsé."""
    if isinstance(source, dict):
        data = source
    else:
        try:
            data = yaml.safe_load(Path(source).read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ProfileError(f"YAML invalide : {e}") from e
    profile = _build(Profile, data, "")
    validate_profile(profile)
    return profile
