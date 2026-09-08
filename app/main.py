"""AurumLab HTTP API and dependency-free demonstration UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.approval import (
    ApprovalBindingError,
    ApprovalNotFoundError,
    ApprovalStateError,
    ApprovalTokenError,
)
from app.bootstrap import build_services
from app.config import settings
from app.evals.models import EvalCase, EvalReport
from app.evals.runner import load_eval_cases
from app.mcp_client import MCPToolCatalog
from app.models import (
    ApprovalGrant,
    ApprovalRequest,
    CacheStats,
    ResumeRunRequest,
    RunRequest,
    RunResponse,
    SkillDescriptor,
)

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
    }


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
