import pytest

from app.agent.incident import compile_incident_spec
from app.approval import ApprovalBinding
from app.bootstrap import build_services
from app.config import Settings
from app.storage import JobRepository

QUESTION = "复盘 checkout-api 服务事故：错误率12%，P95延迟1800ms，持续35分钟，影响2400个请求。"


def test_incident_compiler_builds_a_bounded_typed_spec():
    spec = compile_incident_spec(QUESTION)

    assert spec.service == "checkout-api"
    assert spec.error_rate_pct == 12
    assert spec.p95_latency_ms == 1800
    assert spec.duration_minutes == 35
    assert spec.affected_requests == 2400


def test_incident_compiler_fails_closed_without_measurable_evidence():
    with pytest.raises(ValueError, match="可量化"):
        compile_incident_spec("请复盘 checkout-api 服务事故。")


async def test_incident_skill_executes_the_full_governed_workflow(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runtime.db")))
    pending = await services.agent.run(QUESTION, cache_policy="bypass")
    assert pending.status == "pending_approval"
    assert pending.approval is not None
    assert pending.approval.tool_name == "mcp__runtime__profile_text"
    grant = services.approvals.approve(
        pending.approval.approval_id,
        decided_by="test-operator",
    )
    response = await services.agent.resume(pending.run_id, grant.approval_token)

    assert response.status == "completed"
    assert response.route is not None and response.route.intent == "incident_review"
    assert response.skill == "incident-review@1.0.0"
    assert response.incident_spec is not None
    assert response.incident_result is not None
    assert response.incident_result.severity == "SEV-2"
    assert response.incident_result.root_cause_status == "unverified"
    assert len(response.incident_result.action_items) == 3
    assert response.plan is not None and response.plan.planned_tool_calls == 5
    assert response.plan.completed_steps == [step.step_id for step in response.plan.steps]
    assert [event.stage for event in response.events] == [
        "route_intent",
        "select_skill",
        "build_plan",
        "compile_incident_spec",
        "cache_lookup",
        "approval_resume",
        "mcp__runtime__profile_text",
        "validate_incident_spec",
        "assess_incident_impact",
        "build_incident_action_plan",
        "summarize_incident_review",
    ]
    executions = [item for item in response.tool_audit if item["phase"] == "execution"]
    assert [item["tool"] for item in executions] == [
        "mcp__runtime__profile_text",
        "validate_incident_spec",
        "assess_incident_impact",
        "build_incident_action_plan",
        "summarize_incident_review",
    ]
    assert executions[0]["effect"] == "external" and executions[0]["risk"] == "medium"
    assert all(item["effect"] == "read" and item["risk"] == "low" for item in executions[1:])
    definition = services.agent.tools.get_definition("mcp__runtime__profile_text")
    assert definition.metadata.source == "mcp"
    await services.mcp_clients.stop()


async def test_incident_artifact_can_be_reused_without_tool_calls(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runtime.db")))
    pending = await services.agent.run(QUESTION, cache_policy="refresh")
    assert pending.approval is not None
    grant = services.approvals.approve(
        pending.approval.approval_id,
        decided_by="test-operator",
    )
    source = await services.agent.resume(pending.run_id, grant.approval_token)

    reused = await services.agent.run(QUESTION, cache_policy="use")

    assert reused.cache_status == "exact_hit"
    assert reused.execution_mode == "cache"
    assert reused.incident_result == source.incident_result
    assert reused.tool_audit == []
    assert reused.cache is not None and reused.cache.saved_tool_calls == 5
    await services.mcp_clients.stop()


async def test_incident_checkpoint_recovers_without_reexecuting_completed_steps(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runtime.db")))
    jobs = JobRepository(tmp_path / "runtime.db")
    job, _ = jobs.create(QUESTION, "bypass", 2, idempotency_key=None)

    class SimulatedCrash(BaseException):
        pass

    pending = await services.agent.run_checkpointed(
        QUESTION,
        job_id=job.job_id,
        cache_policy="bypass",
        checkpoint_sink=jobs.save_checkpoint_and_event,
        cancel_probe=lambda: False,
        stage_timeout_seconds=5,
    )
    assert pending.status == "pending_approval"
    assert pending.approval is not None
    grant = services.approvals.approve(
        pending.approval.approval_id,
        decided_by="test-operator",
    )
    approval = pending.approval
    consumed = services.approvals.consume(
        grant.approval_token,
        ApprovalBinding(
            run_id=approval.run_id,
            plan_id=approval.plan_id,
            step_id=approval.step_id,
            tool_name=approval.tool_name,
            arguments_digest=approval.arguments_digest,
            effect=approval.effect,
            risk=approval.risk,
            reason=approval.reason,
        ),
    )

    def crash_after_assessment(checkpoint):
        jobs.save_checkpoint_and_event(checkpoint)
        if checkpoint.completed_steps[-1] == "assess_incident_impact":
            raise SimulatedCrash

    checkpoint = jobs.get_checkpoint(job.job_id)
    assert checkpoint is not None
    with pytest.raises(SimulatedCrash):
        await services.agent.run_checkpointed(
            QUESTION,
            job_id=job.job_id,
            cache_policy="bypass",
            checkpoint_sink=crash_after_assessment,
            cancel_probe=lambda: False,
            stage_timeout_seconds=5,
            checkpoint=checkpoint,
            trusted_consumed_approval=consumed,
        )
    checkpoint = jobs.get_checkpoint(job.job_id)
    assert checkpoint is not None

    restored = await services.agent.run_checkpointed(
        QUESTION,
        job_id=job.job_id,
        cache_policy="bypass",
        checkpoint_sink=jobs.save_checkpoint_and_event,
        cancel_probe=lambda: False,
        stage_timeout_seconds=5,
        checkpoint=checkpoint,
        trusted_consumed_approval=consumed,
    )

    assert restored.status == "completed"
    assert restored.incident_result is not None
    assert len(restored.events) == len({event.stage for event in restored.events}) == 11
    await services.mcp_clients.stop()


async def test_sensitive_incident_input_is_rejected_and_redacted_before_storage(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runtime.db")))

    response = await services.agent.run(
        "复盘 checkout-api 事故：错误率12%，token=super-secret-value。"
    )

    assert response.status == "rejected"
    assert response.tool_audit == []
    assert "super-secret-value" not in response.question
    assert "[REDACTED]" in response.question
    assert "super-secret-value" not in response.model_dump_json()
