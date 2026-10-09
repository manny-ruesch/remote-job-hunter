import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

import db
from config import APPLICANT

CV_PATH = Path("C:/Users/Manny/manny-ruesch.github.io/assets/cv-manny-ruesch.pdf")

def auto_fill_application(job_id_or_url: str):
    """
    Launches a headful browser, navigates to the job application page,
    fills in all standard form fields (Name, Email, Phone, Links, Cover Letter),
    attaches the PDF CV, and leaves the browser open for the user to review.
    """
    job = db.get_job_by_id(job_id_or_url)
    if not job:
        # Try search by URL
        conn = db.get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM jobs WHERE url = ?", (job_id_or_url,))
        row = c.fetchone()
        conn.close()
        job = dict(row) if row else None

    if not job:
        print(f"❌ Job not found: {job_id_or_url}")
        return False

    url = job.get("url")
    if not url:
        print("❌ Job has no URL")
        return False

    pitch = job.get("tailored_pitch", "")
    print(f"\n🚀 Launching Assisted Auto-Fill for {job.get('company')} — {job.get('title')}...")
    print(f"🔗 Target: {url}\n")

    with sync_playwright() as p:
        # Launch visible browser window with persistent args
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3) # Wait for client-side forms to render

            # Look for an "Apply" button or link if form is not directly visible
            apply_triggers = page.locator("a:has-text('Apply'), button:has-text('Apply'), a:has-text('Postuler'), button:has-text('Postuler')")
            if apply_triggers.count() > 0:
                first_trigger = apply_triggers.first
                if first_trigger.is_visible():
                    try:
                        first_trigger.click()
                        time.sleep(2)
                    except Exception:
                        pass

            # 1. Fill First Name
            fn_loc = page.locator("input[name*='first' i], input[id*='first' i], input[placeholder*='first' i], input[autocomplete*='given-name']")
            if fn_loc.count() > 0:
                fn_loc.first.fill("Manny")

            # 2. Fill Last Name
            ln_loc = page.locator("input[name*='last' i], input[id*='last' i], input[placeholder*='last' i], input[autocomplete*='family-name']")
            if ln_loc.count() > 0:
                ln_loc.first.fill("Ruesch")

            # 3. Fill Full Name (if no first/last split)
            if fn_loc.count() == 0 and ln_loc.count() == 0:
                name_loc = page.locator("input[name*='name' i], input[id*='name' i], input[placeholder*='Full Name' i]")
                if name_loc.count() > 0:
                    name_loc.first.fill(APPLICANT["name"])

            # 4. Fill Email
            email_loc = page.locator("input[type='email'], input[name*='email' i], input[id*='email' i], input[placeholder*='email' i]")
            if email_loc.count() > 0:
                email_loc.first.fill(APPLICANT["email"])

            # 5. Fill Phone
            phone_loc = page.locator("input[type='tel'], input[name*='phone' i], input[id*='phone' i], input[placeholder*='phone' i]")
            if phone_loc.count() > 0:
                phone_loc.first.fill(APPLICANT["phone"])

            # 6. Fill Location
            loc_loc = page.locator("input[name*='location' i], input[id*='location' i], input[placeholder*='city' i], input[name*='city' i]")
            if loc_loc.count() > 0:
                loc_loc.first.fill("Berlin, Germany")

            # 7. Fill LinkedIn
            linkedin_loc = page.locator("input[name*='linkedin' i], input[id*='linkedin' i], input[placeholder*='linkedin' i]")
            if linkedin_loc.count() > 0:
                linkedin_loc.first.fill(APPLICANT["linkedin"])

            # 8. Fill Website / Portfolio
            web_loc = page.locator("input[name*='website' i], input[name*='portfolio' i], input[id*='portfolio' i], input[placeholder*='portfolio' i], input[placeholder*='website' i]")
            if web_loc.count() > 0:
                web_loc.first.fill(APPLICANT["portfolio"])

            # 9. Fill GitHub
            gh_loc = page.locator("input[name*='github' i], input[id*='github' i], input[placeholder*='github' i]")
            if gh_loc.count() > 0:
                gh_loc.first.fill(APPLICANT["github"])

            # 10. Fill Cover Letter / Additional Info / Notes
            if pitch:
                cl_loc = page.locator("textarea[name*='cover' i], textarea[id*='cover' i], textarea[placeholder*='cover' i], textarea[name*='additional' i], textarea[name*='notes' i], textarea[name*='comments' i]")
                if cl_loc.count() > 0:
                    cl_loc.first.fill(pitch)

            # 11. Attach Resume / CV PDF
            if CV_PATH.exists():
                file_inputs = page.locator("input[type='file']")
                if file_inputs.count() > 0:
                    try:
                        file_inputs.first.set_input_files(str(CV_PATH))
                        print(f"📎 Attached Resume PDF: {CV_PATH.name}")
                    except Exception as e:
                        print(f"⚠️ Could not attach CV automatically: {e}")

            print("✅ Auto-Fill complete! The browser is left open in front of you.")
            print("👉 Check the inputs, answer any custom company questions, and click 'Submit'!")
            
            # Update status in DB
            db.update_job_status(job["id"], "applied")

            # Keep browser alive until user closes it or presses Enter
            page.wait_for_timeout(300000) # 5 minutes grace period

        except Exception as e:
            print(f"⚠️ Auto-fill error: {e}")
            return False

    return True

if __name__ == "__main__":
    if len(sys.argv) > 1:
        auto_fill_application(sys.argv[1])
    else:
        print("Usage: python auto_fill_engine.py <job_id_or_url>")
