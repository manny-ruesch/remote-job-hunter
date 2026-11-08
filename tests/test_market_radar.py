from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rjh.market_radar import (
    add_skill_to_profile,
    analyze_market_radar,
    canonicalize_skill,
)
from rjh.profile import load_profile
from rjh.scanner import init_jobs_db


SAMPLE_PROFILE_YAML = """# Commentaire de tête à préserver
identity:
  name: "Manny Ruesch"
  city: "Paris"

candidate:
  summary: "AI & Automation Builder (n8n, Python, LLMs)."
  facts:
    - "5 ans d'expérience Python et n8n"

search:
  titles:
    primary: ["AI Automation Engineer", "RevOps Engineer"]
    related: ["Python Developer"]
    exclude: ["Android", "iOS"]
  keywords:
    must_any: ["python", "n8n"]
    boost:
      # Commentaire interne boost
      n8n: 12
      python: 8
      legacy_dead_kw: 4
    penalize:
      php: 10
  location:
    countries: ["FR"]
    eligible_regions: ["worldwide", "eu", "fr"]
"""


class MarketRadarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile_path = self.root / "profile.yaml"
        self.profile_path.write_text(SAMPLE_PROFILE_YAML, encoding="utf-8")
        self.cand_json_path = self.root / "candidate_profile.json"
        self.cand_json_path.write_text(
            json.dumps(
                {
                    "name": "Manny Ruesch",
                    "target_role": "AI Automation Engineer",
                    "core_stack": ["Python", "n8n"],
                    "positive_keywords": ["python", "n8n"],
                }
            ),
            encoding="utf-8",
        )
        self.db_path = self.root / "jobs.db"
        init_jobs_db(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _insert_job(
        self,
        *,
        source: str = "ashby",
        company: str = "Acme",
        title: str = "AI Automation Engineer",
        location: str = "Remote - EU",
        url: str = "https://jobs.ashbyhq.com/acme/1",
        description: str = "",
        fit_score: float = 8.5,
        tech_match: str = "",
        dedupe_key: str = "",
    ) -> None:
        con = sqlite3.connect(str(self.db_path))
        try:
            con.execute(
                """
                INSERT INTO jobs (source, company, title, location, url, description, fit_score, tech_match, dedupe_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source, company, title, location, url, description, fit_score, tech_match, dedupe_key),
            )
            con.commit()
        finally:
            con.close()

    def test_special_punctuation_skills_and_aliases(self) -> None:
        # C++, C#, .NET, Node.js, K8s should all be matched accurately
        for idx in range(4):
            self._insert_job(
                company=f"Corp{idx}",
                title="AI Automation Engineer",
                url=f"https://jobs.ashbyhq.com/corp/{idx}",
                description="We use Python, n8n, C++, C#, .NET, NodeJS and K8s in production.",
                fit_score=8.8,
                tech_match="LangGraph, HubSpot",
                dedupe_key=f"corp{idx}|aiautomationengineer",
            )
        report = analyze_market_radar(
            self.db_path,
            self.profile_path,
            self.cand_json_path,
        )
        self.assertEqual(report.deduped_jobs_count, 4)
        self.assertEqual(report.viable_jobs_count, 4)

        top_names = {it.skill for it in report.top_skills}
        self.assertIn("c++", top_names)
        self.assertIn("c#", top_names)
        self.assertIn(".net", top_names)
        self.assertIn("node.js", top_names)
        self.assertIn("kubernetes", top_names)
        self.assertIn("langgraph", top_names)

        # Python and n8n are in profile -> covered
        covered_names = {it.skill for it in report.covered_skills}
        self.assertIn("python", covered_names)
        self.assertIn("n8n", covered_names)

        # LangGraph, HubSpot, C++, C#, .NET are missing from profile
        missing_names = {it.skill for it in report.missing_skills}
        self.assertIn("langgraph", missing_names)
        self.assertIn("hubspot", missing_names)
        self.assertIn("c++", missing_names)

        # Dead boost keyword detection
        self.assertIn("legacy_dead_kw", report.dead_boost_keywords)
        self.assertNotIn("python", report.dead_boost_keywords)

    def test_deduplication_and_gate_audit(self) -> None:
        # Same job inserted 3 times from different sources -> counted once
        for src in ("ashby", "linkedin", "wttj"):
            self._insert_job(
                source=src,
                company="DupCo",
                title="AI Automation Engineer",
                url=f"https://example.com/{src}",
                description="Python, n8n, LangChain",
                fit_score=9.0,
                dedupe_key="dupco|aiautomationengineer",
            )
        # Excluded title job -> rejected by title_prefilter, skills must NOT pollute missing_skills
        self._insert_job(
            company="MobileCo",
            title="Senior Android Developer",
            url="https://example.com/android",
            description="Swift, Kotlin, Java, Gradle, Mobile",
            fit_score=1.0,
            dedupe_key="mobileco|seniorandroiddeveloper",
        )
        report = analyze_market_radar(self.db_path, self.profile_path, self.cand_json_path)
        self.assertEqual(report.total_jobs_analyzed, 4)
        self.assertEqual(report.deduped_jobs_count, 2)
        self.assertEqual(report.viable_jobs_count, 1)

        # Verify gate stats recorded the title_prefilter rejection
        gate_ids = {g.gate_id: g for g in report.gate_stats}
        self.assertIn("title_prefilter", gate_ids)
        self.assertEqual(gate_ids["title_prefilter"].rejected_count, 1)

    def test_add_skill_to_profile_preserves_comments_and_is_idempotent(self) -> None:
        # Add C# (has special char #)
        res1 = add_skill_to_profile(
            "C#",
            self.profile_path,
            self.cand_json_path,
            weight=7.0,
        )
        self.assertEqual(res1["status"], "ok")
        self.assertEqual(res1["weight"], 7.0)

        raw_after = self.profile_path.read_text(encoding="utf-8")
        # Comments must still be present
        self.assertIn("# Commentaire de tête à préserver", raw_after)
        self.assertIn("# Commentaire interne boost", raw_after)

        # Profile must still load cleanly via strict load_profile()
        prof = load_profile(self.profile_path)
        self.assertEqual(prof.search.keywords.boost.get("c#"), 7.0)

        # Re-adding with lower weight (4.0) must NOT downgrade existing weight (7.0)
        res2 = add_skill_to_profile(
            "c#",
            self.profile_path,
            self.cand_json_path,
            weight=4.0,
        )
        self.assertEqual(res2["weight"], 7.0)
        prof2 = load_profile(self.profile_path)
        self.assertEqual(prof2.search.keywords.boost.get("c#"), 7.0)

        # Check candidate_profile.json idempotence
        cand_data = json.loads(self.cand_json_path.read_text(encoding="utf-8"))
        c_sharp_entries = [x for x in cand_data["core_stack"] if x.lower() == "c#"]
        self.assertEqual(len(c_sharp_entries), 1)

    def test_canonicalize_skill_non_tech_sectors(self) -> None:
        canon, cat = canonicalize_skill("EHPAD")
        self.assertEqual(canon, "geriatrie")
        self.assertEqual(cat, "Santé, Médical & Social")

        canon2, cat2 = canonicalize_skill("Comptabilité")
        self.assertEqual(canon2, "comptabilite")
        self.assertEqual(cat2, "Finance, RH & Juridique")


if __name__ == "__main__":
    unittest.main()
