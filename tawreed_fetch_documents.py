"""
Phase 2 (OQ Tawreed) — Tender document fetcher
------------------------------------------------
Mirrors fetch_documents.py's role for PDO: for each pending OQ tender, opens
its detail page (single-page accordion layout, unlike PDO's tabbed popup),
saves the full page text as description.txt, and downloads any real
attachments found (JAGGAER's DownloadProxy pattern). Results feed
extract_sow.py exactly like PDO's notes.txt does — no OQ-specific SOW
parser needed since the page text is already clean field/value text.

Run:
    python tawreed_fetch_documents.py                # up to MAX_DOCS_PER_RUN pending
    python tawreed_fetch_documents.py Tender_37131 …  # only these tender codes
"""

import asyncio
import json
import os
import re
import sys

from playwright.async_api import Page, async_playwright

from tawreed_scraper import (
    PORTAL_URL,
    STATE_FILE,
    close_notice_popup,
    click_href,
    ensure_logged_in,
    log,
    screenshot,
)
from tender_scraper import DOCS_DIR, OUT_FILE

MAX_PER_RUN = int(os.getenv("MAX_DOCS_PER_RUN", "10"))
SOURCE_OQ = "OQ Tawreed"
PUBLIC_LIST_HREF = "/esop/guest/go/neg/rfq/public"


def load_tenders() -> list[dict]:
    if not OUT_FILE.exists():
        sys.exit("tenders.json not found — run tawreed_scraper.py first.")
    return json.loads(OUT_FILE.read_text(encoding="utf-8"))


def save_tenders(tenders: list[dict]) -> None:
    OUT_FILE.write_text(
        json.dumps(tenders, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


PURCHASE_REQUIRED = "purchase_required"


async def fetch_tender_documents(page: Page, ref: str) -> "list[str] | None | str":
    """
    Click the tender's title link from its row on the public list (matched
    by ref = Tender Code, which is unique and lives in its own column),
    save the detail page's full text, and download any real attachments.

    Returns:
      - list[str]: paths relative to BASE_DIR, on success
      - None: fetch failed (transient — worth retrying next run)
      - PURCHASE_REQUIRED: the tender's title link routes through
        contractPayment/view.si instead of straight to initDetailRfq.do —
        confirmed live 2026-07-22 this is a genuine access gate (some
        tenders navigate to the detail page immediately; others hang
        indefinitely behind a "Task in progress" spinner with NO further
        navigation, no JS error, no browser dialog — for 15s+, confirmed by
        hand). This is Tawreed's real "you must Express Interest / purchase
        this tender's documents" gate. We deliberately do NOT click through
        it (that's a real action, not a read): see project notes on why.
    """
    tender_dir = DOCS_DIR / ref
    tender_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []

    row = page.locator(f"tr:has-text('{ref}')").first
    try:
        if not await row.count():
            log.warning("  Row for %s not found on the list page", ref)
            return None
        # The title cell is the only real link in the row (JAGGAER's
        # javascript: contractPayment/... handler) — target it precisely
        # rather than the first <a>, in case the row grows other links later.
        link = row.locator("a[href*='javascript']").first
        if not await link.count():
            link = row.locator("a").first
        await link.click(timeout=8_000)
    except Exception as exc:
        log.warning("  Could not click into %s: %s", ref, exc)
        return None

    # Wait for actual navigation to the detail page (URL changes to
    # detailRfqSettings.do). Confirmed live 2026-07-22: checking only for
    # `ref` in page text is NOT sufficient — the ref is trivially also
    # present on the LIST page (in its own row), so if the click doesn't
    # navigate, that check false-passes and would capture list/nav-menu text
    # as if it were the tender detail.
    for _ in range(8):
        await asyncio.sleep(1)
        if "detailRfqSettings" in page.url:
            break
    await close_notice_popup(page)

    if "detailRfqSettings" not in page.url:
        gated = await page.evaluate("""() => !!document.querySelector(
            '.pleaseWaitDialog, [class*="pleaseWait" i]')""")
        if gated:
            log.info("  %s is gated behind Express Interest / purchase — not downloading.", ref)
            return PURCHASE_REQUIRED
        log.error("  Never reached the detail page for %s (stuck at %s) — skipping.", ref, page.url[:80])
        return None

    # Belt-and-suspenders: also confirm the ref text is on THIS (now-detail) page
    verified = await page.evaluate("(ref) => document.body.innerText.includes(ref)", ref)
    if not verified:
        log.error("  Detail page does not show %s — wrong tender opened; skipping.", ref)
        return None

    await screenshot(page, f"detail_{ref}")

    # Printable View (opens a new tab) is a strictly richer, purely read-only
    # source than the main detail page — confirmed 2026-07-22: it includes
    # the tender's real Description field (often blank on the main page),
    # plus line items, tax/VAT/RIYADA questions, and an Attachments listing.
    # It is NOT a substitute for the actual attached SOW file, which is gated
    # behind "Express Interest" (a real action we don't take automatically —
    # see project notes) — but its text is a real quality improvement over
    # the main page alone, with no extra risk since it's read-only.
    text = None
    try:
        async with page.context.expect_page(timeout=8_000) as pinfo:
            await page.locator("button, a", has_text="Printable View").first.click(timeout=8_000)
        pview = await pinfo.value
        await pview.wait_for_load_state("load", timeout=15_000)
        await asyncio.sleep(3)
        text = await pview.evaluate("() => document.body.innerText")
        await pview.close()
    except Exception as exc:
        log.warning("  Printable View unavailable for %s (%s) — using main page text", ref, exc)

    if not text or not text.strip():
        text = await page.evaluate("() => document.body.innerText")

    if text.strip():
        # Strip the app-shell chrome (nav skip-links, clock, toolbar button
        # labels) that innerText picks up above the real content — identical
        # noise on every tender/every page, not useful content.
        noise = {"Go to : Navigation Menu", "Go to : Main Content", "Page Actions List",
                 "Decide Later", "Printable View", "Express Interest", "Main Content",
                 "Print", "Download PDF", "Close", "Zoom 100%"}
        time_re = re.compile(r"^\d{1,2}:\d{2} Gulf Standard Time$")
        print_re = re.compile(r"^Date & Time of Print:.*$")
        cleaned = "\n".join(ln for ln in text.splitlines()
                            if ln not in noise and not time_re.match(ln) and not print_re.match(ln))
        desc_path = tender_dir / "description.txt"
        desc_path.write_text(cleaned, encoding="utf-8")
        saved.append(f"documents/{ref}/description.txt")

    att_labels = await page.evaluate("""() => [...document.querySelectorAll('a')]
        .filter(a => (a.getAttribute('onclick')||'').includes('DownloadProxy'))
        .map(a => a.innerText.trim())
        .filter(Boolean)
    """)
    for label in att_labels:
        fname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label)[:120]
        out_path = tender_dir / fname
        if out_path.exists():
            log.info("  Already have: %s", fname)
            saved.append(f"documents/{ref}/{fname}")
            continue
        try:
            async with page.expect_download(timeout=20_000) as dl_ctx:
                await page.locator("a", has_text=label).first.click(timeout=8_000)
            dl = await dl_ctx.value
            real_fname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", dl.suggested_filename or fname)[:120]
            out_path = tender_dir / real_fname
            await dl.save_as(str(out_path))
            saved.append(f"documents/{ref}/{real_fname}")
            log.info("  Downloaded: %s", real_fname)
        except Exception as exc:
            log.warning("  Download failed for %r: %s", label, exc)

    return saved


async def return_to_public_list(page: Page) -> bool:
    """
    Get back to the "Tenders Open to All Suppliers" list after a detail page.

    Confirmed live 2026-07-16: neither page.go_back() (lands on a SECOND
    detail-page history entry — detail nav pushes more than one state) nor
    clicking the Angular toolkit's own sidebar href (not present on detail
    pages at all) reliably returns to the list. The one proven-reliable path
    is the exact sequence ensure_logged_in() already uses at startup:
    navigate to the bare portal root (safe — doesn't trip the CSRF guard,
    unlike a deep-link goto) then click through from there. Slightly slower
    per tender, but it works every time because it's the same cold-start path.
    """
    try:
        await page.goto(PORTAL_URL, wait_until="load", timeout=30_000)
    except Exception:
        pass
    await asyncio.sleep(3)
    await close_notice_popup(page)
    ok = await click_href(page, PUBLIC_LIST_HREF, "Tenders Open to All Suppliers")
    await close_notice_popup(page)
    return ok and "pubRfq/list" in page.url and await page.locator("table tr").count() >= 3


async def main() -> None:
    tenders = load_tenders()
    only = {a for a in sys.argv[1:]}

    def is_pending(t: dict) -> bool:
        if t.get("source") != SOURCE_OQ or not t.get("reference_number"):
            return False
        if only:
            return t["reference_number"] in only
        return not t.get("doc_fetch_done")

    todo = [t for t in tenders if is_pending(t)]
    if not todo:
        log.info("Nothing to fetch — all OQ tenders already processed.")
        return
    if not only and len(todo) > MAX_PER_RUN:
        log.info("Limiting to %d of %d pending OQ tenders (MAX_DOCS_PER_RUN).",
                 MAX_PER_RUN, len(todo))
        todo = todo[:MAX_PER_RUN]

    log.info("Fetching documents for %d OQ tender(s): %s",
             len(todo), ", ".join(t["reference_number"] for t in todo))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=str(STATE_FILE) if STATE_FILE.exists() else None,
            accept_downloads=True,
        )
        page = await ctx.new_page()
        try:
            await ensure_logged_in(page, ctx)
            await click_href(page, PUBLIC_LIST_HREF, "Tenders Open to All Suppliers")

            for tender in todo:
                ref = tender["reference_number"]
                docs = await fetch_tender_documents(page, ref)
                if docs is None:
                    tender["doc_fetch_done"] = False
                    tender["doc_fetch_note"] = "fetch failed — see scraper.log"
                elif docs == PURCHASE_REQUIRED:
                    # We successfully determined this tender's status — it's
                    # gated behind Express Interest/purchase — so this IS a
                    # completed, not a failed, fetch. Not a transient error:
                    # don't keep retrying it every run.
                    tender["documents"] = []
                    tender["doc_fetch_done"] = True
                    tender["purchase_required"] = True
                    tender["doc_fetch_note"] = "Requires Express Interest / purchase to view full details"
                else:
                    tender["documents"] = docs
                    tender["doc_fetch_done"] = True
                    tender["purchase_required"] = False
                    tender["doc_fetch_note"] = "" if docs else "no documents found"
                save_tenders(tenders)   # checkpoint after every tender
                await asyncio.sleep(2)  # politeness delay

                if not await return_to_public_list(page):
                    log.error("  Could not return to the tenders list after %s (url=%s) — "
                             "stopping (remaining tenders stay pending for next run).",
                             ref, page.url[:90])
                    break
        finally:
            await ctx.storage_state(path=str(STATE_FILE))
            await browser.close()

    save_tenders(tenders)
    done = sum(1 for t in tenders if t.get("source") == SOURCE_OQ and t.get("doc_fetch_done"))
    total_oq = sum(1 for t in tenders if t.get("source") == SOURCE_OQ)
    log.info("Document fetch complete — %d/%d OQ tenders processed.", done, total_oq)
    log.info("Next: python extract_sow.py  (offline, no portal access)")


if __name__ == "__main__":
    asyncio.run(main())
