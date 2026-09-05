from hermes.async_engine.retry import (
    NonRetryableError,
    RetryableError,
    RetryPolicy,
    classify_failure,
)


def test_retryable_markers():
    for msg in ("operation timed out", "connection reset by peer",
                "temporarily unavailable", "HTTP 503 Service Unavailable",
                "provider overloaded", "rate limit exceeded", "502 Bad Gateway"):
        assert classify_failure(RuntimeError(msg)) is True, msg


def test_non_retryable_markers():
    for msg in ("invalid payload", "invalid task type",
                "authentication failed", "schema error", "validation error"):
        assert classify_failure(RuntimeError(msg)) is False, msg


def test_classification_helpers():
    assert classify_failure(RetryableError("boom")) is True
    assert classify_failure(NonRetryableError("sad")) is False


def test_unknown_error_is_not_retried_by_default():
    # unknown failures default to non-retryable to avoid infinite loops
    assert classify_failure(RuntimeError("something weird")) is False


def test_backoff_schedule():
    p = RetryPolicy(max_attempts=3)
    assert p.backoff_seconds(1) == 1
    assert p.backoff_seconds(2) == 5
    assert p.backoff_seconds(3) == 30
    # clamps when attempts exceed schedule length
    assert p.backoff_seconds(9) == 30


def test_should_retry_boundary():
    p = RetryPolicy(max_attempts=3)
    assert p.should_retry(1) is True
    assert p.should_retry(2) is True
    assert p.should_retry(3) is False