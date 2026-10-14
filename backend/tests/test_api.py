import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import Store


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # context manager runs the startup sync
        yield c


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["ready"] and body["last_error"] is None


def test_summary(client):
    s = client.get("/api/summary").json()
    assert (s["meetings"], s["matched"], s["crm_only"], s["calendar_only"]) == (24, 17, 3, 4)
    assert s["records"] == {"crm": 20, "calendar": 22}
    assert s["duplicate_groups"] == 1
    assert s["conflicts_by_field"] == {"location": 1, "start": 3, "status": 1}


def test_list_and_filters(client):
    assert len(client.get("/api/meetings").json()) == 24
    assert len(client.get("/api/meetings", params={"source": "both"}).json()) == 17
    assert len(client.get("/api/meetings", params={"source": "crm"}).json()) == 3
    assert len(client.get("/api/meetings", params={"source": "calendar"}).json()) == 4
    assert len(client.get("/api/meetings", params={"has_conflicts": True}).json()) == 5
    assert len(client.get("/api/meetings", params={"status": "cancelled"}).json()) == 1
    assert len(client.get("/api/meetings", params={"from": "2025-03-25", "to": "2025-03-25"}).json()) == 2

    hits = client.get("/api/meetings", params={"q": "meridian"}).json()
    assert {h["source_ids"]["crm"][0] for h in hits} == {"CRM-1001", "CRM-1007"}


def test_list_item_shape(client):
    row = next(r for r in client.get("/api/meetings").json() if r["source_ids"].get("crm") == ["CRM-1009"])
    assert row["status"] == "cancelled"
    assert row["max_conflict_severity"] == "error"
    assert row["conflict_fields"] == ["status"]


def test_detail_has_provenance_and_raw_records(client):
    row = next(r for r in client.get("/api/meetings", params={"q": "CRM-1005"}).json())
    detail = client.get(f"/api/meetings/{row['id']}").json()
    assert set(detail["raw_records"]) == {"CRM-1005", "CAL-A5", "CAL-A6"}
    assert detail["fields"]["start"]["status"] == "conflict"
    assert detail["fields"]["start"]["crm"].startswith("2025-03-17T11:00:00-04:00")
    assert detail["duplicates"][0]["superseded_ids"] == ["CAL-A5"]
    assert detail["match"]["reasons"]


def test_detail_404(client):
    assert client.get("/api/meetings/nope").status_code == 404


def test_issues_link_to_meetings(client):
    issues = client.get("/api/issues", params={"severity": "warning"}).json()
    assert issues and all(i["severity"] == "warning" for i in issues)
    assert all(i["meeting_id"] for i in issues)


def test_resync(client):
    assert client.post("/api/sync").json()["meetings"] == 24


class BrokenLoader:
    def load(self, name):
        raise ConnectionError("S3 unreachable")


def test_failed_sync_reports_error_and_returns_503():
    with TestClient(app) as c:
        c.app.state.store = Store(c.app.state.store.settings, loader=BrokenLoader())
        assert c.post("/api/sync").status_code == 502
        assert c.get("/api/meetings").status_code == 503
        assert "S3 unreachable" in c.get("/api/health").json()["last_error"]
