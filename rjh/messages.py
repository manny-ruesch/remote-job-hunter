"""Messages FR / EN. Langue : --lang, sinon $RJH_LANG, sinon la locale du système."""
from __future__ import annotations

import locale
import os


def detect_lang(environ=None) -> str:
    env = os.environ if environ is None else environ
    forced = (env.get("RJH_LANG") or "").lower()[:2]
    if forced in ("fr", "en"):
        return forced
    try:
        loc = locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        loc = ""
    loc = loc or env.get("LC_ALL") or env.get("LANG") or ""
    return "fr" if loc.lower().startswith("fr") else "en"


MSG: dict[str, dict[str, str]] = {
    # ---- assistant
    "intro": {"fr": "Bienvenue ! 4 questions pour créer votre profil de recherche (Entrée = valeur proposée).",
              "en": "Welcome! 4 quick questions to create your search profile (Enter = suggested value)."},
    "q1": {"fr": "1/4 · Quel poste cherchez-vous ?\n    Exemples : infirmier · comptable · développeur Python · chef de projet\n    (plusieurs possibles, séparés par des virgules)",
           "en": "1/4 · What job are you looking for?\n    Examples: nurse · accountant · Python developer · project manager\n    (several allowed, separated by commas)"},
    "q1_empty": {"fr": "Indiquez au moins un poste.", "en": "Please enter at least one job title."},
    "q2": {"fr": "2/4 · Comment voulez-vous travailler ?\n  1) 100 % télétravail\n  2) Plutôt télétravail (sur site accepté)\n  3) Sur place",
           "en": "2/4 · How do you want to work?\n  1) Fully remote\n  2) Mostly remote (on-site accepted)\n  3) On-site"},
    "q2_country": {"fr": "   Dans quel pays vivez-vous ? (2 lettres : FR, BE, CA, CH…)", "en": "   Which country do you live in? (2 letters: FR, DE, CA, US…)"},
    "q2_country_bad": {"fr": "Deux lettres, par exemple FR.", "en": "Two letters, for example FR."},
    "q2_city": {"fr": "   Dans quelle ville ?", "en": "   Which city?"},
    "q2_city_opt": {"fr": "   Dans quelle ville ? (Entrée pour passer)", "en": "   Which city? (Enter to skip)"},
    "q3": {"fr": "3/4 · Quel est votre niveau ?\n  1) Débutant (0 à 2 ans)\n  2) Confirmé (3 à 7 ans)\n  3) Expérimenté (8 ans et plus)",
           "en": "3/4 · What is your level?\n  1) Junior (0 to 2 years)\n  2) Mid-level (3 to 7 years)\n  3) Senior (8+ years)"},
    "q4": {"fr": "4/4 · Comment voulez-vous utiliser l'IA pour analyser les offres ?\n  1) J'ai un abonnement ChatGPT, Claude ou Gemini, mais pas de clé API  (recommandé pour démarrer)\n  2) J'ai une clé API Gemini\n  3) Autre : OpenAI, Anthropic, Ollama (modèle local)…",
           "en": "4/4 · How do you want to use AI to analyse job postings?\n  1) I have a ChatGPT, Claude or Gemini subscription but no API key  (best way to start)\n  2) I have a Gemini API key\n  3) Other: OpenAI, Anthropic, Ollama (local model)…"},
    "choice": {"fr": "Votre choix [{default}] : ", "en": "Your choice [{default}]: "},
    "choice_bad": {"fr": "Tapez un des numéros proposés.", "en": "Type one of the listed numbers."},
    "key_prompt": {"fr": "   Collez votre clé (rien ne s'affichera). Entrée pour la définir plus tard : ", "en": "   Paste your key (nothing will show). Press Enter to set it later: "},
    "key_saved": {"fr": "   Clé enregistrée dans {path} (lisible par vous seul). Elle n'est jamais écrite dans le profil.", "en": "   Key saved in {path} (readable by you only). It is never written to the profile."},
    "key_later": {"fr": "   Pensez à définir {var} (dans {path} ou votre environnement) avant de lancer 'rjh doctor'.", "en": "   Remember to set {var} (in {path} or your environment) before running 'rjh doctor'."},
    "model_picked": {"fr": "   Modèle choisi automatiquement : {model}", "en": "   Model picked automatically: {model}"},
    "model_unknown": {"fr": "   Je n'ai pas pu lister les modèles ; 'rjh doctor' vous proposera ceux de votre clé.", "en": "   Could not list models; 'rjh doctor' will suggest the ones available to your key."},
    "q4_provider": {"fr": "   Fournisseur (openai, anthropic, ollama, openrouter, groq, mistral) :", "en": "   Provider (openai, anthropic, ollama, openrouter, groq, mistral):"},
    "q4_provider_bad": {"fr": "Fournisseur inconnu.", "en": "Unknown provider."},
    "q4_model": {"fr": "   Nom du modèle (voir la documentation de votre fournisseur) :", "en": "   Model name (see your provider's documentation):"},
    "exists": {"fr": "Un profil existe déjà : {path}\nLe remplacer ? (une copie .bak est gardée) [o/N] : ", "en": "A profile already exists: {path}\nReplace it? (a .bak copy is kept) [y/N]: "},
    "cancelled": {"fr": "Annulé : rien n'a été modifié.", "en": "Cancelled: nothing was changed."},
    "done": {"fr": "\nProfil créé : {path}", "en": "\nProfile created: {path}"},
    "summary": {"fr": "  Je cherche : {titles}\n  Mode : {mode}{where}\n  Niveau : {level}\n  IA : {ai}", "en": "  Looking for: {titles}\n  Mode: {mode}{where}\n  Level: {level}\n  AI: {ai}"},
    "mode_remote": {"fr": "100 % télétravail", "en": "fully remote"}, "mode_preferred": {"fr": "plutôt télétravail", "en": "mostly remote"}, "mode_onsite": {"fr": "sur place", "en": "on-site"},
    "level_junior": {"fr": "débutant", "en": "junior"}, "level_mid": {"fr": "confirmé", "en": "mid-level"}, "level_senior": {"fr": "expérimenté", "en": "senior"},
    "ai_manual": {"fr": "copier-coller avec votre abonnement (aucune clé)", "en": "copy-paste with your subscription (no key)"},
    "adzuna_hint": {"fr": "\n  ⚠ Pour chercher sur place, il faut une clé Adzuna gratuite (ADZUNA_APP_ID et ADZUNA_APP_KEY), à créer sur developer.adzuna.com.\n    Sans elle, seules les offres en télétravail seront trouvées.",
                    "en": "\n  ⚠ To search on-site you need a free Adzuna key (ADZUNA_APP_ID and ADZUNA_APP_KEY), created at developer.adzuna.com.\n    Without it, only remote jobs will be found."},
    "next": {"fr": "\nProchaines étapes :\n  1. rjh doctor   (vérifie que tout est prêt, ~2 secondes)\n  2. rjh serve    (ouvre le Command Center)\n  Vous pouvez modifier {path} à tout moment.",
             "en": "\nNext steps:\n  1. rjh doctor   (checks everything is ready, ~2 seconds)\n  2. rjh serve    (opens the Command Center)\n  You can edit {path} at any time."},
    "need_args": {"fr": "--yes exige --title et --country.", "en": "--yes requires --title and --country."},
    "key_found": {"fr": "   Clé déjà trouvée dans votre environnement ({var}) : je l'utilise.", "en": "   Key already found in your environment ({var}): using it."},
    # ---- doctor
    "d_title": {"fr": "rjh doctor — vérification de l'installation", "en": "rjh doctor — installation check"},
    "d_python": {"fr": "Python", "en": "Python"}, "d_home": {"fr": "Dossier de données", "en": "Data folder"},
    "d_profile": {"fr": "Profil", "en": "Profile"}, "d_compile": {"fr": "Profil → pré-tri et prompts", "en": "Profile → ranking and prompts"},
    "d_env": {"fr": "Fichier .env", "en": ".env file"}, "d_llm": {"fr": "IA", "en": "AI"},
    "d_source": {"fr": "Source", "en": "Source"}, "d_db": {"fr": "Base SQLite", "en": "SQLite database"},
    "d_ok": {"fr": "Tout est prêt.", "en": "Everything is ready."},
    "d_bad": {"fr": "{n} problème(s) à corriger.", "en": "{n} problem(s) to fix."},
    "d_warn": {"fr": "{n} avertissement(s).", "en": "{n} warning(s)."},
    "d_elapsed": {"fr": "({s:.1f} s)", "en": "({s:.1f} s)"},
    "d_py_old": {"fr": "Python {v} détecté ; 3.11 ou plus est requis", "en": "Python {v} detected; 3.11 or newer is required"},
    "d_py_ok": {"fr": "Python {v}", "en": "Python {v}"},
    "d_home_ok": {"fr": "{path} (accessible en écriture)", "en": "{path} (writable)"},
    "d_home_bad": {"fr": "{path} n'est pas accessible en écriture", "en": "{path} is not writable"},
    "d_home_hint": {"fr": "Changez de dossier avec la variable RJH_HOME.", "en": "Pick another folder with the RJH_HOME variable."},
    "d_noprofile": {"fr": "Aucun profil dans {path}", "en": "No profile in {path}"}, "d_noprofile_hint": {"fr": "Lancez : rjh init", "en": "Run: rjh init"},
    "d_profile_bad": {"fr": "Profil invalide : {err}", "en": "Invalid profile: {err}"}, "d_profile_hint": {"fr": "Corrigez {path} ou relancez : rjh init", "en": "Fix {path} or run: rjh init"},
    "d_profile_ok": {"fr": "{n} poste(s) recherché(s), {mode}", "en": "{n} target job(s), {mode}"},
    "d_compile_ok": {"fr": "le profil se compile correctement", "en": "the profile compiles correctly"},
    "d_env_perm": {"fr": "{path} est lisible par d'autres utilisateurs (droits {mode})", "en": "{path} is readable by other users (mode {mode})"},
    "d_env_hint": {"fr": "Corrigez avec : chmod 600 {path}", "en": "Fix with: chmod 600 {path}"},
    "d_env_ok": {"fr": "{path} (droits corrects)", "en": "{path} (permissions OK)"}, "d_env_none": {"fr": "aucun fichier .env (les clés viennent de l'environnement)", "en": "no .env file (keys come from the environment)"},
    "d_llm_manual": {"fr": "mode manuel : copier-coller avec votre abonnement, aucune clé requise", "en": "manual mode: copy-paste with your subscription, no key needed"},
    "d_llm_nokey": {"fr": "{provider} : variable {var} absente", "en": "{provider}: variable {var} is missing"},
    "d_llm_nokey_hint": {"fr": "Définissez {var} dans {path} ou votre environnement (rjh init sait l'enregistrer).", "en": "Set {var} in {path} or your environment (rjh init can save it)."},
    "d_llm_offline": {"fr": "{provider} : clé {mask}, non vérifiée (mode hors ligne)", "en": "{provider}: key {mask}, not verified (offline mode)"},
    "d_llm_suggest": {"fr": "Modèles disponibles : {models}", "en": "Available models: {models}"},
    "d_src_missing": {"fr": "{name} : variable(s) manquante(s) {vars}", "en": "{name}: missing variable(s) {vars}"},
    "d_src_missing_hint": {"fr": "Créez une clé gratuite chez le fournisseur, puis ajoutez-la à {path}.", "en": "Create a free key with the provider, then add it to {path}."},
    "d_src_unknown": {"fr": "{name} : source inconnue", "en": "{name}: unknown source"},
    "d_src_unknown_hint": {"fr": "Sources disponibles : {names}", "en": "Available sources: {names}"},
    "d_src_none": {"fr": "aucune source activée", "en": "no source enabled"}, "d_src_none_hint": {"fr": "Ajoutez par exemple « remotive » dans sources.enabled.", "en": "Add for instance 'remotive' to sources.enabled."},
    "d_src_ok": {"fr": "{name} : {msg}", "en": "{name}: {msg}"},
    "d_no_boards": {"fr": "{name} : aucune entreprise configurée (sources.boards.{name})", "en": "{name}: no company configured (sources.boards.{name})"},
    "d_db_ok": {"fr": "{path} (écriture OK, mode {mode})", "en": "{path} (writable, {mode} mode)"},
    "d_db_bad": {"fr": "{path} : {err}", "en": "{path}: {err}"},
    "d_db_hint": {"fr": "Vérifiez les droits du dossier, ou définissez RJH_DB.", "en": "Check the folder's permissions, or set RJH_DB."},
    "d_db_nas": {"fr": "Sur un NAS, gardez ce fichier sur un disque local (pas un partage réseau SMB/NFS).", "en": "On a NAS, keep this file on a local disk (not an SMB/NFS network share)."},
    "ping_ok": {"fr": "répond", "en": "reachable"}, "ping_skip": {"fr": "non testée (hors ligne)", "en": "not tested (offline)"},
    "http_auth": {"fr": "clé refusée (HTTP {s})", "en": "key refused (HTTP {s})"}, "http_404": {"fr": "adresse introuvable (HTTP 404)", "en": "address not found (HTTP 404)"},
    "http_429": {"fr": "limite de requêtes atteinte (HTTP 429)", "en": "rate limit reached (HTTP 429)"}, "http_5xx": {"fr": "service indisponible (HTTP {s})", "en": "service unavailable (HTTP {s})"},
    "http_net": {"fr": "connexion impossible", "en": "cannot connect"}, "http_other": {"fr": "erreur HTTP {s}", "en": "HTTP error {s}"},
    # ---- serve
    "s_noauth": {"fr": "Refus de démarrer sur {host} sans jeton : le Command Center serait ouvert à tout le réseau.\nDéfinissez RJH_TOKEN (générez-en un avec : rjh token).",
                 "en": "Refusing to listen on {host} without a token: the Command Center would be open to the whole network.\nSet RJH_TOKEN (generate one with: rjh token)."},
    "s_noprofile": {"fr": "Aucun profil dans {path}. Lancez d'abord : rjh init", "en": "No profile in {path}. Run first: rjh init"},
    "s_noapp": {"fr": "Le Command Center ({app}) est introuvable.\nIndiquez l'application avec --app module:objet (ou la variable RJH_APP).", "en": "The Command Center ({app}) was not found.\nPass the application with --app module:object (or the RJH_APP variable)."},
    "s_noserver": {"fr": "uvicorn n'est pas installé : réinstallez avec 'pipx install --force remote-job-hunter'.", "en": "uvicorn is not installed: reinstall with 'pipx install --force remote-job-hunter'."},
    "s_start": {"fr": "Command Center sur http://{host}:{port}", "en": "Command Center at http://{host}:{port}"},
    # ---- scan
    "sc_start": {"fr": "rjh scan — collecte sur {n_sources} source(s) (seuil HOT : {hot}/10)", "en": "rjh scan — polling {n_sources} source(s) (HOT threshold: {hot}/10)"},
    "sc_collected": {"fr": "  Collectées : {collected} | Nouvelles : {unseen} | Pré-triées : {viable} | Retenues : {selected}", "en": "  Collected: {collected} | Unseen: {unseen} | Viable: {viable} | Selected: {selected}"},
    "sc_nothing": {"fr": "Aucune nouvelle offre à évaluer.", "en": "No new job offers to evaluate."},
    "sc_fallback_manual": {"fr": "  ! Clé {var} absente pour {provider} → bascule automatique vers le Mode Manuel (copier-coller).", "en": "  ! Missing {var} for {provider} → automatic fallback to Manual Mode (copy-paste)."},
    "sc_manual_mode": {"fr": "  Mode Manuel actif : création des lots à copier-coller…", "en": "  Manual Mode active: creating copy-paste batches…"},
    "sc_tickets_created": {"fr": "  ✔ {n} ticket(s) créé(s) ({ids}) — ouvrez le Command Center (rjh serve) pour coller la réponse.", "en": "  ✔ {n} ticket(s) created ({ids}) — open the Command Center (rjh serve) to paste the response."},
    "sc_done": {"fr": "  ✔ Scan terminé : {evaluated} évaluée(s), {hot} HOT, {failed} échec(s).", "en": "  ✔ Scan complete: {evaluated} evaluated, {hot} HOT, {failed} failed."},
}


def t(lang: str, key: str, **kw) -> str:
    entry = MSG[key]
    return (entry.get(lang) or entry["en"]).format(**kw)
