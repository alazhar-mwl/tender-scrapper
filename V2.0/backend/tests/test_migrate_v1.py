from datetime import date, time

from sqlalchemy import func, select

from tenderiq.migrate_v1 import import_v1, verify
from tenderiq.models import Document, Tender, TenderScore

from .conftest import V1_RECORDS, write_v1


def _tender(session, ref) -> Tender:
    return session.scalar(select(Tender).where(Tender.reference == ref))


def test_import_matches_v1(session, v1_dir, docs_dir):
    report = import_v1(session, v1_dir, docs_dir)

    assert (report.created, report.updated) == (2, 0)
    assert verify(session, v1_dir, docs_dir) == []

    oq = _tender(session, "Tender_37494")
    assert (oq.closes_on, oq.closes_time) == (date(2026, 8, 2), time(23, 59))
    assert oq.active is False
    assert oq.estimated_value is None  # "" in v1
    assert oq.scores[0].category == "Instrumentation & Controls"
    assert (docs_dir / oq.documents[0].rel_path).read_text(encoding="utf-8") == "scope text"

    pdo = _tender(session, "4000232440")
    assert (pdo.closes_on, pdo.closes_time) == (date(2026, 8, 18), None)
    # Missing in v1 → unknown, not False / not a guessed date.
    assert pdo.purchase_required is None
    assert pdo.doc_fetch_done is None
    assert pdo.last_seen is None
    assert pdo.scores == []


def test_missing_document_is_recorded_not_skipped(session, v1_dir, docs_dir):
    report = import_v1(session, v1_dir, docs_dir)

    assert report.docs_missing == ["documents/4000232440/never-downloaded.pdf"]
    doc = _tender(session, "4000232440").documents[0]
    assert (doc.present, doc.rel_path) == (False, None)


def test_reimport_updates_in_place(session, v1_dir, docs_dir):
    import_v1(session, v1_dir, docs_dir)
    changed = [dict(r) for r in V1_RECORDS]
    changed[1]["closing_date"] = "25.08.2026"
    changed[1]["category"] = "Other"
    changed[0].pop("category")
    write_v1(v1_dir, changed)

    report = import_v1(session, v1_dir, docs_dir)

    assert (report.created, report.updated, report.docs_copied) == (0, 2, 0)
    assert session.scalar(select(func.count()).select_from(Tender)) == 2
    assert session.scalar(select(func.count()).select_from(Document)) == 2
    assert session.scalar(select(func.count()).select_from(TenderScore)) == 1
    assert _tender(session, "4000232440").closes_on == date(2026, 8, 25)
    assert verify(session, v1_dir, docs_dir) == []


def test_verify_catches_differences(session, v1_dir, docs_dir):
    import_v1(session, v1_dir, docs_dir)
    _tender(session, "4000232440").closes_on = date(2026, 1, 1)
    session.commit()

    problems = verify(session, v1_dir, docs_dir)

    assert len(problems) == 1 and "closes_on" in problems[0]
