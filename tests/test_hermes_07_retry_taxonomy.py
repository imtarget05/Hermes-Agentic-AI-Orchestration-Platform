"""HERMES-07 — retry classification taxonomy.

Acceptance: danh sách lỗi retryable/non-retryable được định nghĩa rõ trong
code, có test cho từng loại. timeout/429/5xx retry được; invalid payload /
auth / business rule / 422 thì KHÔNG retry mù.
"""
from hermes.async_engine.budgets import BudgetExceededError
from hermes.async_engine.retry import (
    NonRetryableError,
    RetryableError,
    RetryPolicy,
    classify_failure,
)

# ---- retryable: transient failures that are safe to retry ------------------

def test_retryable_timeout():
    assert classify_failure(RuntimeError("operation timed out")) is True


def test_retryable_connection_reset():
    assert classify_failure(RuntimeError("connection reset by peer")) is True


def test_retryable_connection_refused():
    assert classify_failure(RuntimeError("connection refused")) is True


def test_retryable_temporarily_unavailable():
    assert classify_failure(RuntimeError("service temporarily unavailable")) is True


def test_retryable_429_rate_limit():
    assert classify_failure(RuntimeError("HTTP 429 Too Many Requests")) is True
    assert classify_failure(RuntimeError("rate limit exceeded")) is True


def test_retryable_5xx():
    for code in ("502 Bad Gateway", "503 Service Unavailable", "504 Gateway Timeout"):
        assert classify_failure(RuntimeError(code)) is True, code


def test_retryable_overloaded():
    assert classify_failure(RuntimeError("provider overloaded")) is True


def test_retryable_explicit_exception():
    assert classify_failure(RetryableError("transient")) is True


# ---- non-retryable: permanent failures that must NOT be retried ------------

def test_nonretryable_invalid_payload():
    assert classify_failure(RuntimeError("invalid payload")) is False


def test_nonretryable_422():
    """422 Unprocessable Entity = client data error, retrying won't help."""
    assert classify_failure(RuntimeError("HTTP 422 Unprocessable Entity")) is False


def test_nonretryable_invalid_task_type():
    assert classify_failure(RuntimeError("invalid task type")) is False


def test_nonretryable_unknown_task_type():
    assert classify_failure(RuntimeError("unknown task_type: foo")) is False


def test_nonretryable_auth_failure():
    for msg in ("authentication failed", "auth failure", "unauthorized", "forbidden"):
        assert classify_failure(RuntimeError(msg)) is False, msg


def test_nonretryable_schema_error():
    assert classify_failure(RuntimeError("schema error: not a string")) is False


def test_nonretryable_validation_error():
    assert classify_failure(RuntimeError("validation error")) is False


def test_nonretryable_malformed():
    assert classify_failure(RuntimeError("malformed request")) is False


def test_nonretryable_business_rule():
    """Domain/business-rule violations are permanent by definition."""
    assert classify_failure(RuntimeError("business rule violated: over budget")) is False
    assert classify_failure(RuntimeError("business_rule: approval required")) is False
    assert classify_failure(RuntimeError("invariant violated")) is False


def test_nonretryable_explicit_exception():
    assert classify_failure(NonRetryableError("permanent")) is False


def test_nonretryable_budget_exceeded():
    """BudgetExceededError must NOT retry (would just burn more budget)."""
    assert classify_failure(BudgetExceededError("budget: token budget reached")) is False


# ---- unknown failures default to non-retryable (avoid infinite loops) -------

def test_unknown_error_not_retried():
    assert classify_failure(RuntimeError("something weird happened")) is False


# ---- RetryPolicy integration ------------------------------------------------

def test_policy_should_retry_respects_max_attempts():
    p = RetryPolicy(max_attempts=3)
    assert p.should_retry(1) is True
    assert p.should_retry(2) is True
    assert p.should_retry(3) is False  # attempt == cap → stop


def test_classify_then_retry_decision():
    """End-to-end: a 422 must never be retried even if attempts remain."""
    p = RetryPolicy(max_attempts=5)
    err = RuntimeError("HTTP 422 invalid payload")
    assert classify_failure(err) is False          # non-retryable
    # even with attempts left, a non-retryable failure is never requeued
    assert (classify_failure(err) and p.should_retry(1)) is False
