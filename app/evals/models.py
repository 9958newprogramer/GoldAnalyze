"""Structured contracts for reproducible Agent evaluations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    question: str = Field(min_length=4, max_length=1_000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    expected_intent: Literal["backtest_strategy", "query_market_data", "external_research", "other"]
    expected_skill: Literal[
        "backtest-strategy",
        "query-market-data",
        "external-research",
        "general-response",
    ]
    expected_artifact: Literal["backtest", "market", "research", "general", "rejected"]
    expected_spec: dict[str, Any] = Field(default_factory=dict)
    expected_status: Literal["completed", "failed", "rejected"] = "completed"
    cache_scenario: Literal["bypass", "exact_hit"] = "bypass"
    expected_cache_status: Literal["bypass", "exact_hit"] = "bypass"
    required_stages: list[str]
    expected_warning_contains: list[str] = Field(default_factory=list)


class EvalDimension(BaseModel):
    score: float = Field(ge=0, le=1)
    weight: float = Field(gt=0, le=1)
    passed: bool
    details: list[str] = Field(default_factory=list)


class EvalCaseResult(BaseModel):
    case_id: str
    question: str
    passed: bool
    score: float = Field(ge=0, le=100)
    run_id: str
    interpreter: str
    latency_ms: float = Field(ge=0)
    dimensions: dict[str, EvalDimension]
    failures: list[str] = Field(default_factory=list)


class EvalReport(BaseModel):
    eval_run_id: str
    dataset_version: str
    threshold: float = Field(ge=0, le=100)
    passed: bool
    score: float = Field(ge=0, le=100)
    passed_cases: int = Field(ge=0)
    total_cases: int = Field(ge=1)
    duration_ms: float = Field(ge=0)
    created_at: datetime
    results: list[EvalCaseResult]
