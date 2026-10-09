import logging
from scrapling import StealthyFetcher
from typing import Optional

# Suppress verbose scrapling logs during batch pipeline runs
logging.getLogger("scrapling").setLevel(logging.ERROR)

def enrich_job_description(url: str) -> Optional[str]:
    """
    Uses Scrapling StealthyFetcher to bypass WAFs/anti-bots, 
    render the dynamic JS page, and extract clean, complete Markdown.
    """
    if not url:
        return None
        
    try:
        stealth = StealthyFetcher()
        page = stealth.fetch(url, timeout=20000)
        if page.status == 200:
            md = page.markdown()
            if md and len(md.strip()) > 100:
                return md[:6000]
    except Exception:
        pass
        
    return None

if __name__ == "__main__":
    test_url = "https://jobs.ashbyhq.com/workhero/2b76354a-1012-476f-a7fe-9e044a1d16e9"
    print(f"Testing Scrapling enricher on: {test_url}")
    result = enrich_job_description(test_url)
    if result:
        print(f"✅ Extracted {len(result)} chars of clean markdown!")
        print("\nPreview:\n" + result[:300] + "...")
    else:
        print("❌ Extraction failed.")
