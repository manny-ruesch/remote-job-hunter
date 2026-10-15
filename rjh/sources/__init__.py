"""Registre des sources + collecte multi-sources avec dédoublonnage inter-sources.

    jobs, reports = collect(profile, prefilter=scorer.title_prefilter)
    for r in reports: print(r.source, "OK" if r.ok else r.errors)     # -> bandeau santé du dashboard

Plugins tiers : déposez un .py dans un dossier et appelez load_plugins("mon/dossier") ;
le module doit décorer sa classe avec @register. (Il s'exécute avec VOS droits : ne chargez que du code de confiance.)
"""
from __future__ import annotations

import importlib.util
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Mapping

from ..http import HttpClient
from ..models import Job
from ..profile import Profile
from .base import (BaseJobSource, BoardSource, KeywordSource, Prefilter, RateLimit, SearchQuery,
                   SourceConfigError, SourceResult)

_REGISTRY: dict[str, type[BaseJobSource]] = {}


def register(cls: type[BaseJobSource]) -> type[BaseJobSource]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} : l'attribut `name` est obligatoire")
    _REGISTRY[cls.name] = cls
    return cls


def available_sources() -> dict[str, type[BaseJobSource]]:
    return dict(_REGISTRY)


def create_source(name: str, profile: Profile, *, http: HttpClient | None = None,
                  env: Mapping[str, str] | None = None) -> BaseJobSource:
    if name not in _REGISTRY:
        raise SourceConfigError(f"source inconnue « {name} ». Disponibles : {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name](profile.sources.options.get(name, {}), http=http, env=env)


def load_plugins(directory: "str | Path") -> list[str]:
    loaded = []
    for path in sorted(Path(directory).glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"rjh_plugin_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)                # @register s'exécute à l'import
        loaded.append(path.stem)
    return loaded


def collect(profile: Profile, *, prefilter: Prefilter | None = None, names: list[str] | None = None,
            http: HttpClient | None = None, env: Mapping[str, str] | None = None) -> tuple[list[Job], list[SourceResult]]:
    """Interroge les sources activées EN PARALLÈLE ; une source cassée n'arrête pas les autres."""
    names = names if names is not None else profile.sources.enabled
    reports: list[SourceResult] = []
    runnable: list[BaseJobSource] = []
    for name in names:
        try:
            runnable.append(create_source(name, profile, http=http, env=env))
        except SourceConfigError as e:
            reports.append(SourceResult(name, errors=[str(e)]))

    def run(src: BaseJobSource) -> SourceResult:
        t0 = time.monotonic()
        try:
            return src.fetch(profile, prefilter)
        except Exception as e:                         # filet de sécurité : un plugin buggé ne tue pas le run
            return SourceResult(src.name, errors=[f"erreur inattendue : {type(e).__name__}: {e}"], duration=time.monotonic() - t0)

    with ThreadPoolExecutor(max_workers=max(1, len(runnable))) as pool:
        reports.extend(pool.map(run, runnable))

    # Dédoublonnage ENTRE sources : à clé égale on garde la description la plus riche.
    best: dict[str, Job] = {}
    for rep in reports:
        for job in rep.jobs:
            cur = best.get(job.dedupe_key)
            if cur is None or len(job.description) > len(cur.description):
                best[job.dedupe_key] = job
    return list(best.values()), reports


from . import adzuna, ashby, greenhouse, hn, jobicy, lever, remotive, wttj, wwr  # noqa: E402,F401  (enregistrent leurs classes)
