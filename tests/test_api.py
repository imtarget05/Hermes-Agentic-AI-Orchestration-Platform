"""HTTP API tests — procurement pipeline: run, recommendation, approvals, inbox, auth."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hermes import api
from hermes.config import settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hermes_db_path", str(tmp_path / "t.db"))
    monkeypatch.setattr(settings, "hermes_routing_path", "routing.json")
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "llm_provider", "stub")  # deterministic tests (no live LLM)
    monkeypatch.setenv("HERMES_PROCUREMENT_DB", str(tmp_path / "p.db"))
    monkeypatch.setenv("HERMES_HITL_AUTO_APPROVE", "true")
    api._runtime = None
    return TestClient(api.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["notifier"] == "mock"
    assert "default" in body["projects"]


def test_procurement_run_recommends_lenovo(client):
    r = client.post("/procurement/run", json={
        "text": "Công ty cần mua 50 laptop cho team Engineering",
        "project": "demo",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["task"]["status"] == "completed"
    rec = body["recommendation"]
    assert rec["vendor"] == "Lenovo"
    assert rec["total_cost"] == 54000.0
    assert rec["reasons"] and rec["evidence_refs"]
    assert [e["to"] for e in body["events"]][0] == "queued"


def test_legacy_run_maps_to_procurement(client):
    # legacy strategy names are mapped to the procurement pipeline
    r = client.post("/run", json={"text": "hello", "project": "demo", "strategy": "fanout"})
    assert r.status_code == 200
    assert r.json()["task"]["status"] == "completed"
    assert r.json()["recommendation"]["vendor"] == "Lenovo"


def test_approval_flow(client):
    r = client.post("/procurement/run", json={"text": "mua 50 laptop", "project": "demo"})
    assert r.status_code == 200
    pending = client.get("/procurement/approvals/pending").json()["pending"]
    assert pending == []  # auto-approved in tests
    resolve = client.post("/procurement/approvals/does-not-exist/resolve",
                          json={"approved": True})
    assert resolve.status_code == 404


def test_list_and_get_task(client):
    task_id = client.post("/run", json={"text": "inbox test", "project": "demo"}).json()["task"]["id"]
    lst = client.get("/tasks").json()
    assert any(t["id"] == task_id for t in lst)
    detail = client.get(f"/tasks/{task_id}").json()
    assert detail["task"]["text"] == "inbox test"
    assert detail["events"]
    assert client.get("/tasks/does-not-exist").status_code == 404


def test_auth_rejects_wrong_token(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "secret")
    r = TestClient(api.app).post("/run", json={"text": "x"}, headers={"X-API-Token": "wrong"})
    assert r.status_code == 401
