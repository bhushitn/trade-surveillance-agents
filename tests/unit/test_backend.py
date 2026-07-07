"""Review API behavior with the offline case pipeline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.app as backend_app


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    backend_app._settings = backend_app.Settings(
        local_state_dir=str(tmp_path_factory.mktemp("state"))
    )
    with TestClient(backend_app.app) as c:
        yield c


def test_queue_lists_pending_cases(client: TestClient) -> None:
    cases = client.get("/api/cases").json()
    assert len(cases) > 0
    assert all(c["status"] == "pending" for c in cases)
    assert all(c["generated_by"] == "offline-template" for c in cases)


def test_case_detail_has_evidence_and_references(client: TestClient) -> None:
    case_id = client.get("/api/cases").json()[0]["case_id"]
    case = client.get(f"/api/cases/{case_id}").json()
    memo = case["memo"]
    assert memo["evidence"], "memo must cite evidence"
    assert memo["recommendation"] in ("escalate", "monitor", "dismiss")
    assert client.get("/api/cases/CASE-9999").status_code == 404


def test_review_approve_and_idempotency(client: TestClient) -> None:
    case_id = client.get("/api/cases").json()[0]["case_id"]
    r = client.post(
        f"/api/cases/{case_id}/review",
        json={"action": "approve", "notes": "checked", "edited_narrative": "edited text"},
    )
    assert r.json()["status"] == "approved"
    case = client.get(f"/api/cases/{case_id}").json()
    assert case["memo"]["narrative"] == "edited text"
    assert case["review_history"][0]["edited"] is True
    second = client.post(f"/api/cases/{case_id}/review", json={"action": "dismiss"})
    assert second.status_code == 409


def test_no_filing_surface(client: TestClient) -> None:
    """The API exposes review actions only; no route files or forwards a case."""
    routes = {r.path for r in backend_app.app.routes}
    assert not any("fil" in p or "submit" in p or "report" in p for p in routes)
    r = client.post(
        f"/api/cases/{client.get('/api/cases').json()[1]['case_id']}/review",
        json={"action": "escalate"},
    )
    assert r.status_code == 422  # only approve and dismiss validate
