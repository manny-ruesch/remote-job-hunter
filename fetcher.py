import urllib.request
import urllib.error
import json
import re
import html
import hashlib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from config import KEYWORD_FILTERS, TARGET_ROLES, EXCLUDED_ROLES

DISQUALIFIED_COMPANIES = {"openai", "anthropic", "cohere"}

def make_job_id(source: str, company: str, title: str, url: str) -> str:
    clean_url = (url or "").split("?")[0].rstrip("/").lower()
    norm_comp = company.lower().strip()
    norm_title = title.lower().strip()
    raw = f"{norm_comp}:{norm_title}:{clean_url}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def clean_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def matches_keywords(title: str, desc: str, company: str = "") -> bool:
    if company and company.lower().strip() in DISQUALIFIED_COMPANIES:
        return False

    t_lower = (title or "").lower()
    
    # 1. Hard exclusion of core software engineering / pure sales / HR roles
    # Protect phrases like 'lead generation', 'lead gen' before checking 'lead'
    is_lead_gen = bool(re.search(r'\blead\s+(generation|gen|qualif|nurtur)\b', t_lower))
    
    for ex in EXCLUDED_ROLES:
        if ex == "lead" and is_lead_gen:
            continue
        pattern = rf'(?<!\w){re.escape(ex)}(?!\w)' if '+' in ex else rf'\b{re.escape(ex)}\b'
        if re.search(pattern, t_lower):
            return False
        
    # 2. Priority check for sweet-spot roles
    for tr in TARGET_ROLES:
        pattern = rf'\b{re.escape(tr)}\b'
        if re.search(pattern, t_lower):
            return True
        
    # 3. Fallback: general keywords with strict word boundaries
    blob = f"{t_lower} {desc[:800].lower()}"
    for k in KEYWORD_FILTERS:
        pattern = rf'(?<!\w){re.escape(k.lower())}(?!\w)' if '+' in k else rf'\b{re.escape(k.lower())}\b'
        if re.search(pattern, blob):
            return True

    return False

matches_criteria = matches_keywords

def detect_job_type(title: str, desc: str = "", raw_type: str = "") -> str:
    blob = f"{title.lower()} {str(desc)[:500].lower()} {raw_type.lower()}"
    if any(pt in blob for pt in ["part-time", "part_time", "part time", "temps partiel", "half-time", "10-20h", "20h/week", "parttime"]):
        return "part_time"
    if any(ct in blob for ct in ["freelance", "contract", "fractional", "contractor", "prestataire"]):
        return "contract"
    return "full_time"

def is_remote_friendly(location: str = "", title: str = "", is_remote_flag: bool = False, desc: str = "") -> bool:
    # Ensure types are safe even if positional args differ
    if isinstance(is_remote_flag, str):
        desc = is_remote_flag
        is_remote_flag = False
        
    blob_loc = f"{str(location).lower()} {str(title).lower()}"
    if any(neg in blob_loc for neg in ["us only", "usa only", "united states only", "must reside in the us"]):
        return False
        
    if is_remote_flag:
        return True
        
    blob = f"{blob_loc} {str(desc)[:400].lower()}"
    
    # 1. Hard exclusions: strictly on-site or hybrid
    if any(neg in blob for neg in ["on-site only", "onsite only", "100% on-site", "hybrid", "in-person required"]):
        return False
        
    # 2. Strict remote requirement: MUST explicitly mention remote / télétravail
    remote_markers = ["remote", "télétravail", "telework", "distributed", "anywhere", "work from home", "home office"]
    return any(rm in blob for rm in remote_markers)

# 100% Free Direct Ashby ATS Boards (Tech scaleups + fast-growing PMEs/studios)
ASHBY_COMPANIES = [
    # SMB-focused / AI Back Office / Fast-growing PMEs & Modern Tools
    "workhero", "attio", "twenty", "supabase", "plain", "ghost", "temporal",
    "dust", "zapier", "buffer", "posthog", "resend", "vapi", "cursor", "linear",
    "replit", "notion", "synthesia", "pinecone", "weaviate", "deepgram",
    "modal", "sentry", "langchain", "perplexity", "clickup", "lemlist"
]

# 100% Free Direct Greenhouse ATS Boards
GREENHOUSE_COMPANIES = [
    "celonis", "tavily", "airtable", "webflow", "gitlab", "canonical",
    "cloudflare", "stripe", "algolia", "contentful", "mattermost",
    "typeform", "braze", "klaviyo", "intercom"
]

# 100% Free Direct Lever ATS Boards
LEVER_COMPANIES = [
    "pigment", "qonto", "aircall", "deliverect"
]

def fetch_ashby_board(company_slug: str) -> list[dict[str, Any]]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("jobs", []):
                title = item.get("title", "")
                job_url = item.get("jobUrl", "")
                location = item.get("location", "Remote")
                desc = clean_html(item.get("descriptionHtml", ""))
                is_remote = item.get("isRemote", False)
                
                if is_remote_friendly(location, title, is_remote) and matches_keywords(title, desc):
                    job_id = make_job_id("ashby", company_slug, title, job_url)
                    j_type = detect_job_type(title, desc, str(item.get("employmentType", "")))
                    results.append({
                        "id": job_id,
                        "source": f"Ashby · {company_slug.capitalize()}",
                        "company": company_slug.capitalize(),
                        "title": title,
                        "location": location or "Full Remote",
                        "url": job_url, # Direct 100% free application URL
                        "salary": "",
                        "tags": ["direct-ats", "ashby", company_slug, j_type],
                        "job_type": j_type,
                        "description": desc[:3500]
                    })
    except Exception:
        pass
    return results

def fetch_greenhouse_board(company_slug: str) -> list[dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("jobs", []):
                title = item.get("title", "")
                job_url = item.get("absolute_url", "")
                location = item.get("location", {}).get("name", "Remote")
                
                if is_remote_friendly(location, title, False) and matches_keywords(title, ""):
                    job_id = make_job_id("greenhouse", company_slug, title, job_url)
                    j_type = detect_job_type(title, "")
                    results.append({
                        "id": job_id,
                        "source": f"Greenhouse · {company_slug.capitalize()}",
                        "company": company_slug.capitalize(),
                        "title": title,
                        "location": location or "Remote",
                        "url": job_url, # Direct 100% free application URL
                        "salary": "",
                        "tags": ["direct-ats", "greenhouse", company_slug, j_type],
                        "job_type": j_type,
                        "description": f"{title} at {company_slug.capitalize()} ({location})"
                    })
    except Exception:
        pass
    return results

def fetch_lever_board(company_slug: str) -> list[dict[str, Any]]:
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            postings = json.loads(resp.read().decode("utf-8"))
            for item in postings:
                title = item.get("text", "")
                job_url = item.get("applyUrl") or item.get("hostedUrl", "")
                cats = item.get("categories", {})
                location = cats.get("location", "Remote")
                workplace = item.get("workplaceType", "")
                desc = item.get("descriptionPlain", "")
                
                is_rem = workplace.lower() == "remote" or "remote" in location.lower()
                if is_remote_friendly(location, title, is_rem) and matches_criteria(title, desc):
                    job_id = make_job_id("lever", company_slug, title, str(item.get("id", "")))
                    j_type = detect_job_type(title, desc, str(cats.get("commitment", "")))
                    results.append({
                        "id": job_id,
                        "source": f"Lever · {company_slug.capitalize()}",
                        "company": company_slug.capitalize(),
                        "title": title,
                        "location": location,
                        "url": job_url,
                        "salary": "",
                        "tags": ["direct-ats", "lever", company_slug, j_type],
                        "job_type": j_type,
                        "description": desc[:3000]
                    })
    except Exception:
        pass
    return results

def fetch_linkedin_guest_jobs(query: str, location: str = "Europe", job_type_filter: str = "") -> list[dict[str, Any]]:
    import urllib.parse
    from scrapling import StealthyFetcher
    
    q = urllib.parse.quote_plus(query)
    loc = urllib.parse.quote_plus(location)
    jt = f"&{job_type_filter}" if job_type_filter else ""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={q}&location={loc}&f_WT=2{jt}&start=0"
    
    results = []
    try:
        fetcher = StealthyFetcher()
        page = fetcher.fetch(url, timeout=12000)
        cards = page.css("li")
        for c in cards:
            title = (c.css(".base-search-card__title::text").get() or "").strip()
            comp = (c.css(".base-search-card__subtitle a::text").get() or c.css(".base-search-card__subtitle::text").get() or "").strip()
            loc_card = (c.css(".job-search-card__location::text").get() or location).strip()
            link = c.css("a.base-card__full-link::attr(href)").get() or ""
            clean_link = link.split("?")[0] if link else ""
            
            # Must explicitly be remote-friendly (LinkedIn f_WT=2 guarantees remote search flag)
            if title and comp and is_remote_friendly(loc_card, title, is_remote_flag=True) and matches_keywords(title, ""):
                job_id = make_job_id("linkedin", comp, title, clean_link)
                fallback_type = "part_time" if "f_JT=P" in jt else ("contract" if "f_JT=C" in jt else "full_time")
                j_type = detect_job_type(title, "", fallback_type)
                results.append({
                    "id": job_id,
                    "source": f"LinkedIn · {comp}",
                    "company": comp,
                    "title": title,
                    "location": loc_card or "Remote Europe",
                    "url": clean_link,
                    "salary": "",
                    "tags": ["linkedin", "full-remote", j_type],
                    "job_type": j_type,
                    "description": f"{title} at {comp} ({loc_card}). Full Remote role ({j_type}) matching AI automation, n8n, Python, and system integration."
                })
    except Exception:
        pass
    return results

def fetch_wttj_jobs(queries: list[str] = None) -> list[dict[str, Any]]:
    if queries is None:
        queries = ["n8n", "AI Automation", "Solutions Engineer", "RevOps"]
        
    url = "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries"
    headers = {
        "x-algolia-application-id": "CSEKHVMS53",
        "x-algolia-api-key": "4bd8f6215d0cc52b26430765769e65a0",
        "Content-Type": "application/json",
        "Referer": "https://www.welcometothejungle.com/",
        "Origin": "https://www.welcometothejungle.com",
        "User-Agent": "Mozilla/5.0"
    }
    
    requests_payload = []
    for q in queries:
        # Full-time remote
        requests_payload.append({
            "indexName": "wk_cms_jobs_production",
            "params": f"query={q}&hitsPerPage=10&page=0&filters=remote:fulltime"
        })
        # Part-time & Freelance remote
        requests_payload.append({
            "indexName": "wk_cms_jobs_production",
            "params": f"query={q}&hitsPerPage=10&page=0&filters=remote:fulltime AND (contract_type:freelance OR contract_type:part_time)"
        })
    
    results = []
    seen_urls = set()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"requests": requests_payload}).encode("utf-8"),
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for batch in data.get("results", []):
                for hit in batch.get("hits", []):
                    title = hit.get("name", "")
                    org = hit.get("organization", {})
                    company = org.get("name", "PME / Startup")
                    org_slug = org.get("slug", "")
                    job_slug = hit.get("slug", "")
                    
                    if not job_slug or not org_slug:
                        continue
                    job_url = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{job_slug}"
                    if job_url in seen_urls:
                        continue
                    seen_urls.add(job_url)
                    
                    office = hit.get("office", {})
                    location = f"{office.get('city', 'Remote')}, {office.get('country', 'Europe')}"
                    contract_type = hit.get("contract_type", "")
                    j_type = detect_job_type(title, "", str(contract_type))
                    
                    if matches_keywords(title, ""):
                        job_id = make_job_id("wttj", company, title, job_url)
                        results.append({
                            "id": job_id,
                            "source": f"WTTJ · {company}",
                            "company": company,
                            "title": title,
                            "location": location or "Full Remote",
                            "url": job_url,
                            "salary": "",
                            "tags": ["wttj", "scaleup", "full-remote", j_type],
                            "job_type": j_type,
                            "description": f"{title} at {company} ({location}). Full Remote role ({j_type}) on Welcome to the Jungle."
                        })
    except Exception:
        pass
    return results

def fetch_hn_whoishiring() -> list[dict[str, Any]]:
    print("📡 Polling Hacker News 'Who is hiring?' (Direct Founders, PMEs & Startups)...")
    results = []
    try:
        user_url = "https://hacker-news.firebaseio.com/v0/user/whoishiring.json"
        req = urllib.request.Request(user_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            user_data = json.loads(r.read().decode("utf-8"))
            latest_thread_id = user_data["submitted"][0]

        story_url = f"https://hacker-news.firebaseio.com/v0/item/{latest_thread_id}.json"
        with urllib.request.urlopen(urllib.request.Request(story_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as sr:
            story = json.loads(sr.read().decode("utf-8"))
            comment_ids = story.get("kids", [])

        # Scan the first 100 comments from founders / CTOs in parallel
        def fetch_comment(cid):
            try:
                c_url = f"https://hacker-news.firebaseio.com/v0/item/{cid}.json"
                with urllib.request.urlopen(urllib.request.Request(c_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=4) as cr:
                    c_data = json.loads(cr.read().decode("utf-8"))
                    raw = c_data.get("text", "")
                    if not raw:
                        return None
                    text = clean_html(raw)
                    if not is_remote_friendly("Remote", text, True):
                        return None
                        
                    lines = [line.strip() for line in text.split("\n") if line.strip()]
                    if not lines:
                        return None
                    header = lines[0]
                    parts = [p.strip() for p in header.split("|")]
                    company = parts[0][:40] if parts else "Startup / PME"
                    title = parts[1][:60] if len(parts) > 1 else "AI & Automation Specialist"
                    location = parts[2][:40] if len(parts) > 2 else "Remote"
                    
                    if matches_criteria(f"{company} - {title}", text):
                        links = re.findall(r'https?://[^\s<>\"\'\)]+', raw)
                        apply_url = links[0] if links else f"https://news.ycombinator.com/item?id={cid}"
                        
                        job_id = make_job_id("hn", company, title, str(cid))
                        j_type = detect_job_type(title, text)
                        return {
                            "id": job_id,
                            "source": f"Hacker News · {company}",
                            "company": company,
                            "title": title,
                            "location": location,
                            "url": apply_url,
                            "salary": "",
                            "tags": ["startup", "pme", "founder-direct", "hackernews", j_type],
                            "job_type": j_type,
                            "description": text[:3500]
                        }
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(fetch_comment, cid) for cid in comment_ids[:100]]
            for fut in as_completed(futures):
                res = fut.result()
                if res:
                    results.append(res)
    except Exception as e:
        print(f"⚠️ HN fetch error: {e}")
        
    print(f"  -> Found {len(results)} matching PME / Startup jobs directly on Hacker News")
    return results

def fetch_wwr_jobs() -> list[dict[str, Any]]:
    categories = [
        "remote-programming-jobs",
        "remote-devops-sysadmin-jobs",
        "all-other-remote-jobs"
    ]
    results = []
    seen = set()
    for cat in categories:
        url = f"https://weworkremotely.com/categories/{cat}.rss"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                root = ET.fromstring(r.read())
                for item in root.findall(".//item"):
                    raw_title = item.find("title").text or ""
                    parts = raw_title.split(":", 1)
                    comp = parts[0].strip() if len(parts) > 1 else "Startup"
                    title = parts[1].strip() if len(parts) > 1 else raw_title
                    link = item.find("link").text or ""
                    if link in seen: continue
                    seen.add(link)
                    raw_desc = item.find("description").text or ""
                    desc = clean_html(raw_desc)
                    
                    if matches_keywords(title, desc):
                        job_id = make_job_id("wwr", comp, title, link)
                        j_type = detect_job_type(title, desc, cat)
                        results.append({
                            "id": job_id,
                            "source": f"We Work Remotely · {comp}",
                            "company": comp,
                            "title": title,
                            "location": "Full Remote",
                            "url": link,
                            "salary": "",
                            "tags": ["wwr", "full-remote", j_type],
                            "job_type": j_type,
                            "description": desc[:3000]
                        })
        except Exception:
            pass
    return results

def fetch_remotive_jobs() -> list[dict[str, Any]]:
    queries = ["automation", "python"]
    results = []
    seen = set()
    for q in queries:
        url = f"https://remotive.com/api/remote-jobs?search={q}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8"))
                for j in data.get("jobs", []):
                    link = j.get("url", "")
                    if link in seen: continue
                    seen.add(link)
                    title = j.get("title", "")
                    comp = j.get("company_name", "")
                    desc = clean_html(j.get("description", ""))
                    loc = j.get("candidate_required_location", "Worldwide")
                    raw_type = j.get("job_type", "")
                    
                    if is_remote_friendly(loc, title, True) and matches_keywords(title, desc):
                        job_id = make_job_id("remotive", comp, title, link)
                        j_type = detect_job_type(title, desc, raw_type)
                        results.append({
                            "id": job_id,
                            "source": f"Remotive · {comp}",
                            "company": comp,
                            "title": title,
                            "location": loc or "Full Remote",
                            "url": link,
                            "salary": j.get("salary", ""),
                            "tags": ["remotive", "full-remote", j_type],
                            "job_type": j_type,
                            "description": desc[:3000]
                        })
        except Exception:
            pass
    return results

def fetch_jobicy_jobs() -> list[dict[str, Any]]:
    url = "https://jobicy.com/api/v2/remote-jobs?count=50"
    results = []
    seen = set()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
            for j in data.get("jobs", []):
                link = j.get("url", "")
                if link in seen: continue
                seen.add(link)
                title = j.get("jobTitle", "")
                comp = j.get("companyName", "")
                loc = j.get("jobGeo", "Worldwide")
                desc = clean_html(j.get("jobDescription", ""))
                raw_type = j.get("jobType", "")
                
                if is_remote_friendly(loc, title, True) and matches_keywords(title, desc):
                    job_id = make_job_id("jobicy", comp, title, link)
                    j_type = detect_job_type(title, desc, raw_type)
                    results.append({
                        "id": job_id,
                        "source": f"Jobicy · {comp}",
                        "company": comp,
                        "title": title,
                        "location": loc or "Full Remote",
                        "url": link,
                        "salary": j.get("annualSalaryMin", ""),
                        "tags": ["jobicy", "full-remote", j_type],
                        "job_type": j_type,
                        "description": desc[:3000]
                    })
    except Exception:
        pass
    return results

def collect_all_jobs() -> list[dict[str, Any]]:
    print("="*65)
    print("🔍 SCANNING 100% FREE DIRECT ATS BOARDS & STARTUPS/PMES")
    print("="*65)
    all_jobs = []
    
    # 1. Hacker News Who is Hiring (PMEs, Seed/Series A startups, Direct founders)
    hn_jobs = fetch_hn_whoishiring()
    all_jobs.extend(hn_jobs)
    
    # 2. Ashby Boards (Direct apply - Tech scaleups + SMB tools)
    print(f"\n📡 Polling {len(ASHBY_COMPANIES)} direct company boards on Ashby in parallel...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        ashby_futs = {executor.submit(fetch_ashby_board, co): co for co in ASHBY_COMPANIES}
        for fut in as_completed(ashby_futs):
            co = ashby_futs[fut]
            try:
                co_jobs = fut.result()
                if co_jobs:
                    print(f"  ✓ {co.capitalize():<12} : {len(co_jobs)} remote matching jobs")
                    all_jobs.extend(co_jobs)
            except Exception:
                pass
            
    # 3. Greenhouse Boards (Direct apply)
    print(f"\n📡 Polling {len(GREENHOUSE_COMPANIES)} direct company boards on Greenhouse in parallel...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        gh_futs = {executor.submit(fetch_greenhouse_board, co): co for co in GREENHOUSE_COMPANIES}
        for fut in as_completed(gh_futs):
            co = gh_futs[fut]
            try:
                co_jobs = fut.result()
                if co_jobs:
                    print(f"  ✓ {co.capitalize():<12} : {len(co_jobs)} remote matching jobs")
                    all_jobs.extend(co_jobs)
            except Exception:
                pass
            
    # 4. Lever Boards (Direct apply)
    print(f"\n📡 Polling {len(LEVER_COMPANIES)} direct company boards on Lever in parallel...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        lever_futs = {executor.submit(fetch_lever_board, co): co for co in LEVER_COMPANIES}
        for fut in as_completed(lever_futs):
            co = lever_futs[fut]
            try:
                co_jobs = fut.result()
                if co_jobs:
                    print(f"  ✓ {co.capitalize():<12} : {len(co_jobs)} remote matching jobs")
                    all_jobs.extend(co_jobs)
            except Exception:
                pass
            
    # 5. LinkedIn Remote Guest Search (Full-time + Part-time / Contract)
    print(f"\n📡 Polling LinkedIn Remote (Full-time & Part-time)...")
    for q in ["n8n", "AI Automation Engineer", "Automation"]:
        li_jobs = fetch_linkedin_guest_jobs(q, "Europe")
        if li_jobs:
            print(f"  ✓ LinkedIn Full-Time [{q:<16}] : {len(li_jobs)} remote matching jobs")
            all_jobs.extend(li_jobs)
        # Part-time & Contract query
        li_pt = fetch_linkedin_guest_jobs(q, "Europe", "f_JT=P")
        if li_pt:
            print(f"  ✓ LinkedIn Part-Time [{q:<16}] : {len(li_pt)} remote matching jobs")
            all_jobs.extend(li_pt)
            
    # 6. Welcome to the Jungle (French & European Scaleups / PMEs)
    print(f"\n📡 Polling Welcome to the Jungle (Full Remote)...")
    wttj_jobs = fetch_wttj_jobs()
    if wttj_jobs:
        print(f"  ✓ WTTJ        : {len(wttj_jobs)} remote matching jobs (Full-time & Freelance)")
        all_jobs.extend(wttj_jobs)
        
    # 7. We Work Remotely (Global Remote Community)
    print(f"\n📡 Polling We Work Remotely (RSS)...")
    wwr_jobs = fetch_wwr_jobs()
    if wwr_jobs:
        print(f"  ✓ WWR         : {len(wwr_jobs)} remote matching jobs")
        all_jobs.extend(wwr_jobs)
        
    # 8. Remotive (Curated Remote Tech Jobs)
    print(f"\n📡 Polling Remotive API...")
    remotive_jobs = fetch_remotive_jobs()
    if remotive_jobs:
        print(f"  ✓ Remotive    : {len(remotive_jobs)} remote matching jobs")
        all_jobs.extend(remotive_jobs)
        
    # 9. Jobicy (Remote Engineering & Operations)
    print(f"\n📡 Polling Jobicy API...")
    jobicy_jobs = fetch_jobicy_jobs()
    if jobicy_jobs:
        print(f"  ✓ Jobicy      : {len(jobicy_jobs)} remote matching jobs")
        all_jobs.extend(jobicy_jobs)
            
    print(f"\n✅ Total 100% free direct jobs gathered: {len(all_jobs)}")
    return all_jobs
