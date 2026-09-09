# Current Status Snapshot

<!-- generated-by: repo-harness refresh-current-status v1 -->

> **Status**: Phase 1-5 delivered + HTTP integration tests + Native Xero connector (Phase A)
> **Reason**: Added OAuth2 token manager, Xero native connector, vendor registry, and 9 new tests

Implemented (all tests for new code green, see tests/test_domains.py,
tests/test_harness.py, tests/test_intent_router.py):

- Phase 0 Harness: `src/hermes/harness/` (guardrails with citation rules,
  EvaluationRegistry/HarnessEvaluator, lifecycle_success_rate).
- Phase 1 KB + Second Brain: `src/hermes/knowledge/` (SQLite FTS5 store,
  multi-tenant, personal-scope owner isolation verified by tests).
- Phase 2 Advisory Council: `src/hermes/advisor/` (parallel personas,
  stub-safe, grounded-context aware).
- Phase 3 Ops Hub: `src/hermes/ops/` (real HTTP connectors via env vars,
  severity sort, `_http_fetch` with graceful error handling).
- Phase 4 Competitor Intel: `src/hermes/competitor/` (collect→analyze→brief,
  injectable fetcher, citation-carrying brief, `POST /competitor/watch` API).
- Wiring: router intents/agents, runtime.services(), API routes, Telegram
  dispatch + PDF→Second Brain fallback. README + docs/spec.md updated.
- EvaluationRegistry wired into async_engine orchestrators: every workflow
  task execution (domain, success, latency) now feeds /harness/metrics
  automatically via a module-level default registry set from runtime.
  Added set_default_eval_registry() + orchestrator on_done/on_fail recording
  (guarded, zero-cost when unused). Locked in by
  tests/test_harness.py::test_orchestrator_records_into_eval_registry.
- HTTP Integration Tests: Added 8 new tests in tests/test_domains.py covering
  Ops Hub HTTP connector (_http_fetch), Competitor API endpoints (watch/brief),
  and full competitor flow with real HTTP fetcher.
- Native Vendor Connectors (Phase A + Xero pilot):
  - `src/hermes/ops/auth.py` — OAuth2TokenManager with refresh-token flow,
    in-memory token cache, auto-refresh on 401, vendor_request() helper.
  - `src/hermes/ops/vendors/` — vendor registry pattern (register_vendor/get_vendor_connectors).
  - `src/hermes/ops/vendors/xero.py` — XeroConnector: fetches overdue invoices,
    maps to attention items with severity (critical/high/medium/low), handles
    Xero date formats and pagination-ready.
  - `src/hermes/ops/connectors.py` — build_connector() now tries native vendor
    connector first, falls back to generic HTTP.
  - 9 new tests: OAuth2 env/config/refresh, Xero mapping (overdue/critical,
    paid/skipped, authorised/low), vendor priority over generic, fallback.

Result: `pytest tests/` → 383 passed, 6 skipped, 0 failed.

## Ops Hub API Configuration

The Ops Hub connectors read from these environment variables:
- `HERMES_CRM_API_URL` — CRM API endpoint (Salesforce, HubSpot, Zoho, ...)
- `HERMES_INVOICING_API_URL` — Invoicing API endpoint (Xero, QuickBooks, FreshBooks, ...)
- `HERMES_CALENDAR_API_URL` — Calendar API endpoint (Google Calendar, Microsoft Graph, ...)
- `HERMES_INBOX_API_URL` — Inbox/Email API endpoint (Gmail, Outlook, ...)

Each API should return JSON array format:
```json
[
  {
    "summary": "Mô tả vấn đề",
    "severity": "high|critical|medium|low",
    "due_at": "2026-09-09T16:00:00Z",
    "action_hint": "Hành động cần làm"
  }
]
```

## Competitor API

Add targets via POST /competitor/watch:
```json
{
  "competitor": "Dell",
  "urls": ["https://www.dell.com/news", "https://www.dell.com/pricing"],
  "feeds": ["https://www.dell.com/rss"]
}
```

Then query brief via GET /competitor/brief or Telegram: "brief về Dell" / "đối thủ cạnh tranh"

