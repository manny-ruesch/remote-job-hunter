"""Pré-tri heuristique PILOTÉ PAR LE PROFIL.

Plus aucun dictionnaire de mots-clés dans le code : `ProfileScorer(profile)` compile le
profil.yaml en matchers (mots entiers, insensibles à la casse et aux accents), en gates
(rejet / pénalité) et produit un score 0-100 EXPLICABLE (liste de raisons).

Corrige par construction les défauts de l'ancien scorer :
  * « ai » ne matche plus dans « maintain » (mots entiers),   * « c++ » est bien détecté,
  * « 5+ years » / « 3-5 ans d'expérience » sont lus,         * « Remote - US » est écarté si non éligible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .models import Job
from .profile import Gate, Profile
from .textutil import norm

# ============================================================ TermSet ======================


class TermSet:
    """Ensemble de termes recherchés comme MOTS ENTIERS dans un texte déjà normalisé (norm()).

    Un terme d'au moins 4 lettres accepte un pluriel / féminin simple (e, s, es) :
    « webhook » trouve « webhooks », « infirmier » trouve « infirmière » (norm -> « infirmiere »).
    """

    def __init__(self, terms):
        cleaned = [norm(t) for t in terms if t and str(t).strip()]
        self.terms = sorted(set(cleaned))
        self._lookup = {re.sub(r"\s+", " ", t): t for t in self.terms}
        self._rx = None
        if self.terms:
            def pattern(t: str) -> str:
                body = re.escape(t).replace(r"\ ", r"\s+")
                return body + ("e?s?" if len(t) >= 4 and t[-1].isalpha() else "")
            alts = "|".join(pattern(t) for t in sorted(self.terms, key=len, reverse=True))
            # Lookarounds sur [a-z0-9] : « c++ », « c# », « node.js », « n8n » fonctionnent ;
            # « ai » ne matche plus dans « maintain ».
            self._rx = re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])")

    def _canon(self, matched: str) -> str:
        txt = re.sub(r"\s+", " ", matched)
        if txt in self._lookup:
            return self._lookup[txt]
        for suffix in ("es", "s", "e"):
            if txt.endswith(suffix) and txt[: -len(suffix)] in self._lookup:
                return self._lookup[txt[: -len(suffix)]]
        return txt

    def find(self, text_norm: str) -> set[str]:
        if not self._rx or not text_norm:
            return set()
        return {self._canon(m.group(0)) for m in self._rx.finditer(text_norm)}

    def __bool__(self) -> bool:
        return bool(self.terms)


# ============================================================ Géographie ===================

EU_MEMBERS = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}
REGIONS = {"EU": EU_MEMBERS}

_COUNTRIES = {
    "FR": ["france"], "DE": ["germany", "allemagne", "deutschland"], "ES": ["spain", "espagne", "espana"],
    "IT": ["italy", "italie", "italia"], "PT": ["portugal"], "NL": ["netherlands", "pays-bas", "pays bas", "holland"],
    "BE": ["belgium", "belgique", "belgie"], "LU": ["luxembourg"], "IE": ["ireland", "irlande"],
    "AT": ["austria", "autriche", "osterreich"], "PL": ["poland", "pologne"], "SE": ["sweden", "suede"],
    "DK": ["denmark", "danemark"], "FI": ["finland", "finlande"], "CZ": ["czechia", "czech republic", "tchequie"],
    "RO": ["romania", "roumanie"], "HU": ["hungary", "hongrie"], "GR": ["greece", "grece"],
    "BG": ["bulgaria", "bulgarie"], "HR": ["croatia", "croatie"], "SK": ["slovakia", "slovaquie"],
    "SI": ["slovenia", "slovenie"], "EE": ["estonia", "estonie"], "LV": ["latvia", "lettonie"],
    "LT": ["lithuania", "lituanie"], "CY": ["cyprus", "chypre"], "MT": ["malta", "malte"],
    "UK": ["united kingdom", "uk", "u.k.", "great britain", "england", "scotland", "wales", "royaume-uni", "royaume uni"],
    "CH": ["switzerland", "suisse", "schweiz"], "NO": ["norway", "norvege"],
    "US": ["united states", "usa", "u.s.a", "u.s.", "us", "etats-unis", "etats unis"],
    "CA": ["canada"], "IN": ["india", "inde"], "AU": ["australia", "australie"], "NZ": ["new zealand"],
    "BR": ["brazil", "bresil"], "MX": ["mexico", "mexique"], "AR": ["argentina"], "CO": ["colombia"],
    "SG": ["singapore", "singapour"], "JP": ["japan", "japon"], "IL": ["israel"], "AE": ["united arab emirates", "uae"],
    "ZA": ["south africa"], "PH": ["philippines"], "PK": ["pakistan"], "NG": ["nigeria"], "ID": ["indonesia"],
}
_CITIES = {
    "FR": ["paris", "lyon", "marseille", "toulouse", "bordeaux", "nantes", "lille", "strasbourg", "nice", "montpellier", "rennes", "pau"],
    "DE": ["berlin", "munich", "munchen", "hamburg", "frankfurt", "cologne", "koln", "stuttgart", "dusseldorf"],
    "UK": ["london", "manchester", "edinburgh", "bristol", "birmingham"], "NL": ["amsterdam", "rotterdam", "utrecht"],
    "IE": ["dublin"], "ES": ["madrid", "barcelona", "valencia"], "PT": ["lisbon", "lisboa", "porto"],
    "IT": ["milan", "milano", "rome", "roma"], "BE": ["brussels", "bruxelles"], "CH": ["zurich", "geneva", "geneve"],
    "AT": ["vienna", "wien"], "PL": ["warsaw", "krakow"], "SE": ["stockholm"], "DK": ["copenhagen"],
    "NO": ["oslo"], "FI": ["helsinki"], "CZ": ["prague"],
    "US": ["new york", "san francisco", "los angeles", "seattle", "austin", "boston", "chicago", "denver",
           "miami", "atlanta", "palo alto", "mountain view", "san jose", "washington dc", "san diego", "portland"],
    "CA": ["toronto", "vancouver", "montreal", "ottawa"], "IN": ["bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune"],
    "AU": ["sydney", "melbourne"], "SG": [], "IL": ["tel aviv"], "AE": ["dubai"], "BR": ["sao paulo"], "MX": ["mexico city"],
}
_US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington state", "west virginia", "wisconsin", "wyoming",
]
_PHRASES = {   # expressions de région -> tags (traitées AVANT les pays : « north america » avant « america »)
    "north america": {"US", "CA"}, "americas": {"US", "CA", "LATAM"}, "latin america": {"LATAM"}, "latam": {"LATAM"},
    "european union": {"EU"}, "europe": {"EU"}, "eea": {"EU"}, "eu": {"EU"}, "european": {"EU"},
    "emea": {"EU", "UK"}, "apac": {"APAC"}, "asia pacific": {"APAC"},
}
_WORLDWIDE = ["worldwide", "world wide", "anywhere in the world", "work from anywhere", "anywhere"]

_GEO_TERMS: dict[str, set[str]] = {}
for _iso, _names in _COUNTRIES.items():
    for _n in _names:
        _GEO_TERMS[_n] = {_iso}
for _iso, _names in _CITIES.items():
    for _n in _names:
        _GEO_TERMS.setdefault(_n, {_iso})
for _s in _US_STATES:
    _GEO_TERMS.setdefault(_s, {"US"})
_GEO_TERMS.update(_PHRASES)
_GEO_RX = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in sorted(_GEO_TERMS, key=len, reverse=True)) + r")(?![a-z0-9])"
)
_WW_RX = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in _WORLDWIDE) + r")(?![a-z0-9])")
# « San Francisco, CA » : abréviation d'État US après une virgule, détectée sur le texte ORIGINAL (majuscules).
_US_ABBR_RX = re.compile(r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")


@dataclass
class GeoInfo:
    tags: set[str] = field(default_factory=set)
    worldwide: bool = False


def classify_location(text: str) -> GeoInfo:
    """Texte de localisation -> tags pays/région. Heuristique : étendre les tables au besoin."""
    n = norm(text)
    tags: set[str] = set()
    for m in _GEO_RX.finditer(n):
        tags |= _GEO_TERMS[re.sub(r"\s+", " ", m.group(0))]
    if _US_ABBR_RX.search(text or ""):
        tags.add("US")
    return GeoInfo(tags=tags, worldwide=bool(_WW_RX.search(n)))


def _expand(tags: set[str]) -> set[str]:
    """FR -> {FR, EU} ; EU -> {EU, FR, DE, ...} : l'éligibilité se propage dans les deux sens."""
    out = set(tags)
    for region, members in REGIONS.items():
        if region in out:
            out |= members
        if out & members:
            out.add(region)
    return out


def eligibility(info: GeoInfo, eligible: list[str], excluded: list[str]) -> bool | None:
    """True / False, ou None si le texte ne permet pas de décider (on donne alors le bénéfice du doute)."""
    if info.worldwide:
        return True
    if not info.tags:
        return None
    bad = _expand({t.upper() for t in excluded})
    left = {t for t in info.tags if t not in bad}
    if not left:
        return False
    if not eligible or any(t.upper() == "WORLDWIDE" for t in eligible):
        return True
    ok = _expand({t.upper() for t in eligible})
    return bool(left & ok)


# ============================================================ Extraction de faits ==========

_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8, "neuf": 9, "dix": 10}
_NW = "|".join(_NUMWORDS)
_YEARS_RX = re.compile(
    rf"(?<![\d.,])(\d{{1,2}}|{_NW})\s*(?:\+|plus)?\s*(?:(?:-|to|a)\s*(\d{{1,2}}|{_NW})\s*)?\+?\s*(?:years?|yrs?|ans?|annees?)\b"
)
_AT_LEAST_RX = re.compile(rf"(?:at least|minimum(?: of)?|min\.?|au moins|plus de)\s*(\d{{1,2}}|{_NW})\s*(?:years?|yrs?|ans?|annees?)\b")
_EXP_CONTEXT = re.compile(r"experience|exp\.|background|track record|expertise|worked|working|proven|required|requis|minimum|at least|au moins|profil|similar|poste")
_BOASTING = re.compile(r"(?:we have|we've|we are|founded|for over|for more than|depuis|fondee|nous avons)\W+(?:\w+\W+){0,3}$")


def _num(tok: str) -> float:
    return float(_NUMWORDS[tok]) if tok in _NUMWORDS else float(tok)


def years_required(text_norm: str) -> float | None:
    """Années d'expérience MINIMALES exigées (bas des fourchettes, « 5+ » = 5), None si non dit.
    Ignore les vantardises (« we have 10 years of experience building… »)."""
    best: float | None = None
    for rx in (_YEARS_RX, _AT_LEAST_RX):
        for m in rx.finditer(text_norm):
            before = text_norm[max(0, m.start() - 40): m.start()]
            window = text_norm[max(0, m.start() - 70): m.end() + 70]
            if not _EXP_CONTEXT.search(window) or _BOASTING.search(before):
                continue
            val = _num(m.group(1))
            if val > 40:
                continue
            best = val if best is None else max(best, val)
    return best


LANG_NAMES = {
    "fr": ["french", "francais"], "de": ["german", "allemand", "deutsch"], "en": ["english", "anglais"],
    "es": ["spanish", "espagnol", "espanol"], "it": ["italian", "italien", "italiano"],
    "nl": ["dutch", "neerlandais", "nederlands"], "pt": ["portuguese", "portugais"], "pl": ["polish", "polonais"],
}
_LANG_CUE = r"(?:native|fluent|fluency|proficien\w*|mother tongue|langue maternelle|courant|bilingue|maitrise|full professional)"
_LANG_REQ = r"(?:required|mandatory|a must|obligatoire|indispensable|essential|fluent|native|courant|exige|exigee|requis|requise)"
_NEGATION = re.compile(r"(?:no need|not required|not necessary|without|pas besoin|non requis|nice to have|a plus|bonus)\W+(?:\w+\W+){0,4}$")


def languages_required(text_norm: str) -> set[str]:
    out: set[str] = set()
    for code, names in LANG_NAMES.items():
        alt = "|".join(names)
        for rx in (re.compile(rf"{_LANG_CUE}\W+(?:\w+\W+){{0,3}}(?:{alt})\b"), re.compile(rf"\b(?:{alt})\W+(?:\w+\W+){{0,2}}(?:is\s+)?{_LANG_REQ}")):
            for m in rx.finditer(text_norm):
                if not _NEGATION.search(text_norm[max(0, m.start() - 40): m.start()]):
                    out.add(code)
    return out


_REMOTE_POS = re.compile(r"(?<![a-z])(?:remote|teletravail|work from home|work from anywhere|distributed|full remote|a distance)(?![a-z])")
_REMOTE_NEG = re.compile(r"(?<![a-z])(?:on-?site|on site|sur site|presentiel|in-office|in office|hybrid|hybride)(?![a-z])")


_REMOTE_NO = re.compile(
    r"(?:no|not|non|without|pas de|sans|aucun)\s+(?:a\s+|any\s+)?(?:fully\s+|full\s+)?(?:remote|teletravail|work from home)"
    r"|(?:remote|teletravail)\s+(?:is\s+)?(?:not|n'est pas)\s+(?:possible|available|allowed|autorise)"
)


def detect_remote(text_norm: str) -> bool | None:
    """True / False, ou None quand le texte ne tranche pas (ou se contredit) : jamais de rejet sur un doute."""
    explicit_no = bool(_REMOTE_NO.search(text_norm))
    t = _REMOTE_NO.sub(" ", text_norm)
    neg, pos = bool(_REMOTE_NEG.search(t)) or explicit_no, bool(_REMOTE_POS.search(t))
    if neg and ("hybrid" in t or not pos):
        return False
    if neg and pos:
        return None
    return True if pos else None


def annual_salary(job: Job, value: float | None) -> float | None:
    if value is None:
        return None
    mult = {"hour": 1800, "day": 220, "month": 12}.get(str(job.extra.get("salary_period", "")).lower(), 1)
    return value * mult


# ============================================================ Scorer =======================

@dataclass
class ScoreDetail:
    score: float
    reasons: list[str] = field(default_factory=list)
    gate: str | None = None
    rejected: bool = False


@dataclass
class _Facts:
    job: Job
    title: str
    desc: str
    loc: str
    geo: GeoInfo
    remote: bool | None
    years: float | None
    langs: set[str]
    company: str


Predicate = Callable[[_Facts], "str | None"]    # renvoie le motif si la condition est vraie


class ProfileScorer:
    def __init__(self, profile: Profile):
        self.profile = profile
        s = profile.search
        self.primary, self.related = TermSet(s.titles.primary), TermSet(s.titles.related)
        self.must_any = TermSet(s.keywords.must_any)
        self.boost = {norm(k): float(v) for k, v in s.keywords.boost.items()}
        self.penal = {norm(k): abs(float(v)) for k, v in s.keywords.penalize.items()}
        self.boost_set, self.penal_set = TermSet(self.boost), TermSet(self.penal)
        self.blacklist = TermSet(s.companies.blacklist)
        self.gates: list[tuple[str, str, float, Predicate]] = self._compile_gates()

    # ---- gates : implicites (déduites du profil) + personnalisées (scoring.gates) --------
    def _compile_gates(self):
        p, s = self.profile, self.profile.search
        g: list[tuple[str, str, float, Predicate]] = []
        excl = TermSet(s.titles.exclude + s.seniority.avoid_title_terms)
        if excl:
            g.append(("title_excluded", "reject", 0, lambda f: ", ".join(sorted(excl.find(f.title))) or None))
        if s.seniority.max_years_required is not None:
            mx = s.seniority.max_years_required
            g.append(("years_required", "penalize", 40, lambda f: f"{f.years:g} ans exigés (max {mx:g})" if f.years is not None and f.years > mx else None))
        if s.location.eligible_regions or s.location.excluded_regions:
            el, ex = s.location.eligible_regions, s.location.excluded_regions
            g.append(("location", "reject", 0, lambda f: f"localisation non éligible ({', '.join(sorted(f.geo.tags))})" if eligibility(f.geo, el, ex) is False else None))
        if s.work_mode.remote == "required":
            def onsite(f: _Facts):
                if f.remote is not False:
                    return None
                if s.work_mode.hybrid_ok and _is_hybrid(f):
                    return None
                return "poste sur site / non télétravaillable"
            g.append(("work_mode", "reject", 0, onsite))
        if s.languages.reject_if_required:
            rej = {c.lower() for c in s.languages.reject_if_required}
            g.append(("language", "penalize", 35, lambda f: f"langue exigée : {', '.join(sorted(f.langs & rej))}" if f.langs & rej else None))
        if s.compensation.min_annual:
            mn, unk = s.compensation.min_annual, s.compensation.unknown_ok
            def sal(f: _Facts):
                top = annual_salary(f.job, f.job.salary_max if f.job.salary_max is not None else f.job.salary_min)
                if top is None:
                    return None if unk else "salaire non communiqué"
                return f"salaire max {top:,.0f} < {mn:,.0f}" if top < mn else None
            g.append(("salary", "penalize", 25, sal))
        if s.contract.types:
            allowed = {t.lower() for t in s.contract.types}
            g.append(("contract", "penalize", 30, lambda f: f"contrat {f.job.employment_type}" if f.job.employment_type and f.job.employment_type.lower() not in allowed else None))
        if self.blacklist:
            g.append(("company_blacklist", "reject", 0, lambda f: f.company if self.blacklist.find(f.company) else None))
        for custom in p.scoring.gates:
            g.append((custom.id, custom.action, custom.penalty, self._compile_custom(custom)))
        return g

    def _compile_custom(self, gate: Gate) -> Predicate:
        conds: list[Predicate] = []
        for key, val in gate.when.items():
            if key == "title_matches":
                ts = TermSet(val); conds.append(lambda f, ts=ts: ", ".join(sorted(ts.find(f.title))) or None)
            elif key == "description_matches":
                ts = TermSet(val); conds.append(lambda f, ts=ts: ", ".join(sorted(ts.find(f.desc))) or None)
            elif key == "years_required_gte":
                conds.append(lambda f, v=val: f"{f.years:g} ans" if f.years is not None and f.years >= v else None)
            elif key == "location_ineligible":
                el, ex = self.profile.search.location.eligible_regions, self.profile.search.location.excluded_regions
                conds.append(lambda f, v=val, el=el, ex=ex: "localisation" if (eligibility(f.geo, el, ex) is False) == v else None)
            elif key == "company_blacklisted":
                conds.append(lambda f, v=val: "société" if bool(self.blacklist.find(f.company)) == v else None)
            elif key == "language_required_in":
                codes = {c.lower() for c in val}
                conds.append(lambda f, codes=codes: ", ".join(sorted(f.langs & codes)) or None)
            elif key == "salary_below":
                conds.append(lambda f, v=val: "salaire" if (t := annual_salary(f.job, f.job.salary_max)) is not None and t < v else None)
            elif key == "remote":
                conds.append(lambda f, v=val: "remote" if f.remote is v else None)
        def all_true(f: _Facts):
            reasons = [c(f) for c in conds]
            return " & ".join(reasons) if all(reasons) else None
        return all_true

    # ---- scoring ------------------------------------------------------------------------
    def facts(self, job: Job) -> _Facts:
        desc = norm(job.description)
        title, loc = norm(job.title), norm(job.location)
        remote = job.remote if job.remote is not None else detect_remote(f"{loc} {title} {desc[:600]}")
        geo = classify_location(job.location)
        if not geo.tags and not geo.worldwide:            # « Engineer (Europe) » : région dans le titre
            geo = GeoInfo(tags=classify_location(job.title).tags)
        return _Facts(job, title, desc, loc, geo, remote,
                      years_required(desc), languages_required(desc), norm(job.company))

    def score(self, job: Job) -> ScoreDetail:
        f = self.facts(job)
        s, wm = self.profile.search, self.profile.search.work_mode
        sc, why = 0.0, []

        hits = self.primary.find(f.title)
        if hits:
            sc += 40 + min(10, 5 * (len(hits) - 1)); why.append(f"titre : {', '.join(sorted(hits))}")
        else:
            rel = self.related.find(f.title)
            if rel:
                sc += 22; why.append(f"titre proche : {', '.join(sorted(rel))}")
            elif self.primary.find(f.desc) or self.related.find(f.desc):
                sc += 8; why.append("rôle cible cité dans la description")
        if self.must_any and not (self.must_any.find(f.title) or self.must_any.find(f.desc)):
            sc -= 30; why.append("aucun mot-clé obligatoire")

        bonus = 0.0
        in_title = self.boost_set.find(f.title)
        for t in in_title:
            bonus += self.boost[t] * 1.5
        for t in self.boost_set.find(f.desc) - in_title:
            bonus += self.boost[t]
        if bonus:
            sc += min(bonus, 30); why.append(f"mots-clés +{min(bonus, 30):g}")
        malus = sum(self.penal[t] for t in self.penal_set.find(f.title) | self.penal_set.find(f.desc))
        if malus:
            sc -= min(malus, 40); why.append(f"mots à éviter -{min(malus, 40):g}")

        if wm.remote in ("required", "preferred") and f.remote is True:
            sc += 10; why.append("télétravail")
        elif wm.remote == "preferred" and f.remote is False:
            sc -= 15; why.append("sur site")
        if f.geo.tags and eligibility(f.geo, s.location.eligible_regions, s.location.excluded_regions):
            sc += 6; why.append("localisation compatible")

        detail = ScoreDetail(score=sc, reasons=why)
        for gate_id, action, penalty, pred in self.gates:
            motif = pred(f)
            if not motif:
                continue
            if action == "reject":
                detail.rejected, detail.gate, detail.score = True, gate_id, -100.0
                detail.reasons.append(f"GATE {gate_id} : {motif}")
                return detail
            detail.score -= penalty
            detail.gate = detail.gate or gate_id
            detail.reasons.append(f"{gate_id} -{penalty:g} : {motif}")
        return detail

    def viable(self, d: ScoreDetail) -> bool:
        return not d.rejected and d.score >= self.profile.scoring.heuristic_min

    def rank(self, jobs: list[Job], keep_rejected: bool = False) -> list[tuple[Job, ScoreDetail]]:
        scored = [(j, self.score(j)) for j in jobs]
        scored.sort(key=lambda x: x[1].score, reverse=True)
        return scored if keep_rejected else [(j, d) for j, d in scored if not d.rejected]

    def title_prefilter(self, job: Job) -> bool:
        """Filtre PEU COÛTEUX pour les sources ATS (qui renvoient TOUTES les offres d'une entreprise)."""
        t = norm(job.title)
        if not (self.primary or self.related or self.must_any):
            return True
        return bool(self.primary.find(t) or self.related.find(t) or self.must_any.find(t))


def _is_hybrid(f: _Facts) -> bool:
    return "hybrid" in f.desc[:600] or "hybride" in f.desc[:600]
