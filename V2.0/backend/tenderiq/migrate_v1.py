"""
Import v1's tenders.json (and its downloaded documents) into the v2 database,
then check the result against the original file.

Safe to re-run: tenders are matched on (portal, reference) and updated in
place, so the final import right before cut-over picks up whatever v1 has
scraped since. v1's files are only read, never changed.

Run from V2.0/backend:
    python -m tenderiq.migrate_v1                 # import + verify
    python -m tenderiq.migrate_v1 --verify-only   # just compare DB to v1
    python -m tenderiq.migrate_v1 --v1-dir C:\\TenderIQ
"""
import argparse
import json
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .dates import parse_iso_date, parse_portal_datetime
from .db import BACKEND_DIR, get_engine, session_factory, upgrade_db
from .models import Document, Source, Tender, TenderScore

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(errors="replace")

# v1 project folder = the parent of V2.0/
DEFAULT_V1_DIR = BACKEND_DIR.parent.parent

# v1 "source" value → (key, display name, portal URL)
V1_SOURCES = {
    "PDO SRM Portal": ("pdo_srm", "PDO SRM", "https://srm.pdo.co.om"),
    "OQ Tawreed": ("oq_tawreed", "OQ Tawreed", "https://tawreed.oq.com"),
}

V1_CLASSIFIER = "v1_classifier"

# v1 field → Tender column, for plain text fields ("" in v1 becomes NULL).
TEXT_FIELDS = {
    "title": "title",
    "rfx_type": "rfx_type",
    "status": "portal_status",
    "response_status": "portal_response_status",
    "estimated_value": "estimated_value",
    "link": "link",
    "description": "description",
    "scope_of_work": "scope_of_work",
    "sow_source": "sow_source",
    "sow_extraction": "sow_extraction",
    "doc_fetch_note": "doc_fetch_note",
}
# Missing in v1 means "unknown" → NULL, never False.
BOOL_FIELDS = {"purchase_required": "purchase_required", "doc_fetch_done": "doc_fetch_done"}
KNOWN_V1_KEYS = set(TEXT_FIELDS) | set(BOOL_FIELDS) | {
    "reference_number", "source", "start_date", "closing_date", "first_seen",
    "last_seen", "active", "documents", "category",
}


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    docs_copied: int = 0
    docs_present: int = 0
    docs_missing: list[str] = field(default_factory=list)
    unknown_keys: Counter = field(default_factory=Counter)


def _text(value) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s.strip() else None


def load_v1(v1_dir: Path) -> list[dict]:
    return json.loads((v1_dir / "tenders.json").read_text(encoding="utf-8"))


def _ensure_sources(session: Session) -> dict[str, Source]:
    by_v1_name = {}
    for v1_name, (key, name, url) in V1_SOURCES.items():
        src = session.scalar(select(Source).where(Source.key == key))
        if src is None:
            src = Source(key=key, name=name, url=url, enabled=True)
            session.add(src)
        by_v1_name[v1_name] = src
    session.flush()
    return by_v1_name


def _import_document(v1_path: str, position: int, source: Source, v1_dir: Path,
                     docs_dir: Path, report: ImportReport) -> Document:
    src_file = v1_dir / v1_path
    # v1 paths look like "documents/<ref>/<file>"; v2 files them per portal.
    rest = Path(v1_path)
    if rest.parts and rest.parts[0] == "documents":
        rest = Path(*rest.parts[1:])
    rel_path = Path(source.key) / rest
    dest = (docs_dir / rel_path).resolve()
    if not dest.is_relative_to(docs_dir.resolve()):
        raise ValueError(f"Document path escapes the documents folder: {v1_path!r}")

    if not src_file.is_file():
        report.docs_missing.append(v1_path)
        return Document(position=position, filename=rest.name, rel_path=None,
                        size_bytes=None, present=False, v1_path=v1_path)

    size = src_file.stat().st_size
    if not dest.is_file() or dest.stat().st_size != size:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)
        report.docs_copied += 1
    report.docs_present += 1
    return Document(position=position, filename=rest.name, rel_path=rel_path.as_posix(),
                    size_bytes=size, present=True, v1_path=v1_path)


def import_v1(session: Session, v1_dir: Path, docs_dir: Path) -> ImportReport:
    report = ImportReport()
    records = load_v1(v1_dir)
    sources = _ensure_sources(session)
    existing = {(t.source_id, t.reference): t for t in session.scalars(select(Tender))}

    for rec in records:
        if rec.get("source") not in sources:
            raise ValueError(f"Unknown v1 source {rec.get('source')!r} on {rec.get('reference_number')}")
        source = sources[rec["source"]]
        report.unknown_keys.update(k for k in rec if k not in KNOWN_V1_KEYS)

        tender = existing.get((source.id, rec["reference_number"]))
        if tender is None:
            tender = Tender(source=source, reference=rec["reference_number"])
            session.add(tender)
            report.created += 1
        else:
            report.updated += 1

        for v1_key, col in TEXT_FIELDS.items():
            setattr(tender, col, _text(rec.get(v1_key)))
        tender.title = tender.title or ""
        for v1_key, col in BOOL_FIELDS.items():
            value = rec.get(v1_key)
            setattr(tender, col, None if value is None else bool(value))

        tender.floated_on, _ = parse_portal_datetime(rec.get("start_date"))
        tender.closes_on, tender.closes_time = parse_portal_datetime(rec.get("closing_date"))
        first_seen = parse_iso_date(rec.get("first_seen"))
        if first_seen is None:
            raise ValueError(f"{rec['reference_number']} has no first_seen date")
        tender.first_seen = first_seen
        tender.last_seen = parse_iso_date(rec.get("last_seen"))
        tender.active = rec.get("active", True) is not False
        tender.v1_raw = rec

        tender.documents = [
            _import_document(p, i, source, v1_dir, docs_dir, report)
            for i, p in enumerate(rec.get("documents") or [])
        ]

        # v1's category came from its AI classifier, so it is AI output.
        v1_score = next((s for s in tender.scores if s.scored_by == V1_CLASSIFIER), None)
        category = _text(rec.get("category"))
        if category and v1_score is None:
            tender.scores.append(TenderScore(scored_by=V1_CLASSIFIER, category=category))
        elif category:
            v1_score.category = category
        elif v1_score is not None:
            tender.scores.remove(v1_score)

    session.commit()
    return report


def _v1_expected(rec: dict) -> dict:
    """A v1 record normalised the same way the importer stores it."""
    closes_on, closes_time = parse_portal_datetime(rec.get("closing_date"))
    exp = {col: _text(rec.get(k)) for k, col in TEXT_FIELDS.items()}
    exp["title"] = exp["title"] or ""
    exp |= {col: None if rec.get(k) is None else bool(rec.get(k)) for k, col in BOOL_FIELDS.items()}
    exp |= {
        "floated_on": parse_portal_datetime(rec.get("start_date"))[0],
        "closes_on": closes_on,
        "closes_time": closes_time,
        "first_seen": parse_iso_date(rec.get("first_seen")),
        "last_seen": parse_iso_date(rec.get("last_seen")),
        "active": rec.get("active", True) is not False,
        "documents": list(rec.get("documents") or []),
        "category": _text(rec.get("category")),
    }
    return exp


def _db_actual(t: Tender) -> dict:
    act = {col: getattr(t, col) for col in list(TEXT_FIELDS.values()) + list(BOOL_FIELDS.values())}
    act |= {
        "floated_on": t.floated_on,
        "closes_on": t.closes_on,
        "closes_time": t.closes_time,
        "first_seen": t.first_seen,
        "last_seen": t.last_seen,
        "active": t.active,
        "documents": [d.v1_path for d in t.documents],
        "category": next((s.category for s in t.scores if s.scored_by == V1_CLASSIFIER), None),
    }
    return act


def verify(session: Session, v1_dir: Path, docs_dir: Path) -> list[str]:
    """Rebuild every v1 record from the DB and diff it. Returns the problems found."""
    problems = []
    records = load_v1(v1_dir)
    keys = {v1: key for v1, (key, _, _) in V1_SOURCES.items()}
    tenders = {(t.source.key, t.reference): t for t in session.scalars(select(Tender))}

    if len(tenders) != len(records):
        problems.append(f"DB has {len(tenders)} tenders, tenders.json has {len(records)}")
    for rec in records:
        t = tenders.get((keys.get(rec.get("source")), rec.get("reference_number")))
        if t is None:
            problems.append(f"{rec.get('reference_number')}: missing from DB")
            continue
        expected, actual = _v1_expected(rec), _db_actual(t)
        for k in expected:
            if expected[k] != actual[k]:
                problems.append(f"{t.reference}.{k}: v1={expected[k]!r} db={actual[k]!r}")
        for d in t.documents:
            if d.present:
                f = docs_dir / d.rel_path
                if not f.is_file() or f.stat().st_size != d.size_bytes:
                    problems.append(f"{t.reference}: document file missing or wrong size: {d.rel_path}")
    return problems


def summary(session: Session) -> str:
    rows = session.execute(
        select(Source.name, Tender.active, func.count()).join(Tender.source)
        .group_by(Source.name, Tender.active)
    ).all()
    by_source = Counter()
    by_active = Counter()
    for name, active, n in rows:
        by_source[name] += n
        by_active["active" if active else "inactive"] += n
    cats = Counter(dict(session.execute(
        select(TenderScore.category, func.count()).group_by(TenderScore.category)).all()))
    docs = session.execute(select(Document.present, func.count()).group_by(Document.present)).all()
    lo, hi = session.execute(select(func.min(Tender.closes_on), func.max(Tender.closes_on))).one()
    no_close = session.scalar(select(func.count()).where(Tender.closes_on.is_(None)))
    with_time = session.scalar(select(func.count()).where(Tender.closes_time.is_not(None)))
    lines = [
        f"Tenders: {sum(by_source.values())}  " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())),
        f"Status:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_active.items())),
        f"Closing dates: {lo} .. {hi}  (missing: {no_close}, with a time: {with_time})",
        f"Categories ({sum(cats.values())} tenders): "
        + ", ".join(f"{k}={v}" for k, v in cats.most_common()),
        "Documents: " + ", ".join(f"{'present' if p else 'missing'}={n}" for p, n in docs),
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v1-dir", type=Path, default=DEFAULT_V1_DIR, help="v1 project folder (has tenders.json)")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    v1_dir = args.v1_dir.resolve()
    docs_dir = config.documents_dir()
    print(f"v1 folder: {v1_dir}\nDatabase:  {config.db_path()}")
    upgrade_db(get_engine())

    with session_factory()() as session:
        if not args.verify_only:
            r = import_v1(session, v1_dir, docs_dir)
            print(f"\nImported: {r.created} new, {r.updated} updated. "
                  f"Documents: {r.docs_present} present ({r.docs_copied} copied), "
                  f"{len(r.docs_missing)} missing in v1.")
            for p in r.docs_missing:
                print(f"  missing in v1: {p}")
            if r.unknown_keys:
                print(f"  v1 fields kept only in v1_raw: {dict(r.unknown_keys)}")

        print("\n" + summary(session))
        problems = verify(session, v1_dir, docs_dir)
    if problems:
        print(f"\nVERIFY FAILED - {len(problems)} difference(s):")
        for p in problems[:50]:
            print("  " + p)
        return 1
    print("\nVerify OK - every v1 tender matches the database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
