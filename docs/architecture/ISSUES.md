# Phase 2 — Issue Diagnosis

## Issue Registry

All issues identified during architecture audit. Format per issue:
- **Issue ID**: ISSUE-{NNN}
- **Component/Module**: affected file(s) or module
- **Category**: see categorization list
- **Severity**: CRITICAL / HIGH / MEDIUM / LOW
- **Evidence**: file:line or file:class.function
- **Impact**: what happens if not fixed
- **Confidence**: HIGH / MEDIUM / LOW / NEEDS_VERIFICATION
- **Status**: OPEN / IN_PROGRESS / RESOLVED / BLOCKED / CLARIFIED / ACCEPTABLE / NOT_A_PROBLEM

---

## ISSUE-001: Duplicate TaskStore Implementations

| Field | Value |
|-------|-------|
| **Component/Module** | `src/hermes/tasks/store.py` (legacy) + `src/hermes/async_engine/store.py` (new) |
| **Category** | Architectural Inconsistency / Duplicated Logic |
| **Severity** | HIGH |
| **Evidence** | Two separate files with overlapping responsibility: `TaskStore` vs `AsyncTaskStore` |
| **Impact** | Developers confused about which to use; potential for inconsistent behavior; maintenance burden doubled |
| **Confidence** | HIGH |
| **Status** | CLARIFIED |

### Details
- **Legacy**: `src/hermes/tasks/store.py` — simpler interface, validates transitions, uses SQLite
- **Newer**: `src/hermes/async_engine/store.py` — full CRUD, idempotency CAS, outbox pattern, SQLite/Postgres
- Both handle task persistence but with different APIs
- No clear migration path from legacy to new

---

## ISSUE-002: Mixed Concerns in Procurement Handlers

| Field | Value |
|-------|-------|
| **Component/Module** | `src/hermes/procurement/handlers.py` |
| **Category** | Mixed Responsibilities / Business Logic in Infrastructure |
| **Severity** | MEDIUM |
| **Evidence** | `handlers.py` — handlers compute business scores AND perform RAG lookups AND call tools |
| **Impact** | Handlers are hard to test in isolation; RAG coupling makes business logic less portable |
| **Confidence** | HIGH |
| **Status** | ACCEPTABLE |
| **Note** | RAG helper (_rag_lines) already exists and is used appropriately by handlers. Architecture is acceptable trade-off. |

### Details
- `_sibling_results()` reads from store inside handler (cross-cutting concern)
- RAG evidence lookup happens directly in handler (infrastructure)
- Tool execution mixed with policy evaluation
- Would benefit from separating: input preparation → business logic → output formatting

**Reassessment (2026-09-07)**:
- Current architecture already follows suggested pattern
- `_rag_lines(task, query, top_k=2)` helper function exists (lines 134-146) and is used appropriately
- All leaf handlers (_price, _vendor, _contract, _spec, _verification) call _rag_lines() appropriately
- Mixed concerns are architecturally acceptable in this case — handlers need RAG evidence inline to produce complete outputs
- Separating RAG would require significant DAG refactoring (passing evidence through context)
- Architecture is a reasonable trade-off between testability and simplicity

---

## ISSUE-003: Approval Resolution Scattered Across Modules

| Field | Value |
|-------|-------|
| **Component/Module** | `src/hermes/messaging/approval_bot.py` + `src/hermes/async_engine/loops/hitl.py` |
| **Category** | Unclear Ownership / Distributed Logic |
| **Severity** | MEDIUM |
| **Evidence** | `approval_bot.py:resolve_approval()` vs `hitl.py:ApprovalStore` + `await_decision()` |
| **Impact** | Two different interfaces for approval; potential for inconsistent state; harder to reason about |
| **Confidence** | HIGH |
| **Status** | OPEN |

### Details
- `approval_bot.py` handles RBAC enforcement and Telegram callback queries
- `hitl.py` handles SQLite-backed approval storage and polling
- Both interact with approval state but through different APIs
- No single source of truth for approval lifecycle

---

## ISSUE-004: Router Intent Classification is Purely Keyword-Based

| Field | Value |
|-------|-------|
| **Component/Module** | `src/hermes/router/intent_router.py` |
| **Category** | Limited Functionality / Brittle Classification |
| **Severity** | MEDIUM |
| **Evidence** | `classify_intent()` uses hardcoded keyword matching with priority ordering |
| **Impact** | Edge cases and ambiguous requests may be misclassified; no LLM fallback for uncertain cases |
| **Confidence** | HIGH |
| **Status** | OPEN |

### Details
- Intent classification: HELP, INBOX, TASK_DETAIL, APPROVAL, PRICE_QUESTION, SIMPLE_TASK, COMPARE_TASK, PROCUREMENT_DECISION, CHITCHAT
- Uses keyword priority: "help" → HELP, "approve" → APPROVAL, etc.
- No confidence scoring; returns best guess even if weak match
- README mentions "LLM classifier if creds available" but code shows keyword-only

---

## ISSUE-005: LLM Client Direct Import Pattern

| Field | Value |
|-------|-------|
| **Component/Module** | `src/hermes/llm/cloudflare.py` (usage patterns) |
| **Category** | Inappropriate Dependencies / Testability |
| **Severity** | LOW |
| **Evidence** | Need to verify if LLM is imported directly vs through gateway in intent_router.py |
| **Impact** | Harder to mock LLM in tests; less portable to different LLM providers |
| **Confidence** | HIGH |
| **Status** | NOT_A_PROBLEM |
| **Note** | Architecture verified correct - all LLM usage goes through gateway.py; CloudflareLLM only instantiated there |

### Details
- Gateway exists at `llm/gateway.py` for abstraction
- Verified all modules use `build_llm()` or `build_router_classifier()` from llm package
- CloudflareLLM is only instantiated in gateway.py (line 82)
- No direct CloudflareLLM imports found outside llm/ module

**Verification (2026-09-07)**:
- `CloudflareLLM` defined in `llm/cloudflare.py`
- `llm/__init__.py` re-exports `CloudflareLLM` but only for internal use
- `gateway.py` instantiates `CloudflareLLM` (line 82)
- All external modules use `build_llm()` or `build_router_classifier()`
- Direct CloudflareLLM imports (outside llm/): NONE found
- ISSUE-005 is NOT a problem - architecture is correct

---

## ISSUE-006: No Formal Error Classification Taxonomy

| Field | Value |
|-------|-------|
| **Component/Module** | `src/hermes/async_engine/retry.py` + `src/hermes/tools/__init__.py` |
| **Category** | Weak Error Handling / Unclear Failure Boundaries |
| **Severity** | MEDIUM |
| **Evidence** | `retry.py` — `_RETRYABLE_MARKERS` and `_NON_RETRYABLE_MARKERS` for classification; `tools/__init__.py` — FatalToolError vs RetryableToolError |
| **Impact** | Inconsistent retry behavior across the codebase; hard to add new error types |
| **Confidence** | HIGH |
| **Status** | OPEN |

### Details
- Error classification exists but is ad-hoc (string marker matching)
- No formal taxonomy or hierarchy
- Different error handling patterns in different layers

---

## ISSUE-007: Missing Test Coverage for Legacy TaskStore

| Field | Value |
|-------|-------|
| **Component/Module** | `src/hermes/tasks/store.py` |
| **Category** | Insufficient Test Coverage |
| **Severity** | MEDIUM |
| **Evidence** | README mentions tests for `test_async_store.py` but no mention of tests for legacy `tasks/store.py` |
| **Impact** | Legacy store has no verification of transition validation or event logging |
| **Confidence** | MEDIUM |
| **Status** | OPEN |

### Details
- AsyncTaskStore has dedicated tests: `test_async_store.py`
- Legacy TaskStore (in tasks/) appears untested
- If legacy store is still used by any code path, that path is uncovered

---

## ISSUE-008: Guardrails Implementation is Placeholder

| Field | Value |
|-------|-------|
| **Component/Module** | `src/hermes/async_engine/loops/guardrails.py` |
| **Category** | Weak Error Handling / Missing Validation |
| **Severity** | LOW |
| **Evidence** | `guardrails.py` — InputGuardrail, OutputGuardrail, ToolCallGuardrail — basic structure only |
| **Impact** | Limited protection against prompt injection, output manipulation |
| **Confidence** | HIGH |
| **Status** | OPEN |

### Details
- Guardrail classes exist with methods but implementation appears minimal
- README mentions guardrails as part of architecture but no mention of tests
- `web_search` tool is stub (not real implementation per README)

---

## Summary by Severity

| Severity | Count | Issue IDs |
|----------|-------|-----------|
| CRITICAL | 0 | — |
| HIGH | 1 | ISSUE-001 |
| MEDIUM | 4 | ISSUE-003, ISSUE-004, ISSUE-006, ISSUE-007 |
| LOW | 2 | ISSUE-005, ISSUE-008 |

## Summary by Category

| Category | Count | Issue IDs |
|----------|-------|-----------|
| Architectural Inconsistency | 1 | ISSUE-001 |
| Duplicated Logic | 1 | ISSUE-001 |
| Mixed Responsibilities | 1 | ISSUE-002 |
| Distributed Logic | 1 | ISSUE-003 |
| Brittle Classification | 1 | ISSUE-004 |
| Testability | 1 | ISSUE-005 |
| Weak Error Handling | 2 | ISSUE-006, ISSUE-008 |
| Insufficient Test Coverage | 1 | ISSUE-007 |

---

**Diagnosis Status**: PHASE 2 COMPLETE
**Artifacts Updated**: `.kilo/plans/1788797860048-phase1-architecture-audit.md`, `.kilo/plans/1788797860048-issues.md`
**Next Phase**: Refactor Plan → REFACTOR_PLAN.md
