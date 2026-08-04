"""
SAP SRM Portal – Tender Scraper (PDO / srm.pdo.co.om)
------------------------------------------------------
Reads credentials from password.env.txt, logs into the SAP NetWeaver Portal,
navigates to RFx and Tenders, paginates through all pages, and saves to tenders.json.

Setup (run once):
    pip install playwright python-dotenv
    playwright install chromium

Run:
    python tender_scraper.py
"""

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Frame, Page, TimeoutError as PWTimeout, async_playwright

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
ENV_FILE   = BASE_DIR / "password.env.txt"
OUT_FILE   = BASE_DIR / "tenders.json"
CHECKPOINT = BASE_DIR / "tenders_partial.json"
STATE_FILE = BASE_DIR / "session_state.json"   # saved cookies — lets runs skip login
DOCS_DIR   = BASE_DIR / "documents"

DEBUG_SCREENSHOTS = True   # saves PNGs to debug/ — set False for silent runs

load_dotenv(ENV_FILE)

PORTAL_URL = os.environ["SAP_PORTAL_URL"].rstrip("/")   # https://srm.pdo.co.om/irj/portal
USERNAME   = os.environ["SAP_USERNAME"]
PASSWORD   = os.environ["SAP_PASSWORD"]
TENDER_URL = os.getenv("SAP_TENDER_URL", "").strip()    # optional direct URL to tender list

# POWL query tab to select before scraping (e.g. "All", "Published").
# The portal remembers the last-used query per user, which is likely why a
# previous run saw only one row — a filtered "my responses" style view.
# "All" ballooned to 10,000+ rows system-wide (2026-07-27) and never finishes
# refreshing before our wait timeout, silently yielding 0 rows. "Published"
# loads near-instantly and is exactly the set of currently-open tenders we want.
POWL_QUERY = os.getenv("SAP_POWL_QUERY", "Published").strip()

# Documents are phase 2 — keep the listing scrape fast and decoupled by default.
SCRAPE_DOCUMENTS = os.getenv("SCRAPE_DOCUMENTS", "false").strip().lower() in ("1", "true", "yes")

# Logging off invalidates the saved session and forces a fresh login next run,
# so default to keeping the session alive for reuse.
LOGOUT_ON_EXIT = os.getenv("SAP_LOGOUT_ON_EXIT", "false").strip().lower() in ("1", "true", "yes")

SEL_USER   = os.getenv("SAP_SEL_USER",   '[name="j_username"], #logonuid, #username')
SEL_PASS   = os.getenv("SAP_SEL_PASS",   '[name="j_password"], #logonPassword, #password')
SEL_SUBMIT = os.getenv("SAP_SEL_SUBMIT", '[type="submit"], #logonButton, .urBtnStd')
SEL_NEXT   = os.getenv("SAP_SEL_NEXT",   ".urPghFwdLnk, [title='Next Page'], [aria-label='Next']")

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Utilities ─────────────────────────────────────────────────────────────────

async def _wait_after_nav(page: Page, screenshot_name: str, delay: float = 4.0) -> None:
    """
    SAP portals have continuous AJAX keepalives so 'networkidle' never fires.
    Wait for 'load' then sleep briefly to let iframes and JS initialise.
    """
    try:
        await page.wait_for_load_state("load", timeout=20_000)
    except PWTimeout:
        pass
    await asyncio.sleep(delay)
    await screenshot(page, screenshot_name)


async def screenshot(page: Page, name: str) -> None:
    if not DEBUG_SCREENSHOTS:
        return
    path = BASE_DIR / "debug" / f"{name}.png"
    path.parent.mkdir(exist_ok=True)
    await page.screenshot(path=str(path), full_page=True)
    log.info("  screenshot → %s", path.name)


def _get(lst: list, idx: int, default: str = "") -> str:
    try:
        v = lst[idx]
        return v if v is not None else default
    except IndexError:
        return default


# ── Frame helpers ─────────────────────────────────────────────────────────────

async def content_frame(page: Page) -> Frame:
    """
    SAP NetWeaver Portal renders content in nested iframes.
    Prefer a frame that actually has 7-digit RFX numbers in its cells.
    Fall back to the frame with the most <td> elements, skipping PDF frames.
    """
    # Priority: a frame with RFX data
    for frame in page.frames:
        try:
            has_rfx = await frame.evaluate("""() => {
                for (const td of document.querySelectorAll('td'))
                    if (/^\\d{7,}$/.test((td.innerText || '').trim())) return true;
                return false;
            }""")
            if has_rfx:
                log.info("  Content frame (RFx data): %s", frame.url[:90])
                return frame
        except Exception:
            continue

    # Fallback: most cells, excluding PDF/document frames
    best, best_count = page.main_frame, 0
    for frame in page.frames:
        url = frame.url.lower()
        if any(url.endswith(ext) for ext in (".pdf", ".doc", ".xls")):
            continue
        try:
            count = await frame.evaluate("() => document.querySelectorAll('td').length")
            if count > best_count:
                best_count, best = count, frame
        except Exception:
            continue
    log.info("  Content frame (fallback, %d cells): %s", best_count, best.url[:90])
    return best


async def frame_locator(page: Page, selector: str, timeout: int = 4_000):
    """Find the first visible element matching selector across all frames."""
    for frame in page.frames:
        try:
            loc = frame.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout)
            return loc, frame
        except PWTimeout:
            continue
    return None, None


# ── Login ──────────────────────────────────────────────────────────────────────

async def _find_login_frame(page: Page) -> Frame | None:
    """Return the frame containing the login form, or None when already authenticated."""
    for f in page.frames:
        try:
            if await f.query_selector('[name="j_username"], #logonuid'):
                return f
        except Exception:
            continue
    return None


async def ensure_logged_in(page: Page, ctx) -> None:
    """
    Navigate to the portal. If the saved session (session_state.json) is still
    valid no login form appears and we skip authentication entirely — the
    strongest lockout protection. Otherwise log in exactly once, no retries,
    and persist the cookies for the next run.
    """
    log.info("Navigating → %s", PORTAL_URL)
    await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(2)
    await screenshot(page, "01_login_page")

    login_frame = await _find_login_frame(page)
    if login_frame is None:
        log.info("Saved session still valid — skipping login.")
        return

    log.info("  Login form frame: %s", login_frame.url[:80])

    async def fill(selectors: str, value: str, label: str) -> None:
        for sel in [s.strip() for s in selectors.split(",")]:
            try:
                el = login_frame.locator(sel).first
                await el.wait_for(state="visible", timeout=5_000)
                await el.fill(value)
                log.info("  Filled %s via %r", label, sel)
                return
            except PWTimeout:
                continue
        raise RuntimeError(f"Could not locate {label} field. Tried: {selectors!r}")

    await fill(SEL_USER, USERNAME, "username")
    await fill(SEL_PASS, PASSWORD, "password")
    await screenshot(page, "02_filled")

    submitted = False
    for sel in [s.strip() for s in SEL_SUBMIT.split(",")]:
        try:
            btn = login_frame.locator(sel).first
            await btn.wait_for(state="visible", timeout=4_000)
            await btn.click()
            submitted = True
            log.info("  Submitted via %r", sel)
            break
        except PWTimeout:
            continue

    if not submitted:
        await login_frame.locator(SEL_PASS.split(",")[0].strip()).press("Enter")
        log.info("  Submitted via Enter key")

    # SAP portals have persistent AJAX keepalives — wait for "load" not "networkidle"
    try:
        await page.wait_for_load_state("load", timeout=30_000)
    except PWTimeout:
        pass   # proceed anyway; content may still be loading in iframes
    await asyncio.sleep(3)
    await screenshot(page, "03_post_login")

    # SAP stays on /irj/portal even after failed login — check page content too
    if re.search(r"logon|login|j_security_check", page.url, re.I):
        raise RuntimeError("Login failed — redirected to login URL.")

    try:
        fail_text = await page.evaluate("""() => {
            const body = document.body ? document.body.innerText : '';
            if (/authentication failed|invalid.*credentials|login.*error|account.*locked/i.test(body))
                return body.slice(0, 200);
            return '';
        }""")
        if fail_text:
            raise RuntimeError(f"Login rejected by portal. Message: {fail_text.strip()[:120]}")
    except RuntimeError:
        raise
    except Exception:
        pass

    log.info("Authenticated. URL: %s", page.url)

    # Persist cookies so subsequent runs skip the login form entirely
    try:
        await ctx.storage_state(path=str(STATE_FILE))
        log.info("Session state saved → %s", STATE_FILE.name)
    except Exception as exc:
        log.warning("Could not save session state: %s", exc)


# ── Navigate to tender listings ────────────────────────────────────────────────

# Exact tab labels in the PDO SRM portal top navigation
_TAB_LABELS = ["RFx and Tenders", "Upcoming Tenders", "Bid Invitations", "Bidding"]


async def _click_tender_tab(page: Page) -> bool:
    """Click the RFx/Tender tab; return True if a tab was found and clicked."""
    for label in _TAB_LABELS:
        # Try by role first (most reliable), then CSS fallback
        for sel in [
            f"role=link[name='{label}']",
            f"a:has-text('{label}')",
            f"[title='{label}']",
        ]:
            try:
                loc = page.locator(sel).first
                await loc.wait_for(state="visible", timeout=3_000)
                log.info("  Clicking tab '%s' via %r", label, sel)
                await loc.click()
                return True
            except PWTimeout:
                continue
    return False


async def _wait_for_rfx_table(page: Page, max_wait: int = 50) -> bool:
    """
    Poll up to max_wait seconds for an RFX number (7+ digit) to appear in any frame.
    SAP SRM typically takes 30-40 s to render the table after tab click.
    """
    for elapsed in range(0, max_wait, 3):
        await asyncio.sleep(3)
        for frame in page.frames:
            try:
                found = await frame.evaluate("""() => {
                    for (const td of document.querySelectorAll('td')) {
                        if (/^\\d{7,}$/.test((td.innerText || '').trim())) return true;
                    }
                    return false;
                }""")
                if found:
                    log.info("  RFx table appeared after ~%ds", elapsed + 3)
                    return True
            except Exception:
                continue
    return False


async def _has_session_conflict(page: Page) -> bool:
    """Return True if SAP is showing a 'already open in another session' warning."""
    for f in page.frames:
        try:
            found = await f.evaluate(
                "() => (document.body.innerText || '').includes('already open in another session')"
            )
            if found:
                return True
        except Exception:
            continue
    return False


async def release_app_session(page: Page) -> None:
    """
    Navigate away from the POWL app before closing the browser so SAP's
    distributed session manager closes the app session. Killing the browser
    outright leaves the query 'open in another session', which renders the
    table read-only (grey) for every run until the server times it out.
    """
    try:
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)   # let the unload/DSM beacon fire
        log.info("Released app session (navigated away from POWL).")
    except Exception as exc:
        log.warning("Could not release app session: %s", exc)


async def goto_tenders(page: Page) -> None:
    """Navigate to the RFx / tender listings."""
    if TENDER_URL:
        log.info("Going directly to tender URL: %s", TENDER_URL)
        await page.goto(TENDER_URL, wait_until="load", timeout=60_000)
        await asyncio.sleep(5)
        await screenshot(page, "04_tender_direct")
        return

    clicked = await _click_tender_tab(page)
    if not clicked:
        log.info("Tab not found on current page — reloading portal root")
        await page.goto(PORTAL_URL, wait_until="load", timeout=60_000)
        await asyncio.sleep(4)
        clicked = await _click_tender_tab(page)

    if not clicked:
        log.warning("Could not find RFx tab — proceeding from current page state.")

    log.info("Waiting for SAP to render the RFx table (up to 50 s) …")
    found = await _wait_for_rfx_table(page, max_wait=50)
    if not found:
        log.warning("RFx table did not appear within timeout — will attempt extraction anyway.")
    await screenshot(page, "04_tender_list")

    # Land on the right query BEFORE the conflict check — selecting a query
    # that a lingering session holds open is what locks the view (grey,
    # read-only table)
    if POWL_QUERY:
        if await select_powl_query(page, POWL_QUERY):
            await _wait_for_rfx_table(page, max_wait=30)
        else:
            log.warning("POWL query %r not found — using the current view as-is.", POWL_QUERY)

    # If SAP warns about a duplicate session, navigate away and back to clear
    # it, then re-select the query
    if await _has_session_conflict(page):
        log.info("Session conflict detected — re-navigating to clear it")
        await page.goto(PORTAL_URL, wait_until="load", timeout=30_000)
        await asyncio.sleep(5)
        await _click_tender_tab(page)
        await _wait_for_rfx_table(page, max_wait=50)
        if POWL_QUERY and await select_powl_query(page, POWL_QUERY):
            await _wait_for_rfx_table(page, max_wait=30)
        await screenshot(page, "04_tender_list_refreshed")
        if await _has_session_conflict(page):
            log.warning("Session conflict persists — row clicks may be locked this run.")


# ── Extract tenders from the current page (frame-aware) ───────────────────────

# Column layout observed in PDO SRM portal (page_001.png):
#   0 = checkbox/icon  1 = RFX Number  2 = RFX Description  3 = RFX Type
#   4 = RFX Status     5 = Start Date  6 = End Date          7 = Response Number
#   8 = Response Status  9 = RFX Version  10 = Response Version  11 = Q&A Sender

async def extract_page(page: Page) -> list[dict]:
    frame = await content_frame(page)

    # Use JS inside the frame so we cross any inner iframes too
    rows_raw: list[dict] = await frame.evaluate("""() => {
        const results = [];
        document.querySelectorAll('tr').forEach(tr => {
            const tds = Array.from(tr.querySelectorAll('td'));
            if (tds.length < 6) return;
            const texts = tds.map(td => (td.innerText || '').trim());
            // RFX numbers are 10-digit SAP doc numbers
            if (!/^\\d{7,}$/.test(texts[1] || '')) return;
            const anchor = tr.querySelector('a[href]');
            results.push({ cells: texts, href: anchor ? anchor.href : '' });
        });
        return results;
    }""")

    if not rows_raw:
        log.warning("  No data rows found in content frame.")

    tenders = []
    for row in rows_raw:
        t = row["cells"]
        tender = {
            "reference_number": _get(t, 1),
            "title":            _get(t, 2),
            "rfx_type":         _get(t, 3),
            "status":           _get(t, 4),
            "start_date":       _get(t, 5),
            "closing_date":     _get(t, 6),   # "End Date" column
            "response_status":  _get(t, 8),
            "description":      "",
            "estimated_value":  "",
            "link":             row["href"],
            "documents":        [],
        }
        tenders.append(tender)
        log.info("  ✓ %s — %s  (closes %s)", tender["reference_number"],
                 tender["title"][:55], tender["closing_date"])

    return tenders


# ── Optionally fetch description + value from each detail page ────────────────

async def enrich_from_detail(page: Page, tender: dict) -> None:
    """
    Open the RFX detail page and pull Description and Estimated Value.
    Skips gracefully if the page structure doesn't match.
    """
    if not tender.get("link"):
        return
    try:
        await page.goto(tender["link"], wait_until="networkidle", timeout=30_000)
        frame = await content_frame(page)
        data = await frame.evaluate("""() => {
            const text = label => {
                const els = Array.from(document.querySelectorAll('td, span, div'));
                const lbl = els.find(e => e.innerText && e.innerText.trim() === label);
                if (!lbl) return '';
                // value is typically the next sibling element
                const next = lbl.nextElementSibling || lbl.parentElement?.nextElementSibling;
                return next ? next.innerText.trim() : '';
            };
            return {
                description:     text('Description') || text('RFx Description') || text('Notes'),
                estimated_value: text('Estimated Value') || text('Budget') || text('Target Value'),
            };
        }""")
        tender["description"]    = data.get("description", "")
        tender["estimated_value"] = data.get("estimated_value", "")
    except Exception as exc:
        log.warning("  Detail page fetch failed for %s: %s", tender["reference_number"], exc)


# ── Clear list filters ────────────────────────────────────────────────────────

async def clear_list_filters(page: Page) -> None:
    """
    The portal sometimes lands with a previous search (e.g. a single RFX number)
    still active. Clear all quick-criteria fields and re-apply to get the full list.
    """
    # The "Current RFx" saved quick-criteria preset pins an RFX Number
    # select-option range that isn't a plain text input (SAP renders it as a
    # matchcode/range widget), so the manual per-field clear below can't see
    # it. The toolbar's own "Clear" button resets the whole preset including
    # that range — try it first.
    clear_loc, _ = await frame_locator(page, "input[value='Clear']", timeout=3_000)
    if clear_loc is None:
        clear_loc, _ = await frame_locator(page, "button:has-text('Clear')", timeout=3_000)
    if clear_loc:
        await clear_loc.click()
        await asyncio.sleep(3)
        await _wait_for_rfx_table(page, max_wait=30)
        await screenshot(page, "05a_clear_clicked")

    # SAP UR inputs ignore direct .value writes and render readonly="" until
    # focused, which also breaks fill(). Go through real keyboard events —
    # click, select-all, delete, Tab — which SAP's own handlers process.
    frame = await content_frame(page)
    inputs = frame.locator("input[type='text'], input[type='search']")
    cleared = False
    for i in range(await inputs.count()):
        inp = inputs.nth(i)
        try:
            if not await inp.is_visible():
                continue
            if (await inp.get_attribute("ct") or "I") != "I":
                continue   # skip SAP comboboxes like "[Standard View]"
            val = await inp.input_value()
            if not (val and val.strip()):
                continue
            await inp.click()
            await inp.press("Control+a")
            await inp.press("Delete")
            await inp.press("Tab")
            after = await inp.input_value()
            if after.strip():
                # keyboard path blocked — strip readonly and clear via UR events
                await inp.evaluate("""el => {
                    el.readOnly = false;
                    el.value = '';
                    el.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, key: 'Delete'}));
                    el.dispatchEvent(new Event('input',  {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.blur();
                }""")
                after = await inp.input_value()
            cleared = cleared or not after.strip()
            log.info("  Filter field %r: %r → %r", await inp.get_attribute("title"), val, after)
        except Exception as exc:
            log.warning("  Filter field %d clear failed: %s", i, exc)

    if cleared:
        log.info("  Re-applying with filters cleared…")
        for sel in ["input[value='Apply']", "button:has-text('Apply')", "[title='Apply']"]:
            loc, _ = await frame_locator(page, sel, timeout=3_000)
            if loc:
                await loc.click()
                break
        await asyncio.sleep(5)
        await _wait_for_rfx_table(page, max_wait=40)
        await screenshot(page, "05_filters_cleared")


# ── Download documents for one RFx ───────────────────────────────────────────

async def fetch_rfx_documents(page: Page, ref: str) -> list[str] | None:
    """
    Click the RFX number hyperlink → wait for popup → click Documents tab → download.
    SAP SRM opens the detail in a new popup window when the row link is clicked.
    Returns list of paths relative to BASE_DIR, or None if the detail screen
    could not be opened / did not belong to `ref` (caller must NOT mark done).
    """
    from urllib.parse import urlparse, unquote as url_unquote

    tender_dir = DOCS_DIR / ref
    tender_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []

    # ── 1. Select the row, then open it via the "Display RFX" toolbar button ──
    # This POWL renders the in-cell RFX link permanently disabled; the real UI
    # flow is: click the row (lead selection, needs a trusted mouse event —
    # SAP UR ignores synthetic el.click()), then the toolbar's "Display RFX".
    frame = await content_frame(page)
    try:
        # SAP UR nests layout tables, so tr:has(td:has-text(...)) matches the
        # OUTERMOST layout row first — clicking it lands outside the data row
        # and lead selection silently stays on the first row (wrong RFx opens).
        # td:has-text(...) in document order puts the innermost cell last.
        cell = frame.locator(f"td:has-text('{ref}')").last
        row = cell.locator("xpath=ancestor-or-self::tr[1]")
        try:
            # description cell (plain text, no disabled link to swallow the click)
            await row.locator("td").nth(2).click(timeout=8_000)
        except Exception:
            await cell.click(timeout=8_000)
        log.info("  Selected row for %s", ref)
    except Exception as exc:
        log.warning("  Could not select row for %s: %s", ref, exc)
        return None
    await asyncio.sleep(4)   # lead-selection round trip enables the toolbar
    await screenshot(page, f"row_selected_{ref}")

    opened = False
    for btn_sel in ["[title='Display RFX']:not(.lsButton--disabled)",
                    "text='Display RFX'", "[title='Display RFX']"]:
        btn, _ = await frame_locator(page, btn_sel, timeout=4_000)
        if btn:
            try:
                await btn.click(timeout=10_000)
                opened = True
                log.info("  Clicked 'Display RFX' for %s via %r", ref, btn_sel)
                break
            except Exception as exc:
                log.warning("  'Display RFX' click failed (%s) — likely still disabled", exc)
    if not opened:
        log.warning("  Could not open %s — Display RFX unavailable (locked view?)", ref)
        return None

    detail_page: Page = page
    is_popup = False

    log.info("  Waiting 20 s for popup/AJAX for %s", ref)

    # Wait, then check if SAP opened a new page in any way
    await asyncio.sleep(20)

    all_pages = page.context.pages
    log.info("  Context has %d page(s) after click", len(all_pages))
    for i, p in enumerate(all_pages):
        log.info("    Page %d: %s", i, p.url[:80])

    # ── 2. Identify the detail page ───────────────────────────────────────────
    if len(all_pages) > 1:
        # SAP opened a popup window
        detail_page = next((p for p in all_pages if p is not page), page)
        is_popup = True
        log.info("  Using popup page for %s", ref)
        try:
            await detail_page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except PWTimeout:
            pass
        await asyncio.sleep(15)   # SAP WebDynpro needs extra time to render iframes
    else:
        # Single page — check if AJAX updated the content
        loaded = False
        for f in detail_page.frames:
            try:
                ok = await f.evaluate("""() => {
                    const t = document.body ? document.body.innerText : '';
                    return (
                        t.includes('Bid Deadline') || t.includes('Header Data') ||
                        t.includes('Questionnaire') ||
                        (t.includes('Documents') && !t.includes('Display RFX'))
                    );
                }""")
                if ok:
                    loaded = True; break
            except Exception:
                continue
        if loaded:
            log.info("  Same-page AJAX detail loaded for %s", ref)
        else:
            log.warning("  Detail may not have loaded for %s (trying anyway)", ref)

    await screenshot(detail_page, f"detail_{ref}")

    # ── 2b. Verify the detail screen belongs to THIS RFx ─────────────────────
    # If lead selection didn't take, Display RFX opens whichever row was
    # selected by default — silently attributing another tender's data to ref.
    verified = False
    for f in detail_page.frames:
        try:
            if await f.evaluate(
                "(ref) => !!document.body && document.body.innerText.includes(ref)", ref
            ):
                verified = True
                break
        except Exception:
            continue
    if not verified:
        log.error("  Detail screen does NOT show RFx %s — wrong row opened; skipping.", ref)
        if is_popup:
            try:
                await detail_page.close()
            except Exception:
                pass
        return None

    # ── 3. Click the "Documents" tab ─────────────────────────────────────────
    docs_tab_clicked = False
    for tab_sel in [
        # PDO supplier view names the tab "Notes and Attachments" — try it first
        # (each miss costs ~4 s per frame scanned).
        "text='Notes and Attachments'",
        "text='Documents'", "td:has-text('Documents')",
        "a:has-text('Documents')", "span:has-text('Documents')",
        "[title='Documents']", "text='Attachments'", "[title='Attachments']",
    ]:
        tab, _ = await frame_locator(detail_page, tab_sel, timeout=4_000)
        if tab:
            await tab.click()
            docs_tab_clicked = True
            log.info("  Clicked Documents tab for %s", ref)
            await asyncio.sleep(8)
            await screenshot(detail_page, f"detail_{ref}_docs")
            break

    if not docs_tab_clicked:
        log.info("  No Documents tab for %s — scanning current view", ref)

    # ── 4. Find download links across all frames ──────────────────────────────
    all_links: list[dict] = []
    for f in detail_page.frames:
        try:
            links = await f.evaluate("""() => {
                const seen = new Set();
                const out = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href || '';
                    const text = (a.innerText || a.title || '').trim().slice(0, 100);
                    if (!href || seen.has(href)) return;
                    if (
                        /\\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z)/i.test(href) ||
                        href.includes('attachment') || href.includes('/bds/') ||
                        href.includes('download')   || href.includes('MIMEType') ||
                        href.toLowerCase().includes('document')
                    ) { seen.add(href); out.push({href, text}); }
                });
                return out;
            }""")
            all_links.extend(links)
        except Exception:
            continue

    log.info("  Found %d document link(s) for %s", len(all_links), ref)

    # ── 4b. Capture the Notes section ─────────────────────────────────────────
    # PDO material RFxs usually have an EMPTY Attachments table; the scope of
    # work lives in the Notes rows (Clauses / PR Material Text / PR Item Text)
    # and in the header fields. Save the rendered text so extract_sow.py
    # (which already reads .txt) can work offline.
    notes_chunks: list[str] = []
    for f in detail_page.frames:
        try:
            txt = await f.evaluate("""() => {
                const t = document.body ? document.body.innerText : '';
                return t.includes('Notes') || t.includes('RFx Number') ? t : '';
            }""")
            if txt and txt not in notes_chunks:
                notes_chunks.append(txt)
        except Exception:
            continue
    if notes_chunks:
        notes_path = tender_dir / "notes.txt"
        notes_path.write_text("\n\n".join(notes_chunks), encoding="utf-8")
        saved.append(f"documents/{ref}/notes.txt")
        log.info("  Saved notes text for %s (%d chars)", ref,
                 sum(len(c) for c in notes_chunks))

    # ── 5. Download each file ─────────────────────────────────────────────────
    for item in all_links:
        href = item["href"]
        label = item["text"] or "document"
        path_part = url_unquote(urlparse(href).path.split("/")[-1])
        fname = path_part if (path_part and "." in path_part) else f"{label[:40]}.pdf"
        fname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", fname)[:120]

        out_path = tender_dir / fname
        if out_path.exists():
            log.info("  Already have: %s", fname)
            saved.append(f"documents/{ref}/{fname}")
            continue

        try:
            async with detail_page.expect_download(timeout=25_000) as dl_ctx:
                await detail_page.evaluate(
                    "(href) => { const a=document.createElement('a'); a.href=href; "
                    "a.download=''; document.body.appendChild(a); a.click(); a.remove(); }",
                    href,
                )
            dl = await dl_ctx.value
            await dl.save_as(str(out_path))
            log.info("  Downloaded: %s", fname)
            saved.append(f"documents/{ref}/{fname}")
        except Exception as exc:
            log.warning("  Download failed %s: %s", fname, exc)

    # ── 6. Close popup or navigate back ──────────────────────────────────────
    if is_popup:
        await detail_page.close()
    else:
        back_clicked = False
        for back_sel in ["a:has-text('Back')", "[title='Back']", "text='Back'"]:
            back, _ = await frame_locator(page, back_sel, timeout=3_000)
            if back:
                await back.click()
                await asyncio.sleep(4)
                back_clicked = True
                break
        if not back_clicked:
            try:
                await page.go_back(wait_until="load", timeout=20_000)
                await asyncio.sleep(3)
            except Exception:
                pass

    log.info("  Documents for %s: %d saved", ref, len(saved))
    return saved


# ── POWL query tabs ───────────────────────────────────────────────────────────
# SAP POWL lists show query tabs like "All (37)" / "Published (12)" above the
# table, and the portal remembers the user's last-selected query. A previous
# run scraped a single row — almost certainly a leftover filtered query.

async def list_powl_queries(page: Page) -> list[str]:
    """Collect POWL query-tab labels (text like 'All (37)') across all frames."""
    labels: list[str] = []
    for f in page.frames:
        try:
            found = await f.evaluate("""() => {
                const out = [];
                for (const el of document.querySelectorAll('a, span')) {
                    const t = (el.innerText || '').trim();
                    if (t.length <= 50 && /^[A-Za-z][^()\\n]*\\(\\d+\\)$/.test(t)) out.push(t);
                }
                return out;
            }""")
            labels.extend(found)
        except Exception:
            continue
    seen: set[str] = set()
    return [x for x in labels if not (x in seen or seen.add(x))]


async def select_powl_query(page: Page, name: str) -> bool:
    """Click the query tab whose label starts with `name`, e.g. 'All (37)'."""
    for f in page.frames:
        try:
            clicked = await f.evaluate("""(name) => {
                const want = name.toLowerCase();
                for (const tag of ['a', 'span']) {
                    for (const el of document.querySelectorAll(tag)) {
                        const t = (el.innerText || '').trim();
                        if (t.toLowerCase().startsWith(want) && /\\(\\d+\\)$/.test(t)) {
                            el.click();
                            return t;
                        }
                    }
                }
                return null;
            }""", name)
            if clicked:
                log.info("  Selected POWL query: %s", clicked)
                await asyncio.sleep(5)
                return True
        except Exception:
            continue
    return False


# ── Diagnostic mode ───────────────────────────────────────────────────────────

async def diagnose(page: Page) -> None:
    """
    Read-only reconnaissance (no clicks into tenders, no downloads): report
    frames, POWL query tabs, and row counts, and save the content frame's HTML
    so selector fixes can be made offline without another portal session.
    """
    log.info("── DIAGNOSTIC MODE — read-only ──")
    for i, f in enumerate(page.frames):
        log.info("  frame[%d] %s", i, f.url[:100])

    queries = await list_powl_queries(page)
    if queries:
        log.info("POWL query tabs visible:")
        for q in queries:
            log.info("  • %s", q)
    else:
        log.warning("No POWL query tabs detected — check debug/diag_content_frame.html")

    async def count_rows() -> int:
        frame = await content_frame(page)
        return await frame.evaluate("""() => {
            let n = 0;
            for (const tr of document.querySelectorAll('tr')) {
                const tds = tr.querySelectorAll('td');
                if (tds.length >= 6 && /^\\d{7,}$/.test((tds[1].innerText || '').trim())) n++;
            }
            return n;
        }""")

    log.info("Data rows in current view: %d", await count_rows())
    await screenshot(page, "diag_current_view")

    frame = await content_frame(page)
    html = await frame.evaluate("() => document.documentElement.outerHTML")
    dbg = BASE_DIR / "debug"
    dbg.mkdir(exist_ok=True)
    (dbg / "diag_content_frame.html").write_text(html, encoding="utf-8")
    log.info("Content frame HTML saved → debug/diag_content_frame.html")

    # Try the broadest-looking query tab and recount
    for name in ("All", "Published"):
        if any(q.lower().startswith(name.lower()) for q in queries):
            if await select_powl_query(page, name):
                await _wait_for_rfx_table(page, max_wait=30)
                log.info("Data rows after selecting '%s': %d", name, await count_rows())
                await screenshot(page, f"diag_query_{name}")
                break

    # Save the POPULATED table HTML + one sample row, for fixing the
    # detail-page/document click selectors offline
    frame = await content_frame(page)
    html = await frame.evaluate("() => document.documentElement.outerHTML")
    (dbg / "diag_content_frame_rows.html").write_text(html, encoding="utf-8")
    row_html = await frame.evaluate("""() => {
        for (const tr of document.querySelectorAll('tr')) {
            const tds = tr.querySelectorAll('td');
            if (tds.length >= 6 && /^\\d{7,}$/.test((tds[1].innerText || '').trim()))
                return tr.outerHTML;
        }
        return '';
    }""")
    (dbg / "diag_sample_row.html").write_text(row_html, encoding="utf-8")
    log.info("Populated table HTML + sample row saved to debug/")


# ── Pagination ────────────────────────────────────────────────────────────────

async def _scroll_window(page: Page) -> bool | None:
    """
    POWL tables show a fixed window (e.g. 10 of 19 rows) and scroll row-wise —
    there is no Next-page link. The scrollbar's buttons render with disabled
    classes even when more rows exist, so click them only when enabled and
    otherwise send mouse-wheel gestures over the table body, which SAP handles
    server-side. Returns True when a scroll was attempted, None when no row
    scrollbar exists (caller falls back to a classic pager). The caller must
    detect the end of the table by seeing no new rows.
    """
    for f in page.frames:
        try:
            if not await f.query_selector("[id$='-scrollV-Nxt']"):
                continue
            clicked = await f.evaluate("""() => {
                const btn = document.querySelector("[id$='-scrollV-Nxt']");
                if (!btn || btn.className.includes('Dsbl') || btn.className.includes('--disabled'))
                    return 0;
                let n = 0;
                for (; n < 10; n++) btn.click();
                return n;
            }""")
            if clicked:
                return True

            # Drag the scrollbar handle down by one handle-height — one
            # handle-height ≈ one window of rows, and the drag is clamped at
            # the track's end so overshooting is harmless
            hdl = f.locator("[id$='-scrollV-hdl']").first
            box = await hdl.bounding_box()
            if box and box["height"] > 0:
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                await page.mouse.move(x, y)
                await page.mouse.down()
                await page.mouse.move(x, y + box["height"], steps=8)
                await page.mouse.up()
                return True

            # Last resort: wheel gesture over the table body
            body = f.locator("[id$='-contentTBody']").first
            await body.hover()
            for _ in range(3):
                await page.mouse.wheel(0, 300)
                await asyncio.sleep(0.4)
            return True
        except Exception:
            continue
    return None


SOURCE_PDO = "PDO SRM Portal"

# Fields written by later pipeline phases (fetch_documents.py, extract_sow.py)
# that a fresh listing scrape must not wipe.
ENRICH_FIELDS = ("documents", "doc_fetch_done", "doc_fetch_note",
                 "scope_of_work", "sow_source", "sow_extraction", "first_seen",
                 "purchase_required")


def merge_tenders(scraped: list[dict], source: str) -> list[dict]:
    """
    Merge a fresh listing scrape from ONE source into tenders.json:
    - tags each scraped record with `source`
    - carries over enrichment fields from the existing record with the same
      reference number (so re-scrapes don't wipe fetched docs / SOW)
    - stamps NEW tenders (no prior record) with first_seen = today, as a proxy
      for "date floated" — neither PDO nor OQ Tawreed exposes a real
      creation/publish date anywhere in the supplier-facing UI (confirmed by
      hand 2026-07-22), so "when we first noticed it" is the best available
      signal.
    - keeps other sources' tenders untouched
    - NEVER deletes a tender that drops off the live portal listing — it's
      kept permanently with `active` flipped to False (and `last_seen`
      frozen at its last confirmed date), so Market Intelligence trends
      accumulate real history instead of shrinking as tenders close. Only
      tenders still present in `scraped` are `active: True`, with
      `last_seen` bumped to today.
    Within the source, empty `scraped` while the source previously had
    entries is treated as a failed scrape (not a real "zero tenders" state)
    and the old entries are returned untouched — the SAP POWL UI has
    repeatedly returned 0 rows on a transiently-slow refresh or a session
    lock (see 2026-07-16/07-27 incidents).
    """
    from datetime import date

    existing: list[dict] = []
    if OUT_FILE.exists():
        try:
            existing = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not read existing %s (%s) — starting fresh", OUT_FILE.name, exc)

    old_by_ref = {t.get("reference_number"): t for t in existing
                  if t.get("source", SOURCE_PDO) == source}

    if not scraped and old_by_ref:
        log.warning(
            "Scrape for %s returned 0 tenders but %d existed — treating as a "
            "failed scrape and keeping the existing entries untouched.",
            source, len(old_by_ref),
        )
        return existing

    today = date.today().isoformat()
    scraped_refs = {t.get("reference_number") for t in scraped}

    # Tenders from other sources: untouched.
    merged = [t for t in existing if t.get("source", SOURCE_PDO) != source]

    # This source's tenders that fell off the live listing: keep permanently,
    # just mark inactive. This is the whole point of the change — Market
    # Intelligence needs the full history, not just today's snapshot.
    for t in existing:
        if t.get("source", SOURCE_PDO) == source and t.get("reference_number") not in scraped_refs:
            t["active"] = False
            merged.append(t)

    for t in scraped:
        t["source"] = source
        t["active"] = True
        old = old_by_ref.get(t.get("reference_number"))
        if old:
            for f in ENRICH_FIELDS:
                if f in old and not t.get(f):
                    t[f] = old[f]
        if not t.get("first_seen"):
            t["first_seen"] = today
        t["last_seen"] = today
        merged.append(t)
    return merged


def _save_checkpoint(tenders: list[dict]) -> None:
    CHECKPOINT.write_text(
        json.dumps(tenders, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


async def scrape_all(page: Page) -> list[dict]:
    await clear_list_filters(page)   # goto_tenders() already selected the query

    if not SCRAPE_DOCUMENTS:
        log.info("Document scraping disabled (set SCRAPE_DOCUMENTS=true to enable) — listings only.")

    all_tenders: list[dict] = []
    seen: set[str] = set()
    window = 1

    while window <= 100:   # hard stop — a stuck scrollbar can't loop forever
        log.info("── Window %d ─────────────────────────────────────", window)
        batch = [t for t in await extract_page(page)
                 if t["reference_number"] not in seen]
        seen.update(t["reference_number"] for t in batch)
        await screenshot(page, f"page_{window:03d}")

        if SCRAPE_DOCUMENTS:
            for tender in batch:
                ref = tender.get("reference_number", "")
                if ref:
                    tender["documents"] = await fetch_rfx_documents(page, ref)

        all_tenders.extend(batch)
        _save_checkpoint(all_tenders)   # crash mid-run keeps everything so far

        # A scroll that surfaces no new rows means we've reached the end
        if not batch and window > 1:
            log.info("No new rows after scrolling — %d unique tenders.", len(seen))
            break

        if await _scroll_window(page):
            await asyncio.sleep(2.5)   # let the scroll round-trip render
            window += 1
            continue

        # No row scrollbar — fall back to a classic Next-page link
        next_loc, _ = await frame_locator(page, SEL_NEXT, timeout=3_000)
        if next_loc is None:
            log.info("No scrollbar or 'Next' button — pagination complete.")
            break
        disabled = (
            await next_loc.get_attribute("disabled")
            or await next_loc.get_attribute("aria-disabled")
            or await next_loc.evaluate(
                "el => el.classList.contains('urPghFwdLnkDsbl') || "
                "      el.classList.contains('sapUiPagingNextDsbl')"
            )
        )
        if disabled:
            log.info("'Next' button disabled — reached last page.")
            break
        log.info("Clicking Next …")
        await next_loc.click()
        await _wait_after_nav(page, f"page_{window + 1:03d}_loading")
        window += 1

    return all_tenders


# ── Main ───────────────────────────────────────────────────────────────────────

async def launch_context(pw):
    """Launch the browser + context with the saved session loaded (shared with fetch_documents.py)."""
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        ignore_https_errors=True,
        accept_downloads=True,
        http_credentials={"username": USERNAME, "password": PASSWORD},
        storage_state=str(STATE_FILE) if STATE_FILE.exists() else None,
    )
    return browser, ctx


async def main(diagnose_only: bool = False) -> None:
    tenders: list[dict] | None = None

    async with async_playwright() as pw:
        browser, ctx = await launch_context(pw)
        page = await ctx.new_page()

        try:
            await ensure_logged_in(page, ctx)
            await goto_tenders(page)
            if diagnose_only:
                await diagnose(page)
            else:
                tenders = await scrape_all(page)
        finally:
            await release_app_session(page)
            if LOGOUT_ON_EXIT:
                # Invalidates the saved session — next run will log in again
                try:
                    logoff, _ = await frame_locator(page, "a:has-text('Log off'), [title='Log off']", timeout=5_000)
                    if logoff:
                        await logoff.click()
                        log.info("Logged out of SAP portal")
                except Exception:
                    pass
            await browser.close()

    if tenders is not None:
        log.info("Total tenders scraped: %d", len(tenders))
        merged = merge_tenders(tenders, SOURCE_PDO)
        OUT_FILE.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        CHECKPOINT.unlink(missing_ok=True)
        log.info("Saved → %s (%d total across sources)", OUT_FILE, len(merged))


if __name__ == "__main__":
    asyncio.run(main(diagnose_only="--diagnose" in sys.argv))
