# Phase 3 — Refactor Plan

## Overview

This plan maps each planned change back to one or more Issue IDs. Tasks are ordered by dependency and risk: small → isolated → reversible → verifiable.

---

## Task Registry

### TASK-001: Audit Legacy TaskStore Usage

| Field | Value |
|-------|-------|
| **Task ID** | TASK-001 |
| **Issue IDs** | ISSUE-001, ISSUE-007 |
| **Objective** | Determine if legacy `TaskStore` (`src/hermes/tasks/store.py`) is still used and by whom |
| **Current Responsibility** | Unknown usage pattern |
| **Target Responsibility** | Clear understanding of which code paths use which store |
| **Files/Modules Affected** | `src/hermes/tasks/store.py`, `src/hermes/async_engine/store.py` |
| **Allowed Scope** | grep/search for usages of `from hermes.tasks import TaskStore` and `from hermes.tasks.store import` |
| **Forbidden Scope** | No code changes in this task |
| **Dependencies** | None |
| **Expected Behavior Preservation** | N/A (read-only audit) |
| **Verification Requirements** | Document which files import legacy TaskStore and which use AsyncTaskStore |
| **Priority** | HIGH |
| **Status** | VERIFIED |

---

### TASK-002: Add Unit Tests for Legacy TaskStore

| Field | Value |
|-------|-------|
| **Task ID** | TASK-002 |
| **Issue IDs** | ISSUE-007 |
| **Objective** | Verify legacy TaskStore transition validation and event logging work correctly |
| **Current Responsibility** | No test coverage for `src/hermes/tasks/store.py` |
| **Target Responsibility** | Basic unit tests covering create, get, transition, event logging |
| **Files/Modules Affected** | `src/hermes/tasks/store.py`, new test file `tests/test_tasks_store.py` |
| **Allowed Scope** | `tests/test_tasks_store.py` — unit tests for TaskStore |
| **Forbidden Scope** | No changes to production code |
| **Dependencies** | TASK-001 (must know if legacy store is still used) |
| **Expected Behavior Preservation** | All existing transitions should work as documented in `ALLOWED_TRANSITIONS` |
| **Verification Requirements** | New tests pass; existing tests still pass |
| **Priority** | MEDIUM |
| **Status** | VERIFIED |

---

### TASK-003: Audit Approval Resolution Code Paths

| Field | Value |
|-------|-------|
| **Task ID** | TASK-003 |
| **Issue IDs** | ISSUE-003 |
| **Objective** | Map all approval-related code paths to understand data flow |
| **Current Responsibility** | Approval logic scattered across `approval_bot.py` and `hitl.py` |
| **Target Responsibility** | Clear ownership: `approval_bot.py` for Telegram-specific, `hitl.py` for generic approval state |
| **Files/Modules Affected** | `src/hermes/messaging/approval_bot.py`, `src/hermes/async_engine/loops/hitl.py` |
| **Allowed Scope** | Read-only audit of approval flow |
| **Forbidden Scope** | No code changes |
| **Dependencies** | None |
| **Expected Behavior Preservation** | N/A (read-only audit) |
| **Verification Requirements** | Documented ownership: hitl.py owns ApprovalStore, approval_bot.py is Telegram-specific consumer |
| **Priority** | MEDIUM |
| **Status** | VERIFIED |

---

### TASK-004: Refactor Procurement Handlers — Extract RAG Logic

| Field | Value |
|-------|-------|
| **Task ID** | TASK-004 |
| **Issue IDs** | ISSUE-002 |
| **Objective** | Separate RAG evidence lookup from business logic in handlers |
| **Current Responsibility** | Handlers do: compute business scores + RAG lookup + tool calls |
| **Target Responsibility** | Handlers focus on business logic; RAG lookup extracted to helper |
| **Files/Modules Affected** | `src/hermes/procurement/handlers.py` |
| **Allowed Scope** | Extract `_get_rag_evidence()` helper; refactor handlers to call it |
| **Forbidden Scope** | No changes to handler signatures or DAG structure |
| **Dependencies** | None |
| **Expected Behavior Preservation** | Same outputs for same inputs; DAG execution unchanged |
| **Verification Requirements** | Existing procurement tests pass; new helper is testable in isolation |
| **Priority** | MEDIUM |
| **Status** | VERIFIED |
| **Note** | Current architecture already follows suggested pattern - _rag_lines() helper exists and is used appropriately |

---

### TASK-005: Verify LLM Gateway Usage Patterns

| Field | Value |
|-------|-------|
| **Task ID** | TASK-005 |
| **Issue IDs** | ISSUE-005 |
| **Objective** | Check if any module imports LLM client directly instead of via gateway |
| **Current Responsibility** | Unknown if direct imports exist |
| **Target Responsibility** | All LLM usage goes through `llm/gateway.py` for testability |
| **Files/Modules Affected** | `src/hermes/llm/`, `src/hermes/router/`, any file that uses LLM |
| **Allowed Scope** | grep for `from.*cloudflare.*import` or `CloudflareLLM` usage |
| **Forbidden Scope** | No code changes |
| **Dependencies** | None |
| **Expected Behavior Preservation** | N/A (read-only audit) |
| **Verification Requirements** | Document any direct imports found |
| **Priority** | LOW |
| **Status** | VERIFIED |
| **Note** | Architecture is correct - gateway is single entry point, no direct CloudflareLLM imports found |

---

### TASK-006: Document Error Classification Taxonomy

| Field | Value |
|-------|-------|
| **Task ID** | TASK-006 |
| **Issue IDs** | ISSUE-006 |
| **Objective** | Create formal error taxonomy documentation |
| **Current Responsibility** | Ad-hoc string marker matching for retry classification |
| **Target Responsibility** | Documented error hierarchy with clear retry semantics |
| **Files/Modules Affected** | `src/hermes/async_engine/retry.py`, `src/hermes/tools/__init__.py` |
| **Allowed Scope** | Add docstrings and constants documenting error hierarchy |
| **Forbidden Scope** | No behavior changes; no new error types |
| **Dependencies** | None |
| **Expected Behavior Preservation** | Identical retry behavior |
| **Verification Requirements** | Existing retry tests pass |
| **Priority** | LOW |
| **Status** | VERIFIED |

---

## Task Dependency Graph

```
TASK-001 (Audit Legacy TaskStore)
    ↓
    ├─→ TASK-002 (Add Tests for Legacy Store) [if still used]
    │
TASK-003 (Audit Approval Resolution)
    │
TASK-004 (Refactor Handlers — Extract RAG)
    │
TASK-005 (Verify LLM Gateway Usage)
    │
TASK-006 (Document Error Taxonomy)
```

## Priority Order (for execution)

1. **TASK-001** (HIGH) — Foundation for understanding TASK-002
2. **TASK-002** (MEDIUM) — Test coverage gap
3. **TASK-003** (MEDIUM) — Understanding approval ownership
4. **TASK-004** (MEDIUM) — Clean up mixed concerns
5. **TASK-005** (LOW) — Verification task
6. **TASK-006** (LOW) — Documentation improvement

## Execution Policy

- **One task at a time**: Execute only the selected Task ID
- **Scope lock**: Only modify files explicitly allowed by the task
- **Verification before completion**: Run relevant tests before marking done
- **BLOCKED tasks**: If completing a task requires modifying outside scope, mark BLOCKED

---

**Plan Status**: PHASE 3 COMPLETE
**Artifacts Updated**: 
- `.kilo/plans/1788797860048-architecture-audit.md`
- `.kilo/plans/1788797860048-phase1-architecture-audit.md`
- `.kilo/plans/1788797860048-issues.md`
- `.kilo/plans/1788797860048-refactor-plan.md`
**Next Phase**: Execute TASK-006
