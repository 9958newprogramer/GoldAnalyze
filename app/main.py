"""AurumLab HTTP API and dependency-free demonstration UI."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry.trace import SpanKind
from redis.exceptions import RedisError

from app import __version__
from app.approval import (
    ApprovalBinding,
    ApprovalBindingError,
    ApprovalNotFoundError,
    ApprovalStateError,
    ApprovalTokenError,
)
from app.bootstrap import build_services
from app.config import settings
from app.evals.models import EvalCase, EvalReport
from app.evals.runner import load_eval_cases
from app.jobs import SensitiveJobInputError
from app.mcp_client import MCPToolCatalog
from app.models import (
    AgentJob,
    ApprovalGrant,
    ApprovalRequest,
    CacheStats,
    JobCreateRequest,
    JobEvent,
    ResumeRunRequest,
    RunRequest,
    RunResponse,
    SkillDescriptor,
)
from app.storage import IdempotencyConflictError, InvalidJobTransitionError

services = build_services()
static_root = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await services.mcp_clients.start()
    services.mcp_clients.register_tools(services.agent.tools)
    try:
        yield
    finally:
        await services.mcp_clients.stop()
        await services.backtest_executor.close()
        await services.job_broker.close()
        services.telemetry.shutdown()


app = FastAPI(
    title="AurumLab",
    version=__version__,
    description=(
        "Agent engineering platform with versioned Skills, bounded plans, governed tools, "
        "MCP Client/Server, and Artifact Memory."
    ),
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=static_root), name="static")


@app.middleware("http")
async def observe_http(request: Request, call_next):
    method = request.method.upper()
    parent = services.telemetry.extract(request.headers)
    started = perf_counter()
    response: Response | None = None
    with services.telemetry.span(
        f"{method} request",
        attributes={"http.request.method": method},
        kind=SpanKind.SERVER,
        context=parent,
    ) as span:
        try:
            response = await call_next(request)
        except Exception as exc:
            services.telemetry.mark_error(span, type(exc).__name__)
            raise
        finally:
            status_code = response.status_code if response is not None else 500
            status_class = f"{status_code // 100}xx"
            route_object = request.scope.get("route")
            route = getattr(route_object, "path", "{unmatched}")
            if route == "{unmatched}" and request.url.path.startswith("/static/"):
                route = "/static/{asset}"
            if not isinstance(route, str) or len(route) > 160:
                route = "{unmatched}"
            span.update_name(f"{method} {route}")
            span.set_attribute("http.route", route)
            span.set_attribute("http.response.status_code", status_code)
            duration_ms = round((perf_counter() - started) * 1_000, 2)
            metric_attributes = {
                "method": method,
                "route": route,
                "status_class": status_class,
            }
            services.telemetry.count("aurumlab.http.server.requests", attributes=metric_attributes)
            services.telemetry.record(
                "aurumlab.http.server.duration", duration_ms, attributes=metric_attributes
            )
        if response is not None:
            trace_id = services.telemetry.current_trace_id()
            if trace_id is not None:
                response.headers["X-Trace-Id"] = trace_id
            return response
    raise RuntimeError("HTTP middleware completed without a response")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_root / "index.html")


@app.get("/api/health")
async def health() -> dict[str, object]:
    mcp_catalog = services.mcp_clients.snapshot()
    return {
        "status": "ok",
        "version": __version__,
        "router": services.agent.router.name,
        "router_mode": services.agent.router.mode,
        "planner": services.agent.planner.name,
        "router_model": services.settings.router_llm_model,
        "router_llm_configured": bool(services.settings.router_llm_api_key),
        "skills_count": len(services.skills.list()),
        "data_source": services.agent.market_repository.source_name,
        "synthetic_data": services.agent.market_repository.synthetic,
        "llm_configured": bool(services.settings.llm_api_key),
        "search_provider": services.agent.search_provider.name,
        "mcp_server_command": "aurumlab-mcp",
        "mcp_client_connected_servers": mcp_catalog.connected_servers,
        "mcp_client_discovered_tools": len(mcp_catalog.tools),
        "pending_approvals": services.approvals.pending_count(),
        "artifact_cache_enabled": services.artifacts.enabled,
        "artifact_cache_entries": services.artifacts.stats().active_entries,
        "artifact_cache_max_entries": services.artifacts.max_entries,
        "async_job_transport": "redis-streams",
        "async_job_stream": services.settings.job_stream_name,
        "async_job_consumer_group": services.settings.job_consumer_group,
        "otel_exporter": services.telemetry.exporter_name,
        "otel_service_name": services.telemetry.service_name,
    }


@app.get("/api/observability")
async def observability_snapshot(
    span_limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, object]:
    """Return bounded, redacted local evidence; OTLP remains the production export path."""
    return services.telemetry.snapshot(span_limit=span_limit)


@app.get("/api/skills", response_model=list[SkillDescriptor])
async def list_skills() -> list[SkillDescriptor]:
    return services.skills.list()


@app.get("/api/tools")
async def list_tools() -> dict[str, list[str]]:
    return {"tools": services.agent.tools.list_names()}


@app.get("/api/mcp/catalog", response_model=MCPToolCatalog)
async def mcp_tool_catalog() -> MCPToolCatalog:
    return services.mcp_clients.snapshot()


@app.get("/api/evals/cases", response_model=list[EvalCase])
async def list_eval_cases() -> list[EvalCase]:
    return load_eval_cases(services.settings.resolved_eval_dataset_path)


@app.get("/api/evals/latest", response_model=EvalReport)
async def latest_eval() -> EvalReport:
    report = services.eval_reports.latest()
    if report is None:
        raise HTTPException(status_code=404, detail="尚未执行评测")
    return report


@app.post("/api/evals/run", response_model=EvalReport)
async def run_eval(threshold: float = Query(default=90.0, ge=0, le=100)) -> EvalReport:
    report = await services.evaluator.run(threshold=threshold)
    services.eval_reports.save(report)
    return report


@app.post("/api/runs", response_model=RunResponse)
async def create_run(payload: RunRequest) -> RunResponse:
    return await services.agent.run(payload.question, cache_policy=payload.cache_policy)


def _validate_job_id(job_id: str) -> None:
    if len(job_id) != 16 or any(character not in "0123456789abcdef" for character in job_id):
        raise HTTPException(status_code=400, detail="无效的 Job ID")


@app.post("/api/jobs", response_model=AgentJob, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: JobCreateRequest,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ] = None,
) -> AgentJob:
    try:
        job, created = await services.job_submission.submit(
            payload.question,
            payload.cache_policy,
            payload.max_attempts,
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail="幂等键已绑定到不同请求") from exc
    except SensitiveJobInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="任务已持久化，但 Redis 暂不可用；使用相同幂等键重试即可修复投递",
        ) from exc
    response.headers["Location"] = f"/api/jobs/{job.job_id}"
    response.headers["X-AurumLab-Job-Created"] = str(created).lower()
    return job


@app.get("/api/jobs/{job_id}", response_model=AgentJob)
async def get_job(job_id: str) -> AgentJob:
    _validate_job_id(job_id)
    job = services.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job 不存在")
    return job


@app.get("/api/jobs/{job_id}/events", response_model=list[JobEvent])
async def get_job_events(
    job_id: str,
    after: int = Query(default=0, ge=0),
) -> list[JobEvent]:
    _validate_job_id(job_id)
    if services.jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job 不存在")
    return services.jobs.events_after(job_id, after)


@app.delete("/api/jobs/{job_id}", response_model=AgentJob)
async def cancel_job(job_id: str) -> AgentJob:
    _validate_job_id(job_id)
    try:
        return services.jobs.request_cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job 不存在") from exc


def _parse_last_event_id(value: str | None) -> int:
    if value is None:
        return 0
    if not value.isdigit() or len(value) > 18:
        raise HTTPException(status_code=400, detail="无效的 Last-Event-ID")
    return int(value)


@app.get("/api/jobs/{job_id}/stream")
async def stream_job_events(
    job_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", max_length=18),
    ] = None,
) -> StreamingResponse:
    _validate_job_id(job_id)
    if services.jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job 不存在")
    cursor = max(after, _parse_last_event_id(last_event_id))

    async def generate():
        nonlocal cursor
        seconds_since_write = 0.0
        while not await request.is_disconnected():
            events = services.jobs.events_after(job_id, cursor)
            for event in events:
                cursor = event.event_id
                data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                yield f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"
                seconds_since_write = 0.0
            job = services.jobs.get(job_id)
            if job is None or (
                job.status in services.jobs.terminal_statuses and cursor >= job.last_event_id
            ):
                return
            await asyncio.sleep(services.settings.sse_poll_interval_seconds)
            seconds_since_write += services.settings.sse_poll_interval_seconds
            if seconds_since_write >= services.settings.sse_heartbeat_seconds:
                yield ": heartbeat\n\n"
                seconds_since_write = 0.0

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _approval_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ApprovalNotFoundError):
        return HTTPException(status_code=404, detail="审批请求不存在")
    if isinstance(exc, ApprovalTokenError):
        return HTTPException(status_code=403, detail="审批凭证无效")
    if isinstance(exc, (ApprovalStateError, ApprovalBindingError)):
        return HTTPException(status_code=409, detail="审批状态或绑定不允许此操作")
    return HTTPException(status_code=400, detail="审批请求无效")


def _assert_approval_intent(actual: str, expected: str) -> None:
    if actual != expected:
        raise HTTPException(status_code=403, detail="缺少明确的审批操作意图")


@app.get(
    "/api/runs/{run_id}/approvals/{approval_id}",
    response_model=ApprovalRequest,
)
async def get_approval(run_id: str, approval_id: str) -> ApprovalRequest:
    try:
        approval = services.approvals.get(approval_id)
        if approval.run_id != run_id:
            raise ApprovalBindingError("approval is bound to another run")
        return approval
    except (
        ApprovalNotFoundError,
        ApprovalTokenError,
        ApprovalStateError,
        ApprovalBindingError,
    ) as exc:
        raise _approval_http_error(exc) from exc


@app.post(
    "/api/runs/{run_id}/approvals/{approval_id}/approve",
    response_model=ApprovalGrant,
)
async def approve_run(
    run_id: str,
    approval_id: str,
    approval_intent: Annotated[str, Header(alias="X-AurumLab-Approval-Intent")],
) -> ApprovalGrant:
    _assert_approval_intent(approval_intent, "approve")
    try:
        approval = services.approvals.get(approval_id)
        if approval.run_id != run_id:
            raise ApprovalBindingError("approval is bound to another run")
        return services.approvals.approve(
            approval_id,
            decided_by="local-demo-operator",
        )
    except (
        ApprovalNotFoundError,
        ApprovalTokenError,
        ApprovalStateError,
        ApprovalBindingError,
    ) as exc:
        raise _approval_http_error(exc) from exc


@app.post(
    "/api/runs/{run_id}/approvals/{approval_id}/deny",
    response_model=RunResponse,
)
async def deny_run(
    run_id: str,
    approval_id: str,
    approval_intent: Annotated[str, Header(alias="X-AurumLab-Approval-Intent")],
) -> RunResponse:
    _assert_approval_intent(approval_intent, "deny")
    try:
        return services.agent.deny_approval(run_id, approval_id)
    except (
        ApprovalNotFoundError,
        ApprovalTokenError,
        ApprovalStateError,
        ApprovalBindingError,
    ) as exc:
        raise _approval_http_error(exc) from exc


@app.post("/api/runs/{run_id}/resume", response_model=RunResponse)
async def resume_run(run_id: str, payload: ResumeRunRequest) -> RunResponse:
    try:
        return await services.agent.resume(run_id, payload.approval_token)
    except (
        ApprovalNotFoundError,
        ApprovalTokenError,
        ApprovalStateError,
        ApprovalBindingError,
    ) as exc:
        raise _approval_http_error(exc) from exc


@app.post(
    "/api/jobs/{job_id}/resume",
    response_model=AgentJob,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_job(job_id: str, payload: ResumeRunRequest) -> AgentJob:
    _validate_job_id(job_id)
    job = services.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job 不存在")
    existing_grant = services.jobs.get_job_approval(job_id)
    if job.status == "queued" and existing_grant is not None:
        try:
            await services.job_broker.initialize()
            await services.job_broker.enqueue(job_id)
            return job
        except RedisError as exc:
            raise HTTPException(
                status_code=503, detail="Redis 暂不可用，可安全重试恢复请求"
            ) from exc
    if job.status != "waiting_approval" or job.run_id is None:
        raise HTTPException(status_code=409, detail="Job 当前不等待审批")
    pending = services.runs.get(job.run_id)
    if pending is None or pending.approval is None or pending.plan is None:
        raise HTTPException(status_code=409, detail="Job 缺少可恢复的审批上下文")
    approval = pending.approval
    binding = ApprovalBinding(
        run_id=approval.run_id,
        plan_id=approval.plan_id,
        step_id=approval.step_id,
        tool_name=approval.tool_name,
        arguments_digest=approval.arguments_digest,
        effect=approval.effect,
        risk=approval.risk,
        reason=approval.reason,
    )
    try:
        consumed = services.approvals.consume(payload.approval_token, binding)
        resumed = services.jobs.resume_after_approval(job_id, consumed)
        await services.job_broker.initialize()
        await services.job_broker.enqueue(job_id)
        return resumed
    except (
        ApprovalNotFoundError,
        ApprovalTokenError,
        ApprovalStateError,
        ApprovalBindingError,
    ) as exc:
        raise _approval_http_error(exc) from exc
    except InvalidJobTransitionError as exc:
        raise HTTPException(status_code=409, detail="Job 审批恢复状态冲突") from exc
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="审批已安全消费且 Job 已排队；重试相同恢复请求即可修复投递",
        ) from exc


@app.get("/api/cache/stats", response_model=CacheStats)
async def cache_stats() -> CacheStats:
    return services.artifacts.stats()


@app.get("/api/runs", response_model=list[RunResponse])
async def recent_runs(limit: int = Query(default=10, ge=1, le=50)) -> list[RunResponse]:
    return services.runs.list_recent(limit)


@app.get("/api/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str) -> RunResponse:
    if len(run_id) != 12 or not run_id.isalnum():
        raise HTTPException(status_code=400, detail="无效的 Run ID")
    run = services.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run 不存在")
    return run


def run() -> None:
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    run()
