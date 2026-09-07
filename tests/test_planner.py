import pytest

from app.agent.planner import (
    BoundedPlanner,
    PlanExecutionError,
    PlanRuntime,
    PlanValidationError,
    PlanValidator,
)
from app.models import PlanStep, SkillDescriptor


def _skill() -> SkillDescriptor:
    return SkillDescriptor(
        name="example-skill",
        version="1.0.0",
        description="Planner test fixture",
        allowed_tools=["read_data", "summarize_data"],
        max_tool_calls=2,
        steps=["compile_request", "read_data", "summarize_data"],
    )


def test_bounded_planner_builds_a_valid_skill_plan():
    plan = BoundedPlanner().build(_skill())

    assert plan.validated is True
    assert plan.planned_tool_calls == 2
    assert [step.step_id for step in plan.steps] == [
        "compile_request",
        "read_data",
        "summarize_data",
    ]
    assert plan.steps[0].kind == "control"
    assert plan.steps[1].tool_name == "read_data"
    assert plan.steps[2].depends_on == ["read_data"]


def test_plan_validator_rejects_a_tool_outside_the_skill_allowlist():
    skill = _skill()
    plan = BoundedPlanner().build(skill)
    tampered_steps = list(plan.steps)
    tampered_steps[1] = PlanStep(
        step_id="read_data",
        order=2,
        kind="tool",
        tool_name="execute_shell",
        depends_on=["compile_request"],
    )

    with pytest.raises(PlanValidationError, match="越权 Tool"):
        PlanValidator().validate(
            plan.model_copy(update={"steps": tampered_steps, "validated": False}),
            skill,
        )


def test_plan_validator_rejects_a_plan_over_the_tool_budget():
    under_budgeted_skill = _skill().model_copy(update={"max_tool_calls": 1})

    with pytest.raises(PlanValidationError, match="超出 Skill Tool 调用预算"):
        BoundedPlanner().build(under_budgeted_skill)


def test_plan_validator_rejects_unknown_or_repeated_steps():
    skill = _skill()
    plan = BoundedPlanner().build(skill)
    tampered_steps = list(plan.steps)
    tampered_steps[2] = tampered_steps[2].model_copy(update={"step_id": "read_data"})

    with pytest.raises(PlanValidationError, match="有序步骤完全一致"):
        PlanValidator().validate(
            plan.model_copy(update={"steps": tampered_steps, "validated": False}),
            skill,
        )


def test_plan_validator_rejects_a_forward_or_cyclic_dependency():
    skill = _skill()
    plan = BoundedPlanner().build(skill)
    tampered_steps = list(plan.steps)
    tampered_steps[0] = PlanStep(
        step_id="compile_request",
        order=1,
        kind="control",
        depends_on=["read_data"],
    )

    with pytest.raises(PlanValidationError, match="未知依赖"):
        PlanValidator().validate(
            plan.model_copy(update={"steps": tampered_steps, "validated": False}),
            skill,
        )


def test_plan_runtime_rejects_out_of_order_execution():
    runtime = PlanRuntime(BoundedPlanner().build(_skill()))

    with pytest.raises(PlanExecutionError, match="执行乱序"):
        runtime.begin("read_data", "read_data")


def test_plan_runtime_records_completed_and_skipped_steps():
    runtime = PlanRuntime(BoundedPlanner().build(_skill()))
    runtime.begin("compile_request")
    runtime.complete("compile_request")

    snapshot = runtime.snapshot()

    assert snapshot.completed_steps == ["compile_request"]
    assert snapshot.skipped_steps == ["read_data", "summarize_data"]
    assert snapshot.failed_step is None
