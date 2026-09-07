"""Scraper policy tests: defaults, domain allow/block, env override, rate limiter."""
from __future__ import annotations

from unittest.mock import patch

from hermes.scraper.policy import RateLimiter, ScrapePolicy


def test_default_policy():
    p = ScrapePolicy()
    assert p.rate_limit_per_domain == 2.0
    assert p.ttl_days == 30
    assert p.allowed_domains == []
    assert p.blocked_domains == []
    assert p.max_concurrent == 5
    assert p.dedup_by == "content_hash"


def test_blocked_domain_rejected():
    p = ScrapePolicy(blocked_domains=["badsite.com", "tracker.net"])
    assert p.domain_allowed("badsite.com") is False
    assert p.domain_allowed("tracker.net") is False
    assert p.domain_allowed("good.com") is True


def test_allowed_domains_whitelist():
    p = ScrapePolicy(allowed_domains=["trusted.com"])
    assert p.domain_allowed("trusted.com") is True
    assert p.domain_allowed("other.com") is False
    assert p.domain_allowed("") is False


def test_empty_allow_and_block_allows_all():
    p = ScrapePolicy()
    assert p.domain_allowed("anything.com") is True


def test_env_override_from_yaml(tmp_path, monkeypatch):
    yaml_file = tmp_path / "scraper_policy.yaml"
    yaml_file.write_text("rate_limit_per_domain: 5\nttl_days: 14\n")
    monkeypatch.setenv("HERMES_SCRAPER_POLICY", str(yaml_file))
    p = ScrapePolicy.from_env()
    assert p.rate_limit_per_domain == 5.0
    assert p.ttl_days == 14


def test_rate_limiter_acquire_does_not_sleep_on_first_call():
    limiter = RateLimiter(rate_per_sec=2.0)
    with patch("time.monotonic", return_value=0.0):
        with patch("time.sleep") as mock_sleep:
            limiter.acquire("example.com")
            mock_sleep.assert_not_called()


def test_rate_limiter_acquire_sleeps_when_too_fast():
    limiter = RateLimiter(rate_per_sec=2.0)
    with patch("time.monotonic", side_effect=[0.0, 0.0, 0.1, 0.5]):
        with patch("time.sleep") as mock_sleep:
            limiter.acquire("example.com")
            limiter.acquire("example.com")
            mock_sleep.assert_called_once()
            assert mock_sleep.call_args[0][0] >= 0.4
