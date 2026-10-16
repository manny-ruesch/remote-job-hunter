import re
import tomllib
import unittest

import yaml

from .helpers import ROOT, make_http
import rjh
from rjh import cli, sources as sources_pkg
from rjh.messages import MSG
from rjh.sources import available_sources, create_source

README = (ROOT / "README.md").read_text(encoding="utf-8")


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class Pyproject(unittest.TestCase):
    def setUp(self):
        self.p = tomllib.loads(read("pyproject.toml"))
        self.proj = self.p["project"]

    def test_identity_and_entry_points(self):
        self.assertEqual(self.proj["name"], "remote-job-hunter")
        self.assertEqual(self.proj["scripts"]["rjh"], "rjh.cli:main")
        self.assertEqual(self.proj["scripts"]["remote-job-hunter"], "rjh.cli:main")         # pipx run remote-job-hunter
        self.assertEqual(self.proj["requires-python"], ">=3.11")
        self.assertIn("version", self.proj["dynamic"])
        self.assertEqual(self.p["tool"]["setuptools"]["dynamic"]["version"], {"attr": "rjh.__version__"})

    def test_entry_point_resolves(self):
        module, func = self.proj["scripts"]["rjh"].split(":")
        self.assertIs(getattr(__import__(module, fromlist=[func]), func), cli.main)

    def test_core_dependencies_stay_minimal_and_heavy_ones_are_optional(self):
        deps = " ".join(self.proj["dependencies"]).lower()
        self.assertIn("pyyaml", deps)
        for heavy in ("playwright", "scrapling"):
            self.assertNotIn(heavy, deps)                       # jamais par défaut : poids et risque juridique
            self.assertIn(heavy, " ".join(sum(self.proj["optional-dependencies"].values(), [])).lower())

    def test_version_matches_the_changelog(self):
        top = re.search(r"^## \[(\d+\.\d+\.\d+)\]", read("CHANGELOG.md"), re.M).group(1)
        self.assertEqual(top, rjh.__version__)

    def test_license_is_shipped(self):
        self.assertTrue(read("LICENSE").startswith("MIT License"))
        self.assertIn("MIT", self.proj["license"]["text"])


class Documentation(unittest.TestCase):
    def test_every_rjh_command_in_the_readme_exists(self):
        subcommands = set(next(a for a in cli.build_parser()._actions if a.dest == "cmd").choices)
        used = set(re.findall(r"^\s*rjh (?:--\S+ \S+ )*([a-z]+)", README, re.M))
        self.assertTrue({"init", "doctor", "serve"} <= used)
        self.assertLessEqual(used, subcommands, f"commande(s) inconnue(s) dans le README : {used - subcommands}")

    def test_every_builtin_source_is_in_the_readme_table(self):
        for name in available_sources():
            self.assertIn(f"`{name}`", README, f"source « {name} » absente du tableau du README")

    def test_the_readme_image_is_absolute_so_pypi_can_show_it(self):
        for src in re.findall(r'<img[^>]*src="([^"]+)"', README):
            self.assertTrue(src.startswith("https://"), src)

    def test_the_plugin_example_in_the_guide_really_works(self):
        guide = read("docs/ADD_A_SOURCE.md")
        code = re.search(r"```python\n(.*?)```", guide, re.S).group(1)
        namespace = {}
        try:
            exec(compile(code, "mon_plugin.py", "exec"), namespace)
            from .helpers import example_profile
            payload = {"items": [{"id": 1, "company": "Acme", "title": "Comptable", "url": "https://x/1", "location": "Paris"}]}
            http, _ = make_http(lambda c: payload)
            res = create_source("demo", example_profile(), http=http).fetch(example_profile())
            self.assertEqual([(j.title, j.location) for j in res.jobs], [("Comptable", "Paris")])
        finally:
            sources_pkg._REGISTRY.pop("demo", None)

    def test_every_translated_message_key_used_in_the_code_exists(self):
        used = set()
        call = re.compile(r't\(\s*lang\s*,\s*"([a-z0-9_]+)"(?:\s+if\s+[^"]+?\s+else\s+"([a-z0-9_]+)")?')
        for f in (ROOT / "rjh").glob("*.py"):
            for a, b in call.findall(f.read_text(encoding="utf-8")):
                used |= {a, b} - {""}
        used |= {f"mode_{m}" for m in ("remote", "preferred", "onsite")} | {f"level_{lv}" for lv in ("junior", "mid", "senior")}
        self.assertGreater(len(used), 60)                                    # le test regarde bien quelque chose
        self.assertEqual(sorted(used - set(MSG)), [])
        unused = sorted(set(MSG) - used)
        self.assertLessEqual(len(unused), 12, f"messages jamais utilisés (code mort ?) : {unused}")


class DockerAndRepoHygiene(unittest.TestCase):
    def setUp(self):
        self.compose = yaml.safe_load(read("docker-compose.yml"))
        self.svc = self.compose["services"]["command-center"]
        self.dockerfile = read("Dockerfile")

    def test_compose_is_safe_by_default(self):
        self.assertEqual(self.svc["ports"], ["127.0.0.1:5070:5070"])                      # jamais 0.0.0.0 par défaut
        self.assertIn(":?", self.svc["environment"]["RJH_TOKEN"])                          # jeton OBLIGATOIRE
        self.assertTrue(self.svc.get("restart"))
        self.assertNotIn("privileged", self.svc)
        self.assertTrue(any(v.endswith(":/data") for v in self.svc["volumes"]))
        self.assertIn("rjh-data", self.compose["volumes"])
        for key in ("GOOGLE_API_KEY", "ADZUNA_APP_KEY"):
            self.assertTrue(self.svc["environment"][key].endswith(":-}"))                  # clé facultative : vide par défaut

    def test_dockerfile_is_unprivileged_and_consistent_with_the_cli(self):
        self.assertRegex(self.dockerfile, r"(?m)^USER rjh$")
        self.assertNotRegex(self.dockerfile, r"(?m)^ENTRYPOINT")                           # `run … rjh init` doit fonctionner
        self.assertIn("HEALTHCHECK", self.dockerfile)
        self.assertIn("RJH_HOME=/data", self.dockerfile)
        m = re.search(r'(?m)^CMD (\[.*\])$', self.dockerfile)
        args = yaml.safe_load(m.group(1))
        self.assertEqual(args[:2], ["rjh", "serve"])
        cli.build_parser().parse_args(args[1:])                                           # la commande du conteneur est valide
        self.assertEqual(args[args.index("--port") + 1], "5070")
        self.assertIn("5070", self.dockerfile.split("HEALTHCHECK")[1])                    # même port que le serveur

    def test_the_container_command_would_not_be_refused_for_lack_of_a_token(self):
        # 0.0.0.0 dans le conteneur : légitime parce que le compose exige RJH_TOKEN ET lie l'hôte à 127.0.0.1.
        self.assertIn("0.0.0.0", self.dockerfile)
        self.assertIn("RJH_TOKEN", self.svc["environment"])

    def test_ignore_files_keep_secrets_out(self):
        for name in (".gitignore", ".dockerignore"):
            lines = read(name).splitlines()
            self.assertIn(".env", lines, name)
            self.assertIn("*.db", lines, name)

    def test_github_files_are_valid_and_the_ci_smoke_test_uses_real_commands(self):
        for f in (ROOT / ".github").rglob("*.yml"):
            yaml.safe_load(f.read_text(encoding="utf-8"))
        ci = read(".github/workflows/ci.yml")
        parser = cli.build_parser()
        parser.parse_args(["init", "--yes", "--title", "accountant", "--country", "FR", "--level", "mid"])
        parser.parse_args(["doctor", "--offline"])
        self.assertIn('rjh init --yes --title "accountant" --country FR --level mid', ci)
        self.assertIn("rjh doctor --offline", ci)
        self.assertIn("id-token: write", read(".github/workflows/release.yml"))              # trusted publishing, pas de mot de passe

    def test_no_secret_or_personal_webhook_ships_in_the_package(self):
        pattern = re.compile(r"AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}|discord(?:app)?\.com/api/webhooks/\d+/|-----BEGIN [A-Z ]*PRIVATE KEY")
        ignored_names = {".env", ".env.local", "HOT_OPPORTUNITIES.md", "candidate_profile.json", "profile.yaml"}
        offenders = []
        for f in ROOT.rglob("*"):
            if (
                f.is_file()
                and "tests" not in f.parts
                and ".git" not in f.parts
                and f.name not in ignored_names
                and f.suffix not in (".pyc", ".png", ".gif", ".zip", ".log", ".db")
            ):
                try:
                    if pattern.search(f.read_text(encoding="utf-8")):
                        offenders.append(str(f.relative_to(ROOT)))
                except UnicodeDecodeError:
                    pass
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
