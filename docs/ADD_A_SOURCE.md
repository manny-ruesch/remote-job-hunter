# Ajouter une source en ~30 lignes

Une source est une classe qui sait faire UNE requête et transformer la réponse en `Job`. Le reste (limitation de débit,
nouvelles tentatives, dédoublonnage, diagnostic `rjh doctor`, erreurs visibles) est fourni.

| Votre source… | Héritez de | Vous écrivez |
|---|---|---|
| répond à « je cherche *ce* rôle » (recherche par mots-clés) | `KeywordSource` | `request(q)`, `parse(payload)`, éventuellement `has_more()` |
| liste *toutes* les offres d'une entreprise (ATS) | `BoardSource` | `board_request(slug)`, `parse(payload, company=…)` |

```python
# mon_plugin.py
from rjh.models import Job
from rjh.sources import register
from rjh.sources.base import KeywordSource


@register
class DemoSource(KeywordSource):
    name = "demo"
    max_pages = 1

    def request(self, q):                       # (url, paramètres, en-têtes)
        return "https://demo.example/api/jobs", {"q": q.text}, {}

    def parse(self, payload, **ctx):            # fonction PURE : testable sans réseau
        return [Job("demo", str(x["id"]), x["company"], x["title"], x["url"], x.get("location", ""))
                for x in payload["items"]]
```

Activez-la : `sources.enabled: [demo]` dans `profile.yaml`, et chargez le dossier avec `load_plugins("mon/dossier")`.

## Règles pour une contribution
1. **Un test sur une réponse enregistrée** : copiez une vraie réponse de l'API (sans donnée personnelle) dans `tests/fixtures/` et testez `parse()`.
2. **Aucun secret dans l'URL d'une erreur** : passez les clés en paramètres (`params=`) ou en-têtes ; le client masque la query string.
3. **Respectez le site** : limitez le débit (`rate_limit`), identifiez-vous (User-Agent fourni), lisez ses conditions d'usage.
   Les scrapers furtifs de sites qui l'interdisent ne seront pas acceptés dans le cœur du projet.
4. Déclarez les variables d'environnement requises dans `requires_env` : `rjh doctor` les vérifie pour l'utilisateur.
