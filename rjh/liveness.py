"""Vérification de disponibilité d'offres (Liveness), détection d'Anti-Ghost Jobs
(reposts) et suivi de cadence de relance (Follow-up Tracker J+7 / J+14 / Ghosted J+21).

Conçu avec :
- Protection anti-SSRF stricte (résolution DNS + blocage IP privées/loopback/link-local
  avant chaque requête ET à chaque saut de redirection HTTP).
- Lecture réseau bornée (plafond 256 Ko) contre les bombes mémoire (OOM/tarpits).
- Vérification native API pour Greenhouse, Lever et Ashby (évite le piège SPA 200 OK).
- Normalisation temporelle stricte en UTC (`YYYY-MM-DDTHH:M:SZ`) compatible avec
  `CURRENT_TIMESTAMP` SQLite et ISO-8601.
- Exécution batch en 3 phases (Read -> ThreadPool Network -> Single-Transaction Write)
  pour ne jamais verrouiller SQLite WAL ni bloquer l'event loop FastAPI.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import re
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MAX_LIVENESS_BYTES = 262_144  # 256 KiB ceiling per HTTP response
DEFAULT_TIMEOUT = 6.0
DEFAULT_BATCH_WORKERS = 6
MAX_BATCH_LIMIT = 25

# Marqueurs d'offre expirée (FR + EN)
_EXPIRED_MARKERS = (
    "no longer accepting applications",
    "this job is no longer available",
    "this position has been filled",
    "position is no longer open",
    "job posting is no longer active",
    "sorry, this job is closed",
    "this role is no longer open",
    "cette offre n'est plus disponible",
    "cette offre est expirée",
    "ce poste a été pourvu",
    "l'offre d'emploi n'existe plus",
)

# Regex d'extraction d'URL ATS directes
_GREENHOUSE_RE = re.compile(
    r"^https?://(?:job-boards|boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)/jobs/(\d+)",
    re.IGNORECASE,
)
_GREENHOUSE_EMBED_RE = re.compile(
    r"^https?://(?:job-boards|boards)\.greenhouse\.io/embed/job_app\?.*?for=([a-zA-Z0-9_-]+).*?token=(\d+)",
    re.IGNORECASE,
)
_LEVER_RE = re.compile(
    r"^https?://jobs\.lever\.co/([a-zA-Z0-9_-]+)/([0-9a-fA-F-]{16,64})",
    re.IGNORECASE,
)
_ASHBY_RE = re.compile(
    r"^https?://jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)/([0-9a-fA-F-]{16,64})",
    re.IGNORECASE,
)
_SMARTRECRUITERS_RE = re.compile(
    r"^https?://jobs\.smartrecruiters\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_-]{8,64})",
    re.IGNORECASE,
)
_WORKABLE_RE = re.compile(
    r"^https?://apply\.workable\.com/([a-zA-Z0-9_.-]+)/j/([a-zA-Z0-9_-]{6,32})",
    re.IGNORECASE,
)


class SSRFBlockedError(ValueError):
    """Levée lorsqu'une URL cible ou une redirection pointe vers une IP non-publique."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(ts: str | None) -> datetime | None:
    """Parse une date ISO-8601 ou SQLite (`YYYY-MM-DD HH:MM:SS`) en datetime UTC aware."""
    if not ts or not isinstance(ts, str):
        return None
    raw = ts.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_public_url(url: str, *, resolve_dns: bool = True) -> urllib.parse.SplitResult:
    """Vérifie que l'URL est en HTTP(S) et ne cible aucune adresse IP privée/loopback/réservée."""
    if not url or not isinstance(url, str):
        raise SSRFBlockedError("Empty URL")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFBlockedError(f"Unsupported URL scheme: {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError("Missing hostname in URL")
    lowered = hostname.lower().rstrip(".")
    if lowered in ("localhost", "localhost.localdomain", "ip6-localhost") or lowered.endswith(".local") or lowered.endswith(".internal"):
        raise SSRFBlockedError(f"Blocked internal hostname: {hostname}")

    def _check_ip(ip_str: str) -> None:
        ip_clean = ip_str.split("%")[0]
        addr = ipaddress.ip_address(ip_clean)
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        ):
            raise SSRFBlockedError(f"Blocked non-public IP address: {ip_clean}")

    # Vérifier si le hostname est directement une IP littérale
    try:
        ip_clean = lowered.split("%")[0]
        addr = ipaddress.ip_address(ip_clean)
    except ValueError:
        addr = None

    if addr is not None:
        _check_ip(lowered)
        return parsed

    if resolve_dns:
        try:
            infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise urllib.error.URLError(f"DNS resolution failed for {hostname}: {exc}") from exc
        if not infos:
            raise urllib.error.URLError(f"No DNS records for {hostname}")
        for info in infos:
            sockaddr = info[4]
            if sockaddr and sockaddr[0]:
                _check_ip(str(sockaddr[0]))

    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Valide chaque saut de redirection contre les rebonds SSRF vers localhost/RFC1918."""

    def __init__(self, *, resolve_dns: bool = True, max_redirects: int = 5) -> None:
        super().__init__()
        self._resolve_dns = resolve_dns
        self._redirects = 0
        self._max_redirects = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._redirects += 1
        if self._redirects > self._max_redirects:
            raise urllib.error.HTTPError(req.full_url, code, "Too many redirects", headers, fp)
        full_target = urllib.parse.urljoin(req.full_url, newurl)
        validate_public_url(full_target, resolve_dns=self._resolve_dns)
        return super().redirect_request(req, fp, code, msg, headers, full_target)


def _safe_http_get(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_LIVENESS_BYTES,
    resolve_dns: bool = True,
) -> tuple[int, str, str]:
    """Exécute un GET borné et protégé contre les SSRF. Retourne `(status_code, final_url, body_text)`."""
    validate_public_url(url, resolve_dns=resolve_dns)
    opener = urllib.request.build_opener(_SafeRedirectHandler(resolve_dns=resolve_dns))
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; RemoteJobHunter/0.2; +https://github.com/manny-ruesch/remote-job-hunter)",
            "Accept": "application/json, text/html, */*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        method="GET",
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            status_code = getattr(resp, "status", None) or resp.getcode() or 200
            final_url = resp.geturl() or url
            raw = resp.read(max_bytes)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return int(status_code), final_url, text
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            if exc.fp:
                body = exc.fp.read(4096).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return int(exc.code), url, body


@dataclass
class LivenessResult:
    status: str  # 'active' | 'expired' | 'uncertain'
    reason: str
    checked_at: str
    http_status: int | None = None
    ats_provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_job_liveness(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    resolve_dns: bool = True,
    fetcher: Any | None = None,
) -> LivenessResult:
    """Vérifie si une offre est toujours ouverte sans consommer de token LLM.

    - Greenhouse : vérifie l'endpoint JSON `boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}`
    - Lever : vérifie `api.lever.co/v0/postings/{board}/{id}`
    - Ashby : vérifie la présence de l'offre dans `api.ashbyhq.com/posting-api/job-board/{board}`
    - Générique : GET borné (256 Ko) + détection de 404/410 ou bannières d'expiration.
    """
    now_iso = format_utc(utc_now())
    if not url or not str(url).strip():
        return LivenessResult(status="uncertain", reason="URL manquante", checked_at=now_iso)

    get_fn = fetcher or (lambda target_url: _safe_http_get(target_url, timeout=timeout, resolve_dns=resolve_dns))

    try:
        validate_public_url(url, resolve_dns=resolve_dns)
    except SSRFBlockedError as exc:
        return LivenessResult(status="uncertain", reason=f"URL bloquée (sécurité): {exc}", checked_at=now_iso)
    except urllib.error.URLError as exc:
        return LivenessResult(status="uncertain", reason=f"Résolution DNS impossible: {exc}", checked_at=now_iso)

    # 1. Greenhouse direct check
    gh_match = _GREENHOUSE_RE.match(url) or _GREENHOUSE_EMBED_RE.match(url)
    if gh_match:
        board, job_id = gh_match.group(1), gh_match.group(2)
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
        try:
            code, _, body = get_fn(api_url)
            if code == 200:
                data = json.loads(body) if body else {}
                if data.get("id") or data.get("title"):
                    return LivenessResult(
                        status="active",
                        reason="Offre vérifiée active sur l'API Greenhouse",
                        checked_at=now_iso,
                        http_status=200,
                        ats_provider="greenhouse",
                    )
            if code in (404, 410):
                return LivenessResult(
                    status="expired",
                    reason=f"Offre retirée du portail Greenhouse (HTTP {code})",
                    checked_at=now_iso,
                    http_status=code,
                    ats_provider="greenhouse",
                )
        except Exception as exc:
            return LivenessResult(
                status="uncertain",
                reason=f"Vérification Greenhouse indisponible: {exc}",
                checked_at=now_iso,
                ats_provider="greenhouse",
            )

    # 2. Lever direct check
    lv_match = _LEVER_RE.match(url)
    if lv_match:
        board, posting_id = lv_match.group(1), lv_match.group(2)
        api_url = f"https://api.lever.co/v0/postings/{board}/{posting_id}"
        try:
            code, _, body = get_fn(api_url)
            if code == 200:
                data = json.loads(body) if body else {}
                if data.get("id") or data.get("text"):
                    return LivenessResult(
                        status="active",
                        reason="Offre vérifiée active sur l'API Lever",
                        checked_at=now_iso,
                        http_status=200,
                        ats_provider="lever",
                    )
            if code in (404, 410):
                return LivenessResult(
                    status="expired",
                    reason=f"Offre retirée du portail Lever (HTTP {code})",
                    checked_at=now_iso,
                    http_status=code,
                    ats_provider="lever",
                )
        except Exception as exc:
            return LivenessResult(
                status="uncertain",
                reason=f"Vérification Lever indisponible: {exc}",
                checked_at=now_iso,
                ats_provider="lever",
            )

    # 3. Ashby direct API check (évite le piège SPA 200 OK de jobs.ashbyhq.com)
    ashby_match = _ASHBY_RE.match(url)
    if ashby_match:
        board, posting_id = ashby_match.group(1), ashby_match.group(2).lower()
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        try:
            code, _, body = get_fn(api_url)
            if code in (404, 410):
                return LivenessResult(
                    status="expired",
                    reason=f"Portail Ashby introuvable (HTTP {code})",
                    checked_at=now_iso,
                    http_status=code,
                    ats_provider="ashby",
                )
            if code == 200 and body:
                data = json.loads(body)
                jobs_list = data.get("jobs")
                if isinstance(jobs_list, list):
                    for item in jobs_list:
                        item_id = str(item.get("id") or "").lower()
                        item_url = str(item.get("jobUrl") or "").lower()
                        if posting_id == item_id or posting_id in item_url:
                            if item.get("isListed") is False:
                                return LivenessResult(
                                    status="expired",
                                    reason="Offre dépubliée sur Ashby (isListed=false)",
                                    checked_at=now_iso,
                                    http_status=200,
                                    ats_provider="ashby",
                                )
                            return LivenessResult(
                                status="active",
                                reason="Offre présente dans le flux actif Ashby",
                                checked_at=now_iso,
                                http_status=200,
                                ats_provider="ashby",
                            )
                    return LivenessResult(
                        status="expired",
                        reason="Offre absente du job-board actif Ashby",
                        checked_at=now_iso,
                        http_status=200,
                        ats_provider="ashby",
                    )
        except Exception as exc:
            return LivenessResult(
                status="uncertain",
                reason=f"Vérification Ashby indisponible: {exc}",
                checked_at=now_iso,
                ats_provider="ashby",
            )

    # 4. SmartRecruiters direct API check
    sr_match = _SMARTRECRUITERS_RE.match(url)
    if sr_match and sr_match.group(1).lower() not in ("oneclick-ui", "sr-jobs", "api"):
        board, posting_id = sr_match.group(1), sr_match.group(2)
        api_url = f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{posting_id}"
        try:
            code, _, body = get_fn(api_url)
            if code == 200 and body:
                data = json.loads(body)
                if data.get("id") or data.get("name"):
                    return LivenessResult(
                        status="active",
                        reason="Offre vérifiée active sur l'API SmartRecruiters",
                        checked_at=now_iso,
                        http_status=200,
                        ats_provider="smartrecruiters",
                    )
            if code in (404, 410):
                return LivenessResult(
                    status="expired",
                    reason=f"Offre retirée du portail SmartRecruiters (HTTP {code})",
                    checked_at=now_iso,
                    http_status=code,
                    ats_provider="smartrecruiters",
                )
        except Exception as exc:
            return LivenessResult(
                status="uncertain",
                reason=f"Vérification SmartRecruiters indisponible: {exc}",
                checked_at=now_iso,
                ats_provider="smartrecruiters",
            )

    # 5. Workable direct API check
    wk_match = _WORKABLE_RE.match(url)
    if wk_match and wk_match.group(1).lower() not in ("api", "j"):
        board, shortcode = wk_match.group(1), wk_match.group(2).lower()
        api_url = f"https://www.workable.com/api/accounts/{board}?details=false"
        try:
            code, _, body = get_fn(api_url)
            if code in (404, 410):
                return LivenessResult(
                    status="expired",
                    reason=f"Portail Workable introuvable (HTTP {code})",
                    checked_at=now_iso,
                    http_status=code,
                    ats_provider="workable",
                )
            if code == 200 and body:
                data = json.loads(body)
                jobs_list = data.get("jobs") if isinstance(data, dict) else None
                if isinstance(jobs_list, list):
                    for item in jobs_list:
                        sc = str(item.get("shortcode") or item.get("code") or "").lower()
                        if sc == shortcode:
                            return LivenessResult(
                                status="active",
                                reason="Offre présente dans le flux actif Workable",
                                checked_at=now_iso,
                                http_status=200,
                                ats_provider="workable",
                            )
                    return LivenessResult(
                        status="expired",
                        reason="Offre absente du job-board actif Workable",
                        checked_at=now_iso,
                        http_status=200,
                        ats_provider="workable",
                    )
        except Exception as exc:
            return LivenessResult(
                status="uncertain",
                reason=f"Vérification Workable indisponible: {exc}",
                checked_at=now_iso,
                ats_provider="workable",
            )

    # 4. Vérification HTTP générique bornée
    try:
        code, final_url, body = get_fn(url)
    except SSRFBlockedError as exc:
        return LivenessResult(status="uncertain", reason=f"Redirection bloquée: {exc}", checked_at=now_iso)
    except Exception as exc:
        return LivenessResult(status="uncertain", reason=f"Erreur réseau: {exc}", checked_at=now_iso)

    if code in (404, 410):
        return LivenessResult(
            status="expired",
            reason=f"Page d'offre supprimée (HTTP {code})",
            checked_at=now_iso,
            http_status=code,
        )
    if code in (401, 403, 429) or code >= 500:
        return LivenessResult(
            status="uncertain",
            reason=f"Portail protégé ou indisponible (HTTP {code})",
            checked_at=now_iso,
            http_status=code,
        )

    lower_body = (body or "").lower()
    for marker in _EXPIRED_MARKERS:
        if marker in lower_body:
            return LivenessResult(
                status="expired",
                reason=f"Mention d'expiration détectée (« {marker} »)",
                checked_at=now_iso,
                http_status=code,
            )

    # Si redirection vers la racine /careers générique sans identifiant d'offre
    orig_path = urllib.parse.urlsplit(url).path.rstrip("/")
    final_path = urllib.parse.urlsplit(final_url).path.rstrip("/")
    if len(orig_path) > 18 and final_path in ("", "/careers", "/jobs", "/fr/jobs", "/en/jobs"):
        return LivenessResult(
            status="expired",
            reason=f"Redirigé vers l'accueil carrière ({final_path or '/'})",
            checked_at=now_iso,
            http_status=code,
        )

    return LivenessResult(
        status="active",
        reason="Page d'offre accessible et active",
        checked_at=now_iso,
        http_status=code,
    )


def detect_reposts_in_db(db_file: Path | str) -> dict[str, Any]:
    """Détecte les vrais reposts (Anti-Ghost Job) sans confondre la syndication multi-sources simultanée.

    Une offre partageant le même `(company, title)` normalisé ou `dedupe_key` est comptée comme repost si :
      - elle provient de la même source avec une URL ou un ID distinct, OU
      - l'écart de `created_at` entre deux publications dépasse 7 jours, OU
      - une occurrence antérieure a été marquée `liveness_status = 'expired'`.
    """
    path = Path(db_file)
    if not path.exists():
        return {"updated_jobs": 0, "ghost_suspects": 0}

    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA busy_timeout = 5000;")
        rows = con.execute(
            """
            SELECT id, source, company, title, url, dedupe_key,
                   COALESCE(liveness_status, 'unknown') AS liveness_status,
                   COALESCE(repost_count, 0) AS repost_count,
                   created_at
            FROM jobs
            """
        ).fetchall()

        groups: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            dkey = (r["dedupe_key"] or "").strip()
            if not dkey:
                comp = re.sub(r"[^a-z0-9]+", "", (r["company"] or "").lower())
                ttl = re.sub(r"[^a-z0-9]+", "", (r["title"] or "").lower())
                if not comp and not ttl:
                    continue
                dkey = f"{comp}|{ttl}"
            groups.setdefault(dkey, []).append(r)

        updates: list[tuple[int, str]] = []
        ghost_suspects = 0

        for dkey, items in groups.items():
            if len(items) <= 1:
                continue

            # Calculer le nombre de reposts légitimes dans le groupe
            parsed_dates = [parse_utc(it["created_at"]) for it in items]
            valid_dates = [d for d in parsed_dates if d is not None]
            day_span = 0.0
            if len(valid_dates) >= 2:
                day_span = (max(valid_dates) - min(valid_dates)).total_seconds() / 86400.0

            has_expired_sibling = any((it["liveness_status"] or "") == "expired" for it in items)

            # Compter combien d'offres distinctes viennent de la même source
            by_source: dict[str, set[str]] = {}
            for it in items:
                src_base = (it["source"] or "").split("·")[0].strip().lower()
                clean_u = (it["url"] or "").split("?")[0].rstrip("/").lower() or it["id"]
                by_source.setdefault(src_base, set()).add(clean_u)
            same_source_reposts = max((len(urls) - 1 for urls in by_source.values()), default=0)

            calculated_reposts = 0
            if same_source_reposts > 0:
                calculated_reposts = max(calculated_reposts, same_source_reposts)
            if day_span >= 7.0:
                distinct_urls = {
                    (it["url"] or "").split("?")[0].rstrip("/").lower() or it["id"]
                    for it in items
                }
                calculated_reposts = max(calculated_reposts, max(0, len(distinct_urls) - 1))
            if has_expired_sibling:
                calculated_reposts = max(calculated_reposts, 1)

            for it in items:
                new_count = max(int(it["repost_count"] or 0), calculated_reposts)
                if new_count >= 2:
                    ghost_suspects += 1
                if new_count != int(it["repost_count"] or 0):
                    updates.append((new_count, it["id"]))

        if updates:
            con.executemany("UPDATE jobs SET repost_count = ? WHERE id = ?", updates)
            con.commit()

        return {"updated_jobs": len(updates), "ghost_suspects": ghost_suspects}
    finally:
        con.close()


@dataclass
class FollowupInfo:
    state: str  # 'none' | 'scheduled' | 'due' | 'ghosted'
    applied_at: str | None
    next_followup_at: str | None
    followup_count: int
    days_since_applied: int
    days_until_followup: int | None
    suggested_message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_followup_message(company: str, title: str, followup_count: int = 0) -> str:
    comp = (company or "votre équipe").strip()
    role = (title or "ce poste").strip()
    if followup_count == 0:
        return (
            f"Bonjour,\n\n"
            f"Je me permets de revenir vers vous concernant ma candidature au poste de {role} chez {comp}.\n"
            f"Toujours très enthousiaste à l'idée de contribuer à vos enjeux opérationnels, je reste à votre "
            f"disposition pour un court échange de 15 minutes cette semaine.\n\n"
            f"Bien cordialement,"
        )
    return (
        f"Bonjour,\n\n"
        f"Un dernier message rapide au sujet de ma candidature pour le rôle de {role} chez {comp}.\n"
        f"Si le recrutement est toujours ouvert, je serais ravi d'échanger sur la manière dont je peux être "
        f"opérationnel dès les premières semaines.\n\n"
        f"Excellente journée,"
    )


def compute_followup_status(job_row: dict[str, Any], *, now: datetime | None = None) -> FollowupInfo:
    """Calcule l'état de relance (`none`, `scheduled`, `due`, `ghosted`) d'une offre."""
    ref_now = now.astimezone(timezone.utc) if (now and now.tzinfo) else (now.replace(tzinfo=timezone.utc) if now else utc_now())
    status = str(job_row.get("status") or "new").lower()
    followup_count = int(job_row.get("followup_count") or 0)
    company = str(job_row.get("company") or "")
    title = str(job_row.get("title") or "")

    if status != "applied":
        return FollowupInfo(
            state="none",
            applied_at=job_row.get("applied_at"),
            next_followup_at=job_row.get("next_followup_at"),
            followup_count=followup_count,
            days_since_applied=0,
            days_until_followup=None,
            suggested_message="",
        )

    applied_dt = parse_utc(job_row.get("applied_at")) or parse_utc(job_row.get("created_at")) or ref_now
    next_dt = parse_utc(job_row.get("next_followup_at")) or (applied_dt + timedelta(days=7))

    days_since = max(0, int((ref_now - applied_dt).total_seconds() // 86400))
    delta_seconds = (next_dt - ref_now).total_seconds()
    days_until = int(delta_seconds // 86400) if delta_seconds > 0 else 0

    if days_since >= 21 and followup_count >= 2:
        state = "ghosted"
    elif ref_now >= next_dt:
        state = "due"
    else:
        state = "scheduled"

    return FollowupInfo(
        state=state,
        applied_at=format_utc(applied_dt),
        next_followup_at=format_utc(next_dt),
        followup_count=followup_count,
        days_since_applied=days_since,
        days_until_followup=days_until,
        suggested_message=build_followup_message(company, title, followup_count),
    )


def run_liveness_batch(
    db_file: Path | str,
    *,
    limit: int = 15,
    min_score: float = 7.0,
    ttl_hours: int = 12,
    timeout: float = DEFAULT_TIMEOUT,
    resolve_dns: bool = True,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    """Vérifie un lot d'offres selon le pattern Scatter-Gather + Single-Transaction Write.

    1. Lit les offres éligibles et ferme immédiatement la connexion SQLite.
    2. Exécute les appels HTTP dans un ThreadPool borné (`max_workers=6`).
    3. Applique toutes les mises à jour dans une transaction SQLite unique (< 5 ms).
    """
    path = Path(db_file)
    if not path.exists():
        return {"checked": 0, "active": 0, "expired": 0, "uncertain": 0, "results": []}

    capped_limit = max(1, min(int(limit), MAX_BATCH_LIMIT))
    cutoff_iso = format_utc(utc_now() - timedelta(hours=max(0, ttl_hours)))

    # Phase 1 : Lecture rapide et fermeture immédiate de la connexion
    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA busy_timeout = 5000;")
        candidates = con.execute(
            """
            SELECT id, url, company, title
            FROM jobs
            WHERE status NOT IN ('archived')
              AND fit_score >= ?
              AND url IS NOT NULL AND url != ''
              AND (liveness_checked_at IS NULL OR liveness_checked_at < ?)
            ORDER BY fit_score DESC, created_at DESC
            LIMIT ?
            """,
            (min_score, cutoff_iso, capped_limit),
        ).fetchall()
        work_items = [(r["id"], r["url"], r["company"], r["title"]) for r in candidates]
    finally:
        con.close()

    if not work_items:
        return {"checked": 0, "active": 0, "expired": 0, "uncertain": 0, "results": []}

    # Phase 2 : Appels réseau parallèles hors verrou SQLite
    results_map: dict[str, LivenessResult] = {}

    def _worker(item: tuple[str, str, str, str]) -> tuple[str, LivenessResult]:
        jid, jurl, _, _ = item
        res = check_job_liveness(jurl, timeout=timeout, resolve_dns=resolve_dns, fetcher=fetcher)
        return jid, res

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(DEFAULT_BATCH_WORKERS, len(work_items))) as pool:
        for jid, res in pool.map(_worker, work_items):
            results_map[jid] = res

    # Phase 3 : Écriture transactionnelle unique
    updates = [
        (res.status, res.checked_at, jid)
        for jid, res in results_map.items()
    ]
    con = sqlite3.connect(path, timeout=10.0)
    try:
        con.execute("PRAGMA journal_mode = WAL;")
        con.execute("PRAGMA busy_timeout = 5000;")
        con.executemany(
            "UPDATE jobs SET liveness_status = ?, liveness_checked_at = ? WHERE id = ?",
            updates,
        )
        con.commit()
    finally:
        con.close()

    # Recalculer les reposts (au cas où des offres viennent de passer 'expired')
    detect_reposts_in_db(path)

    counts = {"active": 0, "expired": 0, "uncertain": 0}
    detailed = []
    for jid, jurl, comp, ttl in work_items:
        r = results_map[jid]
        counts[r.status] = counts.get(r.status, 0) + 1
        detailed.append(
            {
                "id": jid,
                "company": comp,
                "title": ttl,
                "url": jurl,
                **r.to_dict(),
            }
        )

    return {
        "checked": len(work_items),
        "active": counts.get("active", 0),
        "expired": counts.get("expired", 0),
        "uncertain": counts.get("uncertain", 0),
        "results": detailed,
    }
