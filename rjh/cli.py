"""Commande `rjh` : init · doctor · scan · serve · token · paths."""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable, MutableMapping, Sequence

from . import __version__
from .doctor import render, run_doctor, to_json, unicode_supported
from .messages import detect_lang, t
from .paths import db_path, env_path, home as default_home, load_dotenv, profile_path
from .profile import ProfileError
from .scanner import run_scan
from .wizard import IO, run_wizard

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DEFAULT_APP = "rjh.web.server:app"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rjh", description="Remote Job Hunter : n'importe quelle recherche d'emploi, n'importe quel LLM.")
    p.add_argument("--version", action="version", version=f"rjh {__version__}")
    p.add_argument("--lang", choices=("fr", "en"), help="langue des messages (sinon : celle du système)")
    p.add_argument("--home", help="dossier des données (sinon : $RJH_HOME ou le dossier par défaut de votre système)")
    sub = p.add_subparsers(dest="cmd")

    i = sub.add_parser("init", help="crée votre profil en 4 questions")
    i.add_argument("--force", action="store_true", help="remplace un profil existant (une copie .bak est gardée)")
    i.add_argument("--yes", action="store_true", help="non interactif : exige --title et --country (Docker, CI)")
    i.add_argument("--title"), i.add_argument("--country"), i.add_argument("--city")
    i.add_argument("--mode", choices=("remote", "preferred", "onsite"))
    i.add_argument("--level", choices=("junior", "mid", "senior"))
    i.add_argument("--ai", help="manual (défaut), gemini, openai, anthropic, ollama…")
    i.add_argument("--model")

    d = sub.add_parser("doctor", help="vérifie que tout est prêt")
    d.add_argument("--offline", action="store_true", help="aucun appel réseau")
    d.add_argument("--json", action="store_true", help="sortie lisible par une machine")
    d.add_argument("--timeout", type=float, default=4.0, help="délai par contrôle réseau, en secondes (défaut 4)")

    s = sub.add_parser("serve", help="lance le Command Center")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5070)
    s.add_argument("--app", help=f"module:objet de l'application ASGI (défaut : $RJH_APP ou {DEFAULT_APP})")

    sc = sub.add_parser("scan", help="collecte, pré-trie et analyse les nouvelles offres")
    sc.add_argument("--max-jobs", type=int, help="nombre maximal d'offres à évaluer (défaut : llm.max_jobs_per_run)")
    sc.add_argument("--manual", action="store_true", help="force la création de tickets Mode Manuel (copier-coller)")
    sc.add_argument("--no-notify", action="store_true", help="désactive les alertes Discord et le digest Markdown")
    sc.add_argument("--dry-run", action="store_true", help="collecte et pré-trie sans appeler le LLM ni écrire en base")
    sc.add_argument("--source", action="append", dest="sources", help="limite la collecte à cette source (répétable)")
    sc.add_argument("--json", action="store_true", help="sortie JSON du rapport de scan")

    sub.add_parser("token", help="génère un jeton d'accès aléatoire pour RJH_TOKEN")
    sub.add_parser("paths", help="affiche où sont rangés vos fichiers")
    return p


def _harden_streams() -> None:
    """Une console Windows en cp1252 plante sur un caractère exotique : on remplace au lieu de planter."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")           # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def cmd_init(args, io: IO, lang: str, home: Path, env: MutableMapping[str, str]) -> int:
    preset = None
    if args.yes:
        preset = {"yes": True, "title": args.title, "country": args.country, "city": args.city, "mode": args.mode,
                  "level": args.level, "ai": args.ai, "model": args.model or ""}
    try:
        path = run_wizard(io, lang=lang, home=home, force=args.force, preset=preset, environ=env)
    except (EOFError, KeyboardInterrupt):
        io.out("\n" + t(lang, "cancelled"))
        return 130
    except ValueError as e:
        io.out(str(e))
        return 2
    return 0 if path else 1


def cmd_doctor(args, out: Callable[[str], None], lang: str, home: Path, env: MutableMapping[str, str],
               http_factory=None) -> int:
    t0 = time.monotonic()
    checks = run_doctor(home, lang=lang, online=not args.offline, timeout=args.timeout, environ=env, http_factory=http_factory)
    elapsed = time.monotonic() - t0
    out(to_json(checks, elapsed) if args.json else render(checks, lang, unicode_ok=unicode_supported(), elapsed=elapsed))
    return 1 if any(c.status == "fail" for c in checks) else 0


def cmd_serve(args, out: Callable[[str], None], lang: str, home: Path, env: MutableMapping[str, str],
              runner: Callable[..., Any] | None = None) -> int:
    if args.host not in LOOPBACK and not env.get("RJH_TOKEN"):
        out(t(lang, "s_noauth", host=args.host))              # le dashboard ne doit jamais s'ouvrir au réseau sans jeton
        return 2
    if not profile_path(home).exists():
        out(t(lang, "s_noprofile", path=profile_path(home)))
        return 2
    app = args.app or env.get("RJH_APP") or DEFAULT_APP
    module, _, attr = app.partition(":")
    if not _importable(module):
        out(t(lang, "s_noapp", app=app))
        return 2
    env["RJH_HOME"] = str(home)                               # l'application lit ses fichiers au même endroit que la CLI
    if runner is None:
        try:
            import uvicorn
        except ImportError:
            out(t(lang, "s_noserver"))
            return 2
        runner = uvicorn.run
    out(t(lang, "s_start", host=args.host, port=args.port))
    runner(app, host=args.host, port=args.port)
    return 0


def cmd_scan(args, out: Callable[[str], None], lang: str, home: Path, env: MutableMapping[str, str],
             http_factory=None) -> int:
    if not profile_path(home).exists():
        out(t(lang, "s_noprofile", path=profile_path(home)))
        return 2
    try:
        report = run_scan(
            home,
            lang=lang,
            max_jobs=args.max_jobs,
            manual_override=args.manual,
            notify=not args.no_notify,
            dry_run=args.dry_run,
            sources=args.sources,
            environ=env,
            http=http_factory() if http_factory else None,
            out=(lambda _m: None) if args.json else out,
        )
    except ProfileError as e:
        out(t(lang, "d_profile_bad", err=e))
        return 2
    if args.json:
        out(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def main(argv: Sequence[str] | None = None, *, io: IO | None = None, environ: MutableMapping[str, str] | None = None,
         http_factory=None, runner=None) -> int:
    env = os.environ if environ is None else environ
    if environ is None:
        _harden_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    io = io or IO()
    lang = args.lang or detect_lang(env)
    home = Path(args.home).expanduser() if args.home else default_home(env)
    load_dotenv(env_path(home), env)                          # les clés du .env, sans écraser l'environnement existant

    if args.cmd == "init":
        return cmd_init(args, io, lang, home, env)
    if args.cmd == "doctor":
        return cmd_doctor(args, io.out, lang, home, env, http_factory)
    if args.cmd == "serve":
        return cmd_serve(args, io.out, lang, home, env, runner)
    if args.cmd == "scan":
        return cmd_scan(args, io.out, lang, home, env, http_factory)
    if args.cmd == "token":
        io.out(secrets.token_urlsafe(32))
        return 0
    if args.cmd == "paths":
        for label, path in (("home", home), ("profile", profile_path(home)), ("env", env_path(home)), ("db", db_path(home, env))):
            io.out(f"{label:8}{path}")
        return 0
    io.out(parser.format_help().rstrip())
    if not profile_path(home).exists():
        io.out("\n" + t(lang, "d_noprofile_hint"))
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    sys.exit(main())
