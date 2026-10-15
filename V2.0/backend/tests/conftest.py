import json
from pathlib import Path

import pytest

from tenderiq.db import get_engine, session_factory, upgrade_db

# A miniature v1 project: one tender per portal, covering the quirks seen in
# the real tenders.json (day-first dates, PDO without a time, missing keys,
# blank strings, a document that was never downloaded).
V1_RECORDS = [
    {
        "reference_number": "Tender_37494",
        "title": "Supply of Material (VIBRATION MON SPARES)",
        "rfx_type": "Tawreed RFQ",
        "status": "Running",
        "start_date": "",
        "closing_date": "02/08/2026 23:59",
        "response_status": "",
        "description": "",
        "estimated_value": "",
        "link": "https://tawreed.oq.com/list",
        "documents": ["documents/Tender_37494/description.txt"],
        "source": "OQ Tawreed",
        "doc_fetch_done": True,
        "scope_of_work": "Supply of vibration monitoring spares",
        "sow_source": "description.txt",
        "sow_extraction": "heading-match",
        "first_seen": "2026-07-22",
        "last_seen": "2026-08-01",
        "purchase_required": False,
        "category": "Instrumentation & Controls",
        "active": False,
    },
    {
        "reference_number": "4000232440",
        "title": "Pipeline inspection services",
        "rfx_type": "RFx for Op. Proc.",
        "status": "Published",
        "start_date": "",
        "closing_date": "18.08.2026",
        "response_status": "No Bid Created",
        "description": "",
        "estimated_value": "",
        "link": "https://srm.pdo.co.om/x",
        "documents": ["documents/4000232440/never-downloaded.pdf"],
        "source": "PDO SRM Portal",
        "first_seen": "2026-08-04",
        "active": True,
    },
]


def write_v1(v1_dir: Path, records: list[dict]) -> None:
    (v1_dir / "tenders.json").write_text(json.dumps(records), encoding="utf-8")


@pytest.fixture
def v1_dir(tmp_path: Path) -> Path:
    d = tmp_path / "v1"
    (d / "documents" / "Tender_37494").mkdir(parents=True)
    (d / "documents" / "Tender_37494" / "description.txt").write_text("scope text", encoding="utf-8")
    write_v1(d, V1_RECORDS)
    return d


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data" / "documents"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def session(tmp_path: Path):
    engine = get_engine(str(tmp_path / "test.db"))
    upgrade_db(engine)
    with session_factory(engine)() as s:
        yield s
    engine.dispose()
