import tempfile
import textwrap
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .helpers import example_profile, make_http
from rjh.heuristic import ProfileScorer
from rjh.models import Job
from rjh.sources import available_sources, collect, create_source, load_plugins
from rjh import sources as sources_pkg
from rjh.sources.base import SourceConfigError

PROFILE = example_profile()

# ⚠ Fixtures écrites d'après la documentation de chaque API, de mémoire : à confronter à une vraie réponse.
REMOTIVE = {"jobs": [
    {"id": 101, "url": "https://remotive.com/j/101", "title": "Automation Engineer", "company_name": "Zeta",
     "job_type": "full_time", "publication_date": "2026-10-01T10:00:00", "candidate_required_location": "Europe",
     "salary": "50k", "category": "Software", "description": "<p>Build <b>n8n</b> workflows</p><ul><li>python</li></ul>"},
    {"id": 102, "url": "https://remotive.com/j/102", "title": "Barista", "company_name": "Cafe", "job_type": "contract",
     "publication_date": "2026-10-02T10:00:00", "candidate_required_location": "USA", "description": "<p>Coffee</p>"},
]}
ADZUNA = {"count": 120, "results": [
    {"id": "9001", "title": "<strong>Automation</strong> Engineer", "company": {"display_name": "Beta SAS"},
     "location": {"display_name": "Pau, Pyrénées-Atlantiques"}, "redirect_url": "https://adzuna.fr/land/9001",
     "description": "Workflows n8n et python…", "created": "2026-10-03T08:00:00Z", "salary_min": 42000, "salary_max": 48000,
     "contract_time": "full_time", "contract_type": "permanent"}]}
GREENHOUSE = {"jobs": [
    {"id": 7, "title": "Workflow Automation Lead", "absolute_url": "https://boards.greenhouse.io/stripe/jobs/7",
     "location": {"name": "Remote - Europe"}, "updated_at": "2026-10-04T09:00:00Z", "company_name": "Stripe",
     "content": "&lt;p&gt;Own our &lt;strong&gt;n8n&lt;/strong&gt; automations.&lt;/p&gt;&lt;p&gt;3+ years of experience.&lt;/p&gt;"},
    {"id": 8, "title": "Office Cleaner", "absolute_url": "https://boards.greenhouse.io/stripe/jobs/8",
     "location": {"name": "Dublin"}, "updated_at": "2026-10-04T09:00:00Z", "content": "&lt;p&gt;Clean&lt;/p&gt;"}]}


def route(call):
    url = urlsplit(call["url"])
    if url.netloc == "remotive.com":
        return REMOTIVE
    if url.netloc == "api.adzuna.com":
        return ADZUNA
    if url.netloc == "boards-api.greenhouse.io":
        slug = url.path.split("/")[3]            # /v1/boards/<slug>/jobs
        return (404, {"error": "not found"}) if slug == "renamed" else GREENHOUSE
    return (500, {})


class JobIdentity(unittest.TestCase):
    def test_same_job_on_two_sources_shares_a_dedupe_key_but_not_an_id(self):
        a = Job("linkedin", "1", "Superchat GmbH", "Revenue Operations Engineer (m/f/d)", "u1", "Berlin, Germany")
        b = Job("ashby", "9", "superchat gmbh", "Revenue Operations Engineer", "u2", "Berlin")
        self.assertEqual(a.dedupe_key, b.dedupe_key)
        self.assertNotEqual(a.id, b.id)
        self.assertNotEqual(a.dedupe_key, Job("ashby", "9", "Superchat GmbH", "Revenue Operations Engineer", "u2", "Paris").dedupe_key)


class Remotive(unittest.TestCase):
    def test_parse_is_pure_and_strips_html(self):
        src = create_source("remotive", PROFILE, http=make_http(route)[0])
        jobs = src.parse(REMOTIVE)
        self.assertEqual(jobs[0].description, "Build n8n workflows\npython")
        self.assertEqual((jobs[0].remote, jobs[0].employment_type, jobs[0].location), (True, "full_time", "Europe"))
        self.assertEqual(jobs[1].employment_type, "contract")

    def test_queries_come_from_the_profile_and_duplicates_are_merged(self):
        http, t = make_http(route)
        res = create_source("remotive", PROFILE, http=http).fetch(PROFILE)
        searches = [parse_qs(urlsplit(c["url"]).query)["search"][0] for c in t.calls]
        self.assertEqual(searches, ["automation engineer", "no-code developer", "workflow automation"])   # max_titles: 3
        self.assertEqual(len(res.jobs), 2)                                  # 3 requêtes, mêmes offres : fusionnées
        self.assertTrue(res.ok)
        self.assertIn("rjh", t.calls[0]["headers"]["User-Agent"])


class Adzuna(unittest.TestCase):
    ENV = {"ADZUNA_APP_ID": "ID1", "ADZUNA_APP_KEY": "SECRET_KEY"}

    def test_missing_credentials_are_a_config_error_naming_the_variables(self):
        with self.assertRaises(SourceConfigError) as cm:
            create_source("adzuna", PROFILE, env={})
        self.assertIn("ADZUNA_APP_KEY", str(cm.exception))

    def test_pagination_country_and_params(self):
        http, t = make_http(route)
        profile = example_profile()
        profile.sources.options["adzuna"] = {"countries": ["fr"], "max_titles": 1, "max_age_days": 14}
        res = create_source("adzuna", profile, http=http, env=self.ENV).fetch(profile)
        paths = [urlsplit(c["url"]).path for c in t.calls]
        self.assertEqual(paths, ["/v1/api/jobs/fr/search/1", "/v1/api/jobs/fr/search/2"])     # count=120 -> page 2 ; max_pages=2
        q = parse_qs(urlsplit(t.calls[0]["url"]).query)
        self.assertEqual((q["what"], q["app_id"], q["max_days_old"]), (["automation engineer"], ["ID1"], ["14"]))
        self.assertNotIn("where", q)                                         # paramètre vide : non envoyé
        self.assertEqual(len(res.jobs), 1)                                   # même offre sur les deux pages : une seule
        job = res.jobs[0]
        self.assertEqual((job.title, job.employment_type, job.salary_max), ("Automation Engineer", "full_time", 48000))
        self.assertTrue(job.extra["description_is_snippet"])

    def test_errors_are_reported_without_leaking_the_key(self):
        http, _ = make_http(lambda c: (500, {"error": "boom"}), max_retries=0)
        res = create_source("adzuna", PROFILE, http=http, env=self.ENV).fetch(PROFILE)
        self.assertFalse(res.ok)
        self.assertEqual(res.jobs, [])
        self.assertNotIn("SECRET_KEY", " ".join(res.errors))


class Greenhouse(unittest.TestCase):
    def profile(self, boards):
        p = example_profile()
        p.sources.boards = {"greenhouse": boards}
        return p

    def test_board_source_filters_client_side_and_reports_broken_boards(self):
        p = self.profile(["stripe", "renamed", "airtable"])
        http, t = make_http(route)
        res = create_source("greenhouse", p, http=http).fetch(p, prefilter=ProfileScorer(p).title_prefilter)
        self.assertEqual(res.errors, ["renamed : HTTP 404"])                 # visible, nommé
        self.assertEqual(res.per_board, {"stripe": 1, "airtable": 0})        # airtable : mêmes ids déjà vus -> dédoublonnés
        self.assertEqual([j.title for j in res.jobs], ["Workflow Automation Lead"])   # « Office Cleaner » filtré
        self.assertEqual(res.jobs[0].description, "Own our n8n automations.\n3+ years of experience.")
        self.assertEqual(res.jobs[0].company, "Stripe")
        self.assertTrue(all(parse_qs(urlsplit(c["url"]).query)["content"] == ["true"] for c in t.calls))

    def test_no_boards_configured_is_reported(self):
        p = self.profile([])
        res = create_source("greenhouse", p, http=make_http(route)[0]).fetch(p)
        self.assertIn("aucune entreprise", res.errors[0])

    def test_company_name_falls_back_to_the_slug(self):
        self.assertEqual(create_source("greenhouse", PROFILE, http=make_http(route)[0]).company_name("my-cool_co", {}), "My Cool Co")


class Collect(unittest.TestCase):
    def test_parallel_collection_isolates_failures_and_dedupes_across_sources(self):
        p = example_profile()
        p.sources.enabled = ["remotive", "adzuna", "greenhouse", "inconnue"]
        p.sources.boards = {"greenhouse": ["stripe"]}
        http, _ = make_http(route)
        jobs, reports = collect(p, prefilter=ProfileScorer(p).title_prefilter, http=http, env={})   # pas de clés Adzuna
        by = {r.source: r for r in reports}
        self.assertIn("ADZUNA_APP_ID", by["adzuna"].errors[0])
        self.assertIn("source inconnue", by["inconnue"].errors[0])
        self.assertTrue(by["remotive"].ok and by["greenhouse"].ok)
        self.assertEqual(sorted(j.title for j in jobs), ["Automation Engineer", "Workflow Automation Lead"])

    def test_cross_source_duplicate_keeps_the_richest_description(self):
        class A(sources_pkg.base.KeywordSource):
            name = "dup_a"
            def request(self, q): return "https://a.example/x", {}, {}
            def parse(self, payload, **ctx): return [Job("dup_a", "1", "Acme", "Automation Engineer (m/f/d)", "ua", "Paris", "court")]
        class B(sources_pkg.base.KeywordSource):
            name = "dup_b"
            def request(self, q): return "https://b.example/x", {}, {}
            def parse(self, payload, **ctx): return [Job("dup_b", "2", "ACME", "Automation Engineer", "ub", "Paris, France", "une description beaucoup plus longue et riche")]
        sources_pkg.register(A); sources_pkg.register(B)
        try:
            p = example_profile()
            p.sources.enabled = ["dup_a", "dup_b"]
            jobs, _ = collect(p, http=make_http(lambda c: {})[0])
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].source, "dup_b")
        finally:
            sources_pkg._REGISTRY.pop("dup_a", None); sources_pkg._REGISTRY.pop("dup_b", None)

    def test_a_crashing_plugin_does_not_stop_the_run(self):
        class Boom(sources_pkg.base.KeywordSource):
            name = "boom"
            def request(self, q): raise RuntimeError("bug du plugin")
            def parse(self, payload, **ctx): return []
        sources_pkg.register(Boom)
        try:
            p = example_profile()
            p.sources.enabled = ["boom", "remotive"]
            jobs, reports = collect(p, http=make_http(route)[0])
            self.assertTrue(any("bug du plugin" in e for r in reports if r.source == "boom" for e in r.errors))
            self.assertTrue(jobs)
        finally:
            sources_pkg._REGISTRY.pop("boom", None)


class Plugins(unittest.TestCase):
    def test_a_third_party_source_in_30_lines(self):
        code = textwrap.dedent('''
            from rjh.models import Job
            from rjh.sources import register
            from rjh.sources.base import KeywordSource

            @register
            class DemoSource(KeywordSource):
                name = "demo"
                max_pages = 1
                def request(self, q):
                    return "https://demo.example/api/jobs", {"q": q.text}, {}
                def parse(self, payload, **ctx):
                    return [Job("demo", str(x["id"]), x["co"], x["t"], x["u"]) for x in payload["items"]]
        ''')
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "demo.py").write_text(code, encoding="utf-8")
            self.assertEqual(load_plugins(d), ["demo"])
        try:
            self.assertIn("demo", available_sources())
            http, t = make_http(lambda c: {"items": [{"id": 1, "co": "X", "t": "Automation Engineer", "u": "https://x/1"}]})
            res = create_source("demo", PROFILE, http=http).fetch(PROFILE)
            self.assertEqual([j.title for j in res.jobs], ["Automation Engineer"])
        finally:
            sources_pkg._REGISTRY.pop("demo", None)

    def test_a_source_without_a_name_is_refused(self):
        class Nameless(sources_pkg.base.KeywordSource):
            def request(self, q): return "", {}, {}
            def parse(self, payload, **ctx): return []
        with self.assertRaises(ValueError):
            sources_pkg.register(Nameless)


if __name__ == "__main__":
    unittest.main()
