"""Démo de bout en bout SANS réseau ni clé : collecte (simulée) -> pré-tri par le profil -> lots -> prompt à coller.
    python examples/demo_offline.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rjh.heuristic import ProfileScorer
from rjh.manual import ManualService, SqliteTicketStore
from rjh.models import Job
from rjh.profile import load_profile

profile = load_profile(Path(__file__).resolve().parents[1] / "profile.example.yaml")
scorer = ProfileScorer(profile)

jobs = [
    Job("demo", "1", "Stripe", "Workflow Automation Lead", "https://example.org/1", "Remote - Europe",
        "Own our n8n automations with python and webhooks. 3+ years of experience. Fully remote.", posted_at="2026-10-04"),
    Job("demo", "2", "Beta SAS", "Automation Engineer", "https://example.org/2", "Remote - US",
        "Build workflows in python. Fully remote.", posted_at="2026-10-05"),
    Job("demo", "3", "Gamma", "Automation Engineer", "https://example.org/3", "Remote, France",
        "Native German required. n8n and python. 7+ years of experience.", posted_at="2026-10-06"),
    Job("demo", "4", "Cafe", "Barista", "https://example.org/4", "Paris, France", "Make coffee.", posted_at="2026-10-06"),
]

print("=== PRÉ-TRI (profil.yaml) ===")
for j, d in scorer.rank(jobs, keep_rejected=True):
    print(f"{d.score:6.1f}  {'REJET ' if d.rejected else '      '}{j.company:9} {j.title:26} | {'; '.join(d.reasons)}")

viable = [j for j, d in scorer.rank(jobs) if scorer.viable(d)]
tickets = ManualService(SqliteTicketStore(), profile).create_batches(viable)
print(f"\n=== {len(tickets)} lot(s) à coller ; {len(viable)} offre(s) retenue(s) sur {len(jobs)} ===\n")
print(tickets[0].prompt)
