"""Governed Tool Registry with risk metadata, review decisions, budget, and audit."""

from __future__ import annotations

import hmac
import re
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Literal

from app.approval import (
    ApprovalBinding,
    ApprovalBindingError,
    ApprovalRepository,
    ApprovalRequired,
    arguments_digest,
)
from app.models import ApprovalRequest
from app.observability import Telemetry

ToolHandler = Callable[..., Awaitable[Any]]
ToolEffect = Literal["read", "external", "write", "privileged"]
ToolRisk = Literal["low", "medium", "high"]
PolicyAction = Literal["allow", "deny", "review"]


@dataclass(frozen=True)
class ToolMetadata:
    effect: ToolEffect
    risk: ToolRisk
    requires_approval: bool = False
    source: Literal["local", "mcp"] = "local"

    def __post_init__(self) -> None:
        if self.effect not in {"read", "external", "write", "privileged"}:
            raise ValueError("unsupported Tool effect")
        if self.risk not in {"low", "medium", "high"}:
            raise ValueError("unsupported Tool risk")
        if self.source not in {"local", "mcp"}:
            raise ValueError("unsupported Tool source")
        if type(self.requires_approval) is not bool:
            raise ValueError("requires_approval must be a boolean")
        if self.effect in {"external", "write", "privileged"} and self.risk == "low":
            raise ValueError("external/write/privileged Tools cannot be low risk")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    handler: ToolHandler
    metadata: ToolMetadata


@dataclass(frozen=True)
class GovernanceDecision:
    action: PolicyAction
    reason: str
    tool_name: str
    effect: ToolEffect
    risk: ToolRisk


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, name: str, handler: ToolHandler, metadata: ToolMetadata) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name) is None:
            raise ValueError(f"Tool 名称不合法：{name}")
        if name in self._definitions:
            raise ValueError(f"Tool 已注册：{name}")
        self._definitions[name] = ToolDefinition(
            name=name,
            handler=handler,
            metadata=metadata,
        )

    def get(self, name: str) -> ToolHandler:
        return self.get_definition(name).handler

    def get_definition(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"Tool 未注册：{name}") from exc

    def contains(self, name: str) -> bool:
        return name in self._definitions

    def list_names(self) -> list[str]:
        return sorted(self._definitions)

    def list_definitions(self) -> list[ToolDefinition]:
        return [self._definitions[name] for name in self.list_names()]


@dataclass
class ToolPolicy:
    policy_name: str
    allowed_tools: set[str]
    max_calls: int
    denied_effects: set[ToolEffect] = field(default_factory=lambda: {"privileged"})
    review_effects: set[ToolEffect] = field(default_factory=lambda: {"external", "write"})
    review_risks: set[ToolRisk] = field(default_factory=lambda: {"high"})
    calls: int = 0
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    def evaluate(self, definition: ToolDefinition) -> GovernanceDecision:
        metadata = definition.metadata
        if definition.name not in self.allowed_tools:
            return GovernanceDecision(
                action="deny",
                reason="not_allowlisted",
                tool_name=definition.name,
                effect=metadata.effect,
                risk=metadata.risk,
            )
        if metadata.effect in self.denied_effects:
            return GovernanceDecision(
                action="deny",
                reason="effect_blocked",
                tool_name=definition.name,
                effect=metadata.effect,
                risk=metadata.risk,
            )
        if (
            metadata.requires_approval
            or metadata.effect in self.review_effects
            or metadata.risk in self.review_risks
        ):
            return GovernanceDecision(
                action="review",
                reason=(
                    "tool_requires_approval"
                    if metadata.requires_approval
                    else (
                        "effect_requires_approval"
                        if metadata.effect in self.review_effects
                        else "risk_requires_approval"
                    )
                ),
                tool_name=definition.name,
                effect=metadata.effect,
                risk=metadata.risk,
            )
        return GovernanceDecision(
            action="allow",
            reason="policy_allowed",
            tool_name=definition.name,
            effect=metadata.effect,
            risk=metadata.risk,
        )

    def ensure_budget(self, decision: GovernanceDecision) -> None:
        if self.calls >= self.max_calls:
            self.record(decision, action="deny", reason="budget_exhausted")
            raise PermissionError("Skill Tool 调用预算已耗尽")

    def authorize(
        self,
        decision: GovernanceDecision,
        *,
        reason: str | None = None,
        approval_id: str | None = None,
    ) -> int:
        self.ensure_budget(decision)
        self.calls += 1
        self.record(
            decision,
            action="allow",
            reason=reason or decision.reason,
            approval_id=approval_id,
            call=self.calls,
        )
        return self.calls

    def record(
        self,
        decision: GovernanceDecision,
        *,
        action: PolicyAction | None = None,
        reason: str | None = None,
        approval_id: str | None = None,
        call: int | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "phase": "authorization",
            "policy": self.policy_name,
            "tool": decision.tool_name,
            "decision": action or decision.action,
            "reason": reason or decision.reason,
            "effect": decision.effect,
            "risk": decision.risk,
        }
        if approval_id is not None:
            entry["approval_id"] = approval_id
        if call is not None:
            entry["call"] = call
        self.audit_log.append(entry)


class ToolGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolPolicy,
        *,
        approvals: ApprovalRepository | None = None,
        run_id: str | None = None,
        plan_id: str | None = None,
        approval_token: str | None = None,
        trusted_consumed_approval: ApprovalRequest | None = None,
        on_approval_consumed: Callable[[ApprovalRequest], None] | None = None,
        telemetry: Telemetry | None = None,
    ):
        self.registry = registry
        self.policy = policy
        self.approvals = approvals
        self.run_id = run_id
        self.plan_id = plan_id
        self.approval_token = approval_token
        self.trusted_consumed_approval = trusted_consumed_approval
        self.on_approval_consumed = on_approval_consumed
        self.telemetry = telemetry
        self.consumed_approval: ApprovalRequest | None = None
        self._request_memory: dict[tuple[str, str], Any] = {}

    async def call(
        self,
        tool_name: str,
        *,
        memory_key: str | None = None,
        step_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            definition = self.registry.get_definition(tool_name)
        except KeyError:
            self.policy.audit_log.append(
                {
                    "phase": "authorization",
                    "policy": self.policy.policy_name,
                    "tool": tool_name,
                    "decision": "deny",
                    "reason": "unknown_tool",
                }
            )
            if self.telemetry is not None:
                self.telemetry.count(
                    "aurumlab.agent.tool.decisions",
                    attributes={"tool": "unknown", "decision": "deny"},
                )
            raise
        decision = self.policy.evaluate(definition)
        attributes = {
            "aurumlab.tool.name": tool_name,
            "aurumlab.tool.effect": decision.effect,
            "aurumlab.tool.risk": decision.risk,
            "aurumlab.tool.decision": decision.action,
        }
        scope = (
            self.telemetry.span("aurumlab.tool.call", attributes=attributes)
            if self.telemetry is not None
            else nullcontext()
        )
        with scope:
            if self.telemetry is not None:
                self.telemetry.count(
                    "aurumlab.agent.tool.decisions",
                    attributes={
                        "tool": tool_name,
                        "effect": decision.effect,
                        "risk": decision.risk,
                        "decision": decision.action,
                    },
                )
            return await self._call_evaluated(
                definition,
                decision,
                tool_name=tool_name,
                memory_key=memory_key,
                step_id=step_id,
                arguments=kwargs,
            )

    async def _call_evaluated(
        self,
        definition: ToolDefinition,
        decision: GovernanceDecision,
        *,
        tool_name: str,
        memory_key: str | None,
        step_id: str | None,
        arguments: dict[str, Any],
    ) -> Any:
        memory_slot = (tool_name, memory_key) if memory_key is not None else None
        if memory_slot is not None and memory_slot in self._request_memory:
            if decision.action == "deny":
                self.policy.record(decision)
                raise PermissionError(f"Skill 不允许调用 Tool：{tool_name}")
            self.policy.audit_log.append(
                {
                    "phase": "request_memory",
                    "policy": self.policy.policy_name,
                    "tool": tool_name,
                    "decision": "hit",
                    "effect": decision.effect,
                    "risk": decision.risk,
                }
            )
            return self._request_memory[memory_slot]

        if decision.action == "deny":
            self.policy.record(decision)
            raise PermissionError(f"Skill 不允许调用 Tool：{tool_name}")
        self.policy.ensure_budget(decision)
        approval_id: str | None = None
        authorization_reason = decision.reason
        if decision.action == "review":
            approvals, binding = self._approval_binding(
                step_id=step_id or tool_name,
                decision=decision,
                arguments=arguments,
            )
            if self.approval_token is None and self.trusted_consumed_approval is None:
                approval = approvals.request(binding)
                self.policy.record(
                    decision,
                    approval_id=approval.approval_id,
                )
                raise ApprovalRequired(approval)
            if self.trusted_consumed_approval is not None:
                consumed = self._validate_trusted_approval(approvals, binding)
            else:
                consumed = approvals.consume(self.approval_token or "", binding)
            self.consumed_approval = consumed
            approval_id = consumed.approval_id
            authorization_reason = "human_approval_consumed"
            if self.on_approval_consumed is not None:
                self.on_approval_consumed(consumed)

        call_number = self.policy.authorize(
            decision,
            reason=authorization_reason,
            approval_id=approval_id,
        )
        started = perf_counter()
        try:
            result = await definition.handler(**arguments)
            duration_ms = round((perf_counter() - started) * 1_000, 2)
            self.policy.audit_log.append(
                {
                    "phase": "execution",
                    "policy": self.policy.policy_name,
                    "tool": tool_name,
                    "call": call_number,
                    "outcome": "completed",
                    "effect": decision.effect,
                    "risk": decision.risk,
                    "duration_ms": duration_ms,
                }
            )
            if self.telemetry is not None:
                metric_attributes = {"tool": tool_name, "outcome": "completed"}
                self.telemetry.count("aurumlab.agent.tool.calls", attributes=metric_attributes)
                self.telemetry.record(
                    "aurumlab.agent.tool.duration",
                    duration_ms,
                    attributes=metric_attributes,
                )
            if memory_slot is not None:
                self._request_memory[memory_slot] = result
            return result
        except Exception as exc:
            duration_ms = round((perf_counter() - started) * 1_000, 2)
            self.policy.audit_log.append(
                {
                    "phase": "execution",
                    "policy": self.policy.policy_name,
                    "tool": tool_name,
                    "call": call_number,
                    "outcome": "failed",
                    "effect": decision.effect,
                    "risk": decision.risk,
                    "error_type": type(exc).__name__,
                    "duration_ms": duration_ms,
                }
            )
            if self.telemetry is not None:
                metric_attributes = {"tool": tool_name, "outcome": "failed"}
                self.telemetry.count("aurumlab.agent.tool.calls", attributes=metric_attributes)
                self.telemetry.record(
                    "aurumlab.agent.tool.duration",
                    duration_ms,
                    attributes=metric_attributes,
                )
            raise

    def _approval_binding(
        self,
        *,
        step_id: str,
        decision: GovernanceDecision,
        arguments: dict[str, Any],
    ) -> tuple[ApprovalRepository, ApprovalBinding]:
        if self.approvals is None or self.run_id is None or self.plan_id is None:
            self.policy.record(decision, action="deny", reason="approval_context_missing")
            raise PermissionError("Tool 需要人工审批，但审批上下文不可用")
        return (
            self.approvals,
            ApprovalBinding(
                run_id=self.run_id,
                plan_id=self.plan_id,
                step_id=step_id,
                tool_name=decision.tool_name,
                arguments_digest=arguments_digest(arguments),
                effect=decision.effect,
                risk=decision.risk,
                reason=(
                    f"Tool {decision.tool_name} has {decision.effect}/{decision.risk} effects "
                    "and requires explicit human approval."
                ),
            ),
        )

    def _validate_trusted_approval(
        self,
        approvals: ApprovalRepository,
        binding: ApprovalBinding,
    ) -> ApprovalRequest:
        trusted = self.trusted_consumed_approval
        if trusted is None:
            raise ApprovalBindingError("trusted approval is missing")
        persisted = approvals.get(trusted.approval_id)
        expected = (
            binding.run_id,
            binding.plan_id,
            binding.step_id,
            binding.tool_name,
            binding.arguments_digest,
            binding.effect,
            binding.risk,
        )
        actual = (
            persisted.run_id,
            persisted.plan_id,
            persisted.step_id,
            persisted.tool_name,
            persisted.arguments_digest,
            persisted.effect,
            persisted.risk,
        )
        if persisted.status != "consumed" or not all(
            hmac.compare_digest(str(left), str(right))
            for left, right in zip(expected, actual, strict=True)
        ):
            raise ApprovalBindingError("consumed approval does not match this Tool call")
        return persisted
