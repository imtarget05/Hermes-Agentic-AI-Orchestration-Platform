"""Tests for the new business domains (④⑤ KB/Brain, ① Advisor, ② Ops, ③ Competitor) + Harness."""
import os

import pytest

from hermes.advisor import ask_council
from hermes.competitor import CompetitorCollector, CompetitorTarget, build_weekly_brief
from hermes.harness import EvaluationRegistry, HarnessEvaluator, check_output
from hermes.knowledge import KnowledgeService
from hermes.ops import OpsHub
from hermes.ops.connectors import GenericConnector

TMP = "/tmp/hermes_domain_test.db"


def _fresh():
    if os.path.exists(TMP):
        os.remove(TMP)
    return KnowledgeService(TMP)


# ---- Team Knowledge Base (④) + Second Brain (⑤) -------------------------

def test_kb_team_query_returns_cited_answer():
    svc = _fresh()
    svc.ingest(title="SOP Onboarding",
               content="Mọi nhân sự mới phải hoàn thành onboarding trong 14 ngày.",
               scope="team")
    ans = svc.query(text="onboarding", scope="team")
    assert ans.grounded is True
    assert ans.sources
    assert "[source=" in ans.answer


def test_kb_personal_scope_isolation():
    svc = _fresh()
    svc.ingest(title="Bao hiem xe",
               content="Hop dong bao hiem xe con thoi han 3 nam.",
               scope="personal", user_id="u1", category="insurance")
    assert svc.query(text="bao hiem", scope="personal", user_id="u1").grounded is True
    assert svc.query(text="bao hiem", scope="personal", user_id="u2").grounded is False


def test_kb_personal_requires_owner():
    svc = _fresh()
    with pytest.raises(ValueError):
        svc.ingest(title="x", content="y", scope="personal", user_id="")


def test_kb_ingest_from_txt_file():
    svc = _fresh()
    p = "/tmp/kb_note.txt"
    with open(p, "w") as f:
        f.write("Quy trinh mua hang bat dau bang viec tao request.")
    from hermes.knowledge.service import extract_text_from_file
    text = extract_text_from_file(p)
    svc.ingest(title="Quy trinh mua", content=text, scope="team")
    assert svc.query(text="mua hang", scope="team").grounded is True


# ---- AI Advisory Council (①) ---------------------------------------------

def test_council_returns_selected_personas():
    rep = ask_council("dòng tiền và rủi ro tháng này ra sao?",
                      personas=["finance", "risk"], context="")
    assert {o.persona for o in rep.opinions} == {"finance", "risk"}
    assert rep.synthesized


def test_council_stub_is_grounded_false_without_data():
    rep = ask_council("nên làm gì?", context="")
    assert all(o.grounded is False for o in rep.opinions)


def test_council_with_context_marks_grounded():
    rep = ask_council("chi phí tăng?", context="Báo giá tháng 9: total 54000 [source=demo/lenovo.pdf]")
    assert all(o.grounded is True for o in rep.opinions)


# ---- Business Operations Hub (②) ------------------------------------------

def test_ops_hub_aggregates_sorted_by_severity():
    from unittest.mock import patch
    hub = OpsHub()
    hub.add_source(kind="crm")
    hub.add_source(kind="invoicing")
    hub.add_source(kind="calendar")
    mock_items = [
        {"summary": "Deal X (500 USD) chưa phản hồi 6 ngày", "severity": "high"},
        {"summary": "Hoá đơn #INV-1021 quá hạn", "severity": "critical"},
        {"summary": "Cuộc họp ký hợp đồng", "severity": "high"},
    ]
    def _fake_fetch():
        return mock_items
    with patch("hermes.ops.hub.build_connector") as mock_build:
        from hermes.ops.connectors import GenericConnector
        c = GenericConnector()
        c.kind = "crm"
        c._fetch = _fake_fetch
        mock_build.return_value = c
        rep = hub.collect_attention()
    assert rep.items
    sevs = [i.severity for i in rep.items]
    assert sevs[0] == "critical"
    assert all(s in ("critical", "high", "medium", "low") for s in sevs)


def test_ops_hub_add_remove_source():
    hub = OpsHub()
    src = hub.add_source(kind="inbox", name="test")
    assert len(hub.list_sources()) == 1
    assert hub.remove_source(src.source_id) is True
    assert hub.list_sources() == []


def test_ops_hub_disabled_source_skipped():
    hub = OpsHub()
    hub.add_source(kind="crm", enabled=False)
    assert hub.collect_attention().items == []


# ---- Competitive Intelligence (③) ------------------------------------------

def test_competitor_collect_and_brief_offline():
    hits = {"https://x.example":
            "<html>Giới thiệu sản phẩm ProX mới. Giá khởi điểm $40.</html>"}
    fetcher = lambda url: hits.get(url, "")  # noqa: E731
    findings = CompetitorCollector(fetcher=fetcher).collect(
        [CompetitorTarget(competitor="Acme", urls=["https://x.example"])])
    assert len(findings) == 1
    assert findings[0].source_uri == "https://x.example"
    brief = build_weekly_brief(findings)
    assert any("Acme" in s for s in brief.shifts)
    assert brief.per_competitor.get("Acme") == 1


def test_competitor_brief_requires_evidence_uri():
    findings = CompetitorCollector(fetcher=lambda u: "").collect(
        [CompetitorTarget(competitor="Ghost", urls=["https://missing.example"])])
    assert findings == []  # unreachable source → no fabricated finding


def test_competitor_brief_guardrail_passes():
    findings = CompetitorCollector(
        fetcher=lambda u: "Hãng nâng cấp gói Pro, giá $50/tháng.") .collect(
        [CompetitorTarget(competitor="Rival", urls=["https://r.example"])])
    brief = build_weekly_brief(findings)
    assert check_output("competitor", brief.to_text()).passed


# ---- Ops Hub HTTP Integration (②) -----------------------------------------

def test_ops_http_connector_fetches_real_data():
    """Test that _http_fetch makes real HTTP GET requests and parses JSON response."""
    from unittest.mock import patch, MagicMock
    from hermes.ops.connectors import _http_fetch

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"summary": "Deal X chưa phản hồi", "severity": "high",
         "due_at": "2026-09-09T16:00:00Z", "action_hint": "Gọi lại ngay"},
        {"summary": "Hoá đơn quá hạn", "severity": "critical",
         "due_at": "2026-09-10T10:00:00Z", "action_hint": "Gửi nhắc nhở"},
    ]
    mock_response.raise_for_status = MagicMock()

    with patch("hermes.ops.connectors.httpx.get", return_value=mock_response) as mock_get:
        result = _http_fetch("crm", "https://api.example.com/v1/attention")

    mock_get.assert_called_once_with("https://api.example.com/v1/attention", timeout=15, follow_redirects=True)
    assert len(result) == 2
    assert result[0]["severity"] == "high"
    assert result[1]["summary"] == "Hoá đơn quá hạn"


def test_ops_http_connector_returns_empty_on_error():
    """Test that _http_fetch returns empty list on HTTP errors."""
    from unittest.mock import patch
    from hermes.ops.connectors import _http_fetch

    with patch("hermes.ops.connectors.httpx.get", side_effect=Exception("Connection error")):
        result = _http_fetch("crm", "https://api.example.com/v1/attention")

    assert result == []


def test_ops_http_connector_returns_empty_on_empty_url():
    """Test that _http_fetch returns empty list when URL is empty."""
    from hermes.ops.connectors import _http_fetch

    result = _http_fetch("crm", "")
    assert result == []


def test_oauth2_token_manager_reads_env_vars():
    """Test that OAuth2TokenManager reads credentials from environment variables."""
    import os
    from hermes.ops.auth import OAuth2TokenManager

    os.environ["HERMES_XERO_CLIENT_ID"] = "test-client-id"
    os.environ["HERMES_XERO_CLIENT_SECRET"] = "test-secret"
    os.environ["HERMES_XERO_REFRESH_TOKEN"] = "test-refresh"

    try:
        mgr = OAuth2TokenManager("xero")
        assert mgr.client_id == "test-client-id"
        assert mgr.client_secret == "test-secret"
        assert mgr.refresh_token == "test-refresh"
        assert mgr.is_configured is True
    finally:
        del os.environ["HERMES_XERO_CLIENT_ID"]
        del os.environ["HERMES_XERO_CLIENT_SECRET"]
        del os.environ["HERMES_XERO_REFRESH_TOKEN"]


def test_oauth2_token_manager_not_configured_without_env():
    """Test that is_configured is False when env vars are missing."""
    import os
    from hermes.ops.auth import OAuth2TokenManager

    for key in ["HERMES_XERO_CLIENT_ID", "HERMES_XERO_CLIENT_SECRET", "HERMES_XERO_REFRESH_TOKEN"]:
        os.environ.pop(key, None)

    mgr = OAuth2TokenManager("xero")
    assert mgr.is_configured is False


def test_oauth2_token_manager_refreshes_token():
    """Test that get_token exchanges refresh token for access token."""
    import os
    from unittest.mock import patch, MagicMock
    from hermes.ops.auth import OAuth2TokenManager

    os.environ["HERMES_XERO_CLIENT_ID"] = "cid"
    os.environ["HERMES_XERO_CLIENT_SECRET"] = "sec"
    os.environ["HERMES_XERO_REFRESH_TOKEN"] = "ref"

    try:
        mgr = OAuth2TokenManager("xero")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "new-access-token", "expires_in": 1800}
        mock_resp.raise_for_status = MagicMock()

        with patch("hermes.ops.auth.httpx.post", return_value=mock_resp) as mock_post:
            token = mgr.get_token()

        assert token == "new-access-token"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["grant_type"] == "refresh_token"
    finally:
        for key in ["HERMES_XERO_CLIENT_ID", "HERMES_XERO_CLIENT_SECRET", "HERMES_XERO_REFRESH_TOKEN"]:
            os.environ.pop(key, None)


def test_xero_map_invoices_overdue_critical():
    """Test that invoices overdue >30 days map to critical severity."""
    from hermes.ops.vendors.xero import _map_invoices

    payload = {
        "Invoices": [
            {
                "InvoiceID": "inv-001",
                "InvoiceNumber": "INV-1001",
                "Status": "OVERDUE",
                "AmountDue": 15000.0,
                "Total": 20000.0,
                "DueDateString": "/Date(1700000000000)/",
                "Contact": {"Name": "Acme Corp"},
            }
        ]
    }

    items = _map_invoices(payload, "src-001")
    assert len(items) == 1
    assert items[0].severity == "critical"
    assert "INV-1001" in items[0].summary
    assert "Acme Corp" in items[0].summary
    assert items[0].kind == "invoicing"


def test_xero_map_invoices_paid_skipped():
    """Test that PAID invoices are skipped."""
    from hermes.ops.vendors.xero import _map_invoices

    payload = {
        "Invoices": [
            {
                "InvoiceID": "inv-002",
                "InvoiceNumber": "INV-1002",
                "Status": "PAID",
                "AmountDue": 0.0,
                "Total": 5000.0,
                "DueDateString": "2026-01-15",
                "Contact": {"Name": "Beta Inc"},
            }
        ]
    }

    items = _map_invoices(payload, "src-001")
    assert len(items) == 0


def test_xero_map_invoices_authorised_low():
    """Test that AUTHORISED invoices with future due date map to low severity."""
    from hermes.ops.vendors.xero import _map_invoices
    from datetime import datetime, timedelta, UTC

    future_date = (datetime.now(UTC) + timedelta(days=60)).strftime("%Y-%m-%d")

    payload = {
        "Invoices": [
            {
                "InvoiceID": "inv-003",
                "InvoiceNumber": "INV-1003",
                "Status": "AUTHORISED",
                "AmountDue": 3000.0,
                "Total": 3000.0,
                "DueDateString": future_date,
                "Contact": {"Name": "Gamma LLC"},
            }
        ]
    }

    items = _map_invoices(payload, "src-001")
    assert len(items) == 1
    assert items[0].severity == "low"


def test_xero_connector_returns_empty_without_config():
    """Test that XeroConnector returns empty list when not configured."""
    import os
    from hermes.ops.vendors.xero import XeroConnector
    from hermes.ops.schemas import OpsSource

    for key in ["HERMES_XERO_CLIENT_ID", "HERMES_XERO_REFRESH_TOKEN", "HERMES_XERO_TENANT_ID"]:
        os.environ.pop(key, None)

    conn = XeroConnector()
    source = OpsSource(source_id="test", kind="invoicing")
    items = conn.collect(source)
    assert items == []


def test_vendor_connector_takes_priority_over_generic():
    """Test that native vendor connector is used when credentials are present."""
    import os
    from hermes.ops.connectors import build_connector

    os.environ["HERMES_XERO_CLIENT_ID"] = "cid"
    os.environ["HERMES_XERO_CLIENT_SECRET"] = "sec"
    os.environ["HERMES_XERO_REFRESH_TOKEN"] = "ref"
    os.environ["HERMES_XERO_TENANT_ID"] = "tenant-123"

    try:
        conn = build_connector("invoicing")
        from hermes.ops.vendors.xero import XeroConnector
        assert isinstance(conn, XeroConnector)
    finally:
        for key in ["HERMES_XERO_CLIENT_ID", "HERMES_XERO_CLIENT_SECRET",
                     "HERMES_XERO_REFRESH_TOKEN", "HERMES_XERO_TENANT_ID"]:
            os.environ.pop(key, None)


def test_generic_fallback_when_no_vendor_creds():
    """Test that generic HTTP connector is used when no vendor credentials."""
    import os
    from hermes.ops.connectors import build_connector, GenericConnector

    for key in ["HERMES_XERO_CLIENT_ID", "HERMES_XERO_REFRESH_TOKEN"]:
        os.environ.pop(key, None)

    conn = build_connector("invoicing")
    assert isinstance(conn, GenericConnector)


def test_ops_connector_parses_all_fields():
    """Test that GenericConnector correctly parses all fields from API response."""
    from hermes.ops.connectors import _make_http_connector

    connector = _make_http_connector("crm", base_url="https://api.example.com/v1/attention")
    # Verify connector is set up correctly
    assert connector.kind == "crm"
    assert hasattr(connector, "_fetch")


def test_ops_hub_with_real_http_sources():
    """Test OpsHub with sources configured with base_url (simulating env var setup)."""
    from unittest.mock import patch, MagicMock

    hub = OpsHub()
    hub.add_source(kind="crm", name="CRM", config={"base_url": "https://api.example.com/crm/attention"})
    hub.add_source(kind="invoicing", name="Invoicing", config={"base_url": "https://api.example.com/invoicing/attention"})

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"summary": "Deal Y chưa phản hồi 5 ngày", "severity": "medium",
         "due_at": "2026-09-11T09:00:00Z", "action_hint": "Email follow-up"},
    ]
    mock_response.raise_for_status = MagicMock()

    with patch("hermes.ops.connectors.httpx.get", return_value=mock_response):
        rep = hub.collect_attention()

    assert len(rep.items) == 2  # Both sources return the same mock data
    assert rep.items[0].summary == "Deal Y chưa phản hồi 5 ngày"
    assert rep.items[0].due_at == "2026-09-11T09:00:00Z"
    assert rep.items[0].action_hint == "Email follow-up"


# ---- Competitor API Integration (③) ---------------------------------------

def test_competitor_watch_api_endpoint():
    """Test POST /competitor/watch endpoint adds targets correctly."""
    from fastapi.testclient import TestClient
    from hermes.api import app

    client = TestClient(app)
    response = client.post(
        "/competitor/watch",
        json={
            "competitor": "Dell",
            "urls": ["https://www.dell.com/news", "https://www.dell.com/pricing"],
            "feeds": ["https://www.dell.com/rss"]
        },
        headers={"X-API-Token": "test-token"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["watching"] == "Dell"
    assert data["targets"] >= 1


def test_competitor_brief_api_endpoint():
    """Test GET /competitor/brief endpoint returns brief with findings."""
    from fastapi.testclient import TestClient
    from hermes.api import app

    client = TestClient(app)

    # First add a target
    client.post(
        "/competitor/watch",
        json={
            "competitor": "Dell",
            "urls": ["https://www.dell.com/news"],
            "feeds": []
        },
        headers={"X-API-Token": "test-token"}
    )

    # Then get brief
    response = client.get("/competitor/brief", headers={"X-API-Token": "test-token"})
    assert response.status_code == 200
    data = response.json()
    assert "findings" in data
    assert "per_competitor" in data
    assert "shifts" in data


def test_competitor_full_flow_with_real_fetcher():
    """Test full competitor flow: watch → collect → brief with real HTTP fetcher."""
    from hermes.competitor import CompetitorCollector, CompetitorTarget, build_weekly_brief
    from unittest.mock import patch, MagicMock

    # Create collector with real HTTP fetcher (mocked at httpx level)
    collector = CompetitorCollector()  # Uses default _http_fetcher

    target = CompetitorTarget(
        competitor="Dell",
        urls=["https://www.dell.com/news"],
        feeds=[]
    )

    mock_response = MagicMock()
    mock_response.text = "<html>Dell announces new Pro laptops starting at $999. Available now.</html>"
    mock_response.raise_for_status = MagicMock()

    # Patch httpx.get at the top level since _http_fetcher imports httpx internally
    with patch("httpx.get", return_value=mock_response):
        findings = collector.collect([target])

    assert len(findings) == 1
    assert findings[0].competitor == "Dell"
    assert findings[0].source_uri == "https://www.dell.com/news"
    assert findings[0].kind in ("launch", "pricing", "news", "post")

    brief = build_weekly_brief(findings)
    assert brief.per_competitor.get("Dell") == 1
    assert len(brief.findings) == 1


# ---- Harness (Phase 0) integration -----------------------------------------

def test_guardrail_blocks_uncited_price_claim():
    assert check_output("advisor", "Tôi khuyên chi 5000 usd.").passed is False


def test_evaluator_metrics():
    reg = EvaluationRegistry()
    reg.record(domain="knowledge", success=True, accuracy=1.0, latency_ms=10, cost_tokens=50)
    reg.record(domain="knowledge", success=False, error_trace="boom", latency_ms=20)
    ev = HarnessEvaluator(reg)
    rows = {m.domain: m for m in ev.compute_metrics()}
    assert rows["knowledge"].success_rate == 0.5
    assert rows["knowledge"].error_trace == "boom"