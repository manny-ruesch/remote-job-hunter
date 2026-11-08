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
from .discovery import (
    discover_boards_from_db,
    import_linkedin_connections_csv,
    match_warm_intros_for_jobs,
    probe_company_ats,
    sync_discovered_boards_to_profile,
)
from .doctor import render, run_doctor, to_json, unicode_supported
from .market_radar import add_skill_to_profile, analyze_market_radar
from .messages import detect_lang, t
from .paths import db_path, env_path, home as default_home, load_dotenv, profile_path
from .profile import ProfileError, load_profile
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

    mr = sub.add_parser("radar", help="analyse le marché des compétences et audite vos filtres")
    mr.add_argument("--min-score", type=float, default=0.0, help="score minimal pour les offres analysées")
    mr.add_argument("--limit", type=int, default=500, help="nombre maximal d'offres à analyser (défaut 500)")
    mr.add_argument("--add-skill", help="ajoute immédiatement cette compétence dans profile.yaml")
    mr.add_argument("--weight", type=float, default=6.0, help="poids de boost lors d'un --add-skill (défaut 6)")
    mr.add_argument("--json", action="store_true", help="sortie JSON du radar marché")

    disc = sub.add_parser("discover", help="auto-découvre des boards ATS directs et synchronise profile.yaml")
    disc.add_argument("--probe", action="append", default=[], help="entreprise ou slug à sonder sur les 5 ATS publics (répétable)")
    disc.add_argument("--sync", action="store_true", help="ajoute automatiquement les nouveaux boards découverts dans profile.yaml")
    disc.add_argument("--json", action="store_true", help="sortie JSON")

    ref = sub.add_parser("referrals", help="importe Connections.csv LinkedIn et détecte les cooptations (Warm Intros)")
    ref.add_argument("--import-csv", help="chemin vers votre fichier Connections.csv exporté de LinkedIn")
    ref.add_argument("--json", action="store_true", help="sortie JSON")

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


def cmd_radar(args, out: Callable[[str], None], lang: str, home: Path, env: MutableMapping[str, str]) -> int:
    prof_file = profile_path(home)
    if not prof_file.exists():
        out(t(lang, "s_noprofile", path=prof_file))
        return 2
    cand_json = home / "candidate_profile.json"
    if args.add_skill:
        try:
            res = add_skill_to_profile(
                args.add_skill,
                prof_file,
                cand_json if cand_json.exists() else None,
                weight=args.weight,
            )
            if args.json:
                out(json.dumps(res, ensure_ascii=False, indent=2))
            else:
                out(f"+ Compétence ajoutée au profil : {res['skill']} (boost +{res['weight']:g})")
            return 0
        except (ValueError, ProfileError) as e:
            out(str(e))
            return 2

    try:
        prof = load_profile(prof_file)
    except ProfileError as e:
        out(t(lang, "d_profile_bad", err=e))
        return 2

    report = analyze_market_radar(
        db_path(home, env),
        prof,
        cand_json if cand_json.exists() else None,
        min_score=args.min_score,
        limit=args.limit,
    )
    if args.json:
        out(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0

    out(f"=== Market Skill Gap & Filter Audit ({report.deduped_jobs_count} offres uniques, {report.viable_jobs_count} retenues) ===")
    out(f"Couverture du profil sur le Top 10 marché : {report.profile_coverage_pct:.1f}%")
    if report.missing_skills:
        out("\nCompétences demandées absentes de votre profil :")
        for it in report.missing_skills[:8]:
            out(f"  - {it.skill:20} ({it.category}) : {it.job_count} offres ({it.pct_of_jobs:.1f}%) [{it.status}]")
    if report.covered_skills:
        out("\nCompétences fortes de votre profil dans le marché :")
        for it in report.covered_skills[:8]:
            out(f"  + {it.skill:20} ({it.category}) : {it.job_count} offres ({it.pct_of_jobs:.1f}%)")
    if report.gate_stats:
        out("\nAudit des filtres (Gates) :")
        for gs in report.gate_stats[:6]:
            out(f"  * {gs.gate_id:18} ({gs.action}) : {gs.rejected_count} rejet(s), {gs.penalized_count} pénalité(s) ({gs.pct_of_corpus:.1f}%)")
    for rec in report.profile_recommendations:
        out(f"\n-> {rec}")
    return 0


def cmd_discover(args, out: Callable[[str], None], lang: str, home: Path, env: MutableMapping[str, str]) -> int:
    prof_file = profile_path(home)
    if not prof_file.exists():
        prof_file = Path("profile.yaml")
    prof = None
    if prof_file.exists():
        try:
            prof = load_profile(prof_file)
        except ProfileError:
            prof = None

    boards = discover_boards_from_db(db_path(home, env), prof)
    for company in (args.probe or []):
        for b in probe_company_ats(company, profile=prof):
            if not any(x.provider == b.provider and x.slug == b.slug for x in boards):
                boards.append(b)

    sync_res = None
    if args.sync and prof_file.exists():
        new_boards = [b for b in boards if not b.already_configured]
        sync_res = sync_discovered_boards_to_profile(new_boards, prof_file)

    if args.json:
        out(json.dumps({"boards": [b.to_dict() for b in boards], "sync": sync_res}, ensure_ascii=False, indent=2))
        return 0

    out(f"=== Reverse-ATS Auto-Discovery ({len(boards)} board(s) détecté(s)) ===")
    for b in boards:
        tag = "déjà configuré" if b.already_configured else "NOUVEAU"
        out(f"  [{b.provider:15}] {b.slug:22} ({b.job_count} offre(s)) - {tag}")
    if sync_res:
        out(f"\nSynchronisés dans {prof_file.name} : {len(sync_res['added'])} ajouté(s).")
    return 0


def cmd_referrals(args, out: Callable[[str], None], lang: str, home: Path, env: MutableMapping[str, str]) -> int:
    dbf = db_path(home, env)
    import_res = None
    if args.import_csv:
        csv_file = Path(args.import_csv).expanduser()
        if not csv_file.exists():
            out(f"Fichier introuvable : {csv_file}")
            return 2
        import_res = import_linkedin_connections_csv(csv_file.read_bytes(), dbf)

    prof_file = profile_path(home)
    if not prof_file.exists():
        prof_file = Path("profile.yaml")
    cand_name = ""
    if prof_file.exists():
        try:
            cand_name = load_profile(prof_file).identity.name
        except Exception:
            cand_name = ""

    match_res = match_warm_intros_for_jobs(dbf, candidate_name=cand_name, lang=lang)
    if args.json:
        out(json.dumps({"import": import_res, "matches": match_res}, ensure_ascii=False, indent=2))
        return 0

    if import_res:
        out(f"Import LinkedIn : {import_res['imported']} contact(s) importé(s) ({import_res['total_in_db']} au total).")
    out(f"=== Warm Intros & Cooptations ({match_res['matched_jobs_count']} offre(s) avec contact interne) ===")
    for mj in match_res["matched_jobs"][:10]:
        out(f"  * {mj['company']} — {mj['title']} ({mj['contacts_count']} contact(s))")
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
    if args.cmd == "radar":
        return cmd_radar(args, io.out, lang, home, env)
    if args.cmd == "discover":
        return cmd_discover(args, io.out, lang, home, env)
    if args.cmd == "referrals":
        return cmd_referrals(args, io.out, lang, home, env)
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
