"""HTTP API tests — full feature parity: run (all strategies), inbox, auth."""
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
    api._runtime = None
    return TestClient(api.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["notifier"] == "mock"
    assert "default" in body["projects"]


@pytest.mark.parametrize("strategy", ["fanout", "pipeline", "critic"])
def test_run_all_strategies(client, strategy):
    r = client.post("/run", json={"text": f"hello {strategy}", "project": "demo", "strategy": strategy})
    assert r.status_code == 200
    body = r.json()
    assert body["task"]["status"] == "completed"
    assert body["task"]["result"]
    assert [e["to"] for e in body["events"]][0] == "queued"


def test_run_invalid_strategy(client):
    r = client.post("/run", json={"text": "x", "strategy": "bogus"})
    assert r.status_code == 422


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
