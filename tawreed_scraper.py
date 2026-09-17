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
# active-tender list).
#
# NOTE: this MUST be reached by clicking the real anchor — a bare page.goto()
# to the same path trips JAGGAER's CSRF guard ("cross-site request forgery
# control is enabled") because the request then carries no referrer/session
# navigation token. Href below is an exact match against the live menu.
PUBLIC_TENDERS_HREF, PUBLIC_TENDERS_LABEL = "/esop/guest/go/neg/rfq/public", "Tenders Open to All Suppliers"
LIST_LINKS = [
    (PUBLIC_TENDERS_HREF, PUBLIC_TENDERS_LABEL),
]

# "My Tenders" (debug/tawreed_diagnose.json / tawreed_diagnose.png) is a
# 12,224-row / 1,223-page historical archive of every tender ever
# associated with this account — NOT scoped to current opportunities like
# the public list. But it also surfaces tenders OQ invited this account to
# directly, which never appear on the public list — confirmed live
# 2026-09-16 that some currently-open invited tenders were missing from
# tenders.json for exactly this reason. Paging through the full archive
# isn't feasible, so scrape_my_tenders() sorts by closing date and stops
# once the open block ends (see its docstring) instead of walking all
# 1,223 pages.
#
# Confirmed live via the diagnose screenshot: "My Tenders" is a TAB that
# only exists once already on a Tenders list page, sitting next to
# "Tenders Open to All Suppliers" — its nav-menu entry has href="#fh", the
# same ambiguous marker used by pure category togglers ("Tenders",
# "Auctions", ...) elsewhere in the menu, so it can't be trusted to
# navigate anywhere on its own. Reach it by clicking the confirmed-real
# public-list anchor first, then this tab (click_link_text, since the tab
# itself has no real href either) — never directly from the dashboard.
MY_TENDERS_LINK_TEXT = "My Tenders"
MY_TENDERS_URL_MARKER = "joinRfq/list"

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


async def click_link_text(page: Page, text: str, label: str) -> bool:
    """
    Same rationale as click_href, but for menu entries whose anchor uses a
    JS-router href (e.g. "#") instead of a real path, so they have to be
    matched by visible link text instead — "My Tenders" is one of these.
    """
    for f in page.frames:
        try:
            loc = f.locator(f"a:text-is('{text}')").first
            if not await loc.count():
                continue
            try:
                await loc.click(timeout=6_000)
            except Exception:
                log.info("  Real click on %r not actionable — dispatching JS click", label)
                await loc.evaluate("el => el.click()")
            await asyncio.sleep(4)
            await close_notice_popup(page)
            log.info("Clicked %r (text match) — now at %s", label, page.url[:90])
            return True
        except Exception as exc:
            log.warning("  Click on %r failed: %s", label, exc)
            continue
    log.warning("  Anchor with text=%r not found on current page", text)
    return False


def is_open_status(status: str) -> bool:
    """Same status vocabulary as the public list, which only ever shows 'Running'."""
    return "running" in status.lower()


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


async def select_page_size(page: Page, size: int = 100) -> bool:
    """
    Switch the public list's "Show: 10/20/50/100" control to a larger value
    — a genuine native <select id="...pagerComponent.numberOfDisplayedItem">,
    found live 2026-08-06 via diagnostic inspection. This is a DIFFERENT
    request/control than the page-number pager buttons (see
    scrape_paginated_list's docstring for that mechanism's known bug) and
    isn't known to share the same server-side defect — it loads everything
    on one page instead of paging through it.
    """
    for f in page.frames:
        try:
            loc = f.locator("select[id*='numberOfDisplayedItem']").first
            if not await loc.count():
                continue
            await loc.select_option(value=str(size))
            await asyncio.sleep(3)
            return True
        except Exception:
            continue
    return False


async def scrape_paginated_list(page: Page, label: str, max_pages: int = 30) -> list[dict]:
    """
    Extract every row of the current list, deduping by reference_number.

    Tries switching to a large page size first (select_page_size) so
    everything loads on one page — see that function's docstring. Falls
    back to the page-number pager only if that doesn't yield more rows
    than a single default page.

    KNOWN PORTAL LIMITATION in the pager fallback (confirmed 2026-07-16 via
    direct network inspection): clicking a page-number control DOES submit
    the right value server-side — the POST body correctly carries
    pagerComponent.page=2 — but the server's list-async.si response
    sometimes still renders page 1 regardless. This reproduces with a
    genuine Playwright button click (not a synthetic/JS one), so it isn't a
    click-targeting bug on our side; it looks like an intermittent defect in
    Tawreed's own pagination for this list (confirmed still present
    2026-08-06, after having worked correctly on 2026-08-02/08-05 — treat it
    as flaky, not permanently fixed or permanently broken). The list sorts
    ascending by closing deadline by default, so the first page (10 rows) is
    the 10 soonest-closing tenders — the most actionable subset anyway if
    both mechanisms fail. We detect the stall and stop rather than loop.
    """
    first_pass = await extract_rows(page)
    if await select_page_size(page, 100):
        bigger = await extract_rows(page)
        if len(bigger) > len(first_pass):
            log.info("  %r: switched to 100-per-page, got %d row(s) in one page (vs %d default)",
                      label, len(bigger), len(first_pass))
            by_ref = {t["reference_number"]: t for t in bigger if t.get("reference_number")}
            return list(by_ref.values())
        log.info("  %r: page-size switch didn't yield extra rows (%d vs %d) — falling back to pager",
                  label, len(bigger), len(first_pass))

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


async def select_open_filter(page: Page) -> bool:
    """
    Best-effort use of the native 'Filter By' <select id="storedFilterId">
    on My Tenders (confirmed live 2026-09-16, debug/tawreed_my_tenders.json)
    to filter to open tenders server-side, collapsing the 12,224-row
    archive before any pagination — so neither a page-size limitation nor
    a pager stall (see scrape_my_tenders docstring) can under-collect.

    Its actual options, confirmed live, are 'All Tenders', 'Running &
    submitted Tenders', and 'Submission not yet submitted' — a COMPOUND
    status+submission filter, not a plain open/closed one. 'Running &
    submitted Tenders' looked like a match on a naive substring search but
    is wrong: it requires a submission to already exist, which excluded
    every real open tender when tried live (all showed "No Submission
    Prepared"). There is no option here equivalent to "open regardless of
    submission status", so only an EXACT label match is accepted, and
    returns False harmlessly (falling back to closing-date sort + a
    client-side status check — see scrape_my_tenders) when none of these
    options qualifies, as they currently don't.
    """
    OPEN_ONLY_LABELS = {"open", "open tenders", "running", "running tenders"}
    for f in page.frames:
        try:
            # Prefer the exact confirmed-live ID — the generic substring
            # selector also matches a second, unrelated <select> (a hidden
            # 'manage saved filters' listbox, id='ListManager.managingFilterId')
            # and interacting with the wrong one is exactly the kind of
            # thing that silently broke the table on a prior run.
            sel = f.locator("#storedFilterId").first
            if not await sel.count():
                sel = f.locator("select[id*='filter' i], select[name*='filter' i]").first
                if not await sel.count():
                    continue
            options = await sel.locator("option").all_inner_texts()
            current = await sel.evaluate("el => el.options[el.selectedIndex]?.text?.trim() || ''")
            match = next((o for o in options if o.strip().lower() in OPEN_ONLY_LABELS), None)
            if match:
                if current.strip().lower() != match.strip().lower():
                    await sel.select_option(label=match)
                    await asyncio.sleep(3)
                    log.info("  My Tenders: server-side filter set to %r (native select)", match)
                return True
            # No open-only option currently exists on this control (see
            # docstring) — but if it's stuck on something OTHER than the
            # unfiltered baseline (e.g. a prior version of this function
            # picked 'Running & submitted Tenders' by mistake, which
            # silently hides open-but-unsubmitted tenders), reset it. Only
            # touch the control when it actually needs changing — a
            # same-value select_option still fires the page's onchange
            # reload and was observed live to leave the table briefly
            # empty for the very next read.
            all_opt = next((o for o in options if o.strip().lower() == "all tenders"), None)
            if all_opt and current.strip().lower() != "all tenders":
                log.info("  My Tenders: resetting stray filter %r back to 'All Tenders'", current)
                await sel.select_option(label=all_opt)
                await asyncio.sleep(2)
        except Exception:
            continue

    for f in page.frames:
        try:
            trigger = f.locator("text='All Tenders'").first
            if not await trigger.count():
                continue
            await trigger.click(timeout=4_000)
            await asyncio.sleep(1)
            option = f.locator("text=/^(open tenders|running)$/i").first
            if await option.count():
                await option.click(timeout=4_000)
                await asyncio.sleep(3)
                log.info("  My Tenders: server-side filter set (custom dropdown)")
                return True
            await page.keyboard.press("Escape")
        except Exception:
            continue
    return False


async def scrape_my_tenders(page: Page, max_pages: int = 30) -> list[dict]:
    """
    'My Tenders' holds every tender ever associated with this account
    (12,224 rows / 1,223 pages) — mostly closed/awarded history, plus the
    currently-open ones we actually want (see MY_TENDERS_LINK_TEXT comment).
    Walking the whole archive isn't feasible, so instead:

      1. Try select_open_filter() first — a server-side filter collapses
         the archive before any pagination happens, which is the only
         approach immune to both a small page size and a pager stall (see
         below). Unconfirmed against the live portal; see its docstring.
      2. If unavailable: on a fresh load this list was observed sorting by
         'Tender Closing Date/Time' descending (most-future-first) — see
         debug/tawreed_diagnose.png — which clusters every open tender
         (future closing date, by definition) at the front. Switch to the
         100-per-page view (select_page_size — also unconfirmed on this
         specific list; the "Show" control may be a custom widget rather
         than the native <select> that call expects) and read page 1. If
         it has zero open ('Running') rows, don't trust the assumed sort
         direction blindly — click the header once to flip it and re-read.
      3. Page through collecting open rows. If step 1 succeeded, stop as
         soon as any page has none (the filtered list should be
         homogeneous, so that means it's exhausted). Otherwise, only stop
         once a page has none AND at least one open row was already found
         elsewhere — a pager stall or the wrong sort direction could
         otherwise cut this off after only page 1. If none turn up at all,
         log a warning rather than silently returning nothing.
    """
    await close_notice_popup(page)
    filtered = await select_open_filter(page)
    log.info("  My Tenders: %s",
              "using server-side open-tender filter" if filtered
              else "no server-side filter found — falling back to closing-date sort + pager")

    got_size = await select_page_size(page, 100)
    log.info("  My Tenders: page-size switch to 100 %s",
              "OK" if got_size else "unavailable — using default page size")

    by_ref: dict[str, dict] = {}
    found_any_open = False
    flipped_sort = False
    page_num = 1
    while page_num <= max_pages:
        rows = await extract_rows(page)
        open_rows = [t for t in rows if is_open_status(t["status"])]

        if not filtered and page_num == 1 and rows and not open_rows and not flipped_sort:
            log.info("  My Tenders page 1 has no open tenders under the default sort — "
                      "flipping 'Tender Closing Date/Time' sort and re-checking")
            flipped_sort = True
            await click_link_text(page, "Tender Closing Date/Time", "My Tenders: flip closing-date sort")
            rows = await extract_rows(page)
            open_rows = [t for t in rows if is_open_status(t["status"])]

        new = 0
        for t in open_rows:
            ref = t["reference_number"]
            if ref and ref not in by_ref:
                by_ref[ref] = t
                new += 1
        log.info("  My Tenders page %d → %d row(s), %d open (%d new)",
                  page_num, len(rows), len(open_rows), new)

        if open_rows:
            found_any_open = True
        elif filtered or found_any_open:
            log.info("  Page %d had no open tenders — stopping (%s, %d row(s) total)",
                      page_num, "filtered list exhausted" if filtered else "past the open block",
                      len(by_ref))
            break

        old_first_ref = rows[0]["reference_number"] if rows else None
        clicked = await click_pager_page(page, page_num + 1)
        if not clicked:
            log.info("  No control for page %d — reached the end of My Tenders", page_num + 1)
            break
        for _ in range(5):
            await asyncio.sleep(1)
            check = await extract_rows(page)
            if check and (not old_first_ref or check[0]["reference_number"] != old_first_ref):
                break
        page_num += 1
    else:
        if not found_any_open:
            log.warning("  Hit max_pages (%d) without finding any open tender in My Tenders — "
                        "check debug/tawreed_my_tenders*.png", max_pages)

    return list(by_ref.values())


async def diagnose(page: Page, name: str = "diagnose") -> None:
    """Dump everything needed to wire up the list scrape by hand."""
    await screenshot(page, name)
    info = await page.evaluate("""() => {
        const links = [...document.querySelectorAll('a')].slice(0, 120)
            .map(a => ({text: (a.innerText || '').trim().slice(0, 60),
                        href: (a.getAttribute('href') || '').slice(0, 120)}))
            .filter(l => l.text || l.href);
        const tables = [...document.querySelectorAll('table')].slice(0, 10)
            .map(t => ({rows: t.rows.length,
                        firstRow: (t.rows[0]?.innerText || '').slice(0, 200),
                        // A couple of real data rows, not just the header —
                        // needed to confirm status/date values, not just column names.
                        dataRows: [...t.rows].slice(1, 4).map(
                            tr => [...tr.cells].map(td => (td.innerText || '').trim()))}));
        const frames = [...document.querySelectorAll('iframe')]
            .map(f => f.src.slice(0, 120));
        // Any <select> on the page (e.g. page-size or a status/tender-type
        // filter) plus its options — settles whether a given control is a
        // native <select> select_page_size()/select_open_filter() can drive,
        // or a custom widget needing a click-based approach instead.
        const selects = [...document.querySelectorAll('select')].slice(0, 15)
            .map(s => ({id: s.id, name: s.name,
                        options: [...s.options].map(o => o.textContent.trim()).slice(0, 20)}));
        // Elements near a "Filter By" label — the custom-dropdown case if
        // `selects` above doesn't show a matching native control.
        const filterBy = [...document.querySelectorAll('*')]
            .filter(el => el.children.length === 0 &&
                          (el.innerText || '').trim().toLowerCase().includes('filter by'))
            .slice(0, 5)
            .map(el => ({tag: el.tagName, text: (el.innerText || '').trim().slice(0, 60),
                         parentOuter: (el.parentElement?.outerHTML || '').slice(0, 300)}));
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
        return {url: location.href, title: document.title, links, tables, frames, pagers,
                selects, filterBy};
    }""")
    out = DEBUG_DIR / f"tawreed_{name}.json"
    out.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Diagnose dump → %s (%d links, %d tables, %d iframes, %d pager candidates, "
             "%d selects, %d 'filter by' matches)",
             out.name, len(info["links"]), len(info["tables"]), len(info["frames"]),
             len(info.get("pagers", [])), len(info.get("selects", [])), len(info.get("filterBy", [])))


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
                    await diagnose(page, "public_list")
                    if diagnose_only:
                        return
                    # try to recover to the menu for the next link
                    await click_href(page, DASHBOARD_HREF, "Dashboard")
                    continue

                await screenshot(page, shot_name)
                has_table = await page.locator("table tr").count() > 2
                if diagnose_only:
                    await diagnose(page, "public_list")
                elif not has_table:
                    log.warning("No table on %r page — dumping diagnose info.", label)
                    await diagnose(page, "public_list")
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

            # "My Tenders" — filtered to currently-open items only (see
            # MY_TENDERS_LINK_TEXT comment and scrape_my_tenders docstring).
            # It's a tab that sits alongside "Tenders Open to All Suppliers"
            # on the SAME page — confirmed live 2026-09-16 that bouncing
            # through the dashboard first breaks this: the public-list
            # anchor isn't reachable from the dashboard page, only from
            # wherever the LIST_LINKS loop above already left off (or the
            # post-login landing page). So reach it from there directly,
            # only re-clicking the public-list anchor if we're somehow not
            # already on that page.
            on_tenders_page = "pubRfq/list" in page.url or MY_TENDERS_URL_MARKER in page.url
            if not on_tenders_page:
                on_tenders_page = await click_href(page, PUBLIC_TENDERS_HREF, PUBLIC_TENDERS_LABEL)
            clicked = on_tenders_page and await click_link_text(page, MY_TENDERS_LINK_TEXT, "My Tenders")
            if clicked:
                # click_link_text's own sleep isn't always enough for the SPA's
                # route change to land — confirmed live 2026-09-16: page.url was
                # still the old page immediately after the click, but was the
                # right one moments later. Poll instead of checking once.
                for _ in range(10):
                    if MY_TENDERS_URL_MARKER in page.url:
                        break
                    await asyncio.sleep(1)
                else:
                    log.warning("Clicked 'My Tenders' but never saw %r in the URL (stuck at %s) — "
                                "treating as failed", MY_TENDERS_URL_MARKER, page.url[:90])
                    clicked = False
            if not clicked:
                log.warning("Could not reach 'My Tenders' — dumping diagnose info.")
                await diagnose(page, "my_tenders")
            else:
                await screenshot(page, "05_my_tenders")
                has_table = await page.locator("table tr").count() > 2
                if diagnose_only:
                    await diagnose(page, "my_tenders")
                elif not has_table:
                    log.warning("No table on 'My Tenders' page — dumping diagnose info.")
                    await diagnose(page, "my_tenders")
                else:
                    rows = await scrape_my_tenders(page)
                    log.info("  'My Tenders' (open only) → %d row(s)", len(rows))
                    for t in rows:
                        ref = t["reference_number"]
                        if ref and ref not in by_ref:
                            by_ref[ref] = t

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
