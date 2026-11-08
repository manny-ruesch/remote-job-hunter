"""Pack 3 — Reverse-ATS Auto-Discovery & LinkedIn Warm-Intro / Referral Matcher.

Ce module implémente deux fonctionnalités 100 % hors-ligne / publiques :
1. **Reverse-ATS Auto-Discovery** :
   - Extraction des portails ATS directs (`ashby`, `greenhouse`, `lever`, `smartrecruiters`, `workable`)
     depuis les URLs et descriptions des offres déjà présentes en base (y compris celles issues d'agrégateurs
     comme Remotive, WWR, Jobicy, HN ou Adzuna).
   - Probing actif multi-threadé (`probe_company_ats`) : à partir d'un nom d'entreprise ou d'une liste
     d'entreprises cibles, teste en parallèle les 5 APIs ATS publiques (timeout 3.5s, 0 retry, anti-SSRF)
     pour découvrir le job-board officiel et compter ses offres ouvertes.
   - Synchronisation atomique et chirurgicale vers `profile.yaml` (`sync_discovered_boards_to_profile`)
     qui préserve les commentaires YAML et active automatiquement les sources correspondantes.

2. **LinkedIn Warm-Intro & Referral Matcher** :
   - Import résilient du fichier `Connections.csv` exporté depuis LinkedIn (tolère le préambule
     parasite de 3 lignes `"Notes:"`, le BOM UTF-8, les délimiteurs `,` ou `;`, et les en-têtes FR/EN).
   - Normalisation stricte des noms d'entreprises (élagage des suffixes légaux `Inc`, `SAS`, `GmbH`, etc.
     et filtrage des stopwords comme `Freelance` ou `Self-employed`) sans faux positifs de sous-chaînes
     sur les noms courts (`On`, `At`, `Go`, `AI`).
   - Génération automatique de brouillons de demande de cooptation personnalisés (FR/EN) et de liens
     1-clic LinkedIn People Search en repli lorsqu'aucun contact direct n'est présent dans la base.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml

from .liveness import _safe_http_get
from .profile import Profile, load_profile
from .textutil import norm

_DISCOVERY_LOCK = threading.Lock()

SUPPORTED_ATS_PROVIDERS = ("ashby", "greenhouse", "lever", "smartrecruiters", "workable")

RESERVED_ATS_SLUGS: frozenset[str] = frozenset(
    {
        "embed",
        "api",
        "v0",
        "v1",
        "v2",
        "v3",
        "js",
        "css",
        "assets",
        "static",
        "public",
        "jobs",
        "job",
        "postings",
        "posting",
        "boards",
        "board",
        "job-board",
        "posting-api",
        "accounts",
        "account",
        "companies",
        "company",
        "j",
        "sr-jobs",
        "oneclick-ui",
        "login",
        "signin",
        "signup",
        "privacy",
        "terms",
        "help",
        "support",
        "status",
        "favicon",
        "favicon.ico",
        "robots",
        "robots.txt",
        "sitemap",
        "undefined",
        "null",
        "apply",
        "careers",
        "search",
        "widget",
    }
)

_LEGAL_SUFFIX_TOKENS: frozenset[str] = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "gmbh",
        "ag",
        "sas",
        "sasu",
        "sarl",
        "sa",
        "bv",
        "nv",
        "ab",
        "plc",
        "oy",
        "srl",
        "spa",
        "pty",
        "co",
        "company",
        "technologies",
        "technology",
        "tech",
        "labs",
        "lab",
        "group",
        "holding",
        "holdings",
        "international",
        "global",
        "hq",
        "software",
        "systems",
        "solutions",
        "studio",
        "studios",
    }
)

COMPANY_STOPWORDS: frozenset[str] = frozenset(
    {
        "",
        "freelance",
        "freelancer",
        "self employed",
        "selfemployed",
        "independant",
        "independent",
        "auto entrepreneur",
        "autoentrepreneur",
        "stealth",
        "stealth mode",
        "stealth startup",
        "confidential",
        "n a",
        "na",
        "none",
        "unknown",
        "retired",
        "student",
        "unemployed",
        "open to work",
        "looking for opportunities",
        "consultant",
        "consulting",
        "personal project",
        "side project",
    }
)

_VALID_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")

# Regex d'extraction d'URLs ATS dans les offres existantes (ordre précis : routes spécifiques d'abord)
_GH_EMBED_RE = re.compile(
    r"https?://(?:job-boards|boards)\.greenhouse\.io/embed/job_app\?[^\s\"'<>]*?\bfor=([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_GH_BOARD_RE = re.compile(
    r"https?://(?:job-boards|boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_ASHBY_BOARD_RE = re.compile(
    r"https?://jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)",
    re.IGNORECASE,
)
_LEVER_BOARD_RE = re.compile(
    r"https?://jobs\.lever\.co/([a-zA-Z0-9_.-]+)",
    re.IGNORECASE,
)
_SR_BOARD_RE = re.compile(
    r"https?://jobs\.smartrecruiters\.com/([a-zA-Z0-9_.-]+)",
    re.IGNORECASE,
)
_WK_API_RE = re.compile(
    r"https?://apply\.workable\.com/api/v[1-3]/accounts/([a-zA-Z0-9_.-]+)",
    re.IGNORECASE,
)
_WK_BOARD_RE = re.compile(
    r"https?://apply\.workable\.com/([a-zA-Z0-9_.-]+)",
    re.IGNORECASE,
)


@dataclass
class DiscoveredBoard:
    provider: str
    slug: str
    company: str = ""
    job_count: int = 0
    sample_titles: list[str] = field(default_factory=list)
    discovered_from: str = "url_scan"  # 'url_scan' | 'live_probe'
    already_configured: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_valid_ats_slug(slug: str) -> bool:
    """Valide qu'un slug ATS n'est ni un mot réservé système, ni purement numérique, ni malformé."""
    clean = (slug or "").strip().strip("/.").lower()
    if not clean or len(clean) < 2 or len(clean) > 62:
        return False
    if clean in RESERVED_ATS_SLUGS:
        return False
    if clean.isdigit():
        return False
    return bool(_VALID_SLUG_RE.match(clean))


def normalize_company_name(name: str) -> str:
    """Normalise un nom d'entreprise pour l'appariement de graphe (sans suffixes légaux ni bruit)."""
    raw = norm(name or "")
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    tokens = [t for t in raw.split() if t]
    if not tokens:
        return ""
    joined = " ".join(tokens)
    if joined in COMPANY_STOPWORDS:
        return ""
    # Élaguer les suffixes juridiques en fin de nom (tout en gardant au moins 1 token)
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    result = " ".join(tokens)
    if result in COMPANY_STOPWORDS:
        return ""
    return result


def generate_company_slug_candidates(company_or_slug: str) -> list[str]:
    """Génère jusqu'à 2 candidats de slugs propres pour sonder les APIs ATS d'une entreprise."""
    raw = (company_or_slug or "").strip()
    if not raw:
        return []
    # Si l'utilisateur a collé une URL, extraire le domaine ou le dernier segment pertinent
    if "://" in raw:
        extracted = extract_ats_boards_from_text(raw)
        if extracted:
            return [extracted[0].slug]
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        raw = host.split(".")[0] if host else raw

    co_norm = normalize_company_name(raw)
    if not co_norm:
        co_norm = re.sub(r"[^a-z0-9\s-]", "", raw.lower()).strip()

    tokens = [t for t in re.split(r"[\s_-]+", co_norm) if t]
    if not tokens:
        return []

    hyphen_slug = "-".join(tokens)
    compact_slug = "".join(tokens)

    candidates: list[str] = []
    for cand in (compact_slug, hyphen_slug):
        if is_valid_ats_slug(cand) and cand not in candidates:
            candidates.append(cand)
    return candidates[:2]


def extract_ats_boards_from_text(text: str, company_hint: str = "") -> list[DiscoveredBoard]:
    """Extrait tous les couples `(provider, slug)` valides présents dans une URL ou un texte."""
    if not text:
        return []
    found: dict[tuple[str, str], DiscoveredBoard] = {}

    def _add(provider: str, raw_slug: str) -> None:
        slug = raw_slug.strip().strip("/.").lower()
        if not is_valid_ats_slug(slug):
            return
        key = (provider, slug)
        if key not in found:
            found[key] = DiscoveredBoard(
                provider=provider,
                slug=slug,
                company=company_hint or slug,
                discovered_from="url_scan",
            )

    for m in _GH_EMBED_RE.finditer(text):
        _add("greenhouse", m.group(1))
    for m in _GH_BOARD_RE.finditer(text):
        slug = m.group(1)
        if slug.lower() != "embed":
            _add("greenhouse", slug)
    for m in _ASHBY_BOARD_RE.finditer(text):
        _add("ashby", m.group(1))
    for m in _LEVER_BOARD_RE.finditer(text):
        _add("lever", m.group(1))
    for m in _SR_BOARD_RE.finditer(text):
        _add("smartrecruiters", m.group(1))
    for m in _WK_API_RE.finditer(text):
        _add("workable", m.group(1))
    for m in _WK_BOARD_RE.finditer(text):
        slug = m.group(1)
        if slug.lower() not in ("api", "j"):
            _add("workable", slug)

    return list(found.values())


def discover_boards_from_db(
    db_path: str | Path,
    profile: Profile | None = None,
) -> list[DiscoveredBoard]:
    """Scanne la base SQLite (`jobs.url` et `jobs.description`) pour découvrir des boards ATS."""
    path = Path(db_path)
    if not path.exists():
        return []

    configured: dict[str, set[str]] = {p: set() for p in SUPPORTED_ATS_PROVIDERS}
    if profile is not None:
        for prov in SUPPORTED_ATS_PROVIDERS:
            configured[prov] = {s.lower() for s in profile.sources.boards.get(prov, [])}

    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
        desc_expr = "description" if "description" in cols else "'' AS description"
        rows = con.execute(f"SELECT company, title, url, {desc_expr} FROM jobs").fetchall()
    finally:
        con.close()

    by_key: dict[tuple[str, str], DiscoveredBoard] = {}
    for r in rows:
        company = str(r["company"] or "").strip()
        title = str(r["title"] or "").strip()
        blob = f"{r['url'] or ''}\n{r['description'] or ''}"
        for b in extract_ats_boards_from_text(blob, company_hint=company):
            key = (b.provider, b.slug)
            if key not in by_key:
                b.already_configured = b.slug.lower() in configured.get(b.provider, set())
                by_key[key] = b
            entry = by_key[key]
            entry.job_count += 1
            if company and (not entry.company or entry.company == entry.slug):
                entry.company = company
            if title and title not in entry.sample_titles and len(entry.sample_titles) < 3:
                entry.sample_titles.append(title)

    results = list(by_key.values())
    results.sort(key=lambda x: (x.already_configured, -x.job_count, x.provider, x.slug))
    return results


def _probe_single_ats(
    provider: str,
    slug: str,
    *,
    timeout: float = 3.5,
    fetcher: Any | None = None,
) -> DiscoveredBoard | None:
    """Sonde un provider ATS unique pour un slug donné et vérifie qu'il retourne > 0 offres."""
    if not is_valid_ats_slug(slug):
        return None

    if provider == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    elif provider == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    elif provider == "lever":
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    elif provider == "smartrecruiters":
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=25"
    elif provider == "workable":
        url = f"https://www.workable.com/api/accounts/{slug}?details=false"
    else:
        return None

    get_fn = fetcher or (lambda u: _safe_http_get(u, timeout=timeout, resolve_dns=True))
    try:
        code, _, body = get_fn(url)
        if code != 200 or not body:
            return None
        data = json.loads(body)
    except Exception:
        return None

    titles: list[str] = []
    company_name = slug
    count = 0

    if provider in ("ashby", "greenhouse", "workable") and isinstance(data, dict):
        jobs_list = data.get("jobs")
        if not isinstance(jobs_list, list) or not jobs_list:
            return None
        count = len(jobs_list)
        if provider == "workable" and data.get("name"):
            company_name = str(data.get("name")).strip()
        for item in jobs_list[:3]:
            if isinstance(item, dict):
                t = str(item.get("title") or "").strip()
                if t:
                    titles.append(t)
    elif provider == "lever" and isinstance(data, list):
        if not data:
            return None
        count = len(data)
        for item in data[:3]:
            if isinstance(item, dict):
                t = str(item.get("text") or item.get("title") or "").strip()
                if t:
                    titles.append(t)
    elif provider == "smartrecruiters" and isinstance(data, dict):
        content = data.get("content")
        if not isinstance(content, list) or not content:
            return None
        count = int(data.get("totalFound") or len(content))
        for item in content[:3]:
            if isinstance(item, dict):
                t = str(item.get("name") or item.get("title") or "").strip()
                if t:
                    titles.append(t)
                comp = item.get("company")
                if isinstance(comp, dict) and comp.get("name"):
                    company_name = str(comp.get("name")).strip()
    else:
        return None

    if count <= 0:
        return None

    return DiscoveredBoard(
        provider=provider,
        slug=slug,
        company=company_name,
        job_count=count,
        sample_titles=titles,
        discovered_from="live_probe",
    )


def probe_company_ats(
    company_or_slug: str,
    *,
    profile: Profile | None = None,
    timeout: float = 3.5,
    fetcher: Any | None = None,
) -> list[DiscoveredBoard]:
    """Sonde en parallèle les 5 ATS publics pour les candidats de slugs d'une entreprise."""
    slugs = generate_company_slug_candidates(company_or_slug)
    if not slugs:
        return []

    configured: dict[str, set[str]] = {p: set() for p in SUPPORTED_ATS_PROVIDERS}
    if profile is not None:
        for prov in SUPPORTED_ATS_PROVIDERS:
            configured[prov] = {s.lower() for s in profile.sources.boards.get(prov, [])}

    tasks: list[tuple[str, str]] = [
        (prov, s) for s in slugs for prov in SUPPORTED_ATS_PROVIDERS
    ]
    discovered: dict[tuple[str, str], DiscoveredBoard] = {}

    with ThreadPoolExecutor(max_workers=min(8, len(tasks))) as pool:
        futs = {
            pool.submit(_probe_single_ats, prov, s, timeout=timeout, fetcher=fetcher): (prov, s)
            for prov, s in tasks
        }
        for fut in as_completed(futs):
            res = fut.result()
            if res is not None:
                res.already_configured = res.slug.lower() in configured.get(res.provider, set())
                discovered[(res.provider, res.slug)] = res

    results = list(discovered.values())
    results.sort(key=lambda x: (x.already_configured, -x.job_count, x.provider, x.slug))
    return results


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def sync_discovered_boards_to_profile(
    boards: Sequence[dict[str, str] | DiscoveredBoard],
    profile_yaml_path: str | Path,
) -> dict[str, Any]:
    """Ajoute les boards ATS découverts dans `profile.yaml` en préservant les commentaires,
    et active automatiquement les providers correspondants dans `sources.enabled`."""
    path = Path(profile_yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"profile.yaml introuvable : {path}")

    to_add: list[tuple[str, str]] = []
    for item in boards:
        if isinstance(item, DiscoveredBoard):
            prov, slug = item.provider.lower().strip(), item.slug.strip()
        elif isinstance(item, dict):
            prov = str(item.get("provider") or "").lower().strip()
            slug = str(item.get("slug") or "").strip()
        else:
            continue
        if prov in SUPPORTED_ATS_PROVIDERS and is_valid_ats_slug(slug):
            pair = (prov, slug)
            if pair not in to_add:
                to_add.append(pair)

    if not to_add:
        return {"added": [], "already_present": [], "enabled_providers": []}

    with _DISCOVERY_LOCK:
        raw_text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text) or {}
        if not isinstance(data, dict):
            raise ValueError("profile.yaml invalide.")

        sources_sec = data.setdefault("sources", {})
        if not isinstance(sources_sec, dict):
            sources_sec = {}
            data["sources"] = sources_sec

        enabled_list = sources_sec.setdefault("enabled", [])
        if not isinstance(enabled_list, list):
            enabled_list = []
            sources_sec["enabled"] = enabled_list

        boards_sec = sources_sec.setdefault("boards", {})
        if not isinstance(boards_sec, dict):
            boards_sec = {}
            sources_sec["boards"] = boards_sec

        added: list[dict[str, str]] = []
        already_present: list[dict[str, str]] = []
        newly_enabled: list[str] = []

        updated_text = raw_text
        needs_full_dump = False

        for prov, slug in to_add:
            prov_boards = boards_sec.setdefault(prov, [])
            if not isinstance(prov_boards, list):
                prov_boards = []
                boards_sec[prov] = prov_boards
            existing_lower = {str(s).lower() for s in prov_boards}
            if slug.lower() in existing_lower:
                already_present.append({"provider": prov, "slug": slug})
                continue

            prov_boards.append(slug)
            added.append({"provider": prov, "slug": slug})

            # Insertion chirurgicale dans le texte YAML sous `boards:\n    <prov>:`
            prov_pattern = re.compile(
                rf"(^[ \t]*{re.escape(prov)}\s*:\s*(?:#.*)?\r?\n)((?:[ \t]*-[ \t]*[^\r\n]+\r?\n)*)",
                re.MULTILINE,
            )
            m = prov_pattern.search(updated_text)
            if m:
                header_line = m.group(1)
                items_block = m.group(2)
                indent_match = re.match(r"^([ \t]*)-[ \t]*", items_block) if items_block else None
                if indent_match:
                    item_indent = indent_match.group(1)
                else:
                    base_indent = re.match(r"^([ \t]*)", header_line).group(1)  # type: ignore[union-attr]
                    item_indent = base_indent + "  "
                new_block = f"{header_line}{items_block}{item_indent}- {slug}\n"
                updated_text = updated_text[: m.start()] + new_block + updated_text[m.end() :]
            else:
                # Si la clé du provider n'existe pas encore sous `boards:`, on l'insère sous `boards:`
                boards_header = re.search(r"^([ \t]*)boards\s*:\s*(?:#.*)?\r?\n", updated_text, re.MULTILINE)
                if boards_header:
                    b_indent = boards_header.group(1)
                    prov_indent = b_indent + "  "
                    item_indent = prov_indent + "  "
                    insert_pos = boards_header.end()
                    snippet = f"{prov_indent}{prov}:\n{item_indent}- {slug}\n"
                    updated_text = updated_text[:insert_pos] + snippet + updated_text[insert_pos:]
                else:
                    needs_full_dump = True

            if prov not in [str(x).lower() for x in enabled_list]:
                enabled_list.append(prov)
                newly_enabled.append(prov)
                en_match = re.search(
                    r"(^[ \t]*enabled\s*:\s*(?:#.*)?\r?\n)((?:[ \t]*-[ \t]*[^\r\n]+\r?\n)+)",
                    updated_text,
                    re.MULTILINE,
                )
                if en_match:
                    en_items = en_match.group(2)
                    en_indent_m = re.match(r"^([ \t]*)-[ \t]*", en_items)
                    en_indent = en_indent_m.group(1) if en_indent_m else "    "
                    updated_text = (
                        updated_text[: en_match.start()]
                        + f"{en_match.group(1)}{en_items}{en_indent}- {prov}\n"
                        + updated_text[en_match.end() :]
                    )
                else:
                    needs_full_dump = True

        if added:
            if not needs_full_dump:
                try:
                    parsed_check = yaml.safe_load(updated_text)
                    if not isinstance(parsed_check, dict):
                        needs_full_dump = True
                except Exception:
                    needs_full_dump = True

            final_text = (
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
                if needs_full_dump
                else updated_text
            )
            _atomic_write_text(path, final_text)
            # Valide que le profil se charge sans erreur
            load_profile(path)

    return {
        "added": added,
        "already_present": already_present,
        "enabled_providers": newly_enabled,
    }


# ---------------------------------------------------------------------------
# LinkedIn Warm-Intro & Referral Matcher
# ---------------------------------------------------------------------------

_HEADER_ALIASES = {
    "first_name": {"first name", "firstname", "prenom", "prénom", "given name"},
    "last_name": {"last name", "lastname", "nom", "nom de famille", "surname", "family name"},
    "full_name": {"full name", "fullname", "name", "nom complet"},
    "url": {"url", "linkedin url", "profile url", "lien", "linkedin"},
    "email": {"email address", "email", "adresse e mail", "adresse email", "courriel", "e mail"},
    "company": {"company", "entreprise", "societe", "société", "organization", "organisation", "employer"},
    "position": {"position", "poste", "title", "job title", "intitule", "intitulé", "role", "rôle"},
    "connected_on": {"connected on", "connecte le", "connecté le", "date"},
}


def _match_header_column(raw_col: str) -> str | None:
    cleaned = norm(raw_col or "").strip()
    for canonical, aliases in _HEADER_ALIASES.items():
        if cleaned in aliases:
            return canonical
    return None


def import_linkedin_connections_csv(
    csv_content: str | bytes,
    db_path: str | Path,
) -> dict[str, Any]:
    """Importe un fichier `Connections.csv` LinkedIn dans la table `warm_contacts`.

    Gère automatiquement :
    - Le BOM UTF-8 (`utf-8-sig`)
    - Les 3+ lignes de préambule `"Notes:"` propres aux exports LinkedIn
    - Les délimiteurs `,` ou `;`
    - Les colonnes en anglais ou en français
    - Le dédoublonnage idempotent via `contact_key`
    """
    if isinstance(csv_content, bytes):
        text = csv_content.decode("utf-8-sig", errors="replace")
    else:
        text = str(csv_content or "").lstrip("\ufeff")

    lines = text.splitlines()
    if not lines:
        return {"imported": 0, "skipped": 0, "total_in_db": 0, "reason": "Fichier CSV vide"}

    # Recherche de la vraie ligne d'en-tête parmi les 20 premières lignes
    header_idx = 0
    delimiter = ","
    for idx, line in enumerate(lines[:20]):
        if not line.strip():
            continue
        for cand_delim in (",", ";", "\t"):
            try:
                cells = next(csv.reader([line], delimiter=cand_delim))
            except Exception:
                continue
            matched = {_match_header_column(c) for c in cells}
            matched.discard(None)
            if "company" in matched and ("first_name" in matched or "full_name" in matched or "position" in matched):
                header_idx = idx
                delimiter = cand_delim
                break
        else:
            continue
        break

    body_text = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(body_text), delimiter=delimiter)
    if not reader.fieldnames:
        return {"imported": 0, "skipped": 0, "total_in_db": 0, "reason": "Aucun en-tête CSV détecté"}

    col_map: dict[str, str] = {}
    for raw_field in reader.fieldnames:
        if raw_field is None:
            continue
        canon = _match_header_column(raw_field)
        if canon and canon not in col_map:
            col_map[canon] = raw_field

    if "company" not in col_map:
        return {
            "imported": 0,
            "skipped": 0,
            "total_in_db": 0,
            "reason": "Colonne 'Company' / 'Entreprise' introuvable dans le CSV",
        }

    path = Path(db_path)
    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    imported = 0
    skipped = 0
    try:
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA busy_timeout = 5000;")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS warm_contacts (
                contact_key TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                company TEXT NOT NULL,
                company_norm TEXT NOT NULL,
                position TEXT DEFAULT '',
                linkedin_url TEXT DEFAULT '',
                email TEXT DEFAULT '',
                connected_on TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_warm_contacts_co ON warm_contacts(company_norm);")

        for row in reader:
            if not isinstance(row, dict):
                continue
            raw_company = str(row.get(col_map["company"]) or "").strip()
            co_norm = normalize_company_name(raw_company)
            if not co_norm:
                skipped += 1
                continue

            if "full_name" in col_map:
                full_name = str(row.get(col_map["full_name"]) or "").strip()
            else:
                first = str(row.get(col_map.get("first_name", "")) or "").strip()
                last = str(row.get(col_map.get("last_name", "")) or "").strip()
                full_name = f"{first} {last}".strip()

            if not full_name:
                skipped += 1
                continue

            position = str(row.get(col_map.get("position", "")) or "").strip()
            linkedin_url = str(row.get(col_map.get("url", "")) or "").strip()
            email = str(row.get(col_map.get("email", "")) or "").strip()
            connected_on = str(row.get(col_map.get("connected_on", "")) or "").strip()

            key_seed = linkedin_url.lower().rstrip("/") if linkedin_url else f"{norm(full_name)}|{co_norm}|{norm(position)}"
            contact_key = hashlib.sha1(key_seed.encode("utf-8")).hexdigest()[:20]

            con.execute(
                """
                INSERT INTO warm_contacts
                    (contact_key, full_name, company, company_norm, position, linkedin_url, email, connected_on)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(contact_key) DO UPDATE SET
                    full_name = excluded.full_name,
                    company = excluded.company,
                    company_norm = excluded.company_norm,
                    position = excluded.position,
                    linkedin_url = CASE WHEN excluded.linkedin_url != '' THEN excluded.linkedin_url ELSE warm_contacts.linkedin_url END,
                    email = CASE WHEN excluded.email != '' THEN excluded.email ELSE warm_contacts.email END,
                    connected_on = CASE WHEN excluded.connected_on != '' THEN excluded.connected_on ELSE warm_contacts.connected_on END
                """,
                (contact_key, full_name, raw_company, co_norm, position, linkedin_url, email, connected_on),
            )
            imported += 1

        con.commit()
        total_in_db = int(con.execute("SELECT COUNT(*) FROM warm_contacts").fetchone()[0])
    finally:
        con.close()

    return {
        "imported": imported,
        "skipped": skipped,
        "total_in_db": total_in_db,
        "header_line_index": header_idx,
    }


def companies_match(job_company: str, contact_company: str) -> bool:
    """Détermine si l'entreprise d'une offre et celle d'un contact LinkedIn correspondent,
    sans faux positifs sur les noms courts (`On`, `At`, `Go`, `AI`)."""
    j_norm = normalize_company_name(job_company)
    c_norm = normalize_company_name(contact_company)
    if not j_norm or not c_norm:
        return False
    if j_norm == c_norm:
        return True

    j_tokens = j_norm.split()
    c_tokens = c_norm.split()
    shorter_str = j_norm if len(j_norm) <= len(c_norm) else c_norm
    shorter_tokens = j_tokens if len(j_tokens) <= len(c_tokens) else c_tokens
    longer_tokens = c_tokens if len(j_tokens) <= len(c_tokens) else j_tokens

    # Match par préfixe de tokens entiers uniquement si le nom court fait >= 4 caractères
    if len(shorter_str) >= 4 and len(shorter_tokens) >= 1:
        if longer_tokens[: len(shorter_tokens)] == shorter_tokens:
            return True

    return False


def build_referral_draft(
    *,
    contact_name: str,
    contact_position: str,
    company: str,
    job_title: str,
    job_url: str = "",
    candidate_name: str = "",
    lang: str = "fr",
) -> str:
    """Génère un message court et naturel de demande de cooptation (Warm Intro / Referral)."""
    first_name = (contact_name or "").strip().split()[0] if (contact_name or "").strip() else "Bonjour"
    sig = f"\n\nÀ très vite,\n{candidate_name}" if candidate_name else ""
    sig_en = f"\n\nBest,\n{candidate_name}" if candidate_name else ""

    if lang.lower().startswith("en"):
        return (
            f"Hi {first_name},\n\n"
            f"Hope you're doing well at {company}! I noticed an open {job_title} role on your team "
            f"that closely matches what I've been building recently.\n\n"
            f"Before I submit my application formally, would you be open to sharing a quick perspective "
            f"on the team or potentially putting in an internal referral if you think my profile fits?"
            f"{sig_en}"
        )

    return (
        f"Salut {first_name},\n\n"
        f"J'espère que tout roule pour toi chez {company} ! Je viens de voir passer l'offre "
        f"de « {job_title} » chez vous et elle correspond pile à ce sur quoi je travaille en ce moment.\n\n"
        f"Avant de postuler officiellement, est-ce que tu aurais 2 minutes pour me dire comment "
        f"se passe l'ambiance dans l'équipe (et éventuellement me coopter en interne si vous avez "
        f"un programme de referral) ?{sig}"
    )


def build_linkedin_search_links(company: str, job_title: str) -> dict[str, str]:
    """Génère des liens 1-clic de recherche LinkedIn People pour trouver un pair ou un recruteur."""
    clean_co = (company or "").strip()
    # Extraire les 2 premiers mots significatifs du titre pour une recherche ciblée
    title_words = [w for w in re.sub(r"[^a-zA-Z0-9\s]", " ", job_title or "").split() if len(w) > 2][:2]
    role_hint = " ".join(title_words) if title_words else "Engineering"

    q_peers = urllib.parse.quote(f'"{clean_co}" {role_hint}')
    q_recruiters = urllib.parse.quote(f'"{clean_co}" (Recruiter OR Talent OR HR OR Hiring)')
    q_connections = urllib.parse.quote(f'"{clean_co}"')

    return {
        "company_people_url": f"https://www.linkedin.com/search/results/people/?keywords={q_connections}",
        "peers_search_url": f"https://www.linkedin.com/search/results/people/?keywords={q_peers}",
        "recruiters_search_url": f"https://www.linkedin.com/search/results/people/?keywords={q_recruiters}",
    }


def match_warm_intros_for_jobs(
    db_path: str | Path,
    *,
    candidate_name: str = "",
    lang: str = "fr",
    limit: int = 50,
) -> dict[str, Any]:
    """Croise les offres actives en base avec les contacts LinkedIn (`warm_contacts`)
    et prépare les brouillons de cooptation ainsi que les liens de recherche 1-clic."""
    path = Path(db_path)
    if not path.exists():
        return {"total_contacts": 0, "matched_jobs": [], "fallback_targets": []}

    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        contacts: list[dict[str, Any]] = []
        if "warm_contacts" in tables:
            contacts = [dict(r) for r in con.execute("SELECT * FROM warm_contacts").fetchall()]

        jobs = [
            dict(r)
            for r in con.execute(
                """
                SELECT id, source, company, title, location, url, fit_score, status,
                       COALESCE(liveness_status, 'unknown') AS liveness_status
                FROM jobs
                WHERE status NOT IN ('rejected', 'archived')
                  AND COALESCE(liveness_status, 'unknown') != 'expired'
                  AND company IS NOT NULL AND TRIM(company) != ''
                ORDER BY fit_score DESC, created_at DESC
                LIMIT 200
                """
            ).fetchall()
        ]
    finally:
        con.close()

    matched_jobs: list[dict[str, Any]] = []
    fallback_targets: list[dict[str, Any]] = []
    seen_fallback_companies: set[str] = set()

    for job in jobs:
        j_co = str(job.get("company") or "").strip()
        j_title = str(job.get("title") or "").strip()
        j_url = str(job.get("url") or "").strip()

        job_contacts: list[dict[str, Any]] = []
        for c in contacts:
            if companies_match(j_co, str(c.get("company") or "")):
                draft_fr = build_referral_draft(
                    contact_name=str(c.get("full_name") or ""),
                    contact_position=str(c.get("position") or ""),
                    company=j_co,
                    job_title=j_title,
                    job_url=j_url,
                    candidate_name=candidate_name,
                    lang=lang,
                )
                job_contacts.append(
                    {
                        "full_name": c.get("full_name"),
                        "position": c.get("position"),
                        "company": c.get("company"),
                        "linkedin_url": c.get("linkedin_url"),
                        "email": c.get("email"),
                        "connected_on": c.get("connected_on"),
                        "referral_draft": draft_fr,
                    }
                )

        search_links = build_linkedin_search_links(j_co, j_title)
        if job_contacts:
            matched_jobs.append(
                {
                    "job_id": job.get("id"),
                    "company": j_co,
                    "title": j_title,
                    "fit_score": round(float(job.get("fit_score") or 0.0), 1),
                    "status": job.get("status"),
                    "url": j_url,
                    "contacts_count": len(job_contacts),
                    "contacts": job_contacts[:5],
                    "linkedin_search": search_links,
                }
            )
        else:
            co_norm = normalize_company_name(j_co)
            if co_norm and co_norm not in seen_fallback_companies and len(fallback_targets) < 15:
                seen_fallback_companies.add(co_norm)
                fallback_targets.append(
                    {
                        "job_id": job.get("id"),
                        "company": j_co,
                        "title": j_title,
                        "fit_score": round(float(job.get("fit_score") or 0.0), 1),
                        "url": j_url,
                        "linkedin_search": search_links,
                    }
                )

    return {
        "total_contacts": len(contacts),
        "matched_jobs_count": len(matched_jobs),
        "matched_jobs": matched_jobs[:limit],
        "fallback_targets": fallback_targets,
    }
