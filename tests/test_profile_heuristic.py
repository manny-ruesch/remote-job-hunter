import copy
import unittest

import yaml

from .helpers import ROOT, example_profile
from rjh.heuristic import (ProfileScorer, TermSet, classify_location, detect_remote, eligibility,
                           languages_required, years_required)
from rjh.models import Job
from rjh.profile import ProfileError, load_profile
from rjh.textutil import norm


def job(title, desc="", location="", company="Acme", **kw):
    return Job(source="t", source_id=title + location, company=company, title=title, url="https://x/" + title,
               location=location, description=desc, **kw)


class ProfileLoading(unittest.TestCase):
    def test_both_example_profiles_load(self):
        self.assertTrue(example_profile().search.titles.primary)
        nurse = load_profile(ROOT / "examples" / "profile.infirmiere.yaml")
        self.assertEqual(nurse.search.work_mode.cities, ["Lyon", "Villeurbanne"])

    def test_unknown_key_suggests_the_right_one(self):
        with self.assertRaises(ProfileError) as cm:
            load_profile({"search": {"seniorty": {"level": "junior"}}})
        self.assertIn("seniority", str(cm.exception))

    def test_invalid_literal_lists_valid_values(self):
        with self.assertRaises(ProfileError) as cm:
            load_profile({"search": {"work_mode": {"remote": "yes"}}})
        self.assertIn("required", str(cm.exception))

    def test_wrong_type_is_reported_with_its_path(self):
        with self.assertRaises(ProfileError) as cm:
            load_profile({"search": {"seniority": {"years_experience": "beaucoup"}}})
        self.assertIn("search.seniority.years_experience", str(cm.exception))

    def test_single_string_is_accepted_where_a_list_is_expected(self):
        p = load_profile({"search": {"titles": {"primary": "infirmier"}}})
        self.assertEqual(p.search.titles.primary, ["infirmier"])

    def test_gate_validation(self):
        bad = [
            {"scoring": {"gates": [{"id": "a", "when": {"title_match": ["x"]}}]}},        # condition inconnue
            {"scoring": {"gates": [{"id": "a", "when": {"remote": "yes"}}]}},             # mauvais type
            {"scoring": {"gates": [{"id": "a", "when": {"remote": True}}, {"id": "a", "when": {"remote": True}}]}},  # doublon
            {"scoring": {"gates": [{"when": {"remote": True}}]}},                          # id manquant
            {"scoring": {"hot_threshold": 12}},
        ]
        for data in bad:
            with self.assertRaises(ProfileError, msg=str(data)):
                load_profile(data)

    def test_fingerprint_tracks_search_not_identity(self):
        base = example_profile()
        other = copy.deepcopy(base)
        other.identity.name = "Quelqu'un d'autre"
        self.assertEqual(base.fingerprint(), other.fingerprint())
        other.search.titles.primary.append("nouveau rôle")
        self.assertNotEqual(base.fingerprint(), other.fingerprint())

    def test_invalid_yaml_is_a_profile_error(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "p.yaml"
            f.write_text("search: [unclosed", encoding="utf-8")
            with self.assertRaises(ProfileError):
                load_profile(f)


class TermSets(unittest.TestCase):
    def find(self, terms, text):
        return TermSet(terms).find(norm(text))

    def test_whole_words_only(self):
        self.assertEqual(self.find(["ai"], "we maintain an email domain"), set())
        self.assertEqual(self.find(["ai"], "the AI team"), {"ai"})

    def test_symbols_in_terms(self):
        self.assertEqual(self.find(["c++", "c#", "node.js"], "C++ and C# plus Node.js dev"), {"c++", "c#", "node.js"})

    def test_accents_and_case(self):
        self.assertEqual(self.find(["infirmière"], "INFIRMIÈRE diplômée"), {"infirmiere"})

    def test_plural_and_feminine(self):
        self.assertEqual(self.find(["webhook", "infirmier"], "webhooks, infirmières"), {"webhook", "infirmier"})

    def test_short_terms_do_not_get_suffixes(self):
        self.assertEqual(self.find(["ide"], "ides of march"), set())

    def test_flexible_whitespace(self):
        self.assertEqual(self.find(["rest api"], "a REST   API"), {"rest api"})

    def test_empty_set_matches_nothing(self):
        self.assertEqual(self.find([], "anything"), set())


class Facts(unittest.TestCase):
    def test_years_required(self):
        cases = {
            "5+ years of experience": 5, "3-5 years of experience required": 3, "minimum 4 ans d'expérience": 4,
            "at least five years of experience": 5, "plus de 10 ans d'expérience": 10,
            "Our ideal candidate has 6+ years of experience": 6,
            "We have 10 years of experience building products": None,      # vantardise de l'employeur
            "2 years in business": None, "no experience needed": None,
        }
        for text, want in cases.items():
            self.assertEqual(years_required(norm(text)), want, text)

    def test_languages_required(self):
        self.assertEqual(languages_required(norm("Native German speaker required")), {"de"})
        self.assertEqual(languages_required(norm("Allemand courant exigé")), {"de"})
        self.assertEqual(languages_required(norm("No need for native German, English is fine")), set())
        self.assertEqual(languages_required(norm("German is a plus")), set())
        self.assertEqual(languages_required(norm("Fluent French and English")), {"fr", "en"})

    def test_remote_detection(self):
        self.assertTrue(detect_remote(norm("Fully remote position")))
        self.assertFalse(detect_remote(norm("On-site in Berlin, no remote")))
        self.assertFalse(detect_remote(norm("Hybrid: 3 days remote")))
        self.assertIsNone(detect_remote(norm("Great team")))
        self.assertFalse(detect_remote(norm("Pas de télétravail possible, poste sur site")))
        self.assertIsNone(detect_remote(norm("On-site in Paris, remote possible occasionally")))   # contradictoire : on ne tranche pas

    def test_eligibility_table(self):
        table = [
            ("Remote - US", ["FR", "EU"], ["US"], False),
            ("Remote, Europe", ["FR"], [], True),
            ("Paris, France", ["EU"], [], True),
            ("Berlin, Germany", ["FR"], [], False),
            ("Berlin, Germany", ["EU"], [], True),
            ("Worldwide", ["FR"], [], True),
            ("Remote - Anywhere", ["FR"], [], True),
            ("Remote", ["FR"], [], None),
            ("San Francisco, CA", ["FR", "EU"], [], False),
            ("Remote - US/Europe", ["EU"], ["US"], True),
            ("London, UK", ["EU"], [], False),
            ("Remote (Canada)", [], ["CA"], False),
            ("Toronto", ["WORLDWIDE"], [], True),
            ("Pau", ["FR"], [], True),
        ]
        for loc, el, ex, want in table:
            self.assertIs(eligibility(classify_location(loc), el, ex), want, f"{loc} {el} {ex}")


class TechProfile(unittest.TestCase):
    def setUp(self):
        self.scorer = ProfileScorer(example_profile())
        self.desc = "We build workflows with n8n, python, rest api and webhooks. 3+ years of experience."

    def test_ranking_and_gates(self):
        jobs = {
            "good": job("Automation Engineer", self.desc, "Remote - Europe"),
            "us": job("Automation Engineer", self.desc, "Remote - US"),
            "head": job("Head of Automation Engineer", self.desc, "Remote - Europe"),
            "senior_req": job("Automation Engineer", self.desc.replace("3+", "7+"), "Remote - Europe", company="B"),
            "german": job("Automation Engineer", self.desc + " Native German required.", "Remote - Europe", company="C"),
            "onsite": job("Automation Engineer", "On-site in Berlin, 5 days in the office. " + self.desc, "Berlin, Germany", company="D"),
            "barista": job("Barista", "Make coffee.", "Paris, France", company="E"),
        }
        d = {k: self.scorer.score(j) for k, j in jobs.items()}
        self.assertTrue(d["us"].rejected and d["us"].gate == "location")
        self.assertTrue(d["head"].rejected and d["head"].gate == "title_excluded")
        self.assertTrue(d["onsite"].rejected and d["onsite"].gate == "work_mode")
        self.assertEqual(d["senior_req"].gate, "years_required")
        self.assertFalse(d["senior_req"].rejected)
        self.assertEqual(d["german"].gate, "language")
        self.assertGreater(d["good"].score, d["german"].score)
        self.assertGreater(d["good"].score, d["senior_req"].score)
        self.assertFalse(self.scorer.viable(d["barista"]))
        ranked = [j.company for j, _ in self.scorer.rank(list(jobs.values()))]
        self.assertEqual(ranked[0], "Acme")
        self.assertEqual(len(ranked), 4)                      # les 3 rejetées sont écartées
        self.assertEqual(len(self.scorer.rank(list(jobs.values()), keep_rejected=True)), 7)

    def test_reasons_explain_the_score(self):
        d = self.scorer.score(job("Automation Engineer", self.desc, "Remote - Europe"))
        text = " | ".join(d.reasons)
        self.assertIn("titre", text)
        self.assertIn("mots-clés", text)
        self.assertIn("télétravail", text)

    def test_blacklisted_company_and_contract_and_salary(self):
        self.assertTrue(self.scorer.score(job("Automation Engineer", self.desc, "Remote - Europe", company="ACME Staffing")).rejected)
        part = self.scorer.score(job("Automation Engineer", self.desc, "Remote - Europe", employment_type="part_time"))
        self.assertEqual(part.gate, "contract")
        low = self.scorer.score(job("Automation Engineer", self.desc, "Remote - Europe", salary_max=25000))
        self.assertEqual(low.gate, "salary")
        unknown = self.scorer.score(job("Automation Engineer", self.desc, "Remote - Europe"))
        self.assertIsNone(unknown.gate)                       # salaire inconnu : unknown_ok

    def test_custom_gates_from_yaml(self):
        d = self.scorer.score(job("Automation Engineer", self.desc + " Vor Ort in Munich.", "Remote - Europe"))
        self.assertTrue(d.rejected and d.gate == "onsite_germany")
        heavy = self.scorer.score(job("Automation Engineer", self.desc + " Travel up to 50% of the time.", "Remote - Europe"))
        self.assertEqual(heavy.gate, "heavy_travel")

    def test_title_prefilter_for_ats_sources(self):
        self.assertTrue(self.scorer.title_prefilter(job("Workflow Automation Lead")))
        self.assertFalse(self.scorer.title_prefilter(job("Office Cleaner")))


class Universality(unittest.TestCase):
    """LE test du projet : même code, deux métiers sans rapport, deux profils."""

    def setUp(self):
        self.tech = ProfileScorer(example_profile())
        self.nurse = ProfileScorer(load_profile(ROOT / "examples" / "profile.infirmiere.yaml"))

    def test_nurse_profile_ranks_nursing_jobs(self):
        jobs = [
            job("Infirmier(ère) DE - Urgences", "Service des urgences adultes, CDI.", "Lyon", company="CHU"),
            job("Aide-soignante", "Auprès de patients.", "Lyon", company="EHPAD"),
            job("Chauffeur livreur", "Permis B.", "Lyon", company="Transco"),
            job("Infirmière de nuit", "Clinique privée, 3 nuits par semaine.", "Villeurbanne", company="Clinique"),
            job("Infirmier", "Poste en Allemagne.", "Berlin, Germany", company="Charité"),
        ]
        ranked = self.nurse.rank(jobs)
        self.assertEqual([j.company for j, _ in ranked][:2], ["CHU", "Clinique"])
        gates = {j.company: d.gate for j, d in self.nurse.rank(jobs, keep_rejected=True) if d.rejected}
        self.assertEqual(gates, {"EHPAD": "title_excluded", "Charité": "location"})
        self.assertFalse(self.nurse.viable(self.nurse.score(jobs[2])))

    def test_each_profile_ignores_the_other_domain(self):
        tech_job = job("Automation Engineer", "n8n python rest api", "Remote - Europe")
        nurse_job = job("Infirmier DE", "Urgences adultes", "Lyon")
        self.assertTrue(self.tech.viable(self.tech.score(tech_job)))
        self.assertFalse(self.tech.viable(self.tech.score(nurse_job)))
        self.assertTrue(self.nurse.viable(self.nurse.score(nurse_job)))
        self.assertFalse(self.nurse.viable(self.nurse.score(tech_job)))

    def test_old_scorer_bugs_stay_fixed(self):
        data = yaml.safe_load((ROOT / "profile.example.yaml").read_text(encoding="utf-8"))
        data["search"]["titles"]["primary"] = ["ai engineer"]
        data["search"]["keywords"]["penalize"] = {"c++": 30}
        sc = ProfileScorer(load_profile(data))
        self.assertFalse(sc.title_prefilter(job("Maintain Email Domain Manager")))     # « ai » n'est plus une sous-chaîne
        cpp = sc.score(job("AI Engineer", "We use C++ daily", "Remote - Europe"))
        plain = sc.score(job("AI Engineer", "We use go daily", "Remote - Europe"))
        self.assertLess(cpp.score, plain.score)                                        # « c++ » est bien pénalisé


if __name__ == "__main__":
    unittest.main()
