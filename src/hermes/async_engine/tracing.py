"""OpenTelemetry distributed tracing integration (T7.1).

Optional dependency — gracefully degrades to no-op if opentelemetry not installed.
Provides trace context propagation through the async engine:
  - Workflow span
  - Task span (queue → execute → verify)
  - Event bus span
  - Verification span
"""
from __future__ import annotations

import contextlib
import os
from contextvars import ContextVar
from typing import Any, Generator, Optional

# Context variable for current trace context
_current_span: ContextVar[Optional[Any]] = ContextVar("_current_span", default=None)
_current_trace_id: ContextVar[Optional[str]] = ContextVar("_current_trace_id", default=None)
_current_workflow_id: ContextVar[Optional[str]] = ContextVar("_current_workflow_id", default=None)

# Lazy initialization flag
_tracer_initialized = False
_tracer = None
_meter = None


def _init_tracer() -> None:
    """Initialize OpenTelemetry tracer if available and enabled."""
    global _tracer_initialized, _tracer, _meter
    
    if _tracer_initialized:
        return
    
    _tracer_initialized = True
    
    # Check if tracing is enabled via env var
    if os.environ.get("HERMES_OTEL_ENABLED", "").lower() not in ("1", "true", "yes"):
        return
    
    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        
        # Configure tracer provider
        trace_provider = TracerProvider()
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        
        span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(trace_provider)
        
        # Configure meter provider
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True),
            export_interval_millis=30000,
        )
        metrics.set_meter_provider(MeterProvider(metric_readers=[metric_reader]))
        
        _tracer = trace.get_tracer("hermes.async_engine")
        _meter = metrics.get_meter("hermes.async_engine")
        
    except ImportError:
        # OpenTelemetry not installed — tracing disabled
        pass
    except Exception:
        # Any other error — tracing disabled but don't crash
        pass


def get_tracer():
    """Get the tracer instance (initializes on first call)."""
    if not _tracer_initialized:
        _init_tracer()
    return _tracer


def get_meter():
    """Get the meter instance (initializes on first call)."""
    if not _tracer_initialized:
        _init_tracer()
    return _meter


def get_current_span():
    """Get the current active span from context."""
    return _current_span.get()


def get_trace_id() -> Optional[str]:
    """Get the current trace ID."""
    trace_id = _current_trace_id.get()
    if trace_id:
        return trace_id
    span = _current_span.get()
    if span and span.is_recording():
        ctx = span.get_span_context()
        return format(ctx.trace_id, "032x")
    return None


def get_workflow_id() -> Optional[str]:
    """Get the current workflow ID from context."""
    return _current_workflow_id.get()


@contextlib.contextmanager
def trace_workflow(workflow_id: str, operation: str = "workflow.execute") -> Generator[Any, None, None]:
    """Create a workflow-level span."""
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    
    with tracer.start_as_current_span(operation, attributes={
        "workflow.id": workflow_id,
        "hermes.component": "orchestrator",
    }) as span:
        token_span = _current_span.set(span)
        token_trace = _current_trace_id.set(format(span.get_span_context().trace_id, "032x"))
        token_wf = _current_workflow_id.set(workflow_id)
        try:
            yield span
        finally:
            _current_span.reset(token_span)
            _current_trace_id.reset(token_trace)
            _current_workflow_id.reset(token_wf)


@contextlib.contextmanager
def trace_task(task_id: str, task_type: str, workflow_id: str, operation: str = "task.execute") -> Generator[Any, None, None]:
    """Create a task-level span with queue/execute/verify phases."""
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    
    with tracer.start_as_current_span(operation, attributes={
        "task.id": task_id,
        "task.type": task_type,
        "workflow.id": workflow_id,
        "hermes.component": "worker",
    }) as span:
        token_span = _current_span.set(span)
        try:
            yield span
        finally:
            _current_span.reset(token_span)


@contextlib.contextmanager
def trace_verification(task_id: str, task_type: str) -> Generator[Any, None, None]:
    """Create a verification span."""
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    
    with tracer.start_as_current_span("task.verify", attributes={
        "task.id": task_id,
        "task.type": task_type,
        "hermes.component": "verifier",
    }) as span:
        yield span


@contextlib.contextmanager
def trace_event_bus(event_type: str, task_id: str = "") -> Generator[Any, None, None]:
    """Create an event bus emission span."""
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    
    with tracer.start_as_current_span("eventbus.emit", attributes={
        "event.type": event_type,
        "task.id": task_id,
        "hermes.component": "eventbus",
    }) as span:
        yield span


def inject_trace_context(carrier: dict[str, str]) -> None:
    """Inject current trace context into a carrier (for HTTP headers, message headers, etc.)."""
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    propagator = TraceContextTextMapPropagator()
    propagator.inject(carrier)


def extract_trace_context(carrier: dict[str, str]) -> dict[str, str]:
    """Extract trace context from a carrier."""
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    propagator = TraceContextTextMapPropagator()
    return propagator.extract(carrier)


# Metrics helpers
_task_duration_histogram = None
_task_counter = None
_workflow_duration_histogram = None


def _init_metrics():
    global _task_duration_histogram, _task_counter, _workflow_duration_histogram
    meter = get_meter()
    if meter is None:
        return
    try:
        _task_duration_histogram = meter.create_histogram(
            "hermes.task.duration",
            unit="ms",
            description="Task execution duration in milliseconds",
        )
        _task_counter = meter.create_counter(
            "hermes.task.count",
            unit="1",
            description="Number of tasks executed",
        )
        _workflow_duration_histogram = meter.create_histogram(
            "hermes.workflow.duration",
            unit="ms",
            description="Workflow execution duration in milliseconds",
        )
    except Exception:
        pass


def record_task_duration(task_type: str, duration_ms: float, status: str) -> None:
    """Record task execution duration."""
    if _task_duration_histogram is None:
        _init_metrics()
    if _task_duration_histogram:
        _task_duration_histogram.record(duration_ms, attributes={
            "task.type": task_type,
            "status": status,
        })


def record_task_execution(task_type: str, status: str) -> None:
    """Record task execution count."""
    if _task_counter is None:
        _init_metrics()
    if _task_counter:
        _task_counter.add(1, attributes={
            "task.type": task_type,
            "status": status,
        })


def record_workflow_duration(workflow_id: str, duration_ms: float, status: str) -> None:
    """Record workflow execution duration."""
    if _workflow_duration_histogram is None:
        _init_metrics()
    if _workflow_duration_histogram:
        _workflow_duration_histogram.record(duration_ms, attributes={
            "workflow.id": workflow_id,
            "status": status,
        })


# Convenience: no-op context manager for when tracing is disabled
@contextlib.contextmanager
def _noop_trace(*args, **kwargs) -> Generator[None, None, None]:
    yield None


def maybe_trace_workflow(workflow_id: str, operation: str = "workflow.execute"):
    """Return a workflow trace context manager (no-op if tracing disabled)."""
    if get_tracer() is None:
        return _noop_trace()
    return trace_workflow(workflow_id, operation)


def maybe_trace_task(task_id: str, task_type: str, workflow_id: str, operation: str = "task.execute"):
    """Return a task trace context manager (no-op if tracing disabled)."""
    if get_tracer() is None:
        return _noop_trace()
    return trace_task(task_id, task_type, workflow_id, operation)


def maybe_trace_verification(task_id: str, task_type: str):
    """Return a verification trace context manager (no-op if tracing disabled)."""
    if get_tracer() is None:
        return _noop_trace()
    return trace_verification(task_id, task_type)


def maybe_trace_event_bus(event_type: str, task_id: str = ""):
    """Return an event bus trace context manager (no-op if tracing disabled)."""
    if get_tracer() is None:
        return _noop_trace()
    return trace_event_bus(event_type, task_id)
