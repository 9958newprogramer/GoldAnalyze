"""Bounded planning and deterministic step authorization for Skill workflows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from app.models import ExecutionPlan, PlanStep, SkillDescriptor


class PlanValidationError(ValueError):
    """Raised before execution when a plan exceeds its Skill capability boundary."""


class PlanExecutionError(RuntimeError):
    """Raised when the deterministic executor deviates from its validated plan."""


class PlanValidator:
    """Validate an untrusted plan against the selected server-side Skill descriptor."""

    def validate(self, plan: ExecutionPlan, skill: SkillDescriptor) -> ExecutionPlan:
        errors: list[str] = []
        if plan.skill != skill.name or plan.skill_version != skill.version:
            errors.append("plan 与选中的 Skill 版本不一致")
        if plan.max_tool_calls != skill.max_tool_calls:
            errors.append("plan 的 Tool 预算与 Skill 不一致")

        step_ids = [step.step_id for step in plan.steps]
        if step_ids != skill.steps:
            errors.append("plan 步骤必须与 Skill Manifest 的有序步骤完全一致")
        if len(step_ids) != len(set(step_ids)):
            errors.append("plan 包含重复步骤")

        seen: set[str] = set()
        tool_calls = 0
        for expected_order, step in enumerate(plan.steps, start=1):
            if step.order != expected_order:
                errors.append(f"步骤 {step.step_id} 的 order 不连续")
            if not set(step.depends_on).issubset(seen):
                errors.append(f"步骤 {step.step_id} 引用了未完成或未知依赖")
            if expected_order > 1 and plan.steps[expected_order - 2].step_id not in step.depends_on:
                errors.append(f"步骤 {step.step_id} 未依赖紧邻前序步骤")
            if step.kind == "tool":
                tool_calls += 1
                if step.tool_name not in skill.allowed_tools:
                    errors.append(f"步骤 {step.step_id} 绑定了越权 Tool")
            elif step.step_id in skill.allowed_tools:
                errors.append(f"Tool 步骤 {step.step_id} 被伪装为 control step")
            seen.add(step.step_id)

        if tool_calls != plan.planned_tool_calls:
            errors.append("planned_tool_calls 与实际 Tool 步骤数不一致")
        if tool_calls > skill.max_tool_calls:
            errors.append("plan 超出 Skill Tool 调用预算")
        if errors:
            raise PlanValidationError("；".join(errors))
        return plan.model_copy(update={"validated": True})


class BoundedPlanner:
    """Compile a versioned Skill manifest into a minimal, fully bounded plan."""

    name = "deterministic-skill-planner@0.1.0"

    def __init__(self, validator: PlanValidator | None = None):
        self.validator = validator or PlanValidator()

    def build(self, skill: SkillDescriptor) -> ExecutionPlan:
        steps: list[PlanStep] = []
        for index, step_id in enumerate(skill.steps, start=1):
            is_tool = step_id in skill.allowed_tools
            steps.append(
                PlanStep(
                    step_id=step_id,
                    order=index,
                    kind="tool" if is_tool else "control",
                    tool_name=step_id if is_tool else None,
                    depends_on=[skill.steps[index - 2]] if index > 1 else [],
                )
            )
        identity = {
            "skill": skill.name,
            "version": skill.version,
            "steps": [step.model_dump(mode="json") for step in steps],
            "max_tool_calls": skill.max_tool_calls,
        }
        plan_id = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        draft = ExecutionPlan(
            plan_id=plan_id,
            skill=skill.name,
            skill_version=skill.version,
            planner=self.name,
            steps=steps,
            max_tool_calls=skill.max_tool_calls,
            planned_tool_calls=sum(step.kind == "tool" for step in steps),
        )
        return self.validator.validate(draft, skill)


@dataclass
class PlanRuntime:
    """Fail closed if the executor enters a step outside the validated plan order."""

    plan: ExecutionPlan
    _cursor: int = 0
    _completed: list[str] = field(default_factory=list)
    _active: str | None = None
    _failed: str | None = None
    _paused: str | None = None

    def __post_init__(self) -> None:
        if not self.plan.validated:
            raise PlanExecutionError("拒绝执行未经验证的 plan")

    def begin(self, step_id: str, tool_name: str | None = None) -> None:
        if self._active is not None:
            raise PlanExecutionError(f"步骤 {self._active} 尚未结束")
        if self._cursor >= len(self.plan.steps):
            raise PlanExecutionError(f"plan 中不存在额外步骤 {step_id}")
        expected = self.plan.steps[self._cursor]
        if expected.step_id != step_id:
            raise PlanExecutionError(f"执行乱序：期望 {expected.step_id}，实际 {step_id}")
        if expected.tool_name != tool_name:
            raise PlanExecutionError(
                f"步骤 {step_id} Tool 绑定不一致：期望 {expected.tool_name}，实际 {tool_name}"
            )
        if any(dependency not in self._completed for dependency in expected.depends_on):
            raise PlanExecutionError(f"步骤 {step_id} 的依赖尚未完成")
        self._active = step_id

    def complete(self, step_id: str) -> None:
        if self._active != step_id:
            raise PlanExecutionError(f"无法完成未激活步骤 {step_id}")
        self._completed.append(step_id)
        self._active = None
        self._cursor += 1

    def fail(self, step_id: str) -> None:
        if self._active == step_id:
            self._active = None
        self._failed = step_id

    def pause(self, step_id: str) -> None:
        if self._active != step_id:
            raise PlanExecutionError(f"无法暂停未激活步骤 {step_id}")
        self._active = None
        self._paused = step_id

    def snapshot(self) -> ExecutionPlan:
        remaining = [step.step_id for step in self.plan.steps[self._cursor :]]
        return self.plan.model_copy(
            update={
                "completed_steps": list(self._completed),
                "skipped_steps": remaining if self._failed is None and self._paused is None else [],
                "failed_step": self._failed,
                "paused_step": self._paused,
            }
        )
