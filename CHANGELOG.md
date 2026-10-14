# Changelog
Format : [Keep a Changelog](https://keepachangelog.com/) · versions : [SemVer](https://semver.org/).

## [0.2.0]
### Added
- `rjh init` : assistant en 4 questions (FR/EN), mode non interactif (`--yes`) pour Docker et CI.
- `rjh doctor` : vérifie Python, profil, IA, sources et base en parallèle ; `--offline`, `--json`, code de sortie utilisable en CI.
- `rjh serve` : refuse d'écouter hors localhost sans `RJH_TOKEN` ; `rjh token`, `rjh paths`.
- Packaging (`pipx install remote-job-hunter`), Dockerfile et `docker-compose.yml`.
- `BaseJobSource.ping()` pour le diagnostic des sources.
### Changed
- Les erreurs HTTP des fournisseurs LLM n'incluent plus le corps de la réponse (jamais de secret réaffiché).

## [0.1.0]
- Couche universelle : `profile.yaml`, sources en plugins, `LLMProvider` (Gemini, compatible OpenAI, Anthropic, mode manuel), mode copier-coller.
