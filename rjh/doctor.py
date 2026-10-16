"""`rjh doctor` : « tout est prêt ? » en quelques secondes.

Les contrôles réseau (IA, une requête par source) partent EN PARALLÈLE avec un délai court (4 s par défaut,
sans nouvelle tentative) : un diagnostic rapide vaut mieux qu'un diagnostic patient. Chaque problème vient avec
une action concrète. Rien d'écrit n'est jamais affiché de vos secrets : seulement leur présence et leur longueur.

Code de sortie : 0 s'il n'y a aucun échec (les avertissements sont tolérés), 1 sinon -> utilisable en CI / HEALTHCHECK.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .analysis import build_triage_prompt
from .heuristic import ProfileScorer
from .http import HttpClient, RateLimiter
from .llm import LLMAuthError, LLMError, create_provider
from .messages import t
from .models import Job
from .paths import db_path, env_path, env_permissions_issue, mask, profile_path
from .profile import Profile, ProfileError, load_profile, validate_profile
from .sources import SourceConfigError, available_sources, create_source


@dataclass
class Check:
    label: str
    status: str            # ok | warn | fail
    message: str
    hint: str = ""


def _http_message(lang: str, code: str) -> str:
    if code == "auth":
        return t(lang, "http_auth", s="401/403")
    if code in ("404", "429", "net"):
        return t(lang, {"404": "http_404", "429": "http_429", "net": "http_net"}[code])
    if code == "5xx":
        return t(lang, "http_5xx", s="5xx")
    return t(lang, "http_other", s=code.split(":")[-1])


# ------------------------------------------------------------------ contrôles --------------

def check_python(lang: str) -> Check:
    v = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 11):
        return Check(t(lang, "d_python"), "fail", t(lang, "d_py_old", v=v))
    return Check(t(lang, "d_python"), "ok", t(lang, "d_py_ok", v=v))


def check_home(home: Path, lang: str) -> Check:
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".rjh-write-test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError:
        return Check(t(lang, "d_home"), "fail", t(lang, "d_home_bad", path=home), t(lang, "d_home_hint"))
    return Check(t(lang, "d_home"), "ok", t(lang, "d_home_ok", path=home))


def check_profile(home: Path, lang: str) -> tuple[list[Check], Profile | None]:
    path = profile_path(home)
    label = t(lang, "d_profile")
    if not path.exists():
        return [Check(label, "fail", t(lang, "d_noprofile", path=path), t(lang, "d_noprofile_hint"))], None
    try:
        profile = load_profile(path)
    except ProfileError as e:
        return [Check(label, "fail", t(lang, "d_profile_bad", err=e), t(lang, "d_profile_hint", path=path))], None
    mode = {"required": "remote", "preferred": "preferred", "any": "onsite"}[profile.search.work_mode.remote]
    checks = [Check(label, "ok", t(lang, "d_profile_ok", n=len(profile.search.titles.primary), mode=t(lang, f"mode_{mode}")))]
    checks += [Check(label, "warn", w) for w in validate_profile(profile)]
    try:                                         # le profil sait-il VRAIMENT alimenter le pré-tri et les prompts ?
        scorer = ProfileScorer(profile)
        scorer.score(Job("doctor", "1", "Acme", "Test", "https://example.org", "Remote", "test"))
        build_triage_prompt(profile, [("j1", Job("doctor", "1", "Acme", "Test", "https://example.org", "Remote", "test"))])
        checks.append(Check(t(lang, "d_compile"), "ok", t(lang, "d_compile_ok")))
    except Exception as e:                       # noqa: BLE001 : on veut tout attraper et le dire
        checks.append(Check(t(lang, "d_compile"), "fail", f"{type(e).__name__}: {e}"))
    return checks, profile


def check_envfile(home: Path, lang: str) -> Check:
    path = env_path(home)
    if not path.exists():
        return Check(t(lang, "d_env"), "ok", t(lang, "d_env_none"))
    bad = env_permissions_issue(path)
    if bad:
        return Check(t(lang, "d_env"), "warn", t(lang, "d_env_perm", path=path, mode=bad), t(lang, "d_env_hint", path=path))
    return Check(t(lang, "d_env"), "ok", t(lang, "d_env_ok", path=path))


def check_llm(profile: Profile, env: Mapping[str, str], home: Path, lang: str, online: bool, mk_http: Callable[[], HttpClient]) -> Check:
    label = t(lang, "d_llm")
    try:
        provider = create_provider(profile.llm, env=env, http=mk_http() if online else None)
    except LLMAuthError:
        var = profile.llm.api_key_env or "?"
        return Check(label, "fail", t(lang, "d_llm_nokey", provider=profile.llm.provider, var=var),
                     t(lang, "d_llm_nokey_hint", var=var, path=env_path(home)))
    except LLMError as e:
        return Check(label, "fail", str(e))
    if provider.interactive:
        return Check(label, "ok", t(lang, "d_llm_manual"))
    if not online:
        return Check(label, "ok", t(lang, "d_llm_offline", provider=provider.name, mask=mask(provider.cfg.api_key)))
    res = provider.check()
    hint = t(lang, "d_llm_suggest", models=", ".join(res.suggestions)) if res.suggestions else ""
    return Check(label, "ok" if res.ok else "fail", res.message, "" if res.ok else hint)


def check_source(name: str, profile: Profile, env: Mapping[str, str], home: Path, lang: str, online: bool,
                 mk_http: Callable[[], HttpClient]) -> Check:
    label = t(lang, "d_source")
    registry = available_sources()
    if name not in registry:
        return Check(label, "fail", t(lang, "d_src_unknown", name=name), t(lang, "d_src_unknown_hint", names=", ".join(sorted(registry))))
    try:
        src = create_source(name, profile, http=mk_http(), env=env)
    except SourceConfigError:
        missing = ", ".join(v for v in registry[name].requires_env if not env.get(v))
        return Check(label, "fail", t(lang, "d_src_missing", name=name, vars=missing), t(lang, "d_src_missing_hint", path=env_path(home)))
    if not online:
        return Check(label, "ok", t(lang, "d_src_ok", name=name, msg=t(lang, "ping_skip")))
    level, code = src.ping(profile)
    if code == "no_boards":
        return Check(label, "warn", t(lang, "d_no_boards", name=name))
    msg = t(lang, "ping_ok") if code == "ok" else _http_message(lang, code)
    return Check(label, "ok" if level == "ok" else "fail", t(lang, "d_src_ok", name=name, msg=msg))


def check_db(path: Path, lang: str) -> Check:
    label = t(lang, "d_db")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=2)
        try:
            con.execute("BEGIN IMMEDIATE")           # prend le verrou d'écriture : prouve qu'on peut écrire, sans rien modifier
            con.rollback()
            quick = con.execute("PRAGMA quick_check").fetchone()[0]
            mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            con.close()
    except (sqlite3.Error, OSError) as e:
        return Check(label, "fail", t(lang, "d_db_bad", path=path, err=type(e).__name__ + (f": {e}" if str(e) else "")), t(lang, "d_db_hint"))
    if quick != "ok":
        return Check(label, "fail", t(lang, "d_db_bad", path=path, err="quick_check = " + str(quick)), t(lang, "d_db_hint"))
    if str(path).startswith(("\\\\", "//")):
        return Check(label, "warn", t(lang, "d_db_ok", path=path, mode=mode), t(lang, "d_db_nas"))
    return Check(label, "ok", t(lang, "d_db_ok", path=path, mode=mode))


# ------------------------------------------------------------------ orchestration ----------

def run_doctor(home: Path, *, lang: str, online: bool = True, timeout: float = 4.0, environ: Mapping[str, str] | None = None,
               http_factory: Callable[[], HttpClient] | None = None) -> list[Check]:
    env = os.environ if environ is None else environ
    mk_http = http_factory or (lambda: HttpClient(timeout=timeout, max_retries=0, limiter=RateLimiter(0)))
    checks: list[Check] = [check_python(lang), check_home(home, lang)]
    profile_checks, profile = check_profile(home, lang)
    checks += profile_checks
    checks.append(check_envfile(home, lang))
    deferred: list[Callable[[], Check]] = []
    if profile is not None:
        deferred.append(lambda: check_llm(profile, env, home, lang, online, mk_http))
        if not profile.sources.enabled:
            checks.append(Check(t(lang, "d_source"), "fail", t(lang, "d_src_none"), t(lang, "d_src_none_hint")))
        for name in profile.sources.enabled:
            deferred.append(lambda n=name: check_source(n, profile, env, home, lang, online, mk_http))
    deferred.append(lambda: check_db(db_path(home, env), lang))
    with ThreadPoolExecutor(max_workers=max(1, len(deferred))) as pool:      # réseau en parallèle : le plus lent fixe la durée
        futures = [pool.submit(fn) for fn in deferred]
        for f in futures:
            try:
                checks.append(f.result())
            except Exception as e:                # noqa: BLE001 : un contrôle qui plante est un échec signalé, pas un crash
                checks.append(Check("?", "fail", f"{type(e).__name__}: {e}"))
    return checks


def render(checks: list[Check], lang: str, *, unicode_ok: bool, elapsed: float) -> str:
    sym = {"ok": "✔", "warn": "!", "fail": "✘"} if unicode_ok else {"ok": "[OK]", "warn": "[!!]", "fail": "[XX]"}
    arrow = "→" if unicode_ok else "->"
    lines = [t(lang, "d_title"), ""]
    for c in checks:
        lines.append(f"  {sym[c.status]} {c.label} : {c.message}" if lang == "fr" else f"  {sym[c.status]} {c.label}: {c.message}")
        if c.hint:
            lines.append(f"      {arrow} {c.hint}")
    fails = sum(c.status == "fail" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    lines.append("")
    summary = t(lang, "d_bad", n=fails) if fails else (t(lang, "d_ok") + (" " + t(lang, "d_warn", n=warns) if warns else ""))
    lines.append(f"{summary} {t(lang, 'd_elapsed', s=elapsed)}")
    return "\n".join(lines)


def to_json(checks: list[Check], elapsed: float) -> str:
    return json.dumps({"ok": all(c.status != "fail" for c in checks), "elapsed_s": round(elapsed, 2),
                       "checks": [asdict(c) for c in checks]}, ensure_ascii=False, indent=2)


def unicode_supported(stream: Any = None) -> bool:
    enc = (getattr(stream or sys.stdout, "encoding", None) or "").lower().replace("-", "").replace("_", "")
    return enc in ("utf8", "utf16", "utf32")
