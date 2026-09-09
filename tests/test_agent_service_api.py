from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from app.agent_service.api import create_app as create_agent_app
from app.backtest_service.api import create_app as create_backtest_app
from app.backtest_service.port import BacktestHttpClient
from app.bootstrap import build_services
from app.config import Settings
from app.domain.market_data import DemoMarketDataRepository

TOKEN = "test-control-plane-token-32-characters-long"


def _payload(**overrides):
    payload = {
        "request_id": "request-00000001",
        "job_id": "job-00000001",
        "run_id": "run-00000001",
        "idempotency_key": "idempotency-key-00000001",
        "question": "回测黄金日K，10日均线上穿30日均线，2020年至2021年。",
    }
    payload.update(overrides)
    return payload


def _client(tmp_path) -> TestClient:
    app_settings = Settings(
        app_database_path=str(tmp_path / "agent-runtime.db"),
        agent_service_host="127.0.0.1",
        internal_service_token=TOKEN,
        otel_exporter="memory",
    )
    runtime = build_services(app_settings)
    backtest_app = create_backtest_app(
        app_settings,
        repository=DemoMarketDataRepository(),
    )
    remote = BacktestHttpClient(
        base_url="http://backtest.internal",
        service_token=TOKEN,
        allow_insecure_http=True,
        telemetry=runtime.telemetry,
        transport=httpx.ASGITransport(app=backtest_app),
    )
    runtime.agent.backtest_executor = remote
    return TestClient(
        create_agent_app(app_settings, services=replace(runtime, backtest_executor=remote)),
        raise_server_exceptions=False,
    )


def test_agent_service_executes_through_remote_backtest_and_preserves_correlation(tmp_path):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "traceparent": f"00-{'a' * 32}-{'b' * 16}-01",
    }
    with _client(tmp_path) as client:
        response = client.post("/internal/v1/agent-runs/execute", headers=headers, json=_payload())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schema_version"] == "agent-execute-result-v1"
        assert body["request_id"] == "request-00000001"
        assert body["job_id"] == "job-00000001"
        assert body["run_id"] == "run-00000001"
        assert len(body["execution_id"]) == 12
        assert body["artifact"]["status"] == "completed"
        assert body["artifact"]["metrics"]["final_equity"] > 0
        assert body["artifact"]["data_profile"]["synthetic"] is True
        assert len(body["result_digest"]) == 64
        assert "question" not in body["artifact"]
        assert response.headers["x-trace-id"] == "a" * 32
        assert response.headers["cache-control"] == "no-store"

        readiness = client.get("/health/ready")
        assert readiness.status_code == 200
        assert readiness.json()["dependencies"] == {"backtest-service": "ok"}


def test_agent_service_requires_auth_and_rejects_untrusted_contract_fields(tmp_path):
    with _client(tmp_path) as client:
        denied = client.post("/internal/v1/agent-runs/execute", json=_payload())
        assert denied.status_code == 401
        assert denied.json()["code"] == "CONTROL_PLANE_UNAUTHORIZED"

        malformed = client.post(
            "/internal/v1/agent-runs/execute",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={**_payload(), "callback_url": "http://attacker.invalid"},
        )
        assert malformed.status_code == 422
        assert malformed.json()["code"] == "CONTRACT_VALIDATION_FAILED"
        assert "callback_url" not in malformed.text


def test_agent_service_rejects_elapsed_deadline_before_execution(tmp_path):
    requested_at = datetime.now(UTC) - timedelta(minutes=2)
    with _client(tmp_path) as client:
        response = client.post(
            "/internal/v1/agent-runs/execute",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=_payload(
                requested_at=requested_at.isoformat(),
                deadline_at=(requested_at + timedelta(minutes=1)).isoformat(),
            ),
        )
        assert response.status_code == 504
        assert response.json()["code"] == "AGENT_DEADLINE_EXCEEDED"
        assert response.json()["retryable"] is True


def test_agent_service_fails_closed_on_public_bind_without_auth(tmp_path):
    try:
        create_agent_app(
            Settings(
                app_database_path=str(tmp_path / "public.db"),
                agent_service_host="0.0.0.0",
                internal_service_token=None,
                otel_exporter="none",
            )
        )
    except ValueError as exc:
        assert "INTERNAL_SERVICE_TOKEN" in str(exc)
    else:
        raise AssertionError("non-loopback Agent Service must fail closed without authentication")
