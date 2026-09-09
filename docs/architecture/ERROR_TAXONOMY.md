# Error Taxonomy — Hermes Agentic AI Orchestration Platform

## Overview

Hermes uses a two-layer error classification system:

1. **Exception-level**: `RetryableError` / `NonRetryableError` (async_engine/retry.py)
2. **Tool-level**: `RetryableToolError` / `FatalToolError` (tools/__init__.py)

---

## Layer 1: Async Engine Errors (retry.py)

### Exception Hierarchy

```
RuntimeError
├── RetryableError      # Transient, safe to retry
└── NonRetryableError   # Permanent, dead-letter queue
```

### Classification Rules

**RetryableError** (transient failures):
- Timeout errors
- Connection reset / refused
- Temporary provider unavailable
- HTTP 429, 502, 503, 504
- Rate limit / overloaded

**NonRetryableError** (permanent failures):
- Invalid payload
- Invalid task type
- Authentication failure
- Schema validation error
- Domain/business rule violations

### String Marker Classification

The `classify_failure()` function uses string matching against:
- `_RETRYABLE_MARKERS` tuple
- `_NON_RETRYABLE_MARKERS` tuple

Unknown failures default to **non-retryable** to prevent infinite loops.

### Retry Policy

- **Schedule**: 1s → 5s → 30s (attempt 1, 2, 3)
- **Max attempts**: 3 (configurable)
- **Dead-letter**: After max attempts, task goes to DLQ

---

## Layer 2: Tool Errors (tools/__init__.py)

### Exception Hierarchy

```
Exception
├── RetryableToolError    # Triggers retry path
└── FatalToolError       # Immediate failure, no retry
```

### ToolExecutor Behavior

1. If `FatalToolError` caught → re-raise immediately (no retry)
2. If other exception and `spec.retryable` → retry up to `max_retries`
3. If non-retryable spec or max retries exceeded → raise `RetryableToolError`

### Input Guard

`guard_input()` blocks dangerous patterns:
- `rm -rf /`
- Shell injection: `() { :; }`, `:; }`
- System commands: `shutdown`, `reboot`

---

## Error Flow

```
Tool Execution Error
        ↓
FatalToolError? → YES → Immediate failure
        ↓ NO
spec.retryable? → NO → Raise RetryableToolError
        ↓ YES
Max retries exceeded? → YES → Raise RetryableToolError
        ↓ NO
Retry (sleep backoff) → retry loop
```

```
Async Task Error
        ↓
RetryableError? → YES → Retry with backoff
        ↓ NO
NonRetryableError? → YES → Dead-letter queue
        ↓ NO
classify_failure() → string marker matching
        ↓
Retryable marker? → YES → Retry
        ↓ NO
Dead-letter queue (default)
```

---

## Best Practices

1. **Use specific exceptions**: Prefer `RetryableError` / `NonRetryableError` over string markers
2. **Don't retry permanent failures**: Business rule violations should be `NonRetryableError`
3. **Timeout = retryable**: Network timeouts are inherently transient
4. **Validate early**: Catch invalid input before tool execution
