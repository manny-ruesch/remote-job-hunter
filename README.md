<div align="center">

# 🎯 Remote Job Hunter
### Autonomous AI Scouting, Pre-Ranking & Assisted Application Engine

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Playwright](https://img.shields.io/badge/Playwright-Automated_Fill-45ba4b.svg?logo=playwright&logoColor=white)](https://playwright.dev)
[![Scrapling](https://img.shields.io/badge/Scrapling-Stealth_Engine-orange.svg)](https://github.com/D4Vinci/Scrapling)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/manny-ruesch/remote-job-hunter)

<p align="center">
  <b>A production-grade, open-source pipeline that automates the discovery, deep evaluation, pitch tailoring, and assisted application for remote tech opportunities.</b>
  <br />
  <i>Engineered for AI Automation Specialists, Solutions Engineers, and RevOps Builders.</i>
</p>

</div>

---

## ⚡ Why Remote Job Hunter?

Landing high-leverage remote roles today is broken:
- Aggregator boards are bloated with paywalls, mediator subscriptions, and ghost jobs.
- Spray-and-pray applications get buried under hundreds of generic AI-generated resumes.
- Manual scouting across dozens of individual company career pages takes hours every day.

**Remote Job Hunter** replaces this with an autonomous, high-precision system:
1. **Direct Multi-Source Ingestion:** Scrapes **47+ sources** in parallel in ~15 seconds without mediator paywalls.
2. **Local Heuristic Pre-Ranking:** Filters noise (over-senior roles, LeetCode backend, on-site requirements) locally at zero API cost.
3. **Deep Qualitative AI Fit Scoring:** Uses Gemini 2.5 Flash to evaluate tech stack alignment against your real production artifacts.
4. **Bespoke Application Packages:** Generates customized 120-word application pitches, **Proof-of-Work technical architectures** with Mermaid diagrams, and decision-maker cold outreach messages.
5. **1-Click Assisted Auto-Fill:** Launches a visible Playwright browser to pre-fill ATS forms, attach your resume, and leave the screen ready for you to review and submit.
6. **Executive Editorial Command Center:** A local web dashboard designed with an **Editorial Warm Luxury** aesthetic (Newsreader typography, live Kanban pipeline, hotkeys, and real-time log streaming).

---

## 🏛️ System Architecture

```
                                  [ 47+ OPEN & DIRECT SOURCES ]
            ┌──────────────────────┬──────────────────────┬──────────────────────┐
      Direct Ashby           Direct Greenhouse      Direct Lever           Aggregators / Feeds
    (27 companies)          (15 companies)         (4 companies)        (LinkedIn, WTTJ, HN, WWR, Remotive)
            │                      │                      │                      │
            └──────────────────────┴──────────┬───────────┴──────────────────────┘
                                              │  (Parallel ThreadPoolExecutor ~15s)
                                              ▼
                             [ 1. Ingestion & SQLite Deduplication ]
                                • MD5 Content Hash Verification
                                • Full-Time, Part-Time & Freelance Tagging
                                              │
                                              ▼
                             [ 2. Local Heuristic Pre-Ranking Engine ]
                                • Zero-cost lexical density evaluation (<10ms)
                                • Hard gates: Seniority (>5 YoE), Language, On-site
                                • Selects Top 15 Highest-Fit Candidates
                                              │
                                              ▼
                             [ 3. Scrapling Stealth JD Enrichment ]
                                • Bypasses Cloudflare & WAFs (curl-cffi + patchright)
                                • Converts dynamic JavaScript SPAs to clean Markdown
                                              │
                                              ▼
                             [ 4. Gemini Qualitative AI Engine ]
                                ├── Fit Scoring (0 to 10)
                                ├── Bespoke 120-word Application Pitch
                                ├── Proof-of-Work Technical Architecture (Mermaid.js)
                                └── Hiring Manager Cold Outreach (LinkedIn / Email)
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    ▼                                                   ▼
       [ Discord Interactive Bot ]                         [ Editorial Command Center ]
       • Real-time alerts in #remote-jobs                  • FastAPI + Vanilla JS + Mermaid.js
       • Interactive Buttons (Pitch, PoW, DM)              • 1-Click Playwright Auto-Fill
       • Ephemeral mobile responses                        • Instant Executive PDF Cover Letter
```

---

## 🚀 Key Features

### 📡 1. Comprehensive Multi-Source Coverage (47+ Sources)
- **Direct ATS Boards (100% Free Application):**
  - **Ashby:** WorkHero, Attio, Twenty, Supabase, Plain, Ghost, Temporal, Dust, Zapier, Buffer, PostHog, Resend, Vapi, Cursor, Linear, Replit, Notion, Synthesia, Pinecone, Weaviate, Deepgram, Sentry, LangChain, Perplexity, ClickUp, Lemlist.
  - **Greenhouse:** Celonis/Make, Tavily, Airtable, Webflow, GitLab, Canonical, Cloudflare, Stripe, Algolia, Contentful, Mattermost, Typeform, Braze, Klaviyo, Intercom.
  - **Lever:** Pigment, Qonto, Aircall, Deliverect.
- **Major Open Job Platforms:**
  - **LinkedIn Remote:** Guest search API using Scrapling stealth (Full-Time, Part-Time, Contract).
  - **Welcome to the Jungle (WTTJ):** Direct Algolia index search for European scale-ups.
  - **Hacker News (*Who is Hiring?*):** Direct Firebase REST endpoint for seed/founder listings.
  - **We Work Remotely (WWR):** Live RSS feeds across Programming, DevOps, and Support.
  - **Remotive & Jobicy:** Public curated remote tech APIs.

### 🧠 2. Heuristic Pre-Ranking & Adversarial AI Scoring
- **Sub-10ms Lexical Scorer:** Eliminates LeetCode backend infrastructure, legacy enterprise stacks, and over-senior positions before hitting the LLM API.
- **Strict Seniority & Location Hard Gates:** Auto-disqualifies postings requiring 5+ years of experience or mandating on-site office presence.
- **Proven Impact Matching:** Evaluates whether the candidate's actual production portfolio can solve the company's immediate operational bottleneck.

### 🏗️ 3. Proof-of-Work & Decision-Maker Cold Outreach
- **Proof-of-Work Proposal:** Automatically designs a 3-step technical implementation plan (Ingestion, Processing & Guardrails, Action Delivery) with a valid Mermaid.js flowchart.
- **ATS Bypass (Cold DM):** Drafts a 50-word, zero-fluff cold message to the hiring manager (Head of Solutions Engineering, VP Operations, CTO) with a direct link to find their profile on LinkedIn.

### 🤖 4. Assisted Auto-Fill Engine (Playwright)
- Spawns a visible Chromium window on your desktop.
- Pre-fills standard candidate fields: Name, Email, Phone, Location, Portfolio, LinkedIn, GitHub.
- Automatically pastes the customized cover pitch and attaches your local PDF resume.
- **Safety by Design:** Pauses before clicking *Submit*, giving you full control to verify custom questions and submit in 3 seconds.

### 📄 5. Bespoke Executive Cover Letter PDF Generator
- Transforms technical requirements into an articulate, literary business letter (Newsreader typography, executive tone, no coder vibe).
- Automatically compiles a clean, single-page A4 PDF using headless Microsoft Edge directly to your Desktop.

---

## 🛠️ Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/remote-job-hunter.git
cd remote-job-hunter
```

### 2. Install Dependencies
```bash
# Using uv (recommended for ultra-fast setup):
uv pip install -r requirements.txt

# Or using standard pip:
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure Your Profile
Copy the template configuration file:
```bash
cp .env.example .env
```
Edit `.env` with your profile details and API keys:
```ini
GOOGLE_API_KEY="your_gemini_api_key"
DISCORD_WEBHOOK_URL="your_discord_webhook_url" # Optional
APPLICANT_NAME="Your Name"
APPLICANT_EMAIL="your.email@example.com"
APPLICANT_PORTFOLIO="https://your-portfolio.com"
```

### 4. Launch the Command Center
```bash
python dashboard/dashboard_server.py
```
Open your browser at **[http://127.0.0.1:5070](http://127.0.0.1:5070)**.

### 5. Run a Scouting Pass (CLI)
```bash
# Run a full scouting scan:
python run_hunter.py

# View local pipeline stats & CRM metrics:
python run_hunter.py --stats
```

---

## 📁 Project Structure

```
remote_job_hunter/
├── dashboard/
│   ├── dashboard_server.py     # FastAPI backend for local Command Center
│   ├── static/
│   │   └── index.html          # Editorial Warm Luxury Web UI with Mermaid.js
│   ├── run_dashboard.bat       # Windows launcher script
│   └── tunnel_manager.py       # Optional Cloudflare tunnel for mobile access
├── analyzer.py                 # Gemini qualitative evaluation & pitch generation
├── auto_fill_engine.py         # Headful Playwright assisted form autofill
├── config.py                   # Central decoupled environment configuration
├── cover_letter_generator.py   # Bespoke executive PDF cover letter compiler
├── db.py                       # SQLite schema & CRM tracking engine
├── fetcher.py                  # Parallel multi-board scraper (Ashby, Greenhouse, WTTJ, etc.)
├── heuristic_scorer.py         # Local sub-10ms lexical pre-ranking engine
├── notifier.py                 # Discord interactive embed sender & Markdown digest
├── run_hunter.py               # Main CLI orchestration script
├── requirements.txt            # Python dependencies
├── .env.example                # Example environment template
└── README.md                   # Project documentation
```

---

## 🤝 Contributing

Contributions are welcome! Whether you want to add support for a new company board, enhance ATS autofill selectors, or improve the UI:

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for more information.
