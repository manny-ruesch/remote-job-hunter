import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cover_letter_generator
import cv_tailor


class TestSprint1OpsecAndTailoring(unittest.TestCase):
    def test_no_hardcoded_personal_pii_in_source_files(self):
        """Aucun fichier source ne doit contenir d'email hors example.*, de téléphone réel ou de chemin utilisateur Windows en dur."""
        base = Path(__file__).resolve().parent.parent
        source_files = [
            "analyzer.py",
            "auto_fill_engine.py",
            "auto_fill_helper.py",
            "config.py",
            "cover_letter_generator.py",
            "cv_profiler.py",
            "cv_tailor.py",
            "heuristic_scorer.py",
            "notifier.py",
            "dashboard/dashboard_server.py",
            "dashboard/run_dashboard.bat",
            "dashboard/run_tunnel.bat",
            "dashboard/start_dashboard_silent.vbs",
            "run_hunter.bat",
        ]

        email_re = re.compile(r"\b[\w.+-]+@([\w.-]+\.[a-zA-Z]{2,})\b")
        # Numéro français réel (+33 6/7 ou 06/07 hors séquences fictives 00 00 00 00 / 12 34 56 78 / 99 88 77 66)
        phone_re = re.compile(r"(?:\+33|0033)\s*[1-9](?:[\s.\-]?\d{2}){4}\b|\b0[67](?:[\s.\-]?\d{2}){4}\b")
        win_user_re = re.compile(r"[A-Za-z]:[/\\]Users[/\\](?!Public\b|Default\b)[A-Za-z0-9_.\-]+", re.IGNORECASE)

        allowed_email_domains = {"example.com", "example.org", "example.fr", "users.noreply.github.com"}
        allowed_dummy_phones = {
            "+33600000000",
            "+33612345678",
            "+33699887766",
            "0600000000",
            "0612345678",
            "0699887766",
        }

        for rel_path in source_files:
            fpath = base / rel_path
            if not fpath.exists():
                continue
            source = fpath.read_text(encoding="utf-8")

            for match in email_re.finditer(source):
                full_email = match.group(0)
                domain = match.group(1).lower()
                self.assertIn(
                    domain,
                    allowed_email_domains,
                    f"Email non-fictif détecté dans {rel_path} (domaine: {domain})",
                )

            for match in phone_re.finditer(source):
                normalized_phone = re.sub(r"[\s.\-]", "", match.group(0))
                self.assertIn(
                    normalized_phone,
                    allowed_dummy_phones,
                    f"Numéro de téléphone non-fictif détecté dans {rel_path}",
                )

            win_matches = win_user_re.findall(source)
            self.assertEqual(
                win_matches,
                [],
                f"Chemin utilisateur Windows codé en dur détecté dans {rel_path}",
            )

    def test_no_local_profile_values_leaked_in_source_files(self):
        """Vérifie dynamiquement qu'aucune valeur personnelle issue du profile.yaml local n'apparaît dans le code source (ignoré si profile.yaml absent)."""
        base = Path(__file__).resolve().parent.parent
        local_profile = base / "profile.yaml"
        if not local_profile.exists():
            self.skipTest("profile.yaml local absent (ex: environnement CI) — vérification dynamique ignorée")

        import yaml

        data = yaml.safe_load(local_profile.read_text(encoding="utf-8")) or {}
        identity = data.get("identity") or {}
        links = identity.get("links") or {}

        dynamic_sensitive_terms: list[tuple[str, str]] = []
        for field in ("name", "email", "phone"):
            val = str(identity.get(field) or "").strip()
            if val and len(val) >= 4 and "example." not in val.lower():
                dynamic_sensitive_terms.append((f"identity.{field}", val))

        for link_key in ("linkedin", "portfolio"):
            val = str(links.get(link_key) or "").strip()
            if val and len(val) >= 6 and "example." not in val.lower():
                clean_link = re.sub(r"^https?://(www\.)?", "", val).rstrip("/")
                if clean_link:
                    dynamic_sensitive_terms.append((f"identity.links.{link_key}", clean_link))

        if not dynamic_sensitive_terms:
            self.skipTest("Aucune valeur sensible non-fictive trouvée dans profile.yaml")

        source_files = [
            "analyzer.py",
            "auto_fill_engine.py",
            "auto_fill_helper.py",
            "config.py",
            "cover_letter_generator.py",
            "cv_profiler.py",
            "cv_tailor.py",
            "heuristic_scorer.py",
            "notifier.py",
            "dashboard/dashboard_server.py",
        ]
        for rel_path in source_files:
            source = (base / rel_path).read_text(encoding="utf-8")
            for label, secret_val in dynamic_sensitive_terms:
                self.assertNotIn(
                    secret_val.lower(),
                    source.lower(),
                    f"Fuite d'une valeur issue de {label} détectée dans {rel_path}",
                )

    def test_cv_tailor_uses_fallback_alex_dupont_when_profile_missing(self):
        """Si profile.yaml est absent, cv_tailor doit utiliser des données génériques (Alex Dupont / Tech Solutions)."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"RJH_HOME": tmp}, clear=False):
                cv_data = cv_tailor.load_candidate_cv(profile_path=Path(tmp) / "nonexistent.yaml")
                self.assertEqual(cv_data["name"], "Alex Dupont")
                self.assertEqual(cv_data["experience"][0]["company"], "Tech Solutions")
                job = {
                    "id": "job_1",
                    "company": "Acme Corp",
                    "title": "Automation Engineer",
                    "description_snippet": "Python, REST APIs, Webhooks, SQL.",
                }
                kws = cv_tailor.extract_job_keywords(job)
                plan = cv_tailor.build_deterministic_tailoring(job, kws, cv=cv_data)
                html_out = cv_tailor.render_ats_cv_html(job, plan, cv=cv_data)
                self.assertIn("Alex Dupont", html_out)
                self.assertIn("Tech Solutions", html_out)

    def test_cv_tailor_reads_custom_profile_yaml(self):
        """Si profile.yaml est fourni, cv_tailor hydrate le nom, l'email, les compétences et les faits du profil."""
        with tempfile.TemporaryDirectory() as tmp:
            pfile = Path(tmp) / "profile.yaml"
            pfile.write_text(
                """version: 1
identity:
  name: "Sophie Laurent"
  email: "sophie.laurent@example.org"
  phone: "+33 6 00 11 22 33"
  city: "Lyon"
  links:
    linkedin: "https://linkedin.com/in/sophie-laurent"
    github: "https://github.com/sophie-laurent"
search:
  titles:
    primary: ["Data Engineer"]
  keywords:
    boost: { python: 10, sql: 8, airflow: 7 }
  languages:
    spoken: { fr: native, en: C1 }
candidate:
  summary: "Ingénieure Data spécialisée dans les pipelines analytiques."
  facts:
    - "A déployé 15 pipelines ETL en production chez DataCorp"
    - "A réduit les coûts cloud BigQuery de 40%"
""",
                encoding="utf-8",
            )
            cv_data = cv_tailor.load_candidate_cv(profile_path=pfile)
            self.assertEqual(cv_data["name"], "Sophie Laurent")
            self.assertEqual(cv_data["email"], "sophie.laurent@example.org")
            job = {
                "id": "job_2",
                "company": "DataFlow",
                "title": "Data Engineer",
                "description_snippet": "Python, SQL, Airflow ETL pipelines.",
            }
            kws = cv_tailor.extract_job_keywords(job)
            plan = cv_tailor.build_deterministic_tailoring(job, kws, cv=cv_data)
            html_out = cv_tailor.render_ats_cv_html(job, plan, cv=cv_data)
            self.assertIn("Sophie Laurent", html_out)
            self.assertIn("sophie.laurent@example.org", html_out)
            self.assertIn("15 pipelines ETL en production chez DataCorp", html_out)

    def test_cover_letter_system_prompt_uses_profile_or_fallback(self):
        """cover_letter_generator construit son prompt dynamiquement depuis profile.yaml ou le fallback Alex Dupont."""
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Fallback sans profile.yaml
            applicant_fb = cover_letter_generator.load_applicant_profile(Path(tmp) / "missing.yaml")
            self.assertEqual(applicant_fb["name"], "Alex Dupont")
            prompt_fb = cover_letter_generator.build_cover_letter_system_prompt(applicant_fb)
            self.assertIn("Alex Dupont", prompt_fb)
            self.assertIn("Tech Solutions", prompt_fb)

            # 2. Avec un profile.yaml personnalisé
            pfile = Path(tmp) / "profile.yaml"
            pfile.write_text(
                """version: 1
identity:
  name: "Marc Morel"
  email: "marc.morel@example.org"
  city: "Bordeaux"
search:
  titles:
    primary: ["Product Manager"]
candidate:
  summary: "Product Manager B2B SaaS avec 5 ans d'expérience."
  facts:
    - "Croissance de +120% de l'adoption produit chez SaaSCo"
""",
                encoding="utf-8",
            )
            applicant_custom = cover_letter_generator.load_applicant_profile(pfile)
            self.assertEqual(applicant_custom["name"], "Marc Morel")
            prompt_custom = cover_letter_generator.build_cover_letter_system_prompt(applicant_custom)
            self.assertIn("Marc Morel", prompt_custom)
            self.assertIn("Croissance de +120% de l'adoption produit chez SaaSCo", prompt_custom)

    def test_index_html_ui_security_and_clipboard_fixes(self):
        """dashboard/static/index.html ne doit plus avoir d'inline quotes cassées et doit sécuriser les URLs."""
        html_path = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "index.html"
        self.assertTrue(html_path.exists(), "index.html introuvable")
        html_content = html_path.read_text(encoding="utf-8")

        # 1. escapeHtml et safeUrl doivent être définis
        self.assertIn("const escapeHtml = esc;", html_content)
        self.assertIn("function safeUrl(raw)", html_content)
        self.assertIn("function copyReferralDraft(btnOrText)", html_content)

        # 2. Pas d'attribut onclick avec JSON.stringify ou encodeURIComponent cassé par des apostrophes
        self.assertNotIn("onclick='navigator.clipboard.writeText(${JSON.stringify", html_content)
        self.assertIn('data-referral-draft="${esc(firstContact.referral_draft || \'\')}"', html_content)
        self.assertIn("copyReferralDraft(this)", html_content)

        # 3. Les URLs doivent être filtrées par safeUrl et encodées sans apostrophes/guillemets bruts
        self.assertIn("parsed.href", html_content)
        self.assertIn("safeUrl(j.url)", html_content)
        self.assertIn("safeUrl(firstContact.linkedin_url)", html_content)

    def test_readme_and_start_bat_alignment(self):
        """Le README et les scripts de démarrage doivent correspondre fidèlement à l'état du dépôt."""
        base = Path(__file__).resolve().parent.parent
        readme_path = base / "README.md"
        bat_path = base / "start_command_center.bat"

        self.assertTrue(readme_path.exists(), "README.md introuvable")
        self.assertTrue(bat_path.exists(), "start_command_center.bat doit exister à la racine")

        readme_text = readme_path.read_text(encoding="utf-8")
        # Pas de badge PyPI 404
        self.assertNotIn("https://img.shields.io/pypi/v/remote-job-hunter", readme_text)
        # 11 source engines explicitement clarifiés
        self.assertIn("11 source engines", readme_text)
        self.assertIn("50+ configured target company boards", readme_text)

    def test_rjh_env_file_support(self):
        """RJH_ENV_FILE doit charger un fichier .env arbitraire et être prioritaire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_env = Path(tmpdir) / "custom.env"
            custom_env.write_text("GOOGLE_API_KEY=test_custom_secret_key_12345\n", encoding="utf-8")
            import config
            env_override = {"RJH_ENV_FILE": str(custom_env)}
            with mock.patch.dict(os.environ, env_override, clear=False):
                # S'assurer que la clé n'est pas masquée par l'environnement existant
                saved_key = os.environ.pop("GOOGLE_API_KEY", None)
                try:
                    key = config.get_google_api_key(env_file=str(custom_env))
                    self.assertEqual(key, "test_custom_secret_key_12345")
                finally:
                    if saved_key is not None:
                        os.environ["GOOGLE_API_KEY"] = saved_key


if __name__ == "__main__":
    unittest.main()
