"""MCP server exposing the same governed Agent capability to external hosts."""

from __future__ import annotations

import json
import os
from typing import Literal

from mcp.server import MCPServer

from app import __version__
from app.bootstrap import build_services
from app.models import RunResponse, SkillListResponse

services = build_services()

mcp = MCPServer(
    name="aurumlab",
    title="AurumLab Multi-Skill Agent Server",
    description=(
        "An LLM-routed, governed Agent with versioned Skills and persistent Artifact Memory."
    ),
    instructions=(
        "Use handle_agent_request for any natural-language task. "
        "This server is research-only and never places trades."
    ),
    version=__version__,
)


@mcp.tool(structured_output=True)
async def handle_agent_request(
    question: str,
    cache_policy: Literal["use", "refresh", "bypass"] = "use",
) -> RunResponse:
    """Route a natural-language request and execute the selected governed Skill."""
    if not 4 <= len(question.strip()) <= 1_000:
        raise ValueError("question length must be between 4 and 1000 characters")
    return await services.agent.run(question.strip(), cache_policy=cache_policy)


@mcp.tool(structured_output=True)
async def analyze_gold_strategy(question: str) -> RunResponse:
    """Backward-compatible alias; requests are now routed across all active Skills."""
    return await handle_agent_request(question)


@mcp.tool(structured_output=True)
def list_aurumlab_skills() -> SkillListResponse:
    """List versioned Skills available to the AurumLab Agent."""
    return SkillListResponse(skills=services.skills.list())


@mcp.resource("aurum://skills/backtest-strategy", mime_type="application/json")
def backtest_skill() -> str:
    """Read the active backtest Skill descriptor."""
    return services.skills.get("backtest-strategy").model_dump_json(indent=2)


@mcp.resource("aurum://skills", mime_type="application/json")
def all_skills() -> str:
    """Read every active Skill descriptor."""
    payload = SkillListResponse(skills=services.skills.list())
    return payload.model_dump_json(indent=2)


@mcp.resource("aurum://evals/latest", mime_type="application/json")
def latest_eval_report() -> str:
    """Read the latest persisted Agent quality-gate report."""
    report = services.eval_reports.latest()
    if report is None:
        return json.dumps({"error": "eval_report_not_found"})
    return report.model_dump_json(indent=2)


@mcp.resource("aurum://cache/stats", mime_type="application/json")
def artifact_cache_stats() -> str:
    """Read aggregate Artifact Cache health without exposing cached payloads."""
    return services.artifacts.stats().model_dump_json(indent=2)


@mcp.resource("aurum://runs/{run_id}", mime_type="application/json")
def backtest_run(run_id: str) -> str:
    """Read a persisted Agent run as an auditable resource."""
    if len(run_id) != 12 or not run_id.isalnum():
        raise ValueError("invalid run id")
    run = services.runs.get(run_id)
    if run is None:
        return json.dumps({"error": "run_not_found", "run_id": run_id})
    return run.model_dump_json(indent=2)


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError("MCP_TRANSPORT must be stdio, sse, or streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
