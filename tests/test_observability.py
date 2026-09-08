import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import fakeredis.aioredis
import pytest
import yaml

from app.bootstrap import build_services
from app.config import Settings
from app.jobs import AgentWorker, JobSubmissionService, RedisStreamBroker
from app.observability import Telemetry


class OTLPTestHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, str | None, bytes]]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.requests.append((self.path, self.headers.get("Content-Type"), body))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.end_headers()

    def log_message(self, format, *args):
        return None


def _telemetry(tmp_path, name="aurumlab-test"):
    return build_services(
        Settings(
            app_database_path=str(tmp_path / f"{name}.db"),
            otel_service_name=name,
            otel_exporter="memory",
            otel_memory_max_spans=100,
        )
    )


async def test_sync_agent_trace_is_one_tree_and_contains_no_prompt_or_arguments(tmp_path):
    services = _telemetry(tmp_path, "sync-observability")
    sentinel = "PRIVATE_QUERY_SENTINEL"

    with services.telemetry.span("test.request"):
        response = await services.agent.run(f"你好，{sentinel}，你能做什么？")

    assert response.status == "completed"
    assert services.telemetry.force_flush() is True
    snapshot = services.telemetry.snapshot()
    spans = snapshot["recent_spans"]
    names = {span["name"] for span in spans}
    assert {
        "test.request",
        "aurumlab.agent.run",
        "aurumlab.agent.route",
        "aurumlab.agent.plan",
        "aurumlab.plan.step",
        "aurumlab.tool.call",
        "aurumlab.store.run.save",
    } <= names
    assert len({span["trace_id"] for span in spans}) == 1
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert sentinel not in serialized
    assert "approval_token" not in serialized
    assert "tool_arguments" not in serialized
    metric_names = {
        item["name"] for kind in ("counters", "histograms") for item in snapshot["metrics"][kind]
    }
    assert {
        "aurumlab.agent.runs",
        "aurumlab.agent.plan.steps",
        "aurumlab.agent.tool.decisions",
        "aurumlab.agent.tool.calls",
        "aurumlab.agent.run.duration",
    } <= metric_names
    services.telemetry.shutdown()


async def test_trace_context_crosses_job_boundary_while_redis_stays_opaque(tmp_path):
    services = _telemetry(tmp_path, "async-observability")
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    broker = RedisStreamBroker(client)
    submission = JobSubmissionService(services.jobs, broker, services.telemetry)

    with services.telemetry.span("test.http.submit"):
        job, _ = await submission.submit(
            "查询黄金最近5根日K线。",
            "bypass",
            2,
            idempotency_key="trace-job",
        )

    entries = await client.xrange(broker.stream_name)
    assert entries[0][1] == {"job_id": job.job_id}
    assert "traceparent" not in job.model_dump_json()
    traceparent, tracestate = services.jobs.get_trace_context(job.job_id)
    assert traceparent is not None
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", traceparent)
    assert int(traceparent.rsplit("-", 1)[1], 16) & 1 == 1
    assert tracestate is None

    worker = AgentWorker(
        worker_id="observed-worker",
        agent=services.agent,
        jobs=services.jobs,
        broker=broker,
        telemetry=services.telemetry,
    )
    assert await worker.run_once(block_ms=1) is True
    assert services.jobs.get(job.job_id).status == "completed"

    services.telemetry.force_flush()
    spans = services.telemetry.snapshot()["recent_spans"]
    by_name = {span["name"]: span for span in spans}
    assert {
        "test.http.submit",
        "aurumlab.job.submit",
        "aurumlab.job.process",
        "aurumlab.agent.run",
        "aurumlab.store.checkpoint.save",
    } <= set(by_name)
    assert len({span["trace_id"] for span in spans}) == 1
    assert (
        by_name["aurumlab.job.process"]["parent_span_id"]
        == by_name["aurumlab.job.submit"]["span_id"]
    )
    assert (
        by_name["aurumlab.agent.run"]["parent_span_id"]
        == by_name["aurumlab.job.process"]["span_id"]
    )

    await broker.close()
    services.telemetry.shutdown()


def test_memory_export_is_bounded_and_metric_dimensions_are_bounded():
    telemetry = Telemetry(
        service_name="bounded-test",
        service_version="test",
        exporter="memory",
        memory_max_spans=2,
    )
    for index in range(3):
        with telemetry.span(f"span-{index}"):
            pass

    assert [span["name"] for span in telemetry.snapshot()["recent_spans"]] == [
        "span-1",
        "span-2",
    ]
    with pytest.raises(ValueError, match="bounded"):
        telemetry.count("unsafe.metric", attributes={"value": "x" * 81})
    telemetry.shutdown()


def test_real_otlp_http_exporters_send_trace_and_metrics_protobuf():
    OTLPTestHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), OTLPTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    telemetry = Telemetry(
        service_name="otlp-export-test",
        service_version="test",
        exporter="otlp",
        otlp_endpoint=f"http://127.0.0.1:{server.server_port}",
        metric_export_interval_seconds=300,
    )
    try:
        with telemetry.span("exported-span"):
            telemetry.count("aurumlab.test.counter", attributes={"result": "ok"})
        assert telemetry.force_flush() is True
        by_path = {
            path: (content_type, body) for path, content_type, body in OTLPTestHandler.requests
        }
        assert {"/v1/traces", "/v1/metrics"} <= set(by_path)
        assert by_path["/v1/traces"][0] == "application/x-protobuf"
        assert by_path["/v1/metrics"][0] == "application/x-protobuf"
        assert by_path["/v1/traces"][1]
        assert by_path["/v1/metrics"][1]
    finally:
        telemetry.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_observability_compose_is_pinned_and_exposes_only_loopback_ports():
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "compose.observability.yaml").read_text())
    collector = yaml.safe_load((root / "otel-collector.yaml").read_text())

    assert compose["services"]["jaeger"]["image"].endswith(":2.20.0")
    assert compose["services"]["otel-collector"]["image"].endswith(":0.160.0")
    ports = [port for service in compose["services"].values() for port in service.get("ports", [])]
    assert ports
    assert all(port.startswith("127.0.0.1:") for port in ports)
    assert collector["service"]["pipelines"]["traces"]["exporters"] == ["otlphttp/jaeger"]
    assert collector["service"]["pipelines"]["metrics"]["exporters"] == ["prometheus"]
    assert set(collector["service"]["pipelines"]["traces"]["processors"]) == {
        "memory_limiter",
        "batch",
    }
