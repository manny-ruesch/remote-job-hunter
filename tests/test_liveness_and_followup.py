"""Tests unitaires pour le Pack 1 :
- Liveness Check & Anti-SSRF (`rjh/liveness.py`)
- Détection d'Anti-Ghost Jobs / Reposts (`detect_reposts_in_db`, `filter_unseen_jobs`)
- Follow-up Cadence Tracker (`compute_followup_status`, `db.update_job_status`)
- Batch Liveness en 3 phases (Scatter-Gather + Single-Transaction Write)
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rjh.liveness import (
    SSRFBlockedError,
    check_job_liveness,
    compute_followup_status,
    detect_reposts_in_db,
    format_utc,
    parse_utc,
    run_liveness_batch,
    validate_public_url,
)
from rjh.models import Job
from rjh.scanner import filter_unseen_jobs, init_jobs_db


class TestSSRFAndLiveness(unittest.TestCase):
    def test_blocks_private_and_loopback_urls(self):
        for bad_url in [
            "http://127.0.0.1:5070/api/trigger-run",
            "http://localhost:8000/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.168.1.15/secret",
            "http://10.0.0.5/internal",
            "file:///etc/passwd",
            "ftp://example.com/file",
        ]:
            with self.subTest(bad_url=bad_url):
                with self.assertRaises(SSRFBlockedError):
                    validate_public_url(bad_url, resolve_dns=False)

    def test_allows_public_https_url(self):
        parsed = validate_public_url("https://boards.greenhouse.io/stripe/jobs/12345", resolve_dns=False)
        self.assertEqual(parsed.hostname, "boards.greenhouse.io")

    def test_greenhouse_liveness_active_and_expired(self):
        def fake_active(url: str):
            self.assertIn("boards-api.greenhouse.io/v1/boards/acme/jobs/98765", url)
            return 200, url, json.dumps({"id": 98765, "title": "Senior AI Engineer"})

        res = check_job_liveness(
            "https://boards.greenhouse.io/acme/jobs/98765",
            resolve_dns=False,
            fetcher=fake_active,
        )
        self.assertEqual(res.status, "active")
        self.assertEqual(res.ats_provider, "greenhouse")

        def fake_404(url: str):
            return 404, url, "{}"

        res_exp = check_job_liveness(
            "https://job-boards.greenhouse.io/acme/jobs/98765",
            resolve_dns=False,
            fetcher=fake_404,
        )
        self.assertEqual(res_exp.status, "expired")
        self.assertEqual(res_exp.http_status, 404)

    def test_ashby_api_avoids_spa_200_trap(self):
        job_uuid = "11111111-2222-3333-4444-555555555555"

        def fake_ashby_board(url: str):
            self.assertEqual(url, "https://api.ashbyhq.com/posting-api/job-board/linear")
            return 200, url, json.dumps({
                "jobs": [
                    {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "isListed": True}
                ]
            })

        # L'offre n'est plus dans la liste Ashby -> doit être 'expired' même si HTTP = 200
        res_missing = check_job_liveness(
            f"https://jobs.ashbyhq.com/linear/{job_uuid}",
            resolve_dns=False,
            fetcher=fake_ashby_board,
        )
        self.assertEqual(res_missing.status, "expired")
        self.assertEqual(res_missing.ats_provider, "ashby")

        # Si l'offre est présente et listée -> 'active'
        def fake_ashby_present(url: str):
            return 200, url, json.dumps({
                "jobs": [
                    {"id": job_uuid, "isListed": True}
                ]
            })

        res_active = check_job_liveness(
            f"https://jobs.ashbyhq.com/linear/{job_uuid}",
            resolve_dns=False,
            fetcher=fake_ashby_present,
        )
        self.assertEqual(res_active.status, "active")

    def test_generic_html_expired_marker_and_redirect(self):
        def fake_marker(url: str):
            return 200, url, "<html><body>Sorry, this job is no longer accepting applications.</body></html>"

        res = check_job_liveness("https://example.com/careers/job-1234", resolve_dns=False, fetcher=fake_marker)
        self.assertEqual(res.status, "expired")

        def fake_redirect_root(url: str):
            return 200, "https://example.com/careers", "<html><body>All our open roles</body></html>"

        res_redir = check_job_liveness(
            "https://example.com/careers/senior-python-automation-engineer-9981",
            resolve_dns=False,
            fetcher=fake_redirect_root,
        )
        self.assertEqual(res_redir.status, "expired")


class TestAntiGhostRepostAndBatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_jobs.db"
        init_jobs_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cross_source_syndication_is_not_counted_as_repost(self):
        sample_job = Job(
            source="Remotive",
            source_id="j2",
            title="AI Engineer",
            company="Acme",
            location="Remote",
            url="https://remotive.com/jobs/999",
            description="Build AI",
        )
        con = sqlite3.connect(self.db_path)
        now_str = format_utc(datetime.now(timezone.utc))
        con.execute(
            """
            INSERT INTO jobs (id, source, title, company, url, status, fit_score, analysis_status, dedupe_key, created_at)
            VALUES (?, ?, ?, ?, ?, 'hot', 8.5, 'ok', ?, ?)
            """,
            ("j1", "RemoteOK", "AI Engineer", "Acme", "https://remoteok.com/jobs/1", sample_job.dedupe_key, now_str),
        )
        con.commit()
        con.close()

        # Même offre vue simultanément sur Remotive (syndication) -> ne doit PAS incrémenter repost_count
        incoming = [sample_job]
        unseen = filter_unseen_jobs(self.db_path, incoming)
        self.assertEqual(len(unseen), 0)

        con = sqlite3.connect(self.db_path)
        repost_count = con.execute("SELECT repost_count FROM jobs WHERE id = 'j1'").fetchone()[0]
        con.close()
        self.assertEqual(repost_count, 0)

    def test_same_source_new_url_is_counted_as_repost(self):
        reposted_job = Job(
            source="greenhouse",
            source_id="gh_200",
            title="AI Engineer",
            company="Acme",
            location="Remote",
            url="https://boards.greenhouse.io/acme/jobs/200",
            description="Build AI",
        )
        con = sqlite3.connect(self.db_path)
        now_str = format_utc(datetime.now(timezone.utc))
        con.execute(
            """
            INSERT INTO jobs (id, source, title, company, url, status, fit_score, analysis_status, dedupe_key, created_at)
            VALUES (?, ?, ?, ?, ?, 'hot', 8.5, 'ok', ?, ?)
            """,
            ("j1", "Greenhouse · acme", "AI Engineer", "Acme", "https://boards.greenhouse.io/acme/jobs/100", reposted_job.dedupe_key, now_str),
        )
        con.commit()
        con.close()

        # Même source Greenhouse mais nouvel ID/URL -> vrai repost
        incoming = [reposted_job]
        unseen = filter_unseen_jobs(self.db_path, incoming)
        self.assertEqual(len(unseen), 0)

        con = sqlite3.connect(self.db_path)
        row = con.execute("SELECT repost_count, url, liveness_status FROM jobs WHERE id = 'j1'").fetchone()
        con.close()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "https://boards.greenhouse.io/acme/jobs/200")
        self.assertEqual(row[2], "active")

    def test_run_liveness_batch_updates_records(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            """
            INSERT INTO jobs (id, source, title, company, url, status, fit_score, analysis_status)
            VALUES
              ('job_alive', 'Greenhouse', 'Role A', 'CompA', 'https://boards.greenhouse.io/compa/jobs/111', 'hot', 9.0, 'ok'),
              ('job_dead', 'Greenhouse', 'Role B', 'CompB', 'https://boards.greenhouse.io/compb/jobs/222', 'hot', 8.0, 'ok')
            """
        )
        con.commit()
        con.close()

        def fake_fetch(url: str):
            if "111" in url:
                return 200, url, json.dumps({"id": 111, "title": "Role A"})
            return 404, url, "{}"

        summary = run_liveness_batch(self.db_path, limit=10, min_score=7.0, resolve_dns=False, fetcher=fake_fetch)
        self.assertEqual(summary["checked"], 2)
        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["expired"], 1)


class TestFollowupCadence(unittest.TestCase):
    def test_parse_utc_handles_sqlite_and_iso_dates(self):
        dt_sqlite = parse_utc("2026-11-01 14:30:00")
        dt_iso = parse_utc("2026-11-01T14:30:00Z")
        self.assertIsNotNone(dt_sqlite)
        self.assertEqual(dt_sqlite, dt_iso)

    def test_followup_states_scheduled_due_and_ghosted(self):
        now = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

        # J+3 -> scheduled
        row_scheduled = {
            "status": "applied",
            "company": "Stripe",
            "title": "Automation Lead",
            "applied_at": format_utc(now - timedelta(days=3)),
            "next_followup_at": format_utc(now + timedelta(days=4)),
            "followup_count": 0,
        }
        info_sched = compute_followup_status(row_scheduled, now=now)
        self.assertEqual(info_sched.state, "scheduled")
        self.assertEqual(info_sched.days_until_followup, 4)

        # J+8 -> due
        row_due = {
            "status": "applied",
            "company": "Stripe",
            "title": "Automation Lead",
            "applied_at": format_utc(now - timedelta(days=8)),
            "next_followup_at": format_utc(now - timedelta(days=1)),
            "followup_count": 0,
        }
        info_due = compute_followup_status(row_due, now=now)
        self.assertEqual(info_due.state, "due")
        self.assertIn("Stripe", info_due.suggested_message)

        # J+22 avec 2 relances -> ghosted
        row_ghosted = {
            "status": "applied",
            "company": "Stripe",
            "title": "Automation Lead",
            "applied_at": format_utc(now - timedelta(days=22)),
            "next_followup_at": format_utc(now - timedelta(days=1)),
            "followup_count": 2,
        }
        info_ghost = compute_followup_status(row_ghosted, now=now)
        self.assertEqual(info_ghost.state, "ghosted")


if __name__ == "__main__":
    unittest.main()
