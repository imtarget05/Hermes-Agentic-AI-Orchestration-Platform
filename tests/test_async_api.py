from fastapi.testclient import TestClient

import hermes.async_api as api


def _reset(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_ASYNC_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("HERMES_ASYNC_MODE", "memory")
    api._RUNTIME = None


def test_health(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    r = TestClient(api.app).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_submit_and_query_workflow(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    c = TestClient(api.app)
    r = c.post("/async/run", json={"nodes": [
        {"task_id": "r1", "task_type": "research"},
        {"task_id": "a1", "task_type": "analyze", "deps": ["r1"]},
        {"task_id": "rpt", "task_type": "report", "deps": ["a1"]},
    ]})
    assert r.status_code == 200
    body = r.json()
    assert body["dispatched"] == ["r1"]  # only the root is dispatched immediately
    assert body["total"] == 3
    wf = body["workflow_id"]
    state = c.get(f"/async/workflows/{wf}")
    assert state.status_code == 200
    assert state.json()["task_count"] == 3


def test_invalid_task_type_rejected(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    c = TestClient(api.app)
    r = c.post("/async/run", json={"nodes": [
        {"task_id": "x", "task_type": "bogus"}]})
    assert r.status_code == 422


def test_validate_empty_nodes(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    r = TestClient(api.app).post("/async/run", json={"nodes": []})
    assert r.status_code == 422


def test_workflow_not_found(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    r = TestClient(api.app).get("/async/workflows/nope")
    assert r.status_code == 404


def test_metrics_endpoint_without_prometheus(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    r = TestClient(api.app).get("/metrics")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        # queue depth gauge is always set before exposition
        assert "hermes_task_queue_depth" in r.text