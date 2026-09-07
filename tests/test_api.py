from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_home_are_available():
    health = client.get("/api/health")
    home = client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["skills_count"] == 4
    assert health.json()["router"].startswith("governed-llm-router")
    assert health.json()["planner"].startswith("deterministic-skill-planner")
    assert health.json()["router_mode"] == "rule-fallback"
    assert health.json()["router_llm_configured"] is False
    assert home.status_code == 200
    assert "AurumLab" in home.text
    assert home.headers["x-frame-options"] == "DENY"


def test_run_api_returns_trace_and_metrics():
    response = client.post(
        "/api/runs",
        json={
            "question": "黄金日K，20日均线上穿60日均线做多，2020年至2025年回测。",
            "cache_policy": "bypass",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["metrics"]["trade_count"] >= 0
    assert payload["route"]["intent"] == "backtest_strategy"
    assert len(payload["events"]) == 9
    assert payload["plan"]["validated"] is True
    assert payload["plan"]["planned_tool_calls"] == 4
    assert payload["cache_status"] == "bypass"


def test_api_rejects_oversized_input():
    response = client.post("/api/runs", json={"question": "x" * 1_001})
    assert response.status_code == 422


def test_api_rejects_unknown_cache_policy():
    response = client.post(
        "/api/runs",
        json={"question": "查询黄金最近5根日K线。", "cache_policy": "trust_everything"},
    )
    assert response.status_code == 422


def test_eval_api_runs_versioned_quality_gate():
    cases = client.get("/api/evals/cases")
    response = client.post("/api/evals/run")

    assert cases.status_code == 200
    assert len(cases.json()) == 16
    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset_version"] == "v4"
    assert payload["passed"] is True
    assert payload["score"] == 100
    assert payload["passed_cases"] == payload["total_cases"] == 16


def test_cache_stats_api_is_observable():
    response = client.get("/api/cache/stats")

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["entries"] >= 0
