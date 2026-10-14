# Contribuer

Merci ! Les contributions les plus utiles : **nouvelles sources** ([guide](docs/ADD_A_SOURCE.md)), traductions des messages (`rjh/messages.py`), retours de personnes qui ne sont pas développeuses (l'assistant `rjh init` est-il clair ?).

```bash
git clone https://github.com/manny-ruesch/remote-job-hunter && cd remote-job-hunter
python -m venv .venv && . .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
python -m unittest discover -s tests -t .
```

Principes du projet (une PR qui les enfreint sera refusée gentiment) :
- **Rien de personnel dans le dépôt** : ni clé, ni nom, ni chemin absolu, ni capture de votre vraie recherche.
- **Les secrets ne sortent jamais** : pas dans une URL, une erreur, un log, `profile.yaml` ou la sortie de `rjh doctor`.
- **Une panne est visible** : pas de `except: pass`. Une source qui échoue le dit, et `rjh doctor` dit pourquoi.
- **Jamais de score 0.0 pour masquer un échec** : un échec d'analyse se réessaie, il ne se sauvegarde pas.
- **Le projet ne postule jamais à votre place.**

Code de conduite : [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
