"""Client HTTP minimal (stdlib uniquement), partagé par les sources ET les fournisseurs LLM.

  * retries avec backoff exponentiel + gigue, respect de Retry-After (429/5xx)
  * limitation de débit thread-safe (RateLimiter)
  * transport injectable -> tests sans réseau
  * les messages d'erreur ne contiennent JAMAIS la query string (clés d'API, app_key Adzuna…)
"""
from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


def safe_url(url: str) -> str:
    """URL sans query string ni fragment : seule forme autorisée dans logs et erreurs."""
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} sur {safe_url(url)} : {body[:300]}")
        self.status = status
        self.body = body
        self.url = safe_url(url)


# transport(method, url, headers, body, timeout) -> (status, headers, body_bytes)
Transport = Callable[[str, str, dict, "bytes | None", float], "tuple[int, dict, bytes]"]


def urllib_transport(method: str, url: str, headers: dict, body: "bytes | None", timeout: float):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (schéma http(s) validé en amont)
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


class RateLimiter:
    """Espace les requêtes d'au moins `min_interval` secondes, même entre threads."""

    def __init__(self, min_interval: float = 0.0, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = min_interval
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.min_interval
        if start > now:
            self._sleep(start - now)


RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass
class HttpClient:
    user_agent: str = "rjh/0.1 (open-source job hunter)"
    timeout: float = 30.0
    max_retries: int = 3
    backoff_base: float = 1.0
    max_backoff: float = 30.0
    limiter: RateLimiter = field(default_factory=RateLimiter)
    transport: Transport = urllib_transport
    sleep: Callable[[float], None] = time.sleep
    requests_made: int = 0

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> Any:
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError(f"schéma d'URL refusé : {safe_url(url)}")
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        hdrs = {"User-Agent": self.user_agent, "Accept": "application/json", **(headers or {})}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            self.requests_made += 1
            try:
                status, resp_headers, raw = self.transport(method, url, hdrs, body, timeout or self.timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    self.sleep(self._delay(attempt, None))
                    continue
                raise HttpError(0, f"connexion impossible ({type(e).__name__})", url) from e
            resp_headers = {k.lower(): v for k, v in resp_headers.items()}
            if 200 <= status < 300:
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except ValueError as e:
                    raise HttpError(status, "réponse non JSON", url) from e
            if status in RETRY_STATUS and attempt < self.max_retries:
                self.sleep(self._delay(attempt, resp_headers.get("retry-after")))
                continue
            raise HttpError(status, raw.decode("utf-8", "replace"), url)
        raise HttpError(0, str(last_exc), url)  # pragma: no cover

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.max_backoff)
            except ValueError:
                pass
        return min(self.max_backoff, self.backoff_base * (2 ** attempt)) * (0.5 + random.random() / 2)

    def get_json(self, url: str, **kw) -> Any:
        return self.request("GET", url, **kw)

    def post_json(self, url: str, **kw) -> Any:
        return self.request("POST", url, **kw)


def short_http_reason(e: HttpError) -> tuple[str, int]:
    """(catégorie, statut) : de quoi afficher une erreur utile SANS jamais citer le corps de la réponse
    (un serveur peut y refléter une clé d'API)."""
    st = e.status
    if st in (401, 403):
        return "auth", st
    if st == 404:
        return "404", st
    if st == 429:
        return "429", st
    if 500 <= st < 600:
        return "5xx", st
    if st == 0:
        return "net", 0
    return "other", st
