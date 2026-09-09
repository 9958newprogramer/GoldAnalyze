from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.backtest_service.api import create_app
from app.config import Settings
from app.domain.market_data import DemoMarketDataRepository

TOKEN = "test-control-plane-token-32-characters-long"


def _payload(**overrides):
    payload = {
        "request_id": "request-00000001",
        "job_id": "job-00000001",
        "run_id": "run-00000001",
        "idempotency_key": "idempotency-key-00000001",
        "strategy": {
            "symbol": "XAUUSD",
            "timeframe": "1d",
            "fast_window": 10,
            "slow_window": 30,
            "start_date": "2020-01-01",
            "end_date": "2021-01-01",
        },
    }
    payload.update(overrides)
    return payload


def _client() -> TestClient:
    settings = Settings(
        backtest_service_host="127.0.0.1",
        internal_service_token=TOKEN,
        otel_exporter="memory",
    )
    return TestClient(
        create_app(settings, repository=DemoMarketDataRepository()),
        raise_server_exceptions=False,
    )


def test_backtest_service_requires_control_plane_credential():
    with _client() as client:
        denied = client.post("/internal/v1/backtests/execute", json=_payload())
        assert denied.status_code == 401
        assert denied.headers["content-type"].startswith("application/problem+json")
        assert denied.json()["code"] == "CONTROL_PLANE_UNAUTHORIZED"
        assert "token" not in denied.text.lower()

        malformed = client.post(
            "/internal/v1/backtests/execute",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={**_payload(), "reply_topic": "attacker-controlled-topic"},
        )
        assert malformed.status_code == 422
        assert malformed.headers["content-type"].startswith("application/problem+json")
        assert malformed.json()["code"] == "CONTRACT_VALIDATION_FAILED"
        assert "reply_topic" not in malformed.text


def test_backtest_service_executes_deterministically_and_propagates_trace():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "traceparent": f"00-{'a' * 32}-{'b' * 16}-01",
    }
    with _client() as client:
        first = client.post("/internal/v1/backtests/execute", headers=headers, json=_payload())
        second = client.post("/internal/v1/backtests/execute", headers=headers, json=_payload())

        assert first.status_code == 200, first.text
        body = first.json()
        assert body["schema_version"] == "backtest-result-v1"
        assert body["status"] == "completed"
        assert body["data_profile"]["synthetic"] is True
        assert body["metrics"]["final_equity"] > 0
        assert len(body["equity_curve"]) <= 321
        assert body["result_digest"] == second.json()["result_digest"]
        assert first.headers["x-trace-id"] == "a" * 32
        assert first.headers["cache-control"] == "no-store"


def test_backtest_service_rejects_version_conflict_and_expired_deadline():
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with _client() as client:
        conflict = client.post(
            "/internal/v1/backtests/execute",
            headers=headers,
            json=_payload(expected_data_version="immutable-version-does-not-match"),
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "DATA_VERSION_CONFLICT"

        requested_at = datetime.now(UTC) - timedelta(minutes=2)
        expired = client.post(
            "/internal/v1/backtests/execute",
            headers=headers,
            json=_payload(
                requested_at=requested_at.isoformat(),
                deadline_at=(requested_at + timedelta(minutes=1)).isoformat(),
            ),
        )
        assert expired.status_code == 408
        assert expired.json()["code"] == "EXECUTION_DEADLINE_EXCEEDED"
        assert expired.json()["retryable"] is True


def test_backtest_service_exposes_unprotected_health_but_fails_open_bind_without_auth():
    with _client() as client:
        health = client.get("/health/ready")
        assert health.status_code == 200
        assert health.json()["service"] == "backtest-service"
        assert health.json()["dependencies"] == {"market-data": "ok"}

    try:
        create_app(
            Settings(
                backtest_service_host="0.0.0.0",
                internal_service_token=None,
                otel_exporter="none",
            ),
            repository=DemoMarketDataRepository(),
        )
    except ValueError as exc:
        assert "INTERNAL_SERVICE_TOKEN" in str(exc)
    else:
        raise AssertionError("non-loopback service must fail closed without authentication")
