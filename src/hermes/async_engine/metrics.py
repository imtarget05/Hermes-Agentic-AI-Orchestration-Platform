"""Prometheus metrics with a no-op fallback when prometheus_client is absent.

The fallback stores counters as dicts so load tests and unit tests can read
values without a running Prometheus.
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge, Histogram  # type: ignore

    _HAS_PROM = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_PROM = False


class BaseMetrics:
    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._hist_samples: dict[str, list[float]] = {}

    # --- counters ---
    def inc(self, name: str, amount: float = 1.0, labels: dict | None = None) -> None:
        raise NotImplementedError

    # --- gauges ---
    def set_gauge(self, name: str, value: float, labels: dict | None = None) -> None:
        raise NotImplementedError

    def inc_gauge(self, name: str, amount: float = 1.0, labels: dict | None = None) -> None:
        raise NotImplementedError

    def dec_gauge(self, name: str, amount: float = 1.0, labels: dict | None = None) -> None:
        raise NotImplementedError

    # --- histograms ---
    def observe(self, name: str, value: float, labels: dict | None = None) -> None:
        raise NotImplementedError

    # --- read back (for tests / load test) ---
    def counter(self, name: str, labels: dict | None = None) -> float:
        raise NotImplementedError

class NoopMetrics(BaseMetrics):
    """No-op + in-memory reading; used when prometheus_client is missing."""

    def _key(self, name, labels):
        if labels:
            parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
            return f"{name}{{{parts}}}"
        return name

    def inc(self, name, amount=1.0, labels=None):
        k = self._key(name, labels)
        self._counters[k] = self._counters.get(k, 0.0) + amount
        if labels:
            self._counters[name] = self._counters.get(name, 0.0) + amount

    def set_gauge(self, name, value, labels=None):
        k = self._key(name, labels)
        self._gauges[k] = value
        if labels:
            self._gauges[name] = value

    def inc_gauge(self, name, amount=1.0, labels=None):
        k = self._key(name, labels)
        self._gauges[k] = self._gauges.get(k, 0.0) + amount
        if labels:
            self._gauges[name] = self._gauges.get(name, 0.0) + amount

    def dec(self, name, amount=1.0, labels=None):
        self.dec_gauge(name, amount, labels)

    def dec_gauge(self, name, amount=1.0, labels=None):
        k = self._key(name, labels)
        self._gauges[k] = self._gauges.get(k, 0.0) - amount
        if labels:
            self._gauges[name] = self._gauges.get(name, 0.0) - amount

    def observe(self, name, value, labels=None):
        self._hist_samples.setdefault(name, []).append(value)

    def counter(self, name, labels=None):
        return self._counters.get(self._key(name, labels), 0.0)

    def counter_total(self, name: str) -> float:
        return sum(self._counters.get(k, 0.0) for k in self._counters if k.split("{")[0] == name)

    def gauge(self, name, labels=None):
        return self._gauges.get(self._key(name, labels), 0.0)

    def histogram(self, name):
        samples = sorted(self._hist_samples.get(name, []))
        if not samples:
            return 0, 0.0, 0.0
        return len(samples), sum(samples), _p95(samples)


def _p95(sorted_samples: list[float]) -> float:
    if not sorted_samples:
        return 0.0
    idx = max(0, int(0.95 * len(sorted_samples)) - 1)
    return sorted_samples[idx]


class PrometheusMetrics(BaseMetrics):
    """Real Prometheus client metrics (requires prometheus-client)."""

    def __init__(self, namespace: str = "hermes") -> None:
        super().__init__()
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._ns = namespace

    @staticmethod
    def _family(prefix, kind, name):
        return f"{prefix}_{name}"

    def inc(self, name, amount=1.0, labels=None):
        c = self._counters.get(name)
        if c is None:
            c = Counter(self._family(self._ns, "counter", name), name)
            self._counters[name] = c
        if labels:
            c.labels(**labels).inc(amount)
        else:
            c.inc(amount)

    def set_gauge(self, name, value, labels=None):
        g = self._gauges.get(name)
        if g is None:
            g = Gauge(self._family(self._ns, "gauge", name), name)
            self._gauges[name] = g
        if labels:
            g.labels(**labels).set(value)
        else:
            g.set(value)

    def inc_gauge(self, name, amount=1.0, labels=None):
        g = self._gauges.get(name)
        if g is None:
            g = Gauge(self._family(self._ns, "gauge", name), name)
            self._gauges[name] = g
        if labels:
            g.labels(**labels).inc(amount)
        else:
            g.inc(amount)

    def dec(self, name, amount=1.0, labels=None):
        self.dec_gauge(name, amount, labels)

    def dec_gauge(self, name, amount=1.0, labels=None):
        g = self._gauges.get(name)
        if g is None:
            g = Gauge(self._family(self._ns, "gauge", name), name)
            self._gauges[name] = g
        if labels:
            g.labels(**labels).dec(amount)
        else:
            g.dec(amount)

    def observe(self, name, value, labels=None):
        h = self._histograms.get(name)
        if h is None:
            h = Histogram(self._family(self._ns, "histogram", name), name)
            self._histograms[name] = h
        h.observe(value)

    def counter(self, name, labels=None):
        c = self._counters.get(name)
        if c is None:
            return 0.0
        if labels:
            return float(c.labels(**labels)._value.get())
        return float(c._value.get())

    def counter_total(self, name):
        return sum(c._value.get() for n, c in self._counters.items() if n == name)

    def gauge(self, name, labels=None):
        g = self._gauges.get(name)
        if g is None:
            return 0.0
        return float(g._value.get())

    def histogram(self, name):
        h = self._histograms.get(name)
        if h is None:
            return 0, 0.0
        return int(h._sum.get()), float(h._sum.get())


def build_metrics(enabled: bool = True) -> BaseMetrics:
    if enabled and _HAS_PROM:
        return PrometheusMetrics()
    return NoopMetrics()


# Canonical metric names used across the engine (spec §11).
TASKS_TOTAL = "tasks_total"
TASKS_COMPLETED = "tasks_completed_total"
TASKS_FAILED = "tasks_failed_total"
TASKS_RETRIED = "tasks_retried_total"
TASK_DURATION = "task_duration_seconds"
TASK_QUEUE_DEPTH = "task_queue_depth"
WORKER_ACTIVE = "worker_active"
WORKER_UTILIZATION = "worker_utilization"
TASK_LATENCY = "task_latency_seconds"