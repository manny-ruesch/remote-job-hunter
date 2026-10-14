"""Où vivent les fichiers de l'utilisateur, et un chargeur de .env sans dépendance.

    $RJH_HOME  (sinon un dossier par défaut propre à l'OS) contient :
        profile.yaml   le profil de recherche
        .env           les clés d'API (lisible par vous seul : chmod 600)
        jobs.db        la base SQLite (surchargeable avec $RJH_DB)
"""
from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path
from typing import MutableMapping


def home(environ: MutableMapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get("RJH_HOME"):
        return Path(env["RJH_HOME"]).expanduser()
    if sys.platform.startswith("win"):
        return Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "rjh"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "rjh"
    return Path(env.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "rjh"


def profile_path(h: Path | None = None) -> Path:
    return (h or home()) / "profile.yaml"


def env_path(h: Path | None = None) -> Path:
    return (h or home()) / ".env"


def db_path(h: Path | None = None, environ: MutableMapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env["RJH_DB"]).expanduser() if env.get("RJH_DB") else (h or home()) / "jobs.db"


_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_dotenv(path: Path, environ: MutableMapping[str, str] | None = None, override: bool = False) -> list[str]:
    """Charge KEY=VALUE (guillemets optionnels, commentaires #). N'écrase PAS l'environnement existant.
    Une variable déjà définie ET non vide l'emporte. Retourne les noms chargés. Aucune expansion : pas de surprise."""
    env = os.environ if environ is None else environ
    loaded: list[str] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return loaded
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not _KEY.match(key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if override or not env.get(key):            # une variable VIDE compte comme absente (Compose en passe souvent)
            env[key] = value
            loaded.append(key)
    return loaded


def write_env_key(path: Path, key: str, value: str) -> None:
    """Ajoute ou remplace une clé dans le .env, avec des droits 600 (lisible par vous seul)."""
    if not _KEY.match(key) or "\n" in value or "\r" in value:
        raise ValueError("nom de variable ou valeur invalide")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for ln in lines:
        if ln.strip().split("=", 1)[0].replace("export ", "").strip() == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(ln)
    if not done:
        out.append(f"{key}={value}")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # créé en 600 dès le départ : jamais lisible par d'autres
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def env_permissions_issue(path: Path) -> str | None:
    """POSIX : le .env contient des clés ; s'il est lisible par le groupe ou les autres, on le signale."""
    if os.name != "posix" or not Path(path).exists():
        return None
    mode = stat.S_IMODE(Path(path).stat().st_mode)
    return f"{mode:o}" if mode & 0o077 else None


def mask(secret: str) -> str:
    """Jamais un seul caractère de la clé : seulement sa présence et sa longueur."""
    return f"présente ({len(secret)} caractères)" if secret else "absente"
