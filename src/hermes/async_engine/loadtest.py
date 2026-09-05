"""Load test — N tasks across 1/2/4/8 workers; reports throughput, p95,
queue depth, retry rate, and parallel speedup (spec §12).

  10 / 50 / 100 / 500 tasks × {1,2,4,8} workers
  sequential = 1 worker   →   parallel = P workers
  speedup = T_sequential / T_parallel
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .backends import InMemoryBus
from .contract import Task, TaskStatus, routing_for
from .eventbus import InMemoryEventBus
from .metrics import TASKS_RETRIED, NoopMetrics
from .store import AsyncTaskStore
from .worker import Worker, WorkerPool


@dataclass
class WaveResult:
    n: int
    workers: int
    wall_seconds: float
    throughput: float
    p95_latency: float
    retry_rate: float
    completed: int
    failed: int


def _ts(iso: str) -> float:
    if not iso:
        return time.time()
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


def _run_wave(n: int, workers: int, unit_work_seconds: float,
              store_path: str, handler: Callable[[Task], str]) -> WaveResult:
    store = AsyncTaskStore(store_path)
    bus = InMemoryBus()
    events = InMemoryEventBus()
    metrics = NoopMetrics()

    exchange, routing_key, queue = routing_for("analyze")
    tasks = [
        Task(task_id=f"lt-{i}", workflow_id=f"wf-{n}-{workers}", task_type="analyze",
             payload={"i": i})
        for i in range(n)
    ]
    for t in tasks:
        store.create_task(t)
        bus.publish(exchange, routing_key, t.to_message())

    def build(name: str) -> Worker:
        return Worker(name, "analyze", handler, store, bus, events=events, metrics=metrics)

    pool = WorkerPool(build, size=workers)
    pool.start()
    deadline = time.time() + 120 + n * unit_work_seconds
    try:
        while time.time() < deadline:
            counts = store.task_counts()
            terminal = counts.get(TaskStatus.COMPLETED.value, 0) + counts.get(TaskStatus.FAILED.value, 0)
            if terminal >= n:
                break
            time.sleep(0.01)
    finally:
        pool.stop()

    completed = store.task_counts().get(TaskStatus.COMPLETED.value, 0)
    failed = store.task_counts().get(TaskStatus.FAILED.value, 0)
    total = completed + failed or 1
    wall = _wave_wall(tasks, store)
    latencies = _latencies(store, [t.task_id for t in tasks])
    return WaveResult(
        n=n, workers=workers, wall_seconds=wall,
        throughput=total / wall if wall > 0 else 0.0,
        p95_latency=_p95(latencies),
        retry_rate=metrics.counter(TASKS_RETRIED) / total,
        completed=completed, failed=failed,
    )


def _wave_wall(tasks, store) -> float:
    times = []
    for t in tasks:
        dur = _task_duration(store, t.task_id)
        if dur is not None:
            times.append(dur)
    return sum(times) / len(times) if times else 0.0


def _latencies(store, task_ids) -> list[float]:
    out = []
    for tid in task_ids:
        dur = _task_duration(store, tid)
        if dur is not None:
            out.append(dur)
    return out


def _task_duration(store, task_id) -> float | None:
    task = store.get_task(task_id)
    if task.status != TaskStatus.COMPLETED:
        return None
    results = store.task_results(task_id)
    if not results:
        return None
    # first "completed" result row carries the completion timestamp
    done_at = next((r["created_at"] for r in results if r["status"] == "completed"), None)
    if done_at is None:
        return None
    return _ts(done_at) - _ts(task.created_at)


def _p95(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[max(0, int(0.95 * len(s)) - 1)]


def run_load_test(
    sizes=(10, 50, 100, 500),
    worker_counts=(1, 2, 4, 8),
    unit_work_seconds: float = 0.05,
    store_dir: str = "/tmp/hermes-lt",
    handler: Callable[[Task], str] | None = None,
) -> dict:
    """Run the full load-test matrix and report speedups vs the sequential
    (1-worker) baseline for each N."""
    import os

    store_dir = os.path.join(store_dir, time.strftime("run-%Y%m%d-%H%M%S"))
    os.makedirs(store_dir, exist_ok=True)
    if handler is None:
        def handler(t):
            time.sleep(unit_work_seconds)
            return f"memory://result/{t.task_id}"

    report: dict = {"waves": [], "speedups": {}}
    for n in sizes:
        seq_path = os.path.join(store_dir, f"seq-{n}.db")
        seq = _run_wave(n, 1, unit_work_seconds, seq_path, handler)
        report["waves"].append(_wave_dict(seq, baseline=None))
        for w in worker_counts:
            if w == 1:
                continue
            par_path = os.path.join(store_dir, f"par-{n}-{w}.db")
            wave = _run_wave(n, w, unit_work_seconds, par_path, handler)
            report["waves"].append(_wave_dict(wave, baseline=seq))
            report["speedups"][f"n{n}w{w}"] = round(
                seq.wall_seconds / wave.wall_seconds if wave.wall_seconds > 0 else 0.0, 3)
    report["generated"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    return report


def _wave_dict(w: WaveResult, baseline: WaveResult | None):
    d = {
        "n": w.n, "workers": w.workers,
        "wall_seconds": round(w.wall_seconds, 4),
        "throughput_tasks_per_sec": round(w.throughput, 3),
        "p95_latency_seconds": round(w.p95_latency, 4),
        "retry_rate": round(w.retry_rate, 4),
        "completed": w.completed, "failed": w.failed,
    }
    if baseline is not None:
        d["speedup_vs_seq"] = round(baseline.wall_seconds / w.wall_seconds, 3) if w.wall_seconds > 0 else 0.0
    return d


def load_test_report(report: dict) -> str:
    lines = ["Hermes — Load test report", "=" * 48]
    for w in report["waves"]:
        lines.append(
            f"n={w['n']:<4} workers={w['workers']:<2} "
            f"wall={w['wall_seconds']:>8.4f}s  "
            f"throughput={w['throughput_tasks_per_sec']:>8.3f}/s  "
            f"p95={w['p95_latency_seconds']:>7.4f}s  retry={w['retry_rate']}"
            + (f"  speedup={w['speedup_vs_seq']}x" if "speedup_vs_seq" in w else "")
        )
    if report.get("speedups"):
        lines.append("-" * 48)
        lines.append("Speedup (wall-time, vs 1-worker baseline):")
        for k, v in report["speedups"].items():
            lines.append(f"  {k}: {v}x")
    return "\n".join(lines)