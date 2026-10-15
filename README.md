<p align="center">
  <img src="https://raw.githubusercontent.com/manny-ruesch/remote-job-hunter/main/docs/assets/noir-webling-demo.gif" width="760" alt="Noir Webling crawling its web while a scan runs, then a HOT match drops in">
</p>

<h1 align="center">Remote Job Hunter</h1>

<p align="center"><b>Any job search. Any LLM. Zero API key required.</b><br>
A private command center that scans job boards, ranks every offer against <i>your</i> profile,<br>
and lets you use the ChatGPT / Claude / Gemini subscription you already pay for.</p>

<p align="center">
  <a href="https://github.com/manny-ruesch/remote-job-hunter/actions"><img alt="CI" src="https://github.com/manny-ruesch/remote-job-hunter/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/remote-job-hunter/"><img alt="PyPI" src="https://img.shields.io/pypi/v/remote-job-hunter"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green">
</p>

---

## Start in 60 seconds

```bash
pipx install remote-job-hunter     # puts the `rjh` command in your terminal
rjh init                           # 4 questions, no file to edit by hand
rjh doctor                         # ~2 seconds: is your AI, your sources and your database ready?
rjh serve                          # Command Center on http://127.0.0.1:5070
```

No Python? Use [Docker](#run-it-on-a-nas-or-a-vps). No API key? That is the point: read on.

<details><summary><b>Démarrage rapide en français</b></summary>

```bash
pipx install remote-job-hunter
rjh init       # 4 questions : poste, lieu, niveau, IA
rjh doctor     # vérifie que tout est prêt
rjh serve      # ouvre le Command Center
```
Aucune clé d'API n'est nécessaire : choisissez « abonnement ChatGPT / Claude / Gemini » à la question 4.
</details>

## Zero-API-Key: use the subscription you already have

Most tools ask for an API key, which is a separate, pay-per-use product. **Manual mode** does not.

```
  scan boards ─▶ rank with YOUR profile ─▶ batch of 10 offers becomes ONE prompt
                                                        │
        scores, reasons, pitches  ◀─ paste the JSON back ◀─ paste the prompt into ChatGPT / Claude / Gemini
```

1. Click **Export the batch for my LLM** and copy the prompt (one click, works from your phone too).
2. Paste it in the chat you already use. Paste the reply back.
3. Cut off mid-answer? The tool keeps every finished entry and builds a follow-up prompt for **only the missing offers**.

It never logs into your accounts and never touches your session. You stay in your own chat, so there is nothing to link and nothing to leak.
Trade-off, stated plainly: about a minute of copy-paste per 10 offers. If you want it fully automatic, plug in an API key or a local model instead.

## Any job search, not only tech

Everything personal lives in one `profile.yaml`: target roles, seniority, remote or on-site, eligible regions, languages, salary floor, companies to avoid, and your own hard rules. Same engine, different people:

```yaml
search:                                    # a nurse in Lyon                          # a remote automation engineer
  titles: {primary: [infirmier, IDE]}      #   ...                                    #   [automation engineer, revops]
  work_mode: {remote: any, cities: [Lyon]} #                                          #   {remote: required}
```

A typo such as `seniorty` is rejected with a suggestion, never silently ignored. Every score comes with its reasons.

## Supported sources

| Source | Type | API key | Remote only | Good for | Status |
|---|---|---|---|---|---|
| **Remotive** (`remotive`) | by keyword | none | yes | remote jobs, tech and non-tech | ✅ built in |
| **Adzuna** (`adzuna`) | by keyword | free key | no | on-site and remote, many countries | ✅ built in |
| **Greenhouse** (`greenhouse`) | company boards | none | no | following specific employers | ✅ built in |
| **Ashby** (`ashby`) | company boards | none | no | AI scaleups, modern SaaS & PMEs | ✅ built in |
| **Lever** (`lever`) | company boards | none | no | scaleups & direct ATS boards | ✅ built in |
| **Welcome to the Jungle** (`wttj`) | by keyword | none | no | French & European scaleups | ✅ built in |
| **We Work Remotely** (`wwr`) | RSS feeds | none | yes | global remote programming & ops | ✅ built in |
| **Jobicy** (`jobicy`) | JSON feed | none | yes | curated remote opportunities | ✅ built in |
| **Hacker News** (`hn`) | founder thread | none | no | YC startups & founder-direct roles | ✅ built in |
| Your own | plugin | any | any | anything with an API or a feed | 🧩 [~30 lines](docs/ADD_A_SOURCE.md) |
| LinkedIn scrapers | stealth | none | no | | ⚠️ not included: opt-in only, check each site's terms |

One source breaking never stops the others, and `rjh doctor` tells you which one and why.

## Use any LLM

| You have | Choose | Cost |
|---|---|---|
| A ChatGPT, Claude or Gemini **subscription** | `manual` | what you already pay |
| A **Gemini** key | `gemini` | free tier or pay-per-use <!-- TODO(verify): limites du palier gratuit --> |
| An OpenAI, OpenRouter, Groq or Mistral key | `openai`, `openrouter`, `groq`, `mistral` | pay-per-use |
| An **Anthropic** API key | `anthropic` | pay-per-use |
| A computer with a decent GPU or CPU | `ollama` (local, free, private) | electricity |

Model names change fast, so none is hard-coded: `rjh init` picks a current one from what your key can use, and `rjh doctor` tells you when one is retired and what to switch to.
Heads-up: a free API tier may use your prompts to improve the provider's products. Read your provider's terms, or use `ollama` for full privacy.

## Run it on a NAS or a VPS

```bash
git clone https://github.com/manny-ruesch/remote-job-hunter && cd remote-job-hunter
echo "RJH_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" > .env
docker compose run --rm command-center rjh init     # once: answers 4 questions
docker compose up -d                                # Command Center on http://127.0.0.1:5070
```

The port is bound to `127.0.0.1` on purpose. Reach it from elsewhere through a reverse proxy with HTTPS or a private network such as Tailscale, never by opening the raw port. Keep the data volume on a **local disk**, not an SMB/NFS share (SQLite does not like network filesystems).

## Your data stays yours

- Your CV, contact details and notes stay on your machine. Only the text of the offers and a short summary of your search go to the LLM **you** pick; in manual mode, you paste them yourself.
- API keys live in a `.env` readable by you only, are typed hidden, and are never written to `profile.yaml` or printed by `rjh doctor`.
- `rjh serve` refuses to listen beyond localhost without an access token (`RJH_TOKEN`, generate one with `rjh token`).
- The tool never applies for you. <!-- TODO(verify): confirmer l'absence de télémétrie dans le Command Center avant de l'affirmer -->

## Contributing

Bug reports, new sources and translations are very welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). The quickest first contribution is a source: [docs/ADD_A_SOURCE.md](docs/ADD_A_SOURCE.md).
Security issue? Please read [SECURITY.md](SECURITY.md) first. Released under the [MIT License](LICENSE).

<p align="center"><sub>Made with a spider named Noir Webling. 🕷️</sub></p>
