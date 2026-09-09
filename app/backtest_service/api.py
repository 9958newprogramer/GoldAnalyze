"""Internal FastAPI adapter for the deterministic Backtest Engine service."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind

from app import __version__
from app.backtest_service.application import (
    BacktestApplication,
    BacktestApplicationError,
    DataVersionConflict,
    ExecutionDeadlineExceeded,
    WorkloadLimitExceeded,
)
from app.config import Settings, settings
from app.contracts import (
    BacktestExecuteRequest,
    BacktestExecuteResponse,
    ProblemDetails,
    ServiceHealth,
)
from app.domain.market_data import MarketDataRepository, build_market_repository
from app.observability import Telemetry


def _problem(
    *,
    request_id: str | None,
    status_code: int,
    code: str,
    title: str,
    detail: str,
    telemetry: Telemetry,
    retryable: bool = False,
) -> JSONResponse:
    body = ProblemDetails(
        title=title,
        status=status_code,
        code=code,
        detail=detail,
        request_id=request_id,
        trace_id=telemetry.current_trace_id(),
        retryable=retryable,
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
        media_type="application/problem+json",
    )


def create_app(
    app_settings: Settings = settings,
    *,
    repository: MarketDataRepository | None = None,
    telemetry: Telemetry | None = None,
) -> FastAPI:
    if app_settings.internal_service_token and len(app_settings.internal_service_token) < 32:
        raise ValueError("INTERNAL_SERVICE_TOKEN must contain at least 32 characters")
    if not app_settings.internal_service_token and app_settings.backtest_service_host not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise ValueError("INTERNAL_SERVICE_TOKEN is required when binding beyond loopback")

    runtime_telemetry = telemetry or Telemetry(
        service_name="goldanalyze-backtest-service",
        service_version=__version__,
        exporter=app_settings.otel_exporter,
        otlp_endpoint=app_settings.otel_otlp_endpoint,
        sample_ratio=app_settings.otel_sample_ratio,
        memory_max_spans=app_settings.otel_memory_max_spans,
        metric_export_interval_seconds=app_settings.otel_metric_export_interval_seconds,
    )
    application = BacktestApplication(repository or build_market_repository(app_settings))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            runtime_telemetry.shutdown()

    service = FastAPI(
        title="GoldAnalyze Backtest Engine",
        version=__version__,
        description="Internal deterministic compute service; Java owns control-plane state.",
        lifespan=lifespan,
    )
    service.state.application = application
    service.state.telemetry = runtime_telemetry

    @service.middleware("http")
    async def security_and_observability(request: Request, call_next):
        parent = runtime_telemetry.extract(request.headers)
        started = perf_counter()
        response: Response | None = None
        with runtime_telemetry.span(
            f"{request.method.upper()} request",
            attributes={"http.request.method": request.method.upper()},
            kind=SpanKind.SERVER,
            context=parent,
        ) as span:
            try:
                response = await call_next(request)
            except Exception as exc:
                runtime_telemetry.mark_error(span, type(exc).__name__)
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
                runtime_telemetry.count("goldanalyze.backtest.http.requests", attributes=attributes)
                runtime_telemetry.record(
                    "goldanalyze.backtest.http.duration",
                    round((perf_counter() - started) * 1_000, 2),
                    attributes=attributes,
                )
            if response is not None:
                trace_id = runtime_telemetry.current_trace_id()
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
            telemetry=runtime_telemetry,
        )

    @service.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError):
        return _problem(
            request_id=None,
            status_code=422,
            code="CONTRACT_VALIDATION_FAILED",
            title="Contract validation failed",
            detail="The request does not conform to the versioned service contract.",
            telemetry=runtime_telemetry,
        )

    @service.get("/health/live", response_model=ServiceHealth)
    async def liveness() -> ServiceHealth:
        return ServiceHealth(
            service="backtest-service",
            status="ok",
            version=__version__,
            dependencies={},
        )

    @service.get("/health/ready", response_model=ServiceHealth)
    async def readiness() -> ServiceHealth:
        return ServiceHealth(
            service="backtest-service",
            status="ok",
            version=__version__,
            dependencies={"market-data": "ok"},
        )

    @service.post(
        "/internal/v1/backtests/execute",
        response_model=BacktestExecuteResponse,
        responses={
            401: {"model": ProblemDetails},
            408: {"model": ProblemDetails},
            409: {"model": ProblemDetails},
            422: {"model": ProblemDetails},
        },
        dependencies=[Depends(require_control_plane)],
    )
    async def execute_backtest(payload: BacktestExecuteRequest):
        try:
            return await run_in_threadpool(application.execute, payload)
        except DataVersionConflict:
            return _problem(
                request_id=payload.request_id,
                status_code=409,
                code="DATA_VERSION_CONFLICT",
                title="Market data version conflict",
                detail="Configured market data does not match the requested immutable version.",
                telemetry=runtime_telemetry,
            )
        except ExecutionDeadlineExceeded:
            return _problem(
                request_id=payload.request_id,
                status_code=408,
                code="EXECUTION_DEADLINE_EXCEEDED",
                title="Execution deadline exceeded",
                detail="The execution deadline elapsed before the calculation started.",
                telemetry=runtime_telemetry,
                retryable=True,
            )
        except WorkloadLimitExceeded:
            return _problem(
                request_id=payload.request_id,
                status_code=422,
                code="WORKLOAD_LIMIT_EXCEEDED",
                title="Backtest workload rejected",
                detail="The requested dataset or result exceeds the bounded workload limits.",
                telemetry=runtime_telemetry,
            )
        except (ValueError, ArithmeticError):
            return _problem(
                request_id=payload.request_id,
                status_code=422,
                code="BACKTEST_INPUT_INVALID",
                title="Backtest input rejected",
                detail="The supplied strategy cannot be evaluated against the selected dataset.",
                telemetry=runtime_telemetry,
            )
        except BacktestApplicationError as exc:
            return _problem(
                request_id=payload.request_id,
                status_code=500,
                code=exc.code,
                title="Backtest execution failed",
                detail="The calculation could not be completed.",
                telemetry=runtime_telemetry,
                retryable=exc.retryable,
            )

    return service


class ControlPlaneAuthenticationError(Exception):
    """Authentication failure without reflecting credential details."""


app = create_app()


def run() -> None:
    uvicorn.run(
        "app.backtest_service.api:app",
        host=settings.backtest_service_host,
        port=settings.backtest_service_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
