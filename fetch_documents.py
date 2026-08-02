"""
Phase 2 — Tender document fetcher (incremental)
------------------------------------------------
Reads tenders.json, finds tenders whose documents haven't been fetched yet,
locates each one in the portal's RFx list, downloads its attachments to
documents/<ref>/, and records the results back into tenders.json after every
tender (crash-safe).

Only NEW tenders are processed, and at most MAX_DOCS_PER_RUN per session, so
repeat runs stay short and gentle on the portal.

Run:
    python fetch_documents.py                 # up to MAX_DOCS_PER_RUN pending tenders
    python fetch_documents.py 4000232078 ...  # only these reference numbers
"""

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

from tender_scraper import (
    OUT_FILE,
    SEL_NEXT,
    _scroll_window,
    _wait_after_nav,
    clear_list_filters,
    ensure_logged_in,
    extract_page,
    fetch_rfx_documents,
    frame_locator,
    goto_tenders,
    launch_context,
    log,
    release_app_session,
)

MAX_PER_RUN = int(os.getenv("MAX_DOCS_PER_RUN", "10"))
MAX_PAGES = 50   # hard stop so a stuck Next button can't loop forever


def load_tenders() -> list[dict]:
    if not OUT_FILE.exists():
        sys.exit("tenders.json not found — run tender_scraper.py first.")
    return json.loads(OUT_FILE.read_text(encoding="utf-8"))


def save_tenders(tenders: list[dict]) -> None:
    OUT_FILE.write_text(
        json.dumps(tenders, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


async def main() -> None:
    tenders = load_tenders()
    only = {a for a in sys.argv[1:] if a.isdigit()}

    def is_pending(t: dict) -> bool:
        if not t.get("reference_number"):
            return False
        if only:
            return t["reference_number"] in only
        return not t.get("doc_fetch_done")

    todo = [t for t in tenders if is_pending(t)]
    if not todo:
        log.info("Nothing to fetch — all tenders already processed.")
        return
    if not only and len(todo) > MAX_PER_RUN:
        log.info("Limiting to %d of %d pending tenders (MAX_DOCS_PER_RUN).",
                 MAX_PER_RUN, len(todo))
        todo = todo[:MAX_PER_RUN]

    by_ref = {t["reference_number"]: t for t in todo}
    log.info("Fetching documents for %d tender(s): %s",
             len(by_ref), ", ".join(sorted(by_ref)))

    async with async_playwright() as pw:
        browser, ctx = await launch_context(pw)
        page = await ctx.new_page()
        try:
            await ensure_logged_in(page, ctx)
            await goto_tenders(page)   # also selects the POWL query
            await clear_list_filters(page)

            page_num = 1
            stagnant = 0
            while by_ref and page_num <= MAX_PAGES:
                rows = await extract_page(page)
                refs_here = [r["reference_number"] for r in rows
                             if r["reference_number"] in by_ref]
                log.info("── Page %d: %d pending tender(s) visible ──", page_num, len(refs_here))

                for ref in refs_here:
                    tender = by_ref.pop(ref)
                    try:
                        docs = await fetch_rfx_documents(page, ref)
                    except Exception as exc:
                        # Don't let one flaky page state crash the whole batch
                        # and abandon every remaining tender — see the same
                        # fix in tawreed_fetch_documents.py (2026-08-02).
                        log.error("  Unexpected error fetching %s: %s — leaving pending.", ref, exc)
                        docs = None
                    if docs is None:
                        # wrong row opened / detail unavailable — leave pending
                        tender["doc_fetch_done"] = False
                        tender["doc_fetch_note"] = "fetch failed — see scraper.log"
                    else:
                        tender["documents"] = docs
                        tender["doc_fetch_done"] = True
                        tender["doc_fetch_note"] = "" if docs else "no documents found"
                    save_tenders(tenders)   # checkpoint after every tender
                    await asyncio.sleep(3)  # politeness delay

                if not by_ref:
                    break

                # Advance the table's scroll window (POWL scrolls rows; no Next link)
                stagnant = stagnant + 1 if not refs_here else 0
                if stagnant >= 5:
                    break   # several windows with no pending tenders — give up
                if await _scroll_window(page):
                    await asyncio.sleep(2.5)
                    page_num += 1
                    continue

                # No scrollbar — fall back to a classic Next-page link
                next_loc, _ = await frame_locator(page, SEL_NEXT, timeout=3_000)
                if next_loc is None:
                    break
                await next_loc.click()
                await _wait_after_nav(page, f"docs_page_{page_num + 1:03d}")
                page_num += 1

            if by_ref:
                log.warning("%d pending tender(s) not found in the list: %s",
                            len(by_ref), ", ".join(sorted(by_ref)))
        finally:
            await release_app_session(page)
            await browser.close()

    save_tenders(tenders)
    done = sum(1 for t in tenders if t.get("doc_fetch_done"))
    log.info("Document fetch complete — %d/%d tenders processed overall.", done, len(tenders))
    log.info("Next: python extract_sow.py  (offline, no portal access)")


if __name__ == "__main__":
    asyncio.run(main())
