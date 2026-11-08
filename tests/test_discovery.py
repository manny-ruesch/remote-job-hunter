"""Tests unitaires et d'intégration pour le Pack 3 :
Reverse-ATS Auto-Discovery, connecteurs SmartRecruiters & Workable, et LinkedIn Warm-Intro Matcher."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from rjh.discovery import (
    companies_match,
    discover_boards_from_db,
    extract_ats_boards_from_text,
    generate_company_slug_candidates,
    import_linkedin_connections_csv,
    is_valid_ats_slug,
    match_warm_intros_for_jobs,
    normalize_company_name,
    probe_company_ats,
    sync_discovered_boards_to_profile,
)
from rjh.liveness import check_job_liveness
from rjh.profile import Profile, load_profile
from rjh.sources import available_sources, create_source


class TestNewAtsConnectors(unittest.TestCase):
    def test_smartrecruiters_and_workable_registered(self):
        avail = available_sources()
        self.assertIn("smartrecruiters", avail)
        self.assertIn("workable", avail)

    def test_smartrecruiters_parse_builds_rich_description(self):
        prof = Profile()
        prof.sources.boards["smartrecruiters"] = ["alan"]
        src = create_source("smartrecruiters", prof)
        payload = {
            "totalFound": 1,
            "content": [
                {
                    "id": "743999123456",
                    "name": "Senior AI Automation Engineer",
                    "company": {"name": "Alan", "identifier": "Alan"},
                    "location": {"city": "Paris", "region": "IDF", "country": "fr", "remote": True},
                    "typeOfEmployment": {"id": "permanent", "label": "Full-time"},
                    "department": {"label": "Engineering"},
                    "function": {"label": "Information Technology"},
                    "experienceLevel": {"label": "Mid-Senior level"},
                    "releasedDate": "2026-11-01T10:00:00.000Z",
                }
            ],
        }
        jobs = src.parse(payload, slug="alan")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j.source, "smartrecruiters")
        self.assertEqual(j.company, "Alan")
        self.assertEqual(j.title, "Senior AI Automation Engineer")
        self.assertTrue(j.remote)
        self.assertGreater(len(j.description), 50)
        self.assertIn("jobs.smartrecruiters.com/Alan/743999123456", j.url)

    def test_workable_parse_extracts_jobs(self):
        prof = Profile()
        prof.sources.boards["workable"] = ["pennylane"]
        src = create_source("workable", prof)
        payload = {
            "name": "Pennylane",
            "jobs": [
                {
                    "shortcode": "ABC12345",
                    "title": "Staff Backend Engineer (Python)",
                    "city": "Paris",
                    "country": "France",
                    "telecommuting": True,
                    "employment_type": "Full-time",
                    "description": "<p>Build our core accounting &amp; LLM automation platform in Python.</p>",
                    "requirements": "<p>5+ years with Python, PostgreSQL, and FastAPI.</p>",
                    "url": "https://apply.workable.com/pennylane/j/ABC12345/",
                }
            ],
        }
        jobs = src.parse(payload, slug="pennylane")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j.source, "workable")
        self.assertEqual(j.company, "Pennylane")
        self.assertTrue(j.remote)
        self.assertIn("FastAPI", j.description)
        self.assertEqual(j.url, "https://apply.workable.com/pennylane/j/ABC12345/")

    def test_liveness_supports_smartrecruiters_and_workable(self):
        def fake_fetcher(url: str):
            if "api.smartrecruiters.com/v1/companies/alan/postings/743999" in url:
                return 200, url, '{"id": "743999", "name": "AI Engineer"}'
            if "workable.com/api/accounts/pennylane" in url:
                return 200, url, '{"jobs": [{"shortcode": "DEAD999"}]}'
            return 404, url, ""

        sr_live = check_job_liveness(
            "https://jobs.smartrecruiters.com/alan/743999-ai-engineer",
            resolve_dns=False,
            fetcher=fake_fetcher,
        )
        self.assertEqual(sr_live.status, "active")
        self.assertEqual(sr_live.ats_provider, "smartrecruiters")

        wk_expired = check_job_liveness(
            "https://apply.workable.com/pennylane/j/GONE123/",
            resolve_dns=False,
            fetcher=fake_fetcher,
        )
        self.assertEqual(wk_expired.status, "expired")
        self.assertEqual(wk_expired.ats_provider, "workable")


class TestReverseAtsDiscovery(unittest.TestCase):
    def test_extract_ats_boards_handles_embeds_and_blocks_reserved_slugs(self):
        blob = """
        Apply here: https://boards.greenhouse.io/embed/job_app?for=stripe&token=998877
        Or on Lever: https://jobs.lever.co/mistral/1234-5678
        Or Ashby: https://jobs.ashbyhq.com/linear/abcdef12-3456
        Or SmartRecruiters: https://jobs.smartrecruiters.com/doctolib/7439991122
        Or Workable API: https://apply.workable.com/api/v3/accounts/qonto/jobs
        Ignore reserved routes:
        https://boards.greenhouse.io/embed
        https://apply.workable.com/j/SHORTCODE
        https://jobs.smartrecruiters.com/oneclick-ui/something
        https://jobs.lever.co/parse
        """
        boards = extract_ats_boards_from_text(blob)
        pairs = {(b.provider, b.slug) for b in boards}
        self.assertIn(("greenhouse", "stripe"), pairs)
        self.assertIn(("lever", "mistral"), pairs)
        self.assertIn(("ashby", "linear"), pairs)
        self.assertIn(("smartrecruiters", "doctolib"), pairs)
        self.assertIn(("workable", "qonto"), pairs)

        # Aucun slug réservé ne doit fuiter
        slugs = {b.slug for b in boards}
        self.assertNotIn("embed", slugs)
        self.assertNotIn("api", slugs)
        self.assertNotIn("j", slugs)
        self.assertNotIn("oneclick-ui", slugs)
        self.assertFalse(is_valid_ats_slug("embed"))
        self.assertFalse(is_valid_ats_slug("12345"))

    def test_generate_company_slug_candidates_strips_legal_suffixes(self):
        self.assertEqual(generate_company_slug_candidates("Deel Inc."), ["deel"])
        self.assertEqual(generate_company_slug_candidates("Alan SA"), ["alan"])
        self.assertEqual(generate_company_slug_candidates("Twenty HQ"), ["twenty"])
        self.assertEqual(generate_company_slug_candidates("Dust Labs"), ["dust"])

    def test_probe_company_ats_parallel_with_custom_fetcher(self):
        def fake_fetcher(url: str):
            if "api.ashbyhq.com/posting-api/job-board/linear" in url:
                return 200, url, '{"jobs": [{"title": "Product Engineer"}, {"title": "Staff Designer"}]}'
            return 404, url, "{}"

        res = probe_company_ats("Linear Inc.", fetcher=fake_fetcher)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].provider, "ashby")
        self.assertEqual(res[0].slug, "linear")
        self.assertEqual(res[0].job_count, 2)

    def test_sync_discovered_boards_preserves_comments_and_enables_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "profile.yaml"
            p.write_text(
                "# Commentaire utilisateur en tête\n"
                "version: 1\n"
                "identity:\n"
                "  name: Manny\n"
                "search:\n"
                "  titles:\n"
                "    primary: [AI Engineer]\n"
                "sources:\n"
                "  # Liste des sources actives\n"
                "  enabled:\n"
                "    - ashby\n"
                "  boards:\n"
                "    ashby:\n"
                "      - linear\n",
                encoding="utf-8",
            )
            res = sync_discovered_boards_to_profile(
                [
                    {"provider": "ashby", "slug": "supabase"},
                    {"provider": "smartrecruiters", "slug": "alan"},
                ],
                p,
            )
            self.assertEqual(len(res["added"]), 2)
            self.assertIn("smartrecruiters", res["enabled_providers"])

            updated_raw = p.read_text(encoding="utf-8")
            self.assertIn("# Commentaire utilisateur en tête", updated_raw)
            self.assertIn("# Liste des sources actives", updated_raw)

            prof = load_profile(p)
            self.assertIn("supabase", prof.sources.boards["ashby"])
            self.assertIn("alan", prof.sources.boards["smartrecruiters"])
            self.assertIn("smartrecruiters", prof.sources.enabled)


class TestLinkedInWarmIntros(unittest.TestCase):
    def test_company_normalization_and_anti_false_positive_matching(self):
        # Stopwords non-employeurs rejetés
        self.assertEqual(normalize_company_name("Freelance"), "")
        self.assertEqual(normalize_company_name("Self-employed"), "")
        self.assertEqual(normalize_company_name("Stealth Startup"), "")

        # Noms courts ("On", "At", "Go", "AI") ne matchent JAMAIS par sous-chaîne
        self.assertFalse(companies_match("Nonprofit Org", "On"))
        self.assertFalse(companies_match("Relational AI", "At"))
        self.assertFalse(companies_match("Algolia", "Go"))
        self.assertFalse(companies_match("Railway", "AI"))
        self.assertTrue(companies_match("On AG", "On"))

        # Suffixes juridiques ignorés et préfixes de tokens >= 4 caractères supportés
        self.assertTrue(companies_match("Stripe Inc.", "Stripe"))
        self.assertTrue(companies_match("Stripe Payments Europe", "Stripe"))
        self.assertTrue(companies_match("Datadog SAS", "Datadog Inc"))

    def test_import_linkedin_csv_with_notes_preamble_and_match_jobs(self):
        csv_text = (
            "\ufeffNotes:\n"
            '"When exporting your connection data, you may notice that some of the email addresses are missing."\n'
            "\n"
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            "Alice,Martin,https://www.linkedin.com/in/alice-martin,,Stripe Inc.,Staff AI Engineer,12 Oct 2025\n"
            "Bob,Dupont,https://www.linkedin.com/in/bob-dupont,,Freelance,Consultant,01 Jan 2025\n"
            "Claire,Bernard,https://www.linkedin.com/in/claire-b,,On AG,Product Manager,15 Mar 2026\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "jobs.db"
            con = sqlite3.connect(db_file)
            con.execute(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    source TEXT,
                    company TEXT,
                    title TEXT,
                    location TEXT,
                    url TEXT,
                    fit_score REAL,
                    status TEXT,
                    liveness_status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            con.execute(
                "INSERT INTO jobs (id, source, company, title, location, url, fit_score, status, liveness_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("j1", "greenhouse", "Stripe", "AI Automation Architect", "Remote", "https://boards.greenhouse.io/stripe/jobs/123", 9.2, "new", "active"),
            )
            con.execute(
                "INSERT INTO jobs (id, source, company, title, location, url, fit_score, status, liveness_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("j2", "ashby", "Nonprofit Foundation", "Backend Engineer", "Remote", "https://jobs.ashbyhq.com/nonprofit/456", 8.4, "new", "active"),
            )
            con.commit()
            con.close()

            imp = import_linkedin_connections_csv(csv_text, db_file)
            # Alice (Stripe) et Claire (On AG) importées ; Bob (Freelance) ignoré
            self.assertEqual(imp["imported"], 2)
            self.assertEqual(imp["skipped"], 1)
            self.assertEqual(imp["header_line_index"], 3)

            # Réimport idempotent (pas de doublons)
            imp2 = import_linkedin_connections_csv(csv_text, db_file)
            self.assertEqual(imp2["total_in_db"], 2)

            matches = match_warm_intros_for_jobs(db_file, candidate_name="Manny Ruesch", lang="fr")
            self.assertEqual(matches["matched_jobs_count"], 1)
            m = matches["matched_jobs"][0]
            self.assertEqual(m["company"], "Stripe")
            self.assertEqual(m["contacts"][0]["full_name"], "Alice Martin")
            self.assertIn("Salut Alice", m["contacts"][0]["referral_draft"])
            self.assertIn("Manny Ruesch", m["contacts"][0]["referral_draft"])

            # Nonprofit Foundation ne doit PAS matcher Claire ("On AG") et doit être dans fallback_targets
            self.assertEqual(len(matches["fallback_targets"]), 1)
            self.assertEqual(matches["fallback_targets"][0]["company"], "Nonprofit Foundation")

            # Test discover_boards_from_db sur la même base
            boards = discover_boards_from_db(db_file)
            self.assertEqual({(b.provider, b.slug) for b in boards}, {("greenhouse", "stripe"), ("ashby", "nonprofit")})


class TestDiscoveryFastApiEndpoints(unittest.TestCase):
    def test_discovery_and_referrals_endpoints(self):
        import dashboard.dashboard_server as ds

        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "jobs.db"
            con = sqlite3.connect(db_file)
            con.execute(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    source TEXT,
                    company TEXT,
                    title TEXT,
                    location TEXT,
                    url TEXT,
                    description TEXT,
                    fit_score REAL,
                    status TEXT,
                    liveness_status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            con.execute(
                "INSERT INTO jobs (id, source, company, title, location, url, description, fit_score, status, liveness_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("j1", "remotive", "Doctolib", "Senior Python Engineer", "Paris", "https://jobs.smartrecruiters.com/doctolib/74399911", "Great role", 8.8, "new", "active"),
            )
            con.commit()
            con.close()

            with patch.dict("os.environ", {"RJH_DB": str(db_file)}):
                client = TestClient(ds.app)
                r1 = client.get("/api/discovery/boards")
                self.assertEqual(r1.status_code, 200)
                data1 = r1.json()
                self.assertTrue(any(b["provider"] == "smartrecruiters" and b["slug"] == "doctolib" for b in data1["boards"]))

                csv_bytes = (
                    b"Notes:\n\"Export LinkedIn\"\n\n"
                    b"First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
                    b"Jean,Valjean,https://www.linkedin.com/in/jvaljean,,Doctolib SAS,Engineering Manager,10 Nov 2025\n"
                )
                r2 = client.post(
                    "/api/referrals/import-csv",
                    files={"file": ("Connections.csv", csv_bytes, "text/csv")},
                )
                self.assertEqual(r2.status_code, 200)
                data2 = r2.json()
                self.assertEqual(data2["import"]["imported"], 1)
                self.assertEqual(data2["matches"]["matched_jobs_count"], 1)
                self.assertEqual(data2["matches"]["matched_jobs"][0]["company"], "Doctolib")


if __name__ == "__main__":
    unittest.main()
