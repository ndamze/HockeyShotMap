import os
import time
from playwright.sync_api import sync_playwright

APP_URL = os.environ["APP_URL"]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(APP_URL, wait_until="domcontentloaded", timeout=120000)

    # If the sleep page appears, it shows a button like:
    # "Yes, get this app back up!"
    try:
        # Give the page a moment to render any sleep overlay
        page.wait_for_timeout(3000)

        # Try common button texts / selectors used on the sleep page
        possible_texts = [
            "Yes, get this app back up!",
            "Get this app back up",
            "Wake up",
        ]
        woke = False
        for txt in possible_texts:
            btns = page.get_by_role("button", name=txt)
            if btns.count() > 0:
                btns.first.click()
                woke = True
                break

        # If no role-based button found, try a generic selector fallback
        if not woke:
            try:
                page.locator("button:has-text('get this app back up')").first.click()
                woke = True
            except Exception:
                pass

        # Wait for the app to boot if we clicked the wake button
        if woke:
            # Streamlit boot can take a bit; wait until the main app canvas is visible
            page.wait_for_selector("canvas, .stApp, [data-testid='stAppViewContainer']", timeout=180000)

        # As a final nudge, hit the internal health endpoint (if available)
        # Not strictly required; some envs don’t expose it, so ignore failures.
        try:
            page.goto(APP_URL.rstrip("/") + "/_stcore/health", timeout=60000)
        except Exception:
            pass

        # Return to main app
        page.goto(APP_URL, wait_until="load", timeout=120000)

        # Keep the session alive for a moment
        time.sleep(5)

    finally:
        browser.close()
