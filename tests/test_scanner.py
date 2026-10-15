import json
import sqlite3
import tempfile
import textwrap
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from .helpers import make_http
from rjh import cli
from rjh.llm import LLMConfig, LLMProvider, LLMResponse, Usage
from rjh.manual import SqliteTicketStore
from rjh.paths import db_path, profile_path
from rjh.scanner import run_scan
from rjh.sources import create_source
from rjh.wizard import IO


ASHBY_PAYLOAD = {
    "jobs": [
        {
            "id": "ash-1",
            "title": "Automation Engineer",
            "jobUrl": "https://jobs.ashbyhq.com/workhero/ash-1",
            "location": "Remote - Europe",
            "isRemote": True,
            "employmentType": "FullTime",
            "publishedAt": "2026-10-14",
            "descriptionHtml": "<p>Build <b>n8n</b> and Python workflows via REST API.</p>",
        }
    ]
}

LEVER_PAYLOAD = [
    {
        "id": "lev-1",
        "text": "Solutions Engineer",
        "applyUrl": "https://jobs.lever.co/qonto/lev-1/apply",
        "workplaceType": "remote",
        "categories": {"location": "Paris, France", "commitment": "Part-Time"},
        "descriptionPlain": "Integrate webhooks, CRM and Python automations.",
    }
]

JOBICY_PAYLOAD = {
    "jobs": [
        {
            "id": "jobicy-1",
            "url": "https://jobicy.com/jobs/1",
            "jobTitle": "Workflow Automation Specialist",
            "companyName": "Acme Flow",
            "jobGeo": "Europe",
            "jobType": "full-time",
            "annualSalaryMin": 55000,
            "annualSalaryMax": 70000,
            "salaryCurrency": "EUR",
            "jobDescription": "<p>Automate HubSpot & n8n pipelines.</p>",
        }
    ]
}

WWR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>FlowCo: Revenue Operations Engineer</title>
    <link>https://weworkremotely.com/remote-jobs/flowco-revops</link>
    <region>Anywhere in the World</region>
    <description>&lt;p&gt;n8n, Python, REST API and CRM sync.&lt;/p&gt;</description>
  </item>
</channel></rss>"""

WTTJ_PAYLOAD = {
    "results": [
        {
            "hits": [
                {
                    "name": "Automation Engineer",
                    "slug": "automation-engineer",
                    "contract_type": "freelance",
                    "remote": "fulltime",
                    "summary": "Build n8n and Python workflows.",
                    "organization": {"name": "Scaleup FR", "slug": "scaleup-fr"},
                    "office": {"city": "Paris", "country": "France"},
                }
            ]
        }
    ]
}

HN_USER = {"submitted": [999]}
HN_STORY = {"id": 999, "kids": [1001]}
HN_COMMENT = {
    "id": 1001,
    "text": "SeedBot | Automation Engineer | Remote (Europe) | Full-time<p>We use n8n, Python and webhooks. Apply at https://seedbot.example/apply",
}


def route_all(call):
    u = urlsplit(call["url"])
    if u.netloc == "api.ashbyhq.com":
        return ASHBY_PAYLOAD
    if u.netloc == "api.lever.co":
        return LEVER_PAYLOAD
    if u.netloc == "jobicy.com":
        return JOBICY_PAYLOAD
    if u.netloc == "weworkremotely.com":
        return (200, WWR_XML, {"content-type": "application/rss+xml"})
    if "algolia.net" in u.netloc:
        return WTTJ_PAYLOAD
    if u.netloc == "hacker-news.firebaseio.com":
        if u.path.endswith("whoishiring.json"):
            return HN_USER
        if u.path.endswith("/999.json"):
            return HN_STORY
        if u.path.endswith("/1001.json"):
            return HN_COMMENT
    return {}


PROFILE_YAML = textwrap.dedent("""\
    version: 1
    search:
      titles:
        primary: ["automation engineer", "revenue operations engineer", "workflow automation specialist"]
        related: ["solutions engineer"]
      keywords:
        boost: {n8n: 10, python: 8, webhook: 6}
      work_mode: {remote: required}
      location:
        eligible_regions: [FR, EU, WORLDWIDE]
        excluded_regions: [US]
    scoring:
      hot_threshold: 7.5
      heuristic_min: 15
    sources:
      enabled: [ashby, lever, jobicy, wwr, wttj, hn]
      boards:
        ashby: [workhero]
        lever: [qonto]
      options:
        hn: {max_comments: 5}
    llm:
      provider: gemini
      model: gemini-3.8-flash
      api_key_env: GOOGLE_API_KEY
      max_jobs_per_run: 10
      language: fr
    candidate:
      summary: "Ingénieur automatisation n8n & Python."
      facts:
        - "32 workflows n8n en production"
""")


class FakeScanProvider(LLMProvider):
    name = "fake"
    needs_key = False

    def __init__(self):
        super().__init__(LLMConfig(provider="fake"), http=None)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        import re
        ids = re.findall(r'<job id="(j\d+)">', user)
        if "COLD" in system.upper() or "FACTS YOU MAY CITE" in user:
            res = [
                {
                    "id": jid,
                    "hook": "Automatisation directe de vos flux n8n.",
                    "pitch": "Fort de 32 workflows n8n en production, je peux déployer vos intégrations.",
                    "cold_message": "Bonjour, j'ai vu votre poste Automation. 32 workflows en prod.",
                    "questions": ["Quelle est votre volumétrie webhook ?", "Quel CRM utilisez-vous ?"],
                }
                for jid in ids
            ]
        else:
            res = [
                {
                    "id": jid,
                    "score": 8.6 if idx == 0 else 6.2,
                    "verdict": "apply" if idx == 0 else "maybe",
                    "gate": None,
                    "why": "Excellente correspondance n8n + Python",
                    "keywords": ["n8n", "Python", "REST API"],
                }
                for idx, jid in enumerate(ids)
            ]
        return LLMResponse(text=json.dumps({"results": res}), usage=Usage(120, 60))


class NewSourcesTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        profile_path(self.home).write_text(PROFILE_YAML, encoding="utf-8")
        from rjh.profile import load_profile
        self.profile = load_profile(profile_path(self.home))
        self.http, _ = make_http(route_all)

    def test_all_six_new_sources_parse_and_fetch(self):
        for name in ("ashby", "lever", "jobicy", "wwr", "wttj", "hn"):
            src = create_source(name, self.profile, http=self.http)
            res = src.fetch(self.profile)
            self.assertTrue(res.ok, f"{name} failed: {res.errors}")
            self.assertEqual(len(res.jobs), 1, f"{name} jobs: {res.jobs}")
            level, code = src.ping(self.profile)
            self.assertEqual((level, code), ("ok", "ok"), f"{name} ping: {level}, {code}")


class UnifiedScannerTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        profile_path(self.home).write_text(PROFILE_YAML, encoding="utf-8")
        self.http, _ = make_http(route_all)

    def test_scan_auto_fallbacks_to_manual_tickets_when_api_key_missing(self):
        rep = run_scan(self.home, environ={}, http=self.http, notify=False)
        self.assertEqual(rep.mode, "manual_fallback")
        self.assertEqual(rep.collected, 6)
        self.assertEqual(rep.selected, 6)
        self.assertEqual(len(rep.tickets), 1)

        store = SqliteTicketStore(str(db_path(self.home, {})))
        open_tickets = store.list_open()
        self.assertEqual(len(open_tickets), 1)
        self.assertEqual(len(open_tickets[0].id_map), 6)

        con = sqlite3.connect(db_path(self.home, {}))
        statuses = [r[0] for r in con.execute("SELECT analysis_status FROM jobs").fetchall()]
        con.close()
        self.assertEqual(statuses, ["pending_manual"] * 6)

    def test_scan_with_api_provider_evaluates_triage_and_dossier_and_dedupes_next_run(self):
        notified = []
        prov = FakeScanProvider()
        rep = run_scan(
            self.home,
            environ={"GOOGLE_API_KEY": "test-key"},
            http=self.http,
            provider=prov,
            notify=True,
            notify_fn=notified.append,
        )
        self.assertEqual(rep.mode, "api")
        self.assertEqual(rep.evaluated, 6)
        self.assertEqual(rep.hot, 1)
        self.assertEqual(len(notified), 1)
        self.assertEqual(notified[0]["fit_score"], 8.6)
        self.assertTrue(notified[0]["tailored_pitch"].startswith("Fort de 32 workflows"))
        self.assertIn("mermaid_diagram", notified[0]["proof_of_work"])
        self.assertIn("linkedin_search_url", notified[0]["cold_outreach"])

        # Second scan: all 6 jobs are now seen & evaluated -> 0 unseen
        rep2 = run_scan(
            self.home,
            environ={"GOOGLE_API_KEY": "test-key"},
            http=self.http,
            provider=prov,
            notify=False,
        )
        self.assertEqual(rep2.unseen, 0)
        self.assertEqual(rep2.selected, 0)

    def test_cli_scan_json_and_dry_run(self):
        out = []
        io = IO(inp=lambda _p: "", out=out.append, secret=lambda _p: "")
        code = cli.main(
            ["--home", str(self.home), "scan", "--dry-run", "--json"],
            io=io,
            environ={},
            http_factory=lambda: self.http,
        )
        self.assertEqual(code, 0)
        data = json.loads("\n".join(out))
        self.assertEqual(data["mode"], "dry_run")
        self.assertEqual(data["collected"], 6)


if __name__ == "__main__":
    unittest.main()
