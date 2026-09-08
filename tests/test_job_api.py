from dataclasses import replace

import fakeredis.aioredis
from fastapi.testclient import TestClient

import app.main as main_module
from app.bootstrap import build_services
from app.config import Settings
from app.jobs import AgentWorker, JobSubmissionService, RedisStreamBroker
from app.storage import JobRepository


def configured_app(monkeypatch, tmp_path):
    settings = Settings(
        app_database_path=str(tmp_path / "runtime.db"),
        sse_poll_interval_seconds=0.01,
        sse_heartbeat_seconds=0.1,
    )
    services = build_services(settings)
    jobs = JobRepository(settings.resolved_app_database_path)
    broker = RedisStreamBroker(
        fakeredis.aioredis.FakeRedis(decode_responses=True),
        stream_name="test:jobs",
        group_name="test-workers",
    )
    services = replace(
        services,
        jobs=jobs,
        job_broker=broker,
        job_submission=JobSubmissionService(jobs, broker),
    )
    monkeypatch.setattr(main_module, "services", services)
    return services


def test_async_job_api_is_idempotent_and_returns_immediately(monkeypatch, tmp_path):
    configured_app(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        first = client.post(
            "/api/jobs",
            headers={"Idempotency-Key": "request-42"},
            json={"question": "查询黄金最近5根日K线。", "cache_policy": "bypass"},
        )
        repeated = client.post(
            "/api/jobs",
            headers={"Idempotency-Key": "request-42"},
            json={"question": "查询黄金最近5根日K线。", "cache_policy": "bypass"},
        )
        conflict = client.post(
            "/api/jobs",
            headers={"Idempotency-Key": "request-42"},
            json={"question": "查询黄金最近6根日K线。", "cache_policy": "bypass"},
        )

    assert first.status_code == repeated.status_code == 202
    assert first.json()["status"] == "queued"
    assert first.json()["job_id"] == repeated.json()["job_id"]
    assert first.headers["x-aurumlab-job-created"] == "true"
    assert repeated.headers["x-aurumlab-job-created"] == "false"
    assert first.headers["location"].endswith(first.json()["job_id"])
    assert conflict.status_code == 409


def test_worker_result_and_sse_support_last_event_id_reconnect(monkeypatch, tmp_path):
    services = configured_app(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        created = client.post(
            "/api/jobs",
            json={"question": "查询黄金最近5根日K线。", "cache_policy": "bypass"},
        ).json()
        worker = AgentWorker(
            worker_id="api-test-worker",
            agent=services.agent,
            jobs=services.jobs,
            broker=services.job_broker,
        )
        client.portal.call(lambda: worker.run_once(block_ms=1))

        job = client.get(f"/api/jobs/{created['job_id']}")
        all_events = client.get(f"/api/jobs/{created['job_id']}/stream")
        resumed = client.get(
            f"/api/jobs/{created['job_id']}/stream",
            headers={"Last-Event-ID": "2"},
        )

    assert job.json()["status"] == "completed"
    assert "id: 1" in all_events.text
    assert "event: job_completed" in all_events.text
    assert "id: 1\n" not in resumed.text
    assert "id: 2\n" not in resumed.text
    assert "id: 3\n" in resumed.text
    assert all_events.headers["cache-control"] == "no-cache, no-transform"


def test_cancel_endpoint_is_idempotent_for_queued_job(monkeypatch, tmp_path):
    configured_app(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        job = client.post(
            "/api/jobs",
            json={"question": "查询黄金最近5根日K线。"},
        ).json()
        first = client.delete(f"/api/jobs/{job['job_id']}")
        repeated = client.delete(f"/api/jobs/{job['job_id']}")

    assert first.status_code == repeated.status_code == 200
    assert first.json()["status"] == repeated.json()["status"] == "cancelled"


def test_job_api_rejects_unsafe_identifiers_and_cursors(monkeypatch, tmp_path):
    configured_app(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        bad_job = client.get("/api/jobs/not-a-job")
        created = client.post("/api/jobs", json={"question": "查询黄金最近5根日K线。"}).json()
        bad_cursor = client.get(
            f"/api/jobs/{created['job_id']}/stream",
            headers={"Last-Event-ID": "1\r\nevent: injected"},
        )

    assert bad_job.status_code == 400
    assert bad_cursor.status_code in {400, 422}


def test_async_approval_resume_consumes_token_before_requeue(monkeypatch, tmp_path):
    services = configured_app(monkeypatch, tmp_path)
    with TestClient(main_module.app) as client:
        created = client.post(
            "/api/jobs",
            json={
                "question": "搜索互联网最新黄金新闻并返回来源。",
                "cache_policy": "bypass",
            },
        ).json()
        worker = AgentWorker(
            worker_id="approval-worker",
            agent=services.agent,
            jobs=services.jobs,
            broker=services.job_broker,
        )
        client.portal.call(lambda: worker.run_once(block_ms=1))
        waiting = client.get(f"/api/jobs/{created['job_id']}").json()
        pending_run = client.get(f"/api/runs/{waiting['run_id']}").json()
        approval = pending_run["approval"]
        grant = client.post(
            f"/api/runs/{waiting['run_id']}/approvals/{approval['approval_id']}/approve",
            headers={"X-AurumLab-Approval-Intent": "approve"},
        ).json()

        resumed = client.post(
            f"/api/jobs/{created['job_id']}/resume",
            json={"approval_token": grant["approval_token"]},
        )
        replay = client.post(
            f"/api/jobs/{created['job_id']}/resume",
            json={"approval_token": grant["approval_token"]},
        )
        client.portal.call(lambda: worker.run_once(block_ms=1))
        completed = client.get(f"/api/jobs/{created['job_id']}").json()

    assert waiting["status"] == "waiting_approval"
    assert resumed.status_code == replay.status_code == 202
    assert resumed.json()["status"] == replay.json()["status"] == "queued"
    assert completed["status"] == "completed"
    assert services.approvals.get(approval["approval_id"]).status == "consumed"
