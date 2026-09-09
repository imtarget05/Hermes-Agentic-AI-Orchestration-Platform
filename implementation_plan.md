# Implementation Plan — Mở rộng Hermes từ Procurement sang 5 hệ thống business automation

## [Overview]

Mở rộng Hermes từ platform chỉ xử lý mua sắm (procurement) thành orchestration platform đa domain, tích hợp 5 hệ thống lấy từ nội dung 9 ảnh: **① AI Advisory Council** (persona cố vấn), **② Business Operations Hub** (CRM/invoicing/calendar/inbox), **③ Competitive Intelligence** (collect → analyze → weekly brief), **④ Team Knowledge Base** (SOP/quyết định/lịch sử KH cho cả team), **⑤ Second Brain** (hợp đồng/bảo hiểm/tài liệu cá nhân — query bất kỳ lúc nào) — kèm nền tảng **Harness Engineering** (Context, Tools & Guardrails, Evaluation & Observability) áp dụng cho toàn bộ domain mới.

Nguyên tắc thiết kế: tái dùng tối đa hạ tầng hiện có thay vì viết mới:
- `Intent` enum + `RoutingPlan` (`src/hermes/router/routing_plan.py`) — thêm intent mới, router rule-based hiện tại giữ nguyên pattern.
- `RagIndex` BM25-lite (`src/hermes/rag/__init__.py`) — nâng thành RAG multi-tenant cho Knowledge Base + Second Brain.
- `IngestionPipeline` + extractors (`src/hermes/ingestion/`) — dùng cho mọi tài liệu đầu vào (PDF, notes, web).
- DAG engine (`src/hermes/async_engine/`) — chạy DAG cho từng domain.
- Telegram chat (`src/hermes/telegram_chat/`) + approval (`src/hermes/messaging/`) — giao diện người dùng thống nhất.
- API FastAPI (`src/hermes/api.py`) — thêm route theo domain.

## [Types]

Thêm vào `src/hermes/router/routing_plan.py`:
```python
class Intent(str, Enum):
    # giữ nguyên 8 intent cũ
    ASK_ADVISOR = "ask_advisor"            # ① hỏi persona cố vấn
    OPS_STATUS = "ops_status"              # ② "hôm nay cần chú ý gì"
    OPS_CONNECT = "ops_connect"            # ② kết nối CRM/calendar/invoice/inbox
    COMPETITOR_BRIEF = "competitor_brief"  # ③ yêu cầu brief đối thủ
    COMPETITOR_WATCH = "competitor_watch"  # ③ thêm theo dõi đối thủ
    KB_QUERY = "kb_query"                  # ④ hỏi knowledge base team
    KB_INGEST = "kb_ingest"                # ④ nạp SOP/docs
    BRAIN_QUERY = "brain_query"            # ⑤ hỏi tài liệu cá nhân
    BRAIN_INGEST = "brain_ingest"          # ⑤ nạp tài liệu cá nhân

NEW_AGENTS = {
    "ADVISOR": "advisor",
    "OPS_AGGREGATOR": "ops_aggregator",
    "COMPETITOR_COLLECT": "competitor_collect",
    "COMPETITOR_ANALYZE": "competitor_analyze",
    "KB_ANSWER": "kb_answer",
    "BRAIN_ANSWER": "brain_answer",
}
```

Schemas mới:
- `src/hermes/knowledge/schemas.py` — `KnowledgeDoc` (doc_id, tenant_id, scope: `team|personal`, category, title, source_uri, content, ingested_at, ttl_days, owner_user_id).
- `src/hermes/advisor/schemas.py` — `AdvisorPersona`, `AdvisorOpinion`.
- `src/hermes/ops/schemas.py` — `OpsSource` (crm|invoicing|calendar|inbox|custom), `OpsAttentionItem`.
- `src/hermes/competitor/schemas.py` — `CompetitorTarget`, `CompetitorFinding`, `WeeklyBrief`.
- `src/hermes/harness/schemas.py` — `EvalMetric`.

## [Files]

**Thư mục mới:**
- `src/hermes/knowledge/` — schemas, service (ingest + query đa tenant), store (SQLite `hermes_knowledge.db`, bảng `kb_docs` + FTS5).
- `src/hermes/advisor/` — schemas, personas (mặc định, pattern như procurement specialist), council (nhiều persona song song → tổng hợp).
- `src/hermes/ops/` — schemas, connectors (interface + mock CRM/calendar/invoice/inbox — offline-first), hub (aggregate items cần chú ý).
- `src/hermes/competitor/` — schemas, collect (tái dùng `scraper/` fetcher + policy), analyze, brief (weekly brief deterministic).
- `src/hermes/harness/` — schemas, eval (metrics từ TaskEvent + tracing), guardrails (rules per-domain).

**File sửa:**
- `src/hermes/router/routing_plan.py` — Intent + agent names mới.
- `src/hermes/router/intent_router.py` — rule VN/EN cho 9 intent mới, giữ priority order hiện tại.
- `src/hermes/telegram_chat/handler.py` + `intent.py` — dispatch intent mới; format trả lời per-domain.
- `src/hermes/api.py` — routes: `/knowledge/ingest|query`, `/advisor/ask`, `/ops/sources|attention`, `/competitor/watch|brief`, `/harness/metrics` — check `X-API-Token` như cũ.
- `src/hermes/runtime.py` — đăng ký service mới.
- `docs/spec.md`, `README.md` — cập nhật.

## [Functions]

**Mới:**
- `knowledge/service.py`: `ingest(doc) -> str`, `query(tenant_id, user_id, text, scope) -> KnowledgeAnswer` — `personal` lọc theo owner, `team` cho cả tenant; trả lời deterministic kèm evidence.
- `advisor/council.py`: `ask_council(question, personas) -> list[AdvisorOpinion]` — mỗi persona 1 node DAG song song; LLM gateway có sẵn; stub deterministic khi thiếu creds.
- `ops/hub.py`: `collect_attention(tenant_id) -> list[OpsAttentionItem]` — query connector, sort severity + due_at.
- `competitor/collect.py`: `collect(targets)`; `brief.py`: `build_weekly_brief(findings)` — group theo competitor + kind, chỉ tóm tắt từ findings có evidence_uri.
- `harness/eval.py`: `compute_metrics(task_ids)`; `harness/guardrails.py`: `check_output(domain, output)`.

**Sửa:** `intent_router.classify()` thêm 9 nhánh trước CHITCHAT; telegram handler thêm handler per intent; PDF/photo → BRAIN_INGEST/KB_INGEST theo scope.

**Không xóa function nào** — procurement giữ nguyên (regression bắt buộc xanh).

## [Classes]

- **Mới:** `KnowledgeService`, `AdvisorPersona`, `AdvisoryCouncil`, `OpsConnector` (abstract) + `MockCRMConnector`, `MockCalendarConnector`, `MockInvoicingConnector`, `MockInboxConnector`, `CompetitorCollector`, `CompetitorAnalyzer`, `BriefBuilder`, `HarnessEvaluator`, `OutputGuardrail`.
- **Sửa:** `IntentRouter` (thêm rules), `HermesRuntime` (đăng ký service), telegram handler.
## [Dependencies]

**Không thêm dependency mới** cho phase này: httpx (fetch) đã có, SQLite FTS5 built-in, pypdf đã có. APScheduler cho weekly brief định kỳ → phase sau (hiện trigger thủ công qua API/Telegram "brief tuần này").

## [Testing]

- `tests/test_knowledge_service.py` — ingest/query multi-tenant, personal vs team scope, citation.
- `tests/test_advisor_council.py` — persona song song, stub LLM offline, guardrail chặn trả lời không citation.
- `tests/test_ops_hub.py` — mock connectors, severity sort, sources CRUD.
- `tests/test_competitor.py` — collect từ fixture HTML (không mạng), brief deterministic, chặn giá không evidence.
- `tests/test_harness_eval.py` — metrics từ TaskEvent fixture.
- `tests/test_intent_router.py` (sửa) — case mới cho 9 intent.
- `tests/test_api.py` (sửa) — route mới + auth.
- Chạy `pytest tests/ -q` — mục tiêu toàn bộ xanh gồm regression procurement (152 test hiện tại).

## [Implementation Order]

1. **Phase 0 — Harness nền:** `harness/` (schemas, guardrails, eval) + wire vào tracing hiện có.
2. **Phase 1 — Second Brain + Team Knowledge Base (4 & 5):** chung nền knowledge/ — `KnowledgeService` + store FTS5 + intent KB/BRAIN + Telegram ingest PDF + API routes. *Giá trị cao nhất, tái dùng RAG/ingestion nhiều nhất.*
3. **Phase 2 — AI Advisory Council (1):** personas + council DAG song song + guardrail citation.
4. **Phase 3 — Business Operations Hub (2):** connector interface + mock connectors + ops_aggregator + API + Telegram.
5. **Phase 4 — Competitive Intelligence (3):** collect (tái dùng scraper) → analyze → weekly brief + watch.
6. **Phase 5 — Tích hợp ngang:** intent router hoàn chỉnh, README/spec, full regression, cập nhật `tasks/current.md` + handoff.

Mỗi phase kết thúc bằng: pytest xanh → cập nhật task/notes → commit riêng.