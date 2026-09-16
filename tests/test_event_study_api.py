from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.backtest_service.api import create_app
from app.config import Settings
from app.contracts import EventStudyRequest
from app.domain.market_data import Bar, DemoMarketDataRepository

TOKEN = "test-control-plane-token-32-characters-long"


class EventRepositoryStub:
    """Real-source test double for the protected HTTP adapter."""

    source_name = "postgresql:gold:http-test"
    synthetic = False

    def load_event_study(self, request: EventStudyRequest) -> list[Bar]:
        """Return one event with enough forward observations."""

        closes = [100, 96, 92.16, 93.0816, 94, 96.768, 97, 101.376]
        return [
            Bar(
                at=datetime(2024, 1, index + 1, tzinfo=UTC),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1_000,
            )
            for index, close in enumerate(closes)
        ]


def _payload() -> dict[str, object]:
    """Return the canonical Example A HTTP payload."""

    return {
        "schema_version": "event-study-request-v1",
        "request_id": "event-study-001",
        "job_id": "event-job-001",
        "event_name": "two_consecutive_drop_over_3pct",
        "symbol": "XAUUSD",
        "timeframe": "1d",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "conditions": [
            {"offset": -1, "field": "return_pct", "operator": "lte", "value": -3},
            {"offset": 0, "field": "return_pct", "operator": "lte", "value": -3},
        ],
        "forward_days": [1, 3, 5],
    }


def _settings() -> Settings:
    """Build isolated internal-service settings."""

    return Settings(
        backtest_service_host="127.0.0.1",
        internal_service_token=TOKEN,
        otel_exporter="memory",
    )


def test_event_study_api_requires_auth_and_returns_versioned_result():
    """The internal endpoint shares control-plane auth and response contracts."""

    app = create_app(
        _settings(),
        repository=DemoMarketDataRepository(),
        event_repository=EventRepositoryStub(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        denied = client.post("/internal/v1/event-studies/execute", json=_payload())
        accepted = client.post(
            "/internal/v1/event-studies/execute",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=_payload(),
        )

    assert denied.status_code == 401
    assert denied.json()["code"] == "CONTROL_PLANE_UNAUTHORIZED"
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["schema_version"] == "event-study-result-v1"
    assert body["start_date"] == "2024-01-01"
    assert body["end_date"] == "2024-12-31"
    assert body["event_count"] == 1
    assert body["events"][0]["forward_returns"] == {"1": 1.0, "3": 5.0, "5": 10.0}
    assert body["data_profile"]["synthetic"] is False


def test_event_study_api_never_falls_back_to_demo_data():
    """A service without PostgreSQL returns a stable unavailable response."""

    app = create_app(_settings(), repository=DemoMarketDataRepository())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/internal/v1/event-studies/execute",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=_payload(),
        )

    assert response.status_code == 503
    assert response.json()["code"] == "EVENT_STUDY_DATA_UNAVAILABLE"
    assert response.json()["retryable"] is True


def test_event_study_api_rejects_unknown_fields_before_execution():
    """Strict request validation blocks unrecognized caller-controlled fields."""

    app = create_app(
        _settings(),
        repository=DemoMarketDataRepository(),
        event_repository=EventRepositoryStub(),
    )
    payload = {**_payload(), "reply_topic": "attacker-controlled-topic"}
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/internal/v1/event-studies/execute",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["code"] == "CONTRACT_VALIDATION_FAILED"
    assert "reply_topic" not in response.text
