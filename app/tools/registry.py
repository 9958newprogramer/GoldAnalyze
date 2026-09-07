"""Small governed Tool Registry used by the Agent workflow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

ToolHandler = Callable[..., Awaitable[Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        if name in self._handlers:
            raise ValueError(f"Tool 已注册：{name}")
        self._handlers[name] = handler

    def get(self, name: str) -> ToolHandler:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise KeyError(f"Tool 未注册：{name}") from exc

    def contains(self, name: str) -> bool:
        return name in self._handlers

    def list_names(self) -> list[str]:
        return sorted(self._handlers)


@dataclass
class ToolPolicy:
    policy_name: str
    allowed_tools: set[str]
    max_calls: int
    calls: int = 0
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    def authorize(self, tool_name: str) -> int:
        if tool_name not in self.allowed_tools:
            self.audit_log.append(
                {
                    "phase": "authorization",
                    "policy": self.policy_name,
                    "tool": tool_name,
                    "decision": "deny",
                    "reason": "not_allowlisted",
                }
            )
            raise PermissionError(f"Skill 不允许调用 Tool：{tool_name}")
        if self.calls >= self.max_calls:
            self.audit_log.append(
                {
                    "phase": "authorization",
                    "policy": self.policy_name,
                    "tool": tool_name,
                    "decision": "deny",
                    "reason": "budget_exhausted",
                }
            )
            raise PermissionError("Skill Tool 调用预算已耗尽")
        self.calls += 1
        self.audit_log.append(
            {
                "phase": "authorization",
                "policy": self.policy_name,
                "tool": tool_name,
                "decision": "allow",
                "call": self.calls,
            }
        )
        return self.calls


class ToolGateway:
    def __init__(self, registry: ToolRegistry, policy: ToolPolicy):
        self.registry = registry
        self.policy = policy
        self._request_memory: dict[tuple[str, str], Any] = {}

    async def call(self, tool_name: str, *, memory_key: str | None = None, **kwargs: Any) -> Any:
        memory_slot = (tool_name, memory_key) if memory_key is not None else None
        if memory_slot is not None and memory_slot in self._request_memory:
            if tool_name not in self.policy.allowed_tools:
                self.policy.authorize(tool_name)
            self.policy.audit_log.append(
                {
                    "phase": "request_memory",
                    "policy": self.policy.policy_name,
                    "tool": tool_name,
                    "decision": "hit",
                }
            )
            return self._request_memory[memory_slot]
        call_number = self.policy.authorize(tool_name)
        started = perf_counter()
        try:
            result = await self.registry.get(tool_name)(**kwargs)
            self.policy.audit_log.append(
                {
                    "phase": "execution",
                    "policy": self.policy.policy_name,
                    "tool": tool_name,
                    "call": call_number,
                    "outcome": "completed",
                    "duration_ms": round((perf_counter() - started) * 1_000, 2),
                }
            )
            if memory_slot is not None:
                self._request_memory[memory_slot] = result
            return result
        except Exception as exc:
            self.policy.audit_log.append(
                {
                    "phase": "execution",
                    "policy": self.policy.policy_name,
                    "tool": tool_name,
                    "call": call_number,
                    "outcome": "failed",
                    "error_type": type(exc).__name__,
                    "duration_ms": round((perf_counter() - started) * 1_000, 2),
                }
            )
            raise
