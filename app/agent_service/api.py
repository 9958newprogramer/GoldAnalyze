"""Internal FastAPI boundary for Java control-plane to Agent execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind

from app import __version__
from app.backtest_service.port import BacktestCallContext, BacktestServiceError
from app.bootstrap import Services, build_services
from app.config import Settings, settings
from app.contracts import (
    AgentExecuteRequest,
    AgentExecuteResponse,
    AgentResultArtifact,
    ProblemDetails,
    ServiceHealth,
)


class ControlPlaneAuthenticationError(Exception):
    """Authentication failure without reflecting credential details."""


def _problem(
    *,
    request_id: str | None,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    services: Services,
    retryable: bool = False,
) -> JSONResponse:
    body = ProblemDetails(
        title=title,
        status=status_code,
        code=code,
        detail=detail,
        request_id=request_id,
        trace_id=services.telemetry.current_trace_id(),
        retryable=retryable,
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
        media_type="application/problem+json",
    )


def _artifact_digest(artifact: AgentResultArtifact) -> str:
    encoded = json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_app(
    app_settings: Settings = settings,
    *,
    services: Services | None = None,
) -> FastAPI:
    if app_settings.internal_service_token and len(app_settings.internal_service_token) < 32:
        raise ValueError("INTERNAL_SERVICE_TOKEN must contain at least 32 characters")
    if not app_settings.internal_service_token and app_settings.agent_service_host not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise ValueError("INTERNAL_SERVICE_TOKEN is required when binding beyond loopback")

    runtime = services or build_services(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runtime.mcp_clients.start()
        runtime.mcp_clients.register_tools(runtime.agent.tools)
        try:
            yield
        finally:
            await runtime.mcp_clients.stop()
            await runtime.backtest_executor.close()
            await runtime.job_broker.close()
            runtime.telemetry.shutdown()

    service = FastAPI(
        title="GoldAnalyze Agent Service",
        version=__version__,
        description=(
            "Internal Agent execution service. The Java control plane owns users, jobs, "
            "idempotency records, approvals, and durable business state."
        ),
        lifespan=lifespan,
    )
    service.state.services = runtime

    @service.middleware("http")
    async def security_and_observability(request: Request, call_next):
        parent = runtime.telemetry.extract(request.headers)
        started = perf_counter()
        response: Response | None = None
        with runtime.telemetry.span(
            f"{request.method.upper()} request",
            attributes={"http.request.method": request.method.upper()},
            kind=SpanKind.SERVER,
            context=parent,
        ) as span:
            try:
                response = await call_next(request)
            except Exception as exc:
                runtime.telemetry.mark_error(span, type(exc).__name__)
                raise
            finally:
                status_code = response.status_code if response is not None else 500
                route_object = request.scope.get("route")
                route = getattr(route_object, "path", "{unmatched}")
                if not isinstance(route, str) or len(route) > 160:
                    route = "{unmatched}"
                span.update_name(f"{request.method.upper()} {route}")
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status_code)
                attributes = {
                    "method": request.method.upper(),
                    "route": route,
                    "status_class": f"{status_code // 100}xx",
                }
                runtime.telemetry.count("goldanalyze.agent.http.requests", attributes=attributes)
                runtime.telemetry.record(
                    "goldanalyze.agent.http.duration",
                    round((perf_counter() - started) * 1_000, 2),
                    attributes=attributes,
                )
            if response is not None:
                trace_id = runtime.telemetry.current_trace_id()
                if trace_id is not None:
                    response.headers["X-Trace-Id"] = trace_id
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Cache-Control"] = "no-store"
                return response
        raise RuntimeError("HTTP middleware completed without a response")

    async def require_control_plane(
        authorization: Annotated[str | None, Header(max_length=256)] = None,
    ) -> None:
        expected = app_settings.internal_service_token
        if expected is None:
            return
        prefix = "Bearer "
        supplied = (
            authorization[len(prefix) :]
            if authorization and authorization.startswith(prefix)
            else ""
        )
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise ControlPlaneAuthenticationError

    @service.exception_handler(ControlPlaneAuthenticationError)
    async def authentication_error(_: Request, __: ControlPlaneAuthenticationError):
        return _problem(
            request_id=None,
            status_code=401,
            code="CONTROL_PLANE_UNAUTHORIZED",
            title="Unauthorized",
            detail="Valid control-plane service credentials are required.",
            services=runtime,
        )

    @service.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError):
        return _problem(
            request_id=None,
            status_code=422,
            code="CONTRACT_VALIDATION_FAILED",
            title="Contract validation failed",
            detail="The request does not conform to the versioned service contract.",
            services=runtime,
        )

    @service.get("/health/live", response_model=ServiceHealth)
    async def liveness() -> ServiceHealth:
        return ServiceHealth(
            service="agent-service",
            status="ok",
            version=__version__,
            dependencies={},
        )

    @service.get("/health/ready", response_model=ServiceHealth)
    async def readiness() -> ServiceHealth:
        backtest_ready = await runtime.backtest_executor.ready()
        return ServiceHealth(
            service="agent-service",
            status="ok" if backtest_ready else "degraded",
            version=__version__,
            dependencies={"backtest-service": "ok" if backtest_ready else "unavailable"},
        )

    @service.post(
        "/internal/v1/agent-runs/execute",
        response_model=AgentExecuteResponse,
        responses={
            401: {"model": ProblemDetails},
            422: {"model": ProblemDetails},
            503: {"model": ProblemDetails},
            504: {"model": ProblemDetails},
        },
        dependencies=[Depends(require_control_plane)],
    )
    async def execute_agent(payload: AgentExecuteRequest):
        if payload.deadline_at is not None and datetime.now(UTC) >= payload.deadline_at:
            return _problem(
                request_id=payload.request_id,
                status_code=504,
                code="AGENT_DEADLINE_EXCEEDED",
                title="Agent deadline exceeded",
                detail="The execution deadline elapsed before Agent execution started.",
                services=runtime,
                retryable=True,
            )
        context = BacktestCallContext(
            request_id=payload.request_id,
            job_id=payload.job_id,
            run_id=payload.run_id,
            idempotency_key=payload.idempotency_key,
            deadline_at=payload.deadline_at,
        )
        started = perf_counter()
        try:
            if payload.deadline_at is None:
                result = await runtime.agent.run(
                    payload.question,
                    cache_policy=payload.cache_policy,
                    backtest_call_context=context,
                )
            else:
                timeout = max((payload.deadline_at - datetime.now(UTC)).total_seconds(), 0.001)
                async with asyncio.timeout(timeout):
                    result = await runtime.agent.run(
                        payload.question,
                        cache_policy=payload.cache_policy,
                        backtest_call_context=context,
                    )
        except TimeoutError:
            return _problem(
                request_id=payload.request_id,
                status_code=504,
                code="AGENT_DEADLINE_EXCEEDED",
                title="Agent deadline exceeded",
                detail="Agent execution did not finish before the supplied deadline.",
                services=runtime,
                retryable=True,
            )
        except BacktestServiceError as exc:
            return _problem(
                request_id=payload.request_id,
                status_code=503 if exc.retryable else 422,
                code=exc.code,
                title="Backtest dependency failed",
                detail="The Agent could not complete its bounded Backtest Service call.",
                services=runtime,
                retryable=exc.retryable,
            )
        artifact = AgentResultArtifact.from_run(result)
        return AgentExecuteResponse(
            request_id=payload.request_id,
            job_id=payload.job_id,
            run_id=payload.run_id,
            execution_id=result.run_id,
            artifact=artifact,
            result_digest=_artifact_digest(artifact),
            duration_ms=round((perf_counter() - started) * 1_000, 2),
        )

    return service


app = create_app()


def run() -> None:
    uvicorn.run(
        "app.agent_service.api:app",
        host=settings.agent_service_host,
        port=settings.agent_service_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
