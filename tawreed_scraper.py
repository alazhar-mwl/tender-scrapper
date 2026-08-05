"""
OQ Tawreed tender scraper (JAGGAER platform)
--------------------------------------------
Logs into https://tawreed.oq.com (JAGGAER e-sourcing, /esop/ paths), scrapes
the RFQ/tender listings visible to the supplier account, and merges them into
tenders.json with source="OQ Tawreed" — feeding the same TenderIQ dashboard
as the PDO scraper.

Credentials in password.env.txt:
    TAWREED_USERNAME=...
    TAWREED_PASSWORD=...
    TAWREED_URL=https://tawreed.oq.com          (optional override)

Run:
    python tawreed_scraper.py               # scrape listings
    python tawreed_scraper.py --diagnose    # login, dump screenshots + page
                                            # structure to debug/, scrape nothing

Unlike SAP SRM, JAGGAER renders plain HTML tables — no nested iframes, no
POWL locks. Session cookies are reused via tawreed_session.json so repeat
runs skip the login form.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Page, TimeoutError as PWTimeout, async_playwright

# Reuse the shared pipeline pieces (merge-safe saving, logging setup)
from tender_scraper import merge_tenders, log, OUT_FILE

BASE_DIR   = Path(__file__).parent
ENV_FILE   = BASE_DIR / "password.env.txt"
STATE_FILE = BASE_DIR / "tawreed_session.json"
DEBUG_DIR  = BASE_DIR / "debug"

SOURCE_OQ = "OQ Tawreed"

load_dotenv(ENV_FILE)

PORTAL_URL = os.getenv("TAWREED_URL", "https://tawreed.oq.com").rstrip("/")
USERNAME   = os.getenv("TAWREED_USERNAME", "")
PASSWORD   = os.getenv("TAWREED_PASSWORD", "")

# Use JAGGAER's plain login form — OQ's custom skin (login.jst) posts the same
# fields but wraps them in an iframe + notice overlays that complicate clicks.
LOGIN_URL = f"{PORTAL_URL}/esop/guest/login.do"

# JAGGAER login form field candidates (confirmed live: name='login' / name='password')
SEL_USER   = "input[name='login'], input[name='username'], #username, input[name='j_username']"
SEL_PASS   = "input[name='password'], #password, input[name='j_password']"
SEL_SUBMIT = ("button[type='submit'], input[type='submit'], input[value='LOGIN' i], "
              "a:has-text('Login'), button:has-text('Login'), #loginButton")

# Confirmed live 2026-07-16 from the post-login menu (debug/tawreed_diagnose.json):
# "Tenders Open to All Suppliers" -> the public list of everything OQ has
# published (34 tenders, 4 pages, all "Running" — the OQ analogue of PDO's
# active-tender list). There's also a "My Tenders" tab, but that turned out
# to be a 12,224-row / 1,223-page historical archive, not a scoped list of
# current opportunities — deliberately NOT scraped (see project memory).
#
# NOTE: this MUST be reached by clicking the real anchor — a bare page.goto()
# to the same path trips JAGGAER's CSRF guard ("cross-site request forgery
# control is enabled") because the request then carries no referrer/session
# navigation token. Href below is an exact match against the live menu.
LIST_LINKS = [
    ("/esop/guest/go/neg/rfq/public", "Tenders Open to All Suppliers"),
]

# Recovery target if a list click fails partway through — confirmed live
# 2026-07-16 in the Angular toolkit's sidebar nav.
DASHBOARD_HREF = "/esop/toolkit/dashboard/dashboard.do?selectTabId=0"


async def screenshot(page: Page, name: str) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    try:
        await page.screenshot(path=str(DEBUG_DIR / f"tawreed_{name}.png"), full_page=False)
        log.info("  screenshot → tawreed_%s.png", name)
    except Exception as exc:
        log.warning("  screenshot %s failed: %s", name, exc)


async def is_logged_in(page: Page) -> bool:
    """
    Logged-in JAGGAER pages have an actual logout anchor and live outside the
    guest/login areas. (Checking raw HTML for the word 'logout' false-positives
    on the login page itself — its JS bundle contains the string.)
    """
    if "login" in page.url.lower() or "/guest/" in page.url:
        return False
    for f in page.frames:
        try:
            if await f.locator("a[href*='logout' i], a[href*='logoff' i]").count():
                return True
        except Exception:
            continue
    return False


async def close_notice_popup(page: Page) -> None:
    """
    The OQ login page shows an 'Important Notice' overlay (<div id="advice">)
    that intercepts pointer events — and it renders INSIDE the login iframe.
    Hide it in every frame rather than hunting for its close button.
    """
    for f in page.frames:
        try:
            hidden = await f.evaluate("""() => {
                const d = document.getElementById('advice');
                if (d) { d.style.display = 'none'; return true; }
                return false;
            }""")
            if hidden:
                log.info("  Hid notice overlay (#advice) in frame %s", f.url[:70])
        except Exception:
            continue


async def find_login_frame(page: Page):
    """The login form may render in the main document or a nested iframe."""
    for f in page.frames:
        try:
            if await f.locator(SEL_USER).first.count():
                return f
        except Exception:
            continue
    return None


async def ensure_logged_in(page: Page, ctx) -> None:
    if not USERNAME or not PASSWORD:
        sys.exit("TAWREED_USERNAME / TAWREED_PASSWORD missing from password.env.txt")

    await page.goto(PORTAL_URL, wait_until="load", timeout=60_000)
    await asyncio.sleep(3)
    await screenshot(page, "01_landing")

    if await is_logged_in(page):
        log.info("Saved Tawreed session still valid — skipping login.")
        await close_notice_popup(page)
        return

    log.info("Logging in to Tawreed (%s)…", LOGIN_URL)
    await page.goto(LOGIN_URL, wait_until="load", timeout=60_000)
    await asyncio.sleep(3)
    await close_notice_popup(page)
    await screenshot(page, "02_login_page")

    frame = await find_login_frame(page)
    if frame is None:
        sys.exit("Login form not found on the page (no username field in any frame) "
                 "— see debug/tawreed_02_login_page.png")
    log.info("  Login form found in frame: %s", frame.url[:90])

    # Log the form we're about to submit (names only — helps selector tuning)
    form_info = await frame.evaluate("""() => {
        const u = document.querySelector("input[name='username'], #username, input[name='j_username']");
        const form = u ? u.form : document.querySelector('form');
        if (!form) return null;
        return {action: form.action, method: form.method,
                fields: [...form.elements].map(e =>
                    ({name: e.name, type: e.type, id: e.id}))};
    }""")
    log.info("  Login form: %s", json.dumps(form_info)[:400])

    await frame.locator(SEL_USER).first.fill(USERNAME, timeout=15_000)
    await frame.locator(SEL_PASS).first.fill(PASSWORD, timeout=15_000)
    await close_notice_popup(page)   # overlay re-appears on some page states

    old_urls = {f.url for f in page.frames}
    try:
        await frame.locator(SEL_SUBMIT).first.click(timeout=10_000)
    except Exception as exc:
        log.warning("  Submit click failed (%s) — pressing Enter instead", exc)
        await frame.locator(SEL_PASS).first.press("Enter")

    # Wait up to 30 s for ANY frame to navigate away from the login page
    for _ in range(15):
        await asyncio.sleep(2)
        if {f.url for f in page.frames} != old_urls:
            break
    else:
        log.warning("  No frame navigated after submit — pressing Enter in password field")
        try:
            await frame.locator(SEL_PASS).first.press("Enter")
            await asyncio.sleep(8)
        except Exception:
            pass

    try:
        await page.wait_for_load_state("load", timeout=20_000)
    except PWTimeout:
        pass
    await asyncio.sleep(3)
    await screenshot(page, "03_after_login")

    if not await is_logged_in(page):
        # Hunt for an error banner before giving up
        err = ""
        for f in page.frames:
            try:
                err = await f.evaluate("""() => {
                    const cand = document.querySelectorAll(
                        ".error, .errorMessage, .alert, [class*='error' i], font[color='red']");
                    for (const el of cand) {
                        const t = (el.innerText || '').trim();
                        if (t) return t.slice(0, 300);
                    }
                    return '';
                }""")
                if err:
                    break
            except Exception:
                continue
        log.error("  Frames after submit: %s", [f.url[:90] for f in page.frames])
        sys.exit(f"Tawreed login FAILED. Error text on page: {err!r} "
                 f"(see debug/tawreed_03_after_login.png)")

    await close_notice_popup(page)   # post-login "Cybersecurity Fraud" notice
    await ctx.storage_state(path=str(STATE_FILE))
    log.info("Login OK — session saved to %s", STATE_FILE.name)


async def click_href(page: Page, href: str, label: str) -> bool:
    """
    Click the real anchor with this EXACT href (across all frames), so the
    navigation carries proper session/referrer state instead of tripping the
    CSRF guard. Returns True once the click succeeds; caller checks content.

    The Angular toolkit UI keeps its full nav menu inside a collapsed flyout
    (only rail icons are visible), so a normal Playwright click often fails
    actionability checks (element outside viewport / not visible) even though
    the anchor is real and in the DOM. Fall back to a JS-dispatched click,
    which fires the router's click handler regardless of visual state.
    """
    for f in page.frames:
        try:
            loc = f.locator(f"a[href='{href}']").first
            if not await loc.count():
                continue
            try:
                await loc.click(timeout=6_000)
            except Exception:
                log.info("  Real click on %r not actionable — dispatching JS click", label)
                await loc.evaluate("el => el.click()")
            await asyncio.sleep(4)
            await close_notice_popup(page)   # overlay pattern reappears on inner pages
            log.info("Clicked %r (%s) — now at %s", label, href, page.url[:90])
            return True
        except Exception as exc:
            log.warning("  Click on %r failed: %s", label, exc)
            continue
    log.warning("  Anchor with href=%r not found on current page", href)
    return False


async def click_pager_page(page: Page, n: int) -> bool:
    """
    Click the numbered page control in the Angular list UI's pager
    (<span class="IconButton-label">N</span> inside a MUI-style IconButton).
    Scoped to that class specifically — plain digit spans elsewhere on the
    page (row numbers, the "Show: 10" page-size selector) would false-match
    a bare text-based search.
    """
    for f in page.frames:
        try:
            loc = f.locator(f"span.IconButton-label:text-is('{n}')").first
            if not await loc.count():
                continue
            try:
                await loc.click(timeout=5_000)
            except Exception:
                await loc.evaluate("el => (el.closest('button') || el.parentElement || el).click()")
            await asyncio.sleep(3)
            return True
        except Exception:
            continue
    return False


async def scrape_paginated_list(page: Page, label: str, max_pages: int = 30) -> list[dict]:
    """
    Extract every page of the current list's table, deduping by reference_number.

    KNOWN PORTAL LIMITATION (confirmed 2026-07-16 via direct network inspection):
    clicking a page-number control (or the "Show: N" page-size dropdown) DOES
    submit the right value server-side — the POST body correctly carries
    pagerComponent.page=2 — but the server's list-async.si response still
    renders page 1 regardless. This reproduces with a genuine Playwright
    button click (not a synthetic/JS one), so it isn't a click-targeting bug
    on our side; it looks like a defect in Tawreed's own pagination for this
    list. The list sorts ascending by closing deadline by default, so the
    first page (10 rows) is the 10 soonest-closing tenders — the most
    actionable subset anyway. We detect the stall and stop rather than loop.
    """
    by_ref: dict[str, dict] = {}
    page_num = 1
    while page_num <= max_pages:
        rows = await extract_rows(page)
        new = 0
        for t in rows:
            ref = t["reference_number"]
            if ref and ref not in by_ref:
                by_ref[ref] = t
                new += 1
        log.info("  %r page %d → %d row(s) (%d new)", label, page_num, len(rows), new)
        await screenshot(page, f"pager_{label.replace(' ', '_')[:20]}_p{page_num}")
        if page_num > 1 and new == 0:
            log.info("  Page %d returned no new rows — Tawreed's pagination for this list "
                     "doesn't advance past page 1 (known portal limitation, see docstring). "
                     "Stopping with %d row(s) (sorted soonest-closing first).",
                     page_num, len(by_ref))
            break
        clicked = await click_pager_page(page, page_num + 1)
        if not clicked:
            log.info("  No control for page %d — reached the end of %r", page_num + 1, label)
            break
        # Wait (briefly) for the table content to change before re-extracting.
        old_first_ref = rows[0]["reference_number"] if rows else None
        for _ in range(5):
            await asyncio.sleep(1)
            check = await extract_rows(page)
            if check and (not old_first_ref or check[0]["reference_number"] != old_first_ref):
                break
        page_num += 1
    return list(by_ref.values())


async def diagnose(page: Page) -> None:
    """Dump everything needed to wire up the list scrape by hand."""
    await screenshot(page, "diagnose")
    info = await page.evaluate("""() => {
        const links = [...document.querySelectorAll('a')].slice(0, 120)
            .map(a => ({text: (a.innerText || '').trim().slice(0, 60),
                        href: (a.getAttribute('href') || '').slice(0, 120)}))
            .filter(l => l.text || l.href);
        const tables = [...document.querySelectorAll('table')].slice(0, 10)
            .map(t => ({rows: t.rows.length,
                        firstRow: (t.rows[0]?.innerText || '').slice(0, 200)}));
        const frames = [...document.querySelectorAll('iframe')]
            .map(f => f.src.slice(0, 120));
        // Pagination controls in this Angular-style UI aren't plain <a href>
        // — hunt for small clickable elements with page-number-ish text.
        const pagers = [...document.querySelectorAll(
                "button, a, [role='button'], [tabindex], span, div")]
            .filter(el => {
                const t = (el.innerText || '').trim();
                return el.children.length === 0 &&
                       (/^\\d{1,3}$/.test(t) || /^(next|»|→|>)$/i.test(t));
            })
            .slice(0, 30)
            .map(el => ({
                tag: el.tagName, text: (el.innerText || '').trim(),
                cls: (el.className || '').toString().slice(0, 80),
                id: el.id || '', outer: el.outerHTML.slice(0, 160),
            }));
        return {url: location.href, title: document.title, links, tables, frames, pagers};
    }""")
    out = DEBUG_DIR / "tawreed_diagnose.json"
    out.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Diagnose dump → %s (%d links, %d tables, %d iframes, %d pager candidates)",
             out.name, len(info["links"]), len(info["tables"]), len(info["frames"]),
             len(info.get("pagers", [])))


async def extract_rows(page: Page) -> list[dict]:
    """
    Generic first pass: read the largest table on the page and map columns
    heuristically. Will be tightened after the first --diagnose run shows the
    real list structure.
    """
    rows = await page.evaluate("""() => {
        const tables = [...document.querySelectorAll('table')];
        if (!tables.length) return [];
        const biggest = tables.reduce((a, b) => (a.rows.length >= b.rows.length ? a : b));
        return [...biggest.rows].map(tr => [...tr.cells].map(td => (td.innerText || '').trim()));
    }""")
    if len(rows) < 2:
        return []

    header = [h.lower() for h in rows[0]]

    def col(*names):
        for n in names:
            for i, h in enumerate(header):
                if n in h:
                    return i
        return None

    i_ref    = col("code", "number", "reference", "id")
    i_title  = col("title", "description", "object", "subject")
    i_close  = col("closing", "deadline", "expiry", "end date", "time limit")
    i_status = col("status", "phase")

    tenders = []
    for cells in rows[1:]:
        if not any(cells):
            continue
        def get(i):
            return cells[i] if i is not None and i < len(cells) else ""
        ref = get(i_ref) or (cells[0] if cells else "")
        title = get(i_title) or (cells[1] if len(cells) > 1 else "")
        if not ref and not title:
            continue
        tenders.append({
            "reference_number": re.sub(r"\s+", " ", ref).strip(),
            "title": re.sub(r"\s+", " ", title).strip(),
            "rfx_type": "Tawreed RFQ",
            "status": get(i_status),
            "start_date": "",
            "closing_date": get(i_close),
            "response_status": "",
            "description": "",
            "estimated_value": "",
            "link": page.url,
            "documents": [],
        })
    return tenders


async def main(diagnose_only: bool = False) -> None:
    async with async_playwright() as pw:
        # --disable-gpu / --disable-software-rasterizer: headless Chromium's
        # GPU process can crash ("Page.goto: Page crashed") under a Windows
        # session with no active/unlocked desktop compositor — confirmed
        # live 2026-08-05 running via Task Scheduler.
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-software-rasterizer"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=str(STATE_FILE) if STATE_FILE.exists() else None,
            accept_downloads=True,
        )
        page = await ctx.new_page()
        try:
            await ensure_logged_in(page, ctx)
            # ensure_logged_in leaves us on the post-login menu page (or, on a
            # reused session, the portal root) — both carry the "Tender…" anchors.

            by_ref: dict[str, dict] = {}
            for i, (href, label) in enumerate(LIST_LINKS):
                shot_name = f"04_list_{i}"
                clicked = await click_href(page, href, label)
                if not clicked:
                    log.warning("Could not reach %r — dumping diagnose info.", label)
                    await diagnose(page)
                    if diagnose_only:
                        return
                    # try to recover to the menu for the next link
                    await click_href(page, DASHBOARD_HREF, "Dashboard")
                    continue

                await screenshot(page, shot_name)
                has_table = await page.locator("table tr").count() > 2
                if diagnose_only:
                    await diagnose(page)
                elif not has_table:
                    log.warning("No table on %r page — dumping diagnose info.", label)
                    await diagnose(page)
                else:
                    rows = await scrape_paginated_list(page, label)
                    log.info("  %r → %d row(s) total across all pages", label, len(rows))
                    for t in rows:
                        ref = t["reference_number"]
                        if ref and ref not in by_ref:
                            by_ref[ref] = t

                # back to the menu so the next link's anchor is on-page again
                if i < len(LIST_LINKS) - 1:
                    await click_href(page, DASHBOARD_HREF, "Dashboard")

            if diagnose_only:
                return

            tenders = list(by_ref.values())
            log.info("Scraped %d unique Tawreed tender(s) total", len(tenders))
            if tenders:
                merged = merge_tenders(tenders, SOURCE_OQ)
                OUT_FILE.write_text(
                    json.dumps(merged, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                log.info("Saved → %s (%d total across sources)", OUT_FILE.name, len(merged))
            else:
                log.warning("No rows mapped from any list — run --diagnose and inspect.")
        finally:
            await ctx.storage_state(path=str(STATE_FILE))
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main(diagnose_only="--diagnose" in sys.argv))
