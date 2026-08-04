"""
Phase 4 — Market-intelligence classifier (offline except for the AI call)
---------------------------------------------------------------------------
For each tender in tenders.json without a `category` yet, sends its title +
scope of work to Claude and asks it to pick ONE category from a fixed
taxonomy (CATEGORIES below). This turns the raw, low-signal RFx type field
("Technical RFx", "Tawreed RFQ", ...) into the commodity/service grouping the
Market Intelligence dashboard chart is built on.

The taxonomy is intentionally fixed and short (8 slots) — it maps 1:1 onto
the dashboard's 8-color categorical palette. Never let the model invent a
9th category; it must always fall back to "Other" instead.

Requires ANTHROPIC_API_KEY in ai.env.txt (same file server.py's AI features
use — see .gitignore).

Run:
    python classify_tenders.py          # only uncategorized tenders
    python classify_tenders.py --all    # re-classify everything
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(errors="replace")

BASE_DIR = Path(__file__).parent
OUT_FILE = BASE_DIR / "tenders.json"

load_dotenv(BASE_DIR / "ai.env.txt")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# Fixed 8-slot taxonomy — 1:1 with the dashboard's categorical palette slots.
# Keep this list in sync with CATEGORY_COLORS in tender_intelligence_app.html.
CATEGORIES = [
    "Instrumentation & Controls",
    "Valves, Fittings & Mechanical Spares",
    "Electrical & Power Systems",
    "Manpower & Professional Services",
    "IT, Software & Digital Services",
    "Civil, Construction & Facilities",
    "Well Services, Drilling & Production Equipment",
    "Other",
]


def load_tenders() -> list[dict]:
    if not OUT_FILE.exists():
        sys.exit("tenders.json not found — run tender_scraper.py first.")
    return json.loads(OUT_FILE.read_text(encoding="utf-8"))


def save_tenders(tenders: list[dict]) -> None:
    OUT_FILE.write_text(
        json.dumps(tenders, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def classify(title: str, scope: str) -> str:
    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 30,
        "system": (
            "Classify an oil & gas tender into EXACTLY ONE of these categories "
            "— reply with the category text only, nothing else, no punctuation:\n"
            + "\n".join(CATEGORIES)
        ),
        "messages": [{
            "role": "user",
            "content": f"Title: {title}\nScope: {scope[:1500]}",
        }],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    raw = next((c["text"] for c in data.get("content", []) if c.get("type") == "text"), "").strip()
    return raw if raw in CATEGORIES else "Other"


def main() -> None:
    if not ANTHROPIC_API_KEY:
        sys.exit("No ANTHROPIC_API_KEY configured — add it to ai.env.txt.")

    tenders = load_tenders()
    reclassify_all = "--all" in sys.argv
    todo = [t for t in tenders if reclassify_all or not t.get("category")]

    if not todo:
        print("Nothing to classify — every tender already has a category.")
        return

    print(f"Classifying {len(todo)} tender(s)...")
    for i, t in enumerate(todo, 1):
        title = t.get("title", "")
        scope = t.get("scope_of_work") or t.get("description") or title
        try:
            category = classify(title, scope)
            t["category"] = category
            print(f"  [{i}/{len(todo)}] {t.get('reference_number', '?')}: {category}")
        except urllib.error.HTTPError as exc:
            print(f"  [{i}/{len(todo)}] {t.get('reference_number', '?')}: FAILED "
                  f"({exc.code} {exc.read().decode(errors='replace')[:150]})")
        except Exception as exc:
            print(f"  [{i}/{len(todo)}] {t.get('reference_number', '?')}: FAILED ({exc})")
        save_tenders(tenders)   # checkpoint after every tender
        time.sleep(0.5)         # light politeness delay between API calls

    counts: dict[str, int] = {}
    for t in tenders:
        if t.get("category"):
            counts[t["category"]] = counts.get(t["category"], 0) + 1
    print("Done. Breakdown:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
