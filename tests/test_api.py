import re

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
    assert health.json()["async_job_transport"] == "redis-streams"
    assert health.json()["async_job_consumer_group"] == "aurumlab-workers-v1"
    assert health.json()["otel_exporter"] == "memory"
    assert health.json()["otel_service_name"] == "aurumlab"
    assert re.fullmatch(r"[0-9a-f]{32}", health.headers["x-trace-id"])
    assert home.status_code == 200
    assert "AurumLab" in home.text
    assert "Async Job · Redis + SSE" in home.text
    assert home.headers["x-frame-options"] == "DENY"

    upstream_trace_id = "1" * 32
    propagated = client.get(
        "/api/health",
        headers={
            "traceparent": f"00-{upstream_trace_id}-{'2' * 16}-01",
            "baggage": "secret=must-not-propagate",
        },
    )
    assert propagated.headers["x-trace-id"] == upstream_trace_id

    observability = client.get("/api/observability")
    assert observability.status_code == 200
    snapshot = observability.json()
    assert snapshot["privacy"] == "no-prompts-no-tool-arguments-no-tokens"
    assert "must-not-propagate" not in str(snapshot)
    assert any(span["name"] == "GET /api/health" for span in snapshot["recent_spans"])

    attacker_path = "/random-high-cardinality-value-123456"
    assert client.get(attacker_path).status_code == 404
    bounded = client.get("/api/observability").json()
    assert attacker_path not in str(bounded)
    assert any(
        item["attributes"].get("route") == "{unmatched}"
        for item in bounded["metrics"]["counters"]
        if item["name"] == "aurumlab.http.server.requests"
    )

    assert client.get("/static/app.js").status_code == 200
    static_snapshot = client.get("/api/observability").json()
    assert any(
        item["attributes"].get("route") == "/static/{asset}"
        for item in static_snapshot["metrics"]["counters"]
        if item["name"] == "aurumlab.http.server.requests"
    )


def test_application_lifespan_discovers_mcp_client_tools():
    with TestClient(app) as live_client:
        health = live_client.get("/api/health").json()
        catalog = live_client.get("/api/mcp/catalog")
        tools = live_client.get("/api/tools").json()["tools"]

        assert health["mcp_client_connected_servers"] == 1
        assert health["mcp_client_discovered_tools"] == 2
        assert catalog.status_code == 200
        assert catalog.json()["servers"][0]["status"] == "connected"
        assert {item["qualified_name"] for item in catalog.json()["tools"]} == {
            "mcp__runtime__describe_runtime_capabilities",
            "mcp__runtime__profile_text",
        }
        assert "mcp__runtime__profile_text" in tools


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
    assert payload["dataset_version"] == "v5"
    assert payload["passed"] is True
    assert payload["score"] == 100
    assert payload["passed_cases"] == payload["total_cases"] == 16


def test_external_tool_requires_explicit_one_time_approval():
    pending_response = client.post(
        "/api/runs",
        json={
            "question": "搜索互联网最新黄金新闻并返回可靠来源。",
            "cache_policy": "bypass",
        },
    )

    assert pending_response.status_code == 200
    pending = pending_response.json()
    approval = pending["approval"]
    assert pending["status"] == "pending_approval"
    assert pending["plan"]["paused_step"] == "search_external_knowledge"
    assert pending["events"][-1]["stage"] == "approval_required"
    assert pending["tool_audit"][-1]["decision"] == "review"
    assert "approval_token" not in pending_response.text

    approval_path = f"/api/runs/{pending['run_id']}/approvals/{approval['approval_id']}"
    assert client.post(f"{approval_path}/approve").status_code == 422
    assert (
        client.post(
            f"{approval_path}/approve",
            headers={"X-AurumLab-Approval-Intent": "deny"},
        ).status_code
        == 403
    )

    grant_response = client.post(
        f"{approval_path}/approve",
        headers={"X-AurumLab-Approval-Intent": "approve"},
    )
    assert grant_response.status_code == 200
    token = grant_response.json()["approval_token"]
    forged = f"{approval['approval_id']}.{'x' * 43}"
    assert (
        client.post(
            f"/api/runs/{pending['run_id']}/resume",
            json={"approval_token": forged},
        ).status_code
        == 403
    )

    resumed_response = client.post(
        f"/api/runs/{pending['run_id']}/resume",
        json={"approval_token": token},
    )
    assert resumed_response.status_code == 200
    resumed = resumed_response.json()
    assert resumed["status"] == "completed"
    assert resumed["approval"]["status"] == "consumed"
    assert "approval_resume" in [event["stage"] for event in resumed["events"]]
    assert len([item for item in resumed["tool_audit"] if item["phase"] == "execution"]) == 2
    assert token not in resumed_response.text
    assert (
        client.post(
            f"/api/runs/{pending['run_id']}/resume",
            json={"approval_token": token},
        ).status_code
        == 409
    )


def test_operator_can_deny_pending_external_tool_without_execution():
    pending = client.post(
        "/api/runs",
        json={
            "question": "搜索互联网资料：黄金与实际利率的关系。",
            "cache_policy": "bypass",
        },
    ).json()
    approval = pending["approval"]

    denied_response = client.post(
        f"/api/runs/{pending['run_id']}/approvals/{approval['approval_id']}/deny",
        headers={"X-AurumLab-Approval-Intent": "deny"},
    )

    assert denied_response.status_code == 200
    denied = denied_response.json()
    assert denied["status"] == "rejected"
    assert denied["approval"]["status"] == "denied"
    assert denied["plan"]["paused_step"] is None
    assert denied["plan"]["rejected_step"] == "search_external_knowledge"
    assert denied["plan"]["skipped_steps"] == ["summarize_external_research"]
    assert denied["events"][-1]["stage"] == "approval_denied"
    assert not [item for item in denied["tool_audit"] if item["phase"] == "execution"]


def test_cache_stats_api_is_observable():
    response = client.get("/api/cache/stats")

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["entries"] >= 0
