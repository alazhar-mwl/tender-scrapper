from fastapi.testclient import TestClient

from tenderiq.db import get_session
from tenderiq.main import app
from tenderiq.migrate_v1 import import_v1


def _client(session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def test_list_defaults_to_active_tenders(session, v1_dir, docs_dir):
    import_v1(session, v1_dir, docs_dir)
    client = _client(session)

    active = client.get("/api/tenders").json()
    everything = client.get("/api/tenders", params={"status": "all"}).json()
    oq = client.get("/api/tenders", params={"status": "inactive", "source": "oq_tawreed"}).json()

    assert [t["reference"] for t in active] == ["4000232440"]
    assert len(everything) == 2
    assert [t["reference"] for t in oq] == ["Tender_37494"]
    assert oq[0]["category"] == "Instrumentation & Controls"


def test_detail_and_404(session, v1_dir, docs_dir):
    import_v1(session, v1_dir, docs_dir)
    client = _client(session)
    tender_id = client.get("/api/tenders", params={"status": "inactive"}).json()[0]["id"]

    detail = client.get(f"/api/tenders/{tender_id}").json()

    assert detail["closes_time"] == "23:59:00"
    assert detail["documents"] == [{"filename": "description.txt", "size_bytes": 10, "present": True}]
    assert detail["latest_score"]["scored_by"] == "v1_classifier"
    assert client.get("/api/tenders/99999").status_code == 404
    assert client.get("/api/health").json() == {"status": "ok", "tenders": 2}
