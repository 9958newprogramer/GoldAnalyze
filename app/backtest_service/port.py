"""Backtest execution Port with local and internal-HTTP adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from opentelemetry.trace import SpanKind

from app.backtest_service.application import BacktestApplication
from app.contracts import (
    BacktestDataVersionRequest,
    BacktestDataVersionResponse,
    BacktestExecuteRequest,
    BacktestExecuteResponse,
    ProblemDetails,
)
from app.domain.backtest import BacktestResult
from app.domain.market_data import MarketDataRepository
from app.models import DataProfile, StrategySpec
from app.observability import Telemetry


@dataclass(frozen=True)
class BacktestCallContext:
    request_id: str
    job_id: str
    run_id: str
    idempotency_key: str
    deadline_at: datetime | None = None

    @classmethod
    def for_agent_run(
        cls,
        *,
        run_id: str,
        idempotency_key: str,
        job_id: str | None = None,
    ) -> BacktestCallContext:
        return cls(
            request_id=f"bt-{uuid4().hex[:16]}",
            job_id=job_id or f"sync-{run_id}",
            run_id=run_id,
            idempotency_key=idempotency_key,
        )


@dataclass(frozen=True)
class BacktestExecution:
    data_version: str
    profile: DataProfile
    result: BacktestResult
    warnings: list[str]
    result_digest: str


class BacktestExecutionPort(Protocol):
    name: str

    async def data_version(
        self,
        spec: StrategySpec,
        context: BacktestCallContext,
    ) -> str: ...

    async def execute(
        self,
        spec: StrategySpec,
        context: BacktestCallContext,
        *,
        expected_data_version: str,
    ) -> BacktestExecution: ...

    async def ready(self) -> bool: ...

    async def close(self) -> None: ...


class LocalBacktestExecutor:
    """Compatibility adapter for the standalone application and offline evals."""

    name = "local-backtest-adapter"

    def __init__(self, repository: MarketDataRepository):
        self.application = BacktestApplication(repository)

    async def data_version(
        self,
        spec: StrategySpec,
        context: BacktestCallContext,
    ) -> str:
        request = BacktestDataVersionRequest(
            request_id=context.request_id,
            job_id=context.job_id,
            run_id=context.run_id,
            strategy=spec,
        )
        return await asyncio.to_thread(self.application.data_version, request)

    async def execute(
        self,
        spec: StrategySpec,
        context: BacktestCallContext,
        *,
        expected_data_version: str,
    ) -> BacktestExecution:
        request = BacktestExecuteRequest(
            request_id=context.request_id,
            job_id=context.job_id,
            run_id=context.run_id,
            idempotency_key=context.idempotency_key,
            strategy=spec,
            expected_data_version=expected_data_version,
            deadline_at=context.deadline_at,
        )
        response = await asyncio.to_thread(self.application.execute, request)
        return _execution_from_response(response)

    async def close(self) -> None:
        return None

    async def ready(self) -> bool:
        return True


class BacktestServiceError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, status_code: int):
        super().__init__(f"backtest service rejected request: {code}")
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class BacktestHttpClient:
    """Typed internal client. Base URL is deployment config, never prompt input."""

    name = "http-backtest-adapter"

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str | None,
        timeout_seconds: float = 30.0,
        allow_insecure_http: bool = False,
        telemetry: Telemetry | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("BACKTEST_SERVICE_URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("BACKTEST_SERVICE_URL cannot include credentials, query, or fragment")
        is_loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "https" and not is_loopback and not allow_insecure_http:
            raise ValueError("non-loopback Backtest Service requires HTTPS")
        if not is_loopback and (service_token is None or len(service_token) < 32):
            raise ValueError("non-loopback Backtest Service requires a strong service token")
        if service_token is not None and len(service_token) < 32:
            raise ValueError("service token must contain at least 32 characters")
        if not 0.1 <= timeout_seconds <= 300:
            raise ValueError("Backtest Service timeout must be between 0.1 and 300 seconds")

        headers = {"Accept": "application/json"}
        if service_token is not None:
            headers["Authorization"] = f"Bearer {service_token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers=headers,
            transport=transport,
        )
        self.telemetry = telemetry

    async def data_version(
        self,
        spec: StrategySpec,
        context: BacktestCallContext,
    ) -> str:
        payload = BacktestDataVersionRequest(
            request_id=context.request_id,
            job_id=context.job_id,
            run_id=context.run_id,
            strategy=spec,
        )
        response = await self._post(
            "/internal/v1/backtests/data-version",
            payload.model_dump(mode="json"),
        )
        return BacktestDataVersionResponse.model_validate(response.json()).data_version

    async def execute(
        self,
        spec: StrategySpec,
        context: BacktestCallContext,
        *,
        expected_data_version: str,
    ) -> BacktestExecution:
        payload = BacktestExecuteRequest(
            request_id=context.request_id,
            job_id=context.job_id,
            run_id=context.run_id,
            idempotency_key=context.idempotency_key,
            strategy=spec,
            expected_data_version=expected_data_version,
            deadline_at=context.deadline_at,
        )
        response = await self._post(
            "/internal/v1/backtests/execute",
            payload.model_dump(mode="json"),
        )
        return _execution_from_response(BacktestExecuteResponse.model_validate(response.json()))

    async def _post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        headers: dict[str, str] = {}
        if self.telemetry is not None:
            headers.update(self.telemetry.inject().as_dict())
            scope = self.telemetry.span(
                "goldanalyze.backtest.client",
                attributes={"http.request.method": "POST", "http.route": path},
                kind=SpanKind.CLIENT,
            )
        else:
            from contextlib import nullcontext

            scope = nullcontext()
        try:
            with scope:
                response = await self._client.post(path, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BacktestServiceError(
                "BACKTEST_SERVICE_UNAVAILABLE",
                retryable=True,
                status_code=503,
            ) from exc
        if response.is_success:
            return response
        try:
            problem = ProblemDetails.model_validate(response.json())
        except Exception as exc:
            raise BacktestServiceError(
                "BACKTEST_SERVICE_PROTOCOL_ERROR",
                retryable=response.status_code >= 500,
                status_code=response.status_code,
            ) from exc
        raise BacktestServiceError(
            problem.code,
            retryable=problem.retryable,
            status_code=response.status_code,
        )

    async def ready(self) -> bool:
        try:
            response = await self._client.get("/health/ready")
            return response.status_code == 200
        except (httpx.TimeoutException, httpx.NetworkError):
            return False

    async def close(self) -> None:
        await self._client.aclose()


def _execution_from_response(response: BacktestExecuteResponse) -> BacktestExecution:
    return BacktestExecution(
        data_version=response.data_version,
        profile=response.data_profile,
        result=BacktestResult(
            metrics=response.metrics,
            trades=response.trades,
            equity_curve=response.equity_curve,
        ),
        warnings=response.warnings,
        result_digest=response.result_digest,
    )
