"""Governed MCP Client, dynamic Tool Catalog, and local Tool Registry adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from mcp import Client, types
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.tools.registry import ToolMetadata, ToolRegistry

MAX_SCHEMA_BYTES = 64 * 1024
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_DISCOVERY_PAGES = 10

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_UNSAFE_TOOL_METADATA = (
    re.compile(
        r"ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:reveal|print|return).{0,24}(?:system prompt|api[_ -]?key|secret)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:忽略|无视).{0,16}(?:之前|系统).{0,12}(?:指令|规则)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:泄露|显示|返回).{0,16}(?:系统提示词|密钥|密码|token)",
        re.IGNORECASE,
    ),
)


class MCPClientError(RuntimeError):
    """Base error for bounded MCP Client operations."""


class MCPDiscoveryError(MCPClientError):
    """Raised when remote Tool metadata cannot enter the trusted catalog."""


class MCPSchemaDriftError(MCPDiscoveryError):
    """Raised when a previously pinned Tool schema changes unexpectedly."""


class MCPToolCallError(MCPClientError):
    """Raised when a governed remote Tool invocation fails validation or execution."""


class MCPServerSpec(BaseModel):
    """Operator-owned bounds for one MCP Server connection."""

    model_config = ConfigDict(extra="forbid")

    server_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,47}$")
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    transport: Literal["in_process", "stdio"] = "in_process"
    connect_timeout_seconds: float = Field(default=3.0, ge=0.05, le=30)
    call_timeout_seconds: float = Field(default=5.0, ge=0.05, le=60)
    max_tools: int = Field(default=50, ge=1, le=200)
    expected_schema_fingerprints: dict[str, str] = Field(default_factory=dict)

    @field_validator("expected_schema_fingerprints")
    @classmethod
    def validate_schema_pins(cls, value: dict[str, str]) -> dict[str, str]:
        if any(re.fullmatch(r"[a-f0-9]{16}", item) is None for item in value.values()):
            raise ValueError("MCP schema pins must be 16 lowercase hexadecimal characters")
        return value


class MCPRemoteTool(BaseModel):
    """Validated, namespaced Tool metadata safe to expose to the runtime."""

    model_config = ConfigDict(extra="forbid")

    server_id: str
    server_name: str
    server_version: str
    protocol_version: str
    namespace: str
    remote_name: str
    qualified_name: str
    description: str = Field(default="", max_length=500)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    schema_fingerprint: str = Field(pattern=r"^[a-f0-9]{16}$")


class MCPServerHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: str
    namespace: str
    transport: Literal["in_process", "stdio"]
    status: Literal["disconnected", "connected", "degraded", "unavailable"]
    server_name: str = "unknown"
    server_version: str = "unknown"
    protocol_version: str = "unknown"
    discovered_tools: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    last_error_type: str | None = None
    checked_at: datetime


class MCPToolCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[MCPRemoteTool] = Field(default_factory=list)
    servers: list[MCPServerHealth] = Field(default_factory=list)

    @property
    def connected_servers(self) -> int:
        return sum(server.status == "connected" for server in self.servers)


@dataclass(frozen=True)
class MCPServerBinding:
    """Associate validated operator config with an SDK-supported MCP target."""

    spec: MCPServerSpec
    target: Any


@dataclass
class _ServerRuntime:
    binding: MCPServerBinding
    client: Client | None = None
    exit_stack: AsyncExitStack | None = None
    tools: dict[str, MCPRemoteTool] = field(default_factory=dict)
    pinned_fingerprints: dict[str, str] = field(default_factory=dict)
    health: MCPServerHealth | None = None


def _canonical_bytes(
    value: Any,
    error_type: type[MCPClientError] = MCPDiscoveryError,
) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise error_type("MCP payload is not canonical JSON") from exc


def _assert_size(value: Any, limit: int, label: str, error_type: type[MCPClientError]) -> None:
    if len(_canonical_bytes(value, error_type)) > limit:
        raise error_type(f"{label} exceeds the configured size limit")


def _walk_values(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _assert_no_instruction_injection(value: Any, label: str) -> None:
    for item in _walk_values(value):
        if isinstance(item, str) and any(pattern.search(item) for pattern in _UNSAFE_TOOL_METADATA):
            raise MCPDiscoveryError(f"{label} contains instruction-injection content")


def _assert_local_refs_only(value: Any) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#/"):
            raise MCPDiscoveryError("external JSON Schema references are not allowed")
        for item in value.values():
            _assert_local_refs_only(item)
    elif isinstance(value, list):
        for item in value:
            _assert_local_refs_only(item)


def _closed_object_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy a schema and deny undeclared fields for every explicit object shape."""
    copied = json.loads(json.dumps(schema))

    def close(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object" and isinstance(value.get("properties"), dict):
                value.setdefault("additionalProperties", False)
            for item in value.values():
                close(item)
        elif isinstance(value, list):
            for item in value:
                close(item)

    close(copied)
    return copied


def _validate_schema(schema: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise MCPDiscoveryError(f"{label} must be a JSON object")
    _assert_size(schema, MAX_SCHEMA_BYTES, label, MCPDiscoveryError)
    _assert_no_instruction_injection(schema, label)
    _assert_local_refs_only(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise MCPDiscoveryError(f"{label} is not a valid JSON Schema") from exc
    return schema


def schema_fingerprint(input_schema: dict[str, Any], output_schema: dict[str, Any] | None) -> str:
    """Return a stable contract fingerprint; prose metadata is deliberately excluded."""
    contract = {"input": input_schema, "output": output_schema}
    return hashlib.sha256(_canonical_bytes(contract)).hexdigest()[:16]


def _safe_remote_name(remote_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", remote_name.lower()).strip("_")
    if not _SAFE_NAME.fullmatch(normalized):
        raise MCPDiscoveryError("remote Tool name cannot be represented safely")
    return normalized


class MCPClientManager:
    """Own MCP sessions, pin discovered schemas, and adapt Tools into governance."""

    def __init__(self, bindings: list[MCPServerBinding]):
        server_ids = [binding.spec.server_id for binding in bindings]
        namespaces = [binding.spec.namespace for binding in bindings]
        if len(server_ids) != len(set(server_ids)):
            raise ValueError("MCP server_id must be unique")
        if len(namespaces) != len(set(namespaces)):
            raise ValueError("MCP namespace must be unique")
        self._runtimes = {
            binding.spec.server_id: _ServerRuntime(binding=binding) for binding in bindings
        }
        self._started = False
        self._registered_tools: set[str] = set()

    @property
    def started(self) -> bool:
        return self._started

    def snapshot(self) -> MCPToolCatalog:
        tools = sorted(
            (tool for runtime in self._runtimes.values() for tool in runtime.tools.values()),
            key=lambda item: item.qualified_name,
        )
        servers = sorted(
            (self._health(runtime) for runtime in self._runtimes.values()),
            key=lambda item: item.server_id,
        )
        return MCPToolCatalog(tools=tools, servers=servers)

    async def start(self) -> MCPToolCatalog:
        if self._started:
            return self.snapshot()
        self._started = True
        for runtime in self._runtimes.values():
            await self._connect(runtime)
        return self.snapshot()

    async def stop(self) -> None:
        for runtime in reversed(list(self._runtimes.values())):
            if runtime.exit_stack is not None:
                await runtime.exit_stack.aclose()
            runtime.client = None
            runtime.exit_stack = None
            spec = runtime.binding.spec
            runtime.health = MCPServerHealth(
                server_id=spec.server_id,
                namespace=spec.namespace,
                transport=spec.transport,
                status="disconnected",
                server_name=runtime.health.server_name if runtime.health else "unknown",
                server_version=runtime.health.server_version if runtime.health else "unknown",
                protocol_version=runtime.health.protocol_version if runtime.health else "unknown",
                discovered_tools=len(runtime.tools),
                checked_at=datetime.now(UTC),
            )
        self._started = False

    async def refresh(self, server_id: str) -> MCPToolCatalog:
        runtime = self._runtime(server_id)
        if runtime.client is None:
            raise MCPClientError("MCP server is not connected")
        try:
            await self._discover(runtime)
        except Exception as exc:
            self._set_error_health(runtime, "degraded", exc)
            raise
        return self.snapshot()

    def register_tools(self, registry: ToolRegistry) -> list[str]:
        """Register adapters only after discovery; ToolPolicy remains the authorization point."""
        registered: list[str] = []
        for descriptor in self.snapshot().tools:
            name = descriptor.qualified_name
            if name in self._registered_tools:
                continue
            if registry.contains(name):
                raise MCPDiscoveryError(f"MCP qualified Tool collides with local Tool: {name}")

            async def invoke(_qualified_name: str = name, **arguments: Any) -> Any:
                return await self.call(_qualified_name, arguments)

            registry.register(
                name,
                invoke,
                ToolMetadata(
                    effect="external",
                    risk="medium",
                    requires_approval=True,
                    source="mcp",
                ),
            )
            self._registered_tools.add(name)
            registered.append(name)
        return registered

    async def call(self, qualified_name: str, arguments: dict[str, Any]) -> Any:
        runtime, descriptor = self._resolve_tool(qualified_name)
        client = runtime.client
        if client is None or runtime.health is None or runtime.health.status != "connected":
            raise MCPToolCallError("MCP Tool server is not healthy")
        _assert_size(arguments, MAX_ARGUMENT_BYTES, "MCP Tool arguments", MCPToolCallError)
        guarded_input = _closed_object_schema(descriptor.input_schema)
        try:
            Draft202012Validator(
                guarded_input,
                format_checker=FormatChecker(),
            ).validate(arguments)
        except ValidationError as exc:
            raise MCPToolCallError("MCP Tool arguments failed schema validation") from exc

        try:
            async with asyncio.timeout(runtime.binding.spec.call_timeout_seconds):
                result = await client.call_tool(
                    descriptor.remote_name,
                    arguments,
                    read_timeout_seconds=runtime.binding.spec.call_timeout_seconds,
                )
        except TimeoutError as exc:
            self._set_error_health(runtime, "degraded", exc)
            raise MCPToolCallError("MCP Tool call timed out") from exc
        except Exception as exc:
            self._set_error_health(runtime, "degraded", exc)
            raise MCPToolCallError("MCP Tool call failed") from exc

        if not isinstance(result, types.CallToolResult) or result.is_error:
            raise MCPToolCallError("MCP Tool returned an error result")
        payload = result.structured_content
        if descriptor.output_schema is not None:
            if payload is None:
                raise MCPToolCallError("MCP Tool omitted its declared structured output")
            try:
                Draft202012Validator(
                    descriptor.output_schema,
                    format_checker=FormatChecker(),
                ).validate(payload)
            except ValidationError as exc:
                raise MCPToolCallError("MCP Tool output failed schema validation") from exc
        if payload is None:
            payload = {
                "text": [
                    item.text for item in result.content if isinstance(item, types.TextContent)
                ]
            }
        _assert_size(payload, MAX_RESULT_BYTES, "MCP Tool result", MCPToolCallError)
        try:
            _assert_no_instruction_injection(payload, "MCP Tool result")
        except MCPDiscoveryError as exc:
            raise MCPToolCallError("MCP Tool result was rejected by content policy") from exc
        return payload

    async def _connect(self, runtime: _ServerRuntime) -> None:
        spec = runtime.binding.spec
        started = perf_counter()
        stack = AsyncExitStack()
        try:
            client = Client(
                runtime.binding.target,
                raise_exceptions=False,
                read_timeout_seconds=spec.call_timeout_seconds,
            )
            async with asyncio.timeout(spec.connect_timeout_seconds):
                runtime.client = await stack.enter_async_context(client)
                runtime.exit_stack = stack
                await self._discover(runtime, started=started)
        except Exception as exc:  # noqa: BLE001 -- isolate unavailable MCP servers
            await stack.aclose()
            runtime.client = None
            runtime.exit_stack = None
            self._set_error_health(runtime, "unavailable", exc, started)

    async def _discover(
        self,
        runtime: _ServerRuntime,
        *,
        started: float | None = None,
    ) -> None:
        client = runtime.client
        if client is None:
            raise MCPClientError("MCP server is not connected")
        discovery_started = started or perf_counter()
        discovered: list[types.Tool] = []
        cursor: str | None = None
        for _ in range(MAX_DISCOVERY_PAGES):
            result = await client.list_tools(cursor=cursor, cache_mode="refresh")
            discovered.extend(result.tools)
            if len(discovered) > runtime.binding.spec.max_tools:
                raise MCPDiscoveryError("MCP server exceeded its Tool discovery limit")
            cursor = result.next_cursor
            if cursor is None:
                break
        else:
            raise MCPDiscoveryError("MCP Tool discovery exceeded the pagination limit")

        server_info = client.server_info
        server_name = server_info.name if server_info is not None else "unknown"
        server_version = server_info.version if server_info is not None else "unknown"
        protocol_version = str(client.protocol_version or "unknown")
        next_tools: dict[str, MCPRemoteTool] = {}
        safe_names: set[str] = set()
        for tool in discovered:
            safe_name = _safe_remote_name(tool.name)
            if safe_name in safe_names:
                raise MCPDiscoveryError("MCP Tool names collide after safe normalization")
            safe_names.add(safe_name)
            description = (tool.description or "")[:500]
            _assert_no_instruction_injection(description, "MCP Tool description")
            input_schema = _validate_schema(tool.input_schema, "MCP input schema")
            if input_schema.get("type") != "object":
                raise MCPDiscoveryError("MCP Tool input schema root must be an object")
            output_schema = (
                _validate_schema(tool.output_schema, "MCP output schema")
                if tool.output_schema is not None
                else None
            )
            fingerprint = schema_fingerprint(input_schema, output_schema)
            expected = runtime.binding.spec.expected_schema_fingerprints.get(tool.name)
            pinned = runtime.pinned_fingerprints.get(tool.name)
            if expected is not None and fingerprint != expected:
                raise MCPSchemaDriftError("MCP Tool schema differs from operator pin")
            if pinned is not None and fingerprint != pinned:
                raise MCPSchemaDriftError("MCP Tool schema changed after discovery")
            qualified_name = f"mcp__{runtime.binding.spec.namespace}__{safe_name}"
            if len(qualified_name) > 64:
                raise MCPDiscoveryError("qualified MCP Tool name exceeds the planner limit")
            next_tools[qualified_name] = MCPRemoteTool(
                server_id=runtime.binding.spec.server_id,
                server_name=server_name,
                server_version=server_version,
                protocol_version=protocol_version,
                namespace=runtime.binding.spec.namespace,
                remote_name=tool.name,
                qualified_name=qualified_name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                schema_fingerprint=fingerprint,
            )

        missing_pins = set(runtime.binding.spec.expected_schema_fingerprints) - {
            tool.name for tool in discovered
        }
        if missing_pins:
            raise MCPSchemaDriftError("operator-pinned MCP Tool is missing")
        runtime.pinned_fingerprints.update(
            {tool.remote_name: tool.schema_fingerprint for tool in next_tools.values()}
        )
        runtime.tools = next_tools
        runtime.health = MCPServerHealth(
            server_id=runtime.binding.spec.server_id,
            namespace=runtime.binding.spec.namespace,
            transport=runtime.binding.spec.transport,
            status="connected",
            server_name=server_name,
            server_version=server_version,
            protocol_version=protocol_version,
            discovered_tools=len(next_tools),
            latency_ms=round((perf_counter() - discovery_started) * 1_000, 2),
            checked_at=datetime.now(UTC),
        )

    def _runtime(self, server_id: str) -> _ServerRuntime:
        try:
            return self._runtimes[server_id]
        except KeyError as exc:
            raise MCPClientError("unknown MCP server") from exc

    def _resolve_tool(self, qualified_name: str) -> tuple[_ServerRuntime, MCPRemoteTool]:
        matches = [
            (runtime, runtime.tools[qualified_name])
            for runtime in self._runtimes.values()
            if qualified_name in runtime.tools
        ]
        if len(matches) != 1:
            raise MCPToolCallError("unknown or ambiguous MCP Tool")
        return matches[0]

    @staticmethod
    def _health(runtime: _ServerRuntime) -> MCPServerHealth:
        if runtime.health is not None:
            return runtime.health
        spec = runtime.binding.spec
        return MCPServerHealth(
            server_id=spec.server_id,
            namespace=spec.namespace,
            transport=spec.transport,
            status="disconnected",
            checked_at=datetime.now(UTC),
        )

    @staticmethod
    def _set_error_health(
        runtime: _ServerRuntime,
        status: Literal["degraded", "unavailable"],
        exc: Exception,
        started: float | None = None,
    ) -> None:
        spec = runtime.binding.spec
        previous = runtime.health
        runtime.health = MCPServerHealth(
            server_id=spec.server_id,
            namespace=spec.namespace,
            transport=spec.transport,
            status=status,
            server_name=previous.server_name if previous else "unknown",
            server_version=previous.server_version if previous else "unknown",
            protocol_version=previous.protocol_version if previous else "unknown",
            discovered_tools=len(runtime.tools),
            latency_ms=round((perf_counter() - started) * 1_000, 2) if started else 0,
            last_error_type=type(exc).__name__,
            checked_at=datetime.now(UTC),
        )
