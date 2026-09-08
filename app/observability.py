"""Privacy-bounded OpenTelemetry runtime for traces and low-cardinality metrics."""

from __future__ import annotations

import json
import threading
from collections import defaultdict, deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from time import time_ns
from typing import Any, ClassVar, Literal

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    InMemoryMetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

ExporterName = Literal["none", "memory", "console", "otlp"]
AttributeValue = str | bool | int | float


class BoundedSpanExporter(SpanExporter):
    """Thread-safe local demo exporter with a hard memory bound."""

    def __init__(self, max_spans: int = 500):
        self._spans: deque[ReadableSpan] = deque(maxlen=max_spans)
        self._lock = threading.Lock()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def get_finished_spans(self) -> tuple[ReadableSpan, ...]:
        with self._lock:
            return tuple(self._spans)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


@dataclass(frozen=True)
class TraceCarrier:
    traceparent: str | None = None
    tracestate: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "traceparent": self.traceparent,
                "tracestate": self.tracestate,
            }.items()
            if value is not None
        }


class Telemetry:
    """Owns an isolated OTel SDK and a sanitized local evidence snapshot.

    The runtime never records prompts, Tool arguments, approval credentials, API keys, URLs,
    exception messages, or database statements. Metric dimensions are validated and are limited
    to server-controlled enumerations.
    """

    _safe_span_attributes: ClassVar[set[str]] = {
        "aurumlab.run.id",
        "aurumlab.job.id",
        "aurumlab.intent",
        "aurumlab.skill",
        "aurumlab.plan.id",
        "aurumlab.plan.step",
        "aurumlab.tool.name",
        "aurumlab.tool.effect",
        "aurumlab.tool.risk",
        "aurumlab.tool.decision",
        "aurumlab.cache.status",
        "aurumlab.job.status",
        "aurumlab.job.attempt",
        "aurumlab.checkpoint.restored_steps",
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "error.type",
    }

    def __init__(
        self,
        *,
        service_name: str,
        service_version: str,
        exporter: ExporterName = "memory",
        otlp_endpoint: str = "http://127.0.0.1:4318",
        sample_ratio: float = 1.0,
        memory_max_spans: int = 500,
        metric_export_interval_seconds: float = 30.0,
    ):
        if exporter not in {"none", "memory", "console", "otlp"}:
            raise ValueError("unsupported OpenTelemetry exporter")
        if not 0 <= sample_ratio <= 1:
            raise ValueError("OpenTelemetry sample ratio must be between 0 and 1")
        self.service_name = service_name
        self.exporter_name = exporter
        self._span_exporter: BoundedSpanExporter | None = None
        self._metric_reader: InMemoryMetricReader | None = None
        self._metric_lock = threading.Lock()
        self._metric_counters: dict[tuple[str, str], float] = defaultdict(float)
        self._metric_histograms: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}

        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                SERVICE_VERSION: service_version,
                "deployment.environment.name": "local-portfolio",
            }
        )
        self.tracer_provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
        )
        metric_readers: list[Any] = []
        interval_ms = max(int(metric_export_interval_seconds * 1_000), 1_000)
        if exporter != "none":
            self._span_exporter = BoundedSpanExporter(memory_max_spans)
            self.tracer_provider.add_span_processor(SimpleSpanProcessor(self._span_exporter))
            self._metric_reader = InMemoryMetricReader()
            metric_readers.append(self._metric_reader)
        if exporter == "console":
            self.tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            metric_readers.append(
                PeriodicExportingMetricReader(
                    ConsoleMetricExporter(), export_interval_millis=interval_ms
                )
            )
        elif exporter == "otlp":
            base = otlp_endpoint.rstrip("/")
            self.tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))
            )
            metric_readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
                    export_interval_millis=interval_ms,
                )
            )
        self.meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
        self.tracer = self.tracer_provider.get_tracer("aurumlab.runtime", service_version)
        self.meter = self.meter_provider.get_meter("aurumlab.runtime", service_version)
        self._propagator = TraceContextTextMapPropagator()

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        context: Context | None = None,
    ) -> Iterator[trace.Span]:
        with self.tracer.start_as_current_span(
            name,
            context=context,
            kind=kind,
            attributes=dict(attributes or {}),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            yield span

    @staticmethod
    def mark_error(span: trace.Span, error_type: str) -> None:
        span.set_attribute("error.type", error_type)
        span.set_status(Status(StatusCode.ERROR))

    def inject(self) -> TraceCarrier:
        carrier: dict[str, str] = {}
        self._propagator.inject(carrier)
        return TraceCarrier(
            traceparent=carrier.get("traceparent"),
            tracestate=carrier.get("tracestate"),
        )

    def extract(self, carrier: TraceCarrier | Mapping[str, str] | None) -> Context | None:
        if carrier is None:
            return None
        values = carrier.as_dict() if isinstance(carrier, TraceCarrier) else dict(carrier)
        if not values:
            return None
        return self._propagator.extract(carrier=values)

    @staticmethod
    def current_trace_id() -> str | None:
        context = trace.get_current_span().get_span_context()
        return f"{context.trace_id:032x}" if context.is_valid else None

    def count(
        self,
        name: str,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        amount: int = 1,
    ) -> None:
        safe = self._metric_attributes(attributes)
        with self._metric_lock:
            instrument = self._counters.get(name)
            if instrument is None:
                instrument = self.meter.create_counter(name)
                self._counters[name] = instrument
        instrument.add(amount, safe)
        key = (name, self._attribute_key(safe))
        with self._metric_lock:
            self._metric_counters[key] += amount

    def record(
        self,
        name: str,
        value: float,
        *,
        attributes: Mapping[str, AttributeValue] | None = None,
        unit: str = "ms",
    ) -> None:
        safe = self._metric_attributes(attributes)
        with self._metric_lock:
            instrument = self._histograms.get(name)
            if instrument is None:
                instrument = self.meter.create_histogram(name, unit=unit)
                self._histograms[name] = instrument
        instrument.record(value, safe)
        key = (name, self._attribute_key(safe))
        with self._metric_lock:
            values = self._metric_histograms[key]
            values.append(float(value))
            if len(values) > 1_000:
                del values[: len(values) - 1_000]

    def snapshot(self, *, span_limit: int = 100) -> dict[str, Any]:
        spans: list[dict[str, Any]] = []
        if self._span_exporter is not None:
            for item in self._span_exporter.get_finished_spans()[-span_limit:]:
                context = item.context
                parent = item.parent
                attributes = {
                    key: value
                    for key, value in (item.attributes or {}).items()
                    if key in self._safe_span_attributes
                }
                spans.append(
                    {
                        "name": item.name,
                        "trace_id": f"{context.trace_id:032x}",
                        "span_id": f"{context.span_id:016x}",
                        "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                        "kind": item.kind.name.lower(),
                        "status": item.status.status_code.name.lower(),
                        "duration_ms": round(
                            ((item.end_time or time_ns()) - item.start_time) / 1_000_000,
                            3,
                        ),
                        "attributes": attributes,
                    }
                )
        with self._metric_lock:
            counters = [
                {
                    "name": name,
                    "attributes": json.loads(attribute_key),
                    "value": value,
                }
                for (name, attribute_key), value in sorted(self._metric_counters.items())
            ]
            histograms = []
            for (name, attribute_key), values in sorted(self._metric_histograms.items()):
                if not values:
                    continue
                histograms.append(
                    {
                        "name": name,
                        "attributes": json.loads(attribute_key),
                        "count": len(values),
                        "sum": round(sum(values), 3),
                        "min": round(min(values), 3),
                        "max": round(max(values), 3),
                    }
                )
        return {
            "service_name": self.service_name,
            "exporter": self.exporter_name,
            "privacy": "no-prompts-no-tool-arguments-no-tokens",
            "recent_spans": spans,
            "metrics": {"counters": counters, "histograms": histograms},
        }

    def force_flush(self) -> bool:
        return self.tracer_provider.force_flush() and self.meter_provider.force_flush()

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()

    @staticmethod
    def _metric_attributes(
        attributes: Mapping[str, AttributeValue] | None,
    ) -> dict[str, AttributeValue]:
        output: dict[str, AttributeValue] = {}
        for key, value in (attributes or {}).items():
            if len(key) > 80 or isinstance(value, str) and len(value) > 80:
                raise ValueError("metric attributes must be bounded server-controlled values")
            output[str(key)] = value
        return output

    @staticmethod
    def _attribute_key(attributes: Mapping[str, AttributeValue]) -> str:
        return json.dumps(dict(attributes), sort_keys=True, separators=(",", ":"))
