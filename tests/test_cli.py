import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from .helpers import MockServer, make_http
from rjh import cli
from rjh.doctor import render, run_doctor, unicode_supported
from rjh.http import HttpClient, RateLimiter
from rjh.paths import env_path, load_dotenv, profile_path
from rjh.profile import load_profile
from rjh.wizard import IO, normalize_country, parse_titles, pick_gemini_model, run_wizard


def feeder(answers):
    """Comme un vrai input() : à court de réponses (Ctrl+D / flux fermé), il lève EOFError."""
    it = iter(answers)

    def inp(_prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None
    return inp


def scripted(*answers, secret=""):
    out = []
    return IO(inp=feeder(answers), out=out.append, secret=lambda _p: secret), out


def tmp_home():
    return Path(tempfile.mkdtemp())


def run_cli(argv, env=None, answers=(), **kw):
    out = []
    io = IO(inp=feeder(answers), out=out.append, secret=lambda _p: "")
    code = cli.main(argv, io=io, environ=env if env is not None else {}, **kw)
    return code, "\n".join(out)


class Pure(unittest.TestCase):
    def test_parse_titles(self):
        self.assertEqual(parse_titles("infirmier, IDE ; infirmier\n 'Aide soignant'."), ["infirmier", "IDE", "Aide soignant"])
        self.assertEqual(parse_titles("a / b"), ["a", "b"])
        self.assertEqual(parse_titles("  ,  "), [])
        self.assertEqual(len(parse_titles(",".join(str(i) for i in range(20)))), 6)

    def test_country(self):
        self.assertEqual(normalize_country(" fr "), "FR")
        self.assertEqual(normalize_country("gb"), "UK")
        for bad in ("France", "F", "12", "", None):
            self.assertIsNone(normalize_country(bad))

    def test_gemini_model_pick_is_dynamic_and_skips_variants(self):
        models = ["models/gemini-2.5-flash", "models/gemini-3.5-flash", "models/gemini-3.5-flash-lite",
                  "models/gemini-3.5-pro", "models/gemini-3.5-flash-image", "models/gemini-3.10-flash"]
        self.assertEqual(pick_gemini_model(models), "gemini-3.10-flash")            # 3.10 > 3.5 (comparaison numérique)
        self.assertEqual(pick_gemini_model(models[:5]), "gemini-3.5-flash")
        self.assertEqual(pick_gemini_model(["models/gemini-pro-flash-latest", "models/gemini-x-flash-lite"]), "gemini-pro-flash-latest")
        self.assertIsNone(pick_gemini_model(["models/embedding-001"]))
        self.assertIsNone(pick_gemini_model([]))


class Wizard(unittest.TestCase):
    def test_remote_flow_builds_a_valid_profile(self):
        home = tmp_home()
        io, _ = scripted("comptable, contrôleur de gestion", "1", "fr", "2", "1")
        path = run_wizard(io, lang="fr", home=home)
        p = load_profile(path)
        self.assertEqual(p.search.titles.primary, ["comptable", "contrôleur de gestion"])
        self.assertEqual((p.search.work_mode.remote, p.search.work_mode.hybrid_ok), ("required", False))
        self.assertEqual(p.search.location.eligible_regions, ["FR"])
        self.assertEqual((p.search.seniority.level, p.search.seniority.years_experience, p.search.seniority.max_years_required), ("mid", 5, 7))
        self.assertEqual(p.sources.enabled, ["remotive"])
        self.assertEqual((p.llm.provider, p.llm.api_key_env, p.llm.language), ("manual", "", "fr"))

    def test_onsite_flow_asks_the_city_and_warns_about_adzuna_keys(self):
        io, out = scripted("infirmier", "3", "fr", "", "Lyon", "1", "1")        # ville vide refusée, puis « Lyon »
        p = load_profile(run_wizard(io, lang="fr", home=tmp_home()))
        self.assertEqual((p.search.work_mode.remote, p.search.work_mode.cities), ("any", ["Lyon"]))
        self.assertEqual(p.sources.enabled, ["adzuna"])
        self.assertEqual(p.sources.options["adzuna"]["where"], "Lyon")
        self.assertIn("ADZUNA_APP_ID", "\n".join(out))
        io2, out2 = scripted("infirmier", "3", "fr", "Lyon", "1", "1")
        run_wizard(io2, lang="fr", home=tmp_home(), environ={"ADZUNA_APP_ID": "a", "ADZUNA_APP_KEY": "b"})
        self.assertNotIn("ADZUNA_APP_ID", "\n".join(out2))

    def test_invalid_answers_are_asked_again(self):
        io, out = scripted("", "dev python", "9", "x", "1", "France", "fr", "2", "1")
        p = load_profile(run_wizard(io, lang="fr", home=tmp_home()))
        text = "\n".join(out)
        self.assertIn("au moins un poste", text)
        self.assertIn("numéros proposés", text)
        self.assertIn("Deux lettres", text)
        self.assertEqual(p.search.titles.primary, ["dev python"])

    def test_uk_is_normalised_for_regions_and_adzuna(self):
        p = load_profile(run_wizard(scripted("nurse", "1", "gb", "2", "1")[0], lang="en", home=tmp_home()))
        self.assertEqual(p.search.location.eligible_regions, ["UK"])
        self.assertEqual(p.search.location.countries, ["gb"])
        self.assertEqual(p.llm.language, "en")

    def test_special_characters_survive_the_yaml_round_trip(self):
        titles = ["Chef de projet: IT", "null", "123", "C++ developer", "Ingénieur \"R&D\""]
        io, _ = scripted(", ".join(titles), "1", "FR", "2", "1")
        # note : parse_titles retire les guillemets de bord ; on vérifie donc l'égalité avec sa propre sortie
        self.assertEqual(load_profile(run_wizard(io, lang="fr", home=tmp_home())).search.titles.primary, parse_titles(", ".join(titles)))

    def test_api_key_is_typed_hidden_saved_to_env_600_and_never_in_the_profile(self):
        home = tmp_home()
        io, out = scripted("comptable", "1", "FR", "2", "2", secret="AIza-SECRET-KEY-123")
        path = run_wizard(io, lang="fr", home=home, list_models=lambda prov, key: ["models/gemini-3.5-flash", "models/gemini-2.5-flash"])
        self.assertNotIn("SECRET-KEY", path.read_text(encoding="utf-8"))
        self.assertNotIn("SECRET-KEY", "\n".join(out))
        self.assertIn("GOOGLE_API_KEY=AIza-SECRET-KEY-123", env_path(home).read_text(encoding="utf-8"))
        if os.name == "posix":
            self.assertEqual(oct(env_path(home).stat().st_mode & 0o777), "0o600")
        p = load_profile(path)
        self.assertEqual((p.llm.provider, p.llm.model, p.llm.api_key_env), ("gemini", "gemini-3.5-flash", "GOOGLE_API_KEY"))

    def test_key_already_in_the_environment_is_reused_not_copied(self):
        home = tmp_home()
        asked = []
        io = IO(inp=feeder(["comptable", "1", "FR", "2", "2"]), out=lambda m: None, secret=lambda p: asked.append(p) or "")
        run_wizard(io, lang="fr", home=home, environ={"GOOGLE_API_KEY": "from-env"}, list_models=lambda *a: ["models/gemini-3.5-flash"])
        self.assertEqual(asked, [])                              # aucune saisie demandée
        self.assertFalse(env_path(home).exists())                # et la clé n'est pas dupliquée sur le disque

    def test_no_key_yet_still_creates_a_profile_and_explains(self):
        io, out = scripted("comptable", "1", "FR", "2", "2", secret="")
        p = load_profile(run_wizard(io, lang="fr", home=tmp_home(), list_models=lambda *a: []))
        self.assertEqual((p.llm.provider, p.llm.model), ("gemini", ""))
        self.assertIn("GOOGLE_API_KEY", "\n".join(out))

    def test_other_providers(self):
        io, _ = scripted("comptable", "1", "FR", "2", "3", "banana", "ollama", "llama3")
        p = load_profile(run_wizard(io, lang="fr", home=tmp_home()))
        self.assertEqual((p.llm.provider, p.llm.model, p.llm.api_key_env), ("ollama", "llama3", ""))
        io2, _ = scripted("comptable", "1", "FR", "2", "3", "openai", "un-modele", secret="sk-x")
        home = tmp_home()
        p2 = load_profile(run_wizard(io2, lang="fr", home=home))
        self.assertEqual(p2.llm.api_key_env, "OPENAI_API_KEY")
        self.assertIn("OPENAI_API_KEY=sk-x", env_path(home).read_text(encoding="utf-8"))

    def test_existing_profile_is_protected_and_backed_up(self):
        home = tmp_home()
        run_wizard(scripted("a", "1", "FR", "2", "1")[0], lang="fr", home=home)
        before = profile_path(home).read_text(encoding="utf-8")
        self.assertIsNone(run_wizard(scripted("n")[0], lang="fr", home=home))
        self.assertEqual(profile_path(home).read_text(encoding="utf-8"), before)
        run_wizard(scripted("o", "b", "1", "FR", "2", "1")[0], lang="fr", home=home)
        self.assertIn("a", profile_path(home).with_name("profile.yaml.bak").read_text(encoding="utf-8"))
        self.assertEqual(load_profile(profile_path(home)).search.titles.primary, ["b"])

    def test_cancel_writes_nothing(self):
        home = tmp_home()
        code, out = run_cli(["--home", str(home), "--lang", "fr", "init"], answers=["comptable"])      # plus de réponses : EOF
        self.assertEqual(code, 130)
        self.assertIn("rien n'a été modifié", out)
        self.assertFalse(profile_path(home).exists())

    def test_non_interactive_mode(self):
        home = tmp_home()
        code, _ = run_cli(["--home", str(home), "--lang", "en", "init", "--yes", "--title", "nurse", "--country", "ca", "--mode", "preferred", "--level", "junior"])
        self.assertEqual(code, 0)
        p = load_profile(profile_path(home))
        self.assertEqual((p.search.work_mode.remote, p.search.seniority.level, p.search.location.eligible_regions), ("preferred", "junior", ["CA"]))
        code, out = run_cli(["--home", str(home), "--lang", "en", "init", "--yes", "--title", "x"])    # --country manquant
        self.assertEqual(code, 1)                                                                       # profil existant : refus, pas d'écrasement
        code, out = run_cli(["--home", str(tmp_home()), "--lang", "en", "init", "--yes", "--title", "x"])
        self.assertEqual(code, 2)
        self.assertIn("--country", out)


GOOD = textwrap.dedent("""\
    version: 1
    search: {titles: {primary: [comptable]}, work_mode: {remote: required}, location: {eligible_regions: [FR]}}
    sources: {enabled: [remotive]}
    llm: {provider: manual}
    """)


def home_with(profile_text=GOOD):
    home = tmp_home()
    profile_path(home).write_text(profile_text, encoding="utf-8")
    return home


def status(checks, label_part):
    return [c for c in checks if label_part.lower() in c.label.lower()]


class Doctor(unittest.TestCase):
    def test_everything_ready_in_manual_mode(self):
        http, t = make_http(lambda c: {})
        checks = run_doctor(home_with(), lang="fr", online=True, environ={}, http_factory=lambda: http)
        self.assertTrue(all(c.status == "ok" for c in checks), [(c.label, c.status, c.message) for c in checks if c.status != "ok"])
        self.assertIn("mode manuel", status(checks, "ia")[0].message)

    def test_missing_profile_and_invalid_profile(self):
        c = run_doctor(tmp_home(), lang="fr", online=False, environ={})
        self.assertEqual(status(c, "profil")[0].status, "fail")
        self.assertIn("rjh init", status(c, "profil")[0].hint)
        c = run_doctor(home_with("search: {seniorty: {}}"), lang="fr", online=False, environ={})
        self.assertIn("seniority", status(c, "profil")[0].message)

    def test_missing_key_names_the_variable_and_the_env_file(self):
        prof = GOOD.replace("llm: {provider: manual}", "llm: {provider: gemini, model: m, api_key_env: GOOGLE_API_KEY}")
        home = home_with(prof)
        c = status(run_doctor(home, lang="fr", online=False, environ={}), "ia")[0]
        self.assertEqual(c.status, "fail")
        self.assertIn("GOOGLE_API_KEY", c.message)
        self.assertIn(".env", c.hint)

    def test_gemini_online_model_available_missing_and_key_never_printed(self):
        models = {"models": [{"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]}]}
        with MockServer(lambda r: (200, models)) as srv:
            def prof(model):
                return GOOD.replace("llm: {provider: manual}", f"llm: {{provider: gemini, model: {model}, api_key_env: K, base_url: '{srv.url}/v1beta'}}")
            env = {"K": "TOP-SECRET-VALUE"}
            ok = run_doctor(home_with(prof("gemini-3.5-flash")), lang="fr", online=True, environ=env)
            self.assertEqual(status(ok, "ia")[0].status, "ok")
            bad = run_doctor(home_with(prof("gemini-2.5-flash")), lang="fr", online=True, environ=env)
            ia = status(bad, "ia")[0]
            self.assertEqual(ia.status, "fail")
            self.assertIn("gemini-3.5-flash", ia.hint)               # suggestion concrète
            for checks in (ok, bad):
                self.assertNotIn("TOP-SECRET", render(checks, "fr", unicode_ok=True, elapsed=0))
            self.assertEqual(srv.requests[0]["headers"]["x-goog-api-key"], "TOP-SECRET-VALUE")
            offline = run_doctor(home_with(prof("gemini-3.5-flash")), lang="fr", online=False, environ=env)
            self.assertIn("16 caractères", status(offline, "ia")[0].message)       # présence et longueur seulement

    def test_sources_missing_env_unknown_and_none(self):
        prof = GOOD.replace("enabled: [remotive]", "enabled: [adzuna, nimporte]")
        c = status(run_doctor(home_with(prof), lang="fr", online=False, environ={}), "source")
        self.assertIn("ADZUNA_APP_ID", c[0].message)
        self.assertIn(".env", c[0].hint)
        self.assertIn("inconnue", c[1].message)
        self.assertIn("remotive", c[1].hint)
        none = GOOD.replace("sources: {enabled: [remotive]}", "sources: {enabled: []}")
        self.assertEqual(status(run_doctor(home_with(none), lang="fr", online=False, environ={}), "source")[0].status, "fail")

    def test_source_ping_outcomes_never_leak_the_response_body(self):
        for status_code, expect in ((200, "répond"), (401, "clé refusée"), (404, "introuvable"), (429, "limite"), (503, "indisponible")):
            http, _ = make_http(lambda c, s=status_code: (s, {"echo": "MY-APP-KEY-LEAKED"}))
            checks = run_doctor(home_with(), lang="fr", online=True, environ={}, http_factory=lambda: http)
            src = status(checks, "source")[0]
            self.assertIn(expect, src.message)
            self.assertEqual(src.status, "ok" if status_code == 200 else "fail")
            self.assertNotIn("LEAKED", render(checks, "fr", unicode_ok=True, elapsed=0))

    def test_network_down_is_reported_not_raised(self):
        def boom(*a):
            raise urllib.error.URLError("down")
        http = HttpClient(transport=boom, limiter=RateLimiter(0), sleep=lambda s: None, max_retries=0)
        src = status(run_doctor(home_with(), lang="fr", online=True, environ={}, http_factory=lambda: http), "source")[0]
        self.assertEqual(src.status, "fail")
        self.assertIn("connexion impossible", src.message)

    def test_boardless_ats_source_is_a_warning(self):
        prof = GOOD.replace("enabled: [remotive]", "enabled: [greenhouse]")
        http, _ = make_http(lambda c: {})
        src = status(run_doctor(home_with(prof), lang="fr", online=True, environ={}, http_factory=lambda: http), "source")[0]
        self.assertEqual(src.status, "warn")

    def test_offline_makes_no_network_call(self):
        http, t = make_http(lambda c: {})
        run_doctor(home_with(), lang="fr", online=False, environ={}, http_factory=lambda: http)
        self.assertEqual(t.calls, [])

    def test_network_checks_run_in_parallel(self):
        def slow(call):
            time.sleep(0.3)
            return {}
        prof = GOOD.replace("enabled: [remotive]", "enabled: [remotive, greenhouse, adzuna, remotive]")
        prof = prof.replace("sources: {", "sources: {boards: {greenhouse: [a]}, ")
        http, _ = make_http(slow)
        t0 = time.monotonic()
        run_doctor(home_with(prof), lang="fr", online=True, environ={"ADZUNA_APP_ID": "i", "ADZUNA_APP_KEY": "k"}, http_factory=lambda: http)
        self.assertLess(time.monotonic() - t0, 0.8)              # 4 appels de 0,3 s : ≥ 1,2 s si séquentiels

    def test_database_problem_is_a_failure_with_a_hint(self):
        d = tmp_home()
        c = status(run_doctor(home_with(), lang="fr", online=False, environ={"RJH_DB": str(d)}), "sqlite")[0]   # un DOSSIER à la place du fichier
        self.assertEqual(c.status, "fail")
        self.assertIn("RJH_DB", c.hint)

    @unittest.skipUnless(os.name == "posix", "droits POSIX")
    def test_world_readable_env_file_is_a_warning(self):
        home = home_with()
        env_path(home).write_text("K=v\n", encoding="utf-8")
        os.chmod(env_path(home), 0o644)
        c = status(run_doctor(home, lang="fr", online=False, environ={}), ".env")[0]
        self.assertEqual(c.status, "warn")
        self.assertIn("chmod 600", c.hint)

    def test_render_ascii_fallback_json_and_exit_codes(self):
        checks = run_doctor(tmp_home(), lang="en", online=False, environ={})
        text = render(checks, "en", unicode_ok=False, elapsed=0.4)
        self.assertIn("[XX]", text)
        self.assertIn("[OK]", text)
        self.assertNotIn("✔", text)
        self.assertTrue(text.isascii() or "—" in text)
        code, out = run_cli(["--home", str(tmp_home()), "--lang", "en", "doctor", "--offline", "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertFalse(data["ok"])
        code, _ = run_cli(["--home", str(home_with()), "--lang", "en", "doctor", "--offline"])
        self.assertEqual(code, 0)

    def test_unicode_detection(self):
        class S: encoding = "cp1252"
        class U: encoding = "UTF-8"
        self.assertFalse(unicode_supported(S()))
        self.assertTrue(unicode_supported(U()))

    def test_english_messages(self):
        text = render(run_doctor(home_with(), lang="en", online=False, environ={}), "en", unicode_ok=True, elapsed=0)
        self.assertIn("Everything is ready.", text)
        self.assertIn("Data folder", text)


class Serve(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.runner = lambda app, **kw: self.calls.append((app, kw))
        self.mod = tempfile.mkdtemp()
        Path(self.mod, "fakeapp_rjh.py").write_text("app = object()\n", encoding="utf-8")
        sys.path.insert(0, self.mod)
        self.addCleanup(lambda: sys.path.remove(self.mod))

    def serve(self, argv, env=None, home=None):
        home = home or home_with()
        return run_cli(["--home", str(home), "--lang", "en", "serve", "--app", "fakeapp_rjh:app", *argv], env=env if env is not None else {}, runner=self.runner)

    def test_refuses_to_listen_on_the_network_without_a_token(self):
        for host in ("0.0.0.0", "192.168.1.20", "::"):
            code, out = self.serve(["--host", host])
            self.assertEqual(code, 2)
            self.assertIn("without a token", out)
        self.assertEqual(self.calls, [])

    def test_network_binding_with_a_token_and_loopback_without(self):
        env = {"RJH_TOKEN": "t0ken"}
        code, _ = self.serve(["--host", "0.0.0.0", "--port", "9000"], env=env)
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[-1][1], {"host": "0.0.0.0", "port": 9000})
        self.assertIn("RJH_HOME", env)                           # l'application retrouve ses fichiers
        self.serve([])
        self.assertEqual(self.calls[-1][1], {"host": "127.0.0.1", "port": 5070})

    def test_requires_a_profile_and_an_importable_app(self):
        code, out = self.serve([], home=tmp_home())
        self.assertEqual(code, 2)
        self.assertIn("rjh init", out)
        code, out = run_cli(["--home", str(home_with()), "--lang", "en", "serve", "--app", "nope.nothing:app"], runner=self.runner)
        self.assertEqual(code, 2)
        self.assertIn("nope.nothing:app", out)

    def test_missing_uvicorn_is_explained(self):
        with mock.patch.dict(sys.modules, {"uvicorn": None}):
            code, out = run_cli(["--home", str(home_with()), "--lang", "en", "serve", "--app", "fakeapp_rjh:app"], env={})
        self.assertEqual(code, 2)
        self.assertIn("uvicorn", out)

    def test_app_can_come_from_the_environment(self):
        code, _ = run_cli(["--home", str(home_with()), "serve"], env={"RJH_APP": "fakeapp_rjh:app"}, runner=self.runner)
        self.assertEqual((code, self.calls[-1][0]), (0, "fakeapp_rjh:app"))


class MainMisc(unittest.TestCase):
    def test_token_paths_and_help(self):
        code, out = run_cli(["token"])
        self.assertEqual((code, len(out)), (0, 43))
        home = tmp_home()
        code, out = run_cli(["--home", str(home), "paths"])
        self.assertIn(str(home / "profile.yaml"), out)
        code, out = run_cli(["--home", str(home), "--lang", "fr"])
        self.assertEqual(code, 0)
        self.assertIn("rjh init", out)

    def test_dotenv_is_loaded_without_overriding_the_environment(self):
        home = tmp_home()
        env_path(home).write_text("# commentaire\nexport A=1\nB='deux'\nC=\"trois\"\n=bad\nBAD KEY=1\n", encoding="utf-8")
        env = {"A": "deja-la"}
        run_cli(["--home", str(home), "paths"], env=env)
        self.assertEqual(env, {"A": "deja-la", "B": "deux", "C": "trois"})
        self.assertEqual(load_dotenv(home / "absent", {}), [])

    def test_an_empty_variable_does_not_mask_the_env_file(self):
        """Docker Compose passe `GOOGLE_API_KEY: ${GOOGLE_API_KEY:-}` : présente mais vide."""
        home = tmp_home()
        env_path(home).write_text("GOOGLE_API_KEY=from-file\nOTHER=x\n", encoding="utf-8")
        env = {"GOOGLE_API_KEY": "", "OTHER": "kept"}
        run_cli(["--home", str(home), "paths"], env=env)
        self.assertEqual((env["GOOGLE_API_KEY"], env["OTHER"]), ("from-file", "kept"))


if __name__ == "__main__":
    unittest.main()
