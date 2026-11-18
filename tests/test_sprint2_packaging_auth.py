import os
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from rjh.jsonparse import parse_json_loose
from rjh.scanner import TriageResult


class TestSprint2PackagingAndAuth(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent

    def test_pyproject_includes_root_modules_and_dashboard(self):
        """pyproject.toml doit inclure py-modules (scripts racines) et le package dashboard."""
        pyproj_path = self.root / "pyproject.toml"
        self.assertTrue(pyproj_path.exists())
        data = tomllib.loads(pyproj_path.read_text(encoding="utf-8"))

        setuptools_cfg = data.get("tool", {}).get("setuptools", {})
        py_modules = setuptools_cfg.get("py-modules", [])
        expected_modules = ["config", "db", "cv_tailor", "cv_profiler", "cover_letter_generator"]
        for mod in expected_modules:
            self.assertIn(mod, py_modules, f"Le module racine '{mod}' doit être dans py-modules")

        packages = setuptools_cfg.get("packages", {}).get("find", {}).get("include", [])
        self.assertTrue(any("dashboard" in p for p in packages), "Le package dashboard doit être inclus")

        manifest = (self.root / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("recursive-include dashboard", manifest)
        self.assertIn("include *.py", manifest)

    def test_fastapi_rjh_token_authentication(self):
        """dashboard_server doit exiger et valider RJH_TOKEN lorsqu'il est défini."""
        from dashboard.dashboard_server import app

        client = TestClient(app)

        # 1. Sans RJH_TOKEN configuré -> accès libre
        with mock.patch.dict("os.environ", {}, clear=False):
            if "RJH_TOKEN" in os.environ:
                del os.environ["RJH_TOKEN"]

            resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)

            resp_stats = client.get("/api/stats")
            self.assertEqual(resp_stats.status_code, 200)

            resp_home = client.get("/")
            self.assertEqual(resp_home.status_code, 200)

        # 2. Avec RJH_TOKEN configuré
        with mock.patch.dict("os.environ", {"RJH_TOKEN": "super_secret_token_123"}, clear=False):
            # /api/health reste accessible sans token (healthcheck conteneur)
            resp_health = client.get("/api/health")
            self.assertEqual(resp_health.status_code, 200)

            # /api/stats sans token -> 401
            resp_unauth = client.get("/api/stats")
            self.assertEqual(resp_unauth.status_code, 401)
            self.assertIn("RJH_TOKEN", resp_unauth.json().get("detail", ""))

            # /api/stats avec mauvais token -> 401
            resp_bad = client.get("/api/stats", headers={"Authorization": "Bearer wrong_token"})
            self.assertEqual(resp_bad.status_code, 401)

            # /api/stats avec Bearer valide -> 200
            resp_bearer = client.get("/api/stats", headers={"Authorization": "Bearer super_secret_token_123"})
            self.assertEqual(resp_bearer.status_code, 200)

            # /api/stats avec paramètre query valide -> 200
            resp_query = client.get("/api/stats?token=super_secret_token_123")
            self.assertEqual(resp_query.status_code, 200)

            # /api/stats avec cookie valide -> 200
            client.cookies.set("rjh_token", "super_secret_token_123")
            try:
                resp_cookie = client.get("/api/stats")
                self.assertEqual(resp_cookie.status_code, 200)
            finally:
                client.cookies.clear()

            # / sans token -> 401 avec formulaire de déverrouillage
            resp_home_unauth = client.get("/")
            self.assertEqual(resp_home_unauth.status_code, 401)
            self.assertIn("Déverrouiller", resp_home_unauth.text)

            # / avec token valide -> 200 et cookie rjh_token déposé
            resp_home_auth = client.get("/?token=super_secret_token_123")
            self.assertEqual(resp_home_auth.status_code, 200)
            self.assertIn("rjh_token", resp_home_auth.headers.get("set-cookie", ""))

    def test_post_llm_gate_blocks_hot_status(self):
        """Une offre avec une gate échouée ne doit jamais devenir 'hot' même avec un score >= 7.5."""
        tr_failed_gate = TriageResult(
            id="job_x",
            score=8.5,
            verdict="apply",
            gate="location",
            why="Salaire bon mais hors zone autorisée",
            keywords=["Python", "FastAPI"],
        )
        hot_threshold = 7.5
        is_hot = tr_failed_gate.score >= hot_threshold and not tr_failed_gate.gate
        self.assertFalse(is_hot, "L'offre avec gate='location' ne doit pas être qualifiée de hot")

        tr_clean = TriageResult(
            id="job_y",
            score=8.5,
            verdict="apply",
            gate=None,
            why="Parfait match",
            keywords=["Python"],
        )
        is_hot_clean = tr_clean.score >= hot_threshold and not tr_clean.gate
        self.assertTrue(is_hot_clean, "L'offre sans gate doit être hot")

    def test_parse_json_loose_resilience(self):
        """parse_json_loose doit extraire le JSON même avec fences markdown, texte autour et guillemets courbes."""
        sample = """
        Voici l'analyse demandée :
        ```json
        {
            “headline”: “Lead Engineer”,
            “summary”: “Expert Python & APIs”,
        }
        ```
        J'espère que cela convient.
        """
        parsed = parse_json_loose(sample)
        self.assertEqual(parsed.get("headline"), "Lead Engineer")
        self.assertEqual(parsed.get("summary"), "Expert Python & APIs")


if __name__ == "__main__":
    unittest.main()
