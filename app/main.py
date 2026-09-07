"""AurumLab HTTP API and dependency-free demonstration UI."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.bootstrap import build_services
from app.config import settings
from app.evals.models import EvalCase, EvalReport
from app.evals.runner import load_eval_cases
from app.models import CacheStats, RunRequest, RunResponse, SkillDescriptor

services = build_services()
static_root = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="AurumLab",
    version=__version__,
    description=(
        "Agent engineering platform with versioned Skills, governed tools, Artifact Memory, "
        "and MCP exposure."
    ),
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
    return {
        "status": "ok",
        "version": __version__,
        "router": services.agent.router.name,
        "router_mode": services.agent.router.mode,
        "router_model": services.settings.router_llm_model,
        "router_llm_configured": bool(services.settings.router_llm_api_key),
        "skills_count": len(services.skills.list()),
        "data_source": services.agent.market_repository.source_name,
        "synthetic_data": services.agent.market_repository.synthetic,
        "llm_configured": bool(services.settings.llm_api_key),
        "search_provider": services.agent.search_provider.name,
        "mcp_server_command": "aurumlab-mcp",
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
