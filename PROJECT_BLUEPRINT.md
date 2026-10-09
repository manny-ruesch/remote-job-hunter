# 🎯 REMOTE JOB HUNTER — SPÉCIFICATIONS TECHNIQUES & DIRECTIVES DE CONCEPTION

Ce document rassemble l'intégralité des directives architecturales, des règles de filtrage métier et le rôle de chaque composant de la plateforme **Remote Job Hunter (Arachnid Engine v2.4 OSS)**.

---

## 🏛️ 1. DIRECTIVES & PRINCIPES FONDATEURS

### Directive 1 : 100 % Gratuit & Zéro Paywall
* Aucune dépendance à des APIs payantes intermédiaires ou des agrégateurs d'emploi à abonnement.
* Utilisation exclusive des flux d'ingestion directs :
  - APIs directes des ATS d'entreprises : **Ashby**, **Greenhouse**, **Lever**.
  - APIs publiques ouvertes : **Hacker News (Firebase)**, **Welcome to the Jungle (Algolia)**, **Remotive REST**, **Jobicy REST**, **We Work Remotely (RSS)**.
  - Scraper furtif **Scrapling** (StealthyFetcher avec curl-cffi) pour LinkedIn Guest sans compte.

### Directive 2 : Ciblage Stratégique & Règle Anti-Seniorité
* **Zone de force :** Profils *AI Automation Builder*, *Solutions Engineer*, *RevOps Engineer*, *Growth Ops*.
* **Bannissement des Frontier Labs :** Exclusion stricte des postes chez OpenAI, Anthropic et Cohere (profils PhD / Core Distributed Systems avec ratio de conversion quasi-nul).
* **Filtre de Séniorité :** Détection et pénalisation automatique (-50 pts au ranking) pour tout rôle mentionnant `Senior`, `Staff`, `Principal`, `Lead`, `Architect` ou exigeant plus de 5 ans d'expérience.
* **Priorité absolue :** Rôles Junior à Mid (0 à 3 ans d'expérience) au sein de startups Seed / Series A-B et PME en pleine modernisation.

### Directive 3 : Pré-Filtrage Lexical Heuristique Local (< 10 ms)
* Évaluation préliminaire ultra-rapide des ~1 500 offres scannées sans consommer le quota de tokens de Gemini.
* Bonus (+30 pts) sur les mots-clés : `n8n`, `make.com`, `zapier`, `python`, `workflow`, `automation`, `revops`, `hubspot`, `api`.
* Malus (-50 pts) sur les technologies incompatibles : `c++`, `kernel`, `firmware`, `android`, `ios`, `swift`, `embedded`.
* Seul le Top 15 des offres les plus denses par cycle est soumis à l'évaluation qualitative de l'IA.

### Directive 4 : Packaging Candidature Augmentée (Gemini)
Pour chaque offre qualifiée avec un score $\ge 7.5/10$, le système génère automatiquement :
1. **Pitch d'accroche personnalisé (120 mots) :** Axé sur des preuves chiffrées en production (32+ workflows n8n, 350h/mois économisées, intégrations REST/CRM).
2. **Architecture Proof-of-Work (PoW) :** Diagnostic du goulot opérationnel de l'entreprise, plan d'implémentation en 3 phases et diagramme de flux **Mermaid.js**.
3. **Cold Outreach Décideur :** Message direct LinkedIn de 50 mots sans flatterie, avec lien direct vers le profil cible (Head of Ops, VP Engineering).

### Directive 5 : Auto-Fill Assisté & Non-Destructif (Playwright)
* Le navigateur Chromium s'ouvre en mode visible (*headful*).
* Pré-remplissage des champs standards (Prénom, Nom, Email, Téléphone, LinkedIn, GitHub, Portfolio, Pitch) et attachement automatique du CV PDF local.
* **Règle de sécurité :** Le script NE soumet JAMAIS le formulaire automatiquement. Il laisse la main à l'utilisateur pour vérifier et cliquer sur « Envoyer » en 2 secondes.

### Directive 6 : Cover Letters Exécutives Compilées en PDF
* Rédaction en prose soignée de style éditorial (sans ton générique IA ni esthétique "hacker").
* Compilation automatique d'un PDF A4 mono-page directement sur le Bureau Windows via Microsoft Edge headless (`--print-to-pdf`).

### Directive 7 : Esthétique Arachnid & Expérience Utilisateur
* Interface sombre tactique (`#030303`), typographie `JetBrains Mono`.
* Mascotte **Noir Webling** en haut à gauche issue de `petdex`.
* Toile interactive Canvas 60 FPS avec modèle physique masse-ressort réagissant à la souris.
* Fil de soie dynamique tracé entre l'araignée et la carte de job survolée.
* Onde de choc radar concentrique balayant l'écran au déclenchement du scan.
* Port serveur fixé à `5070` (le port 5060 étant bloqué par Chromium sous `ERR_UNSAFE_PORT`).

---

## 📂 2. ARBORESCENCE DU PROJET

```
remote_job_hunter/
├── dashboard/
│   ├── dashboard_server.py      # Serveur API FastAPI (port 5070)
│   ├── run_dashboard.bat        # Lanceur Windows pour le Command Center
│   ├── start_dashboard_silent.vbs # Démarrage silencieux en arrière-plan
│   └── static/
│       ├── index.html           # Interface Web complète (Canvas 60fps, GSAP)
│       └── pet_anim_192.gif     # Mascotte animée Noir Webling
├── analyzer.py                  # Évaluation IA Gemini, Pitch, PoW et Cold Outreach
├── auto_fill_engine.py          # Automatisation de pré-remplissage Playwright
├── config.py                    # Chargement dynamique .env & configuration
├── cover_letter_generator.py    # Générateur et compilateur de PDF Cover Letter
├── db.py                        # Gestion SQLite & suivi CRM des candidatures
├── fetcher.py                   # Moissonneur multi-sources (47 plateformes en parallèle)
├── heuristic_scorer.py          # Filtre lexical heuristique sub-10ms
├── notifier.py                  # Dispatch Discord avec boutons interactifs
├── run_hunter.py                # Script principal d'orchestration CLI
├── run_hunter.bat               # Script Windows pour le planificateur de tâches
├── requirements.txt             # Dépendances Python
├── .env.example                 # Modèle de variables d'environnement
├── LICENSE                      # Licence open source MIT
└── README.md                    # Documentation officielle GitHub
```
