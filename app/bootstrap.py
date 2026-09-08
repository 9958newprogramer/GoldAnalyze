"""Build shared application services without coupling them to FastAPI or MCP."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.agent.interpreter import build_interpreter
from app.agent.orchestrator import AurumAgent
from app.agent.router import build_intent_router
from app.approval import ApprovalRepository
from app.config import Settings, settings
from app.domain.market_data import build_market_repository
from app.evals.runner import EvalRunner
from app.jobs import JobSubmissionService, RedisStreamBroker
from app.mcp_client import MCPClientManager, MCPServerBinding, MCPServerSpec
from app.mcp_provider import mcp_tool_provider
from app.memory import ArtifactCache
from app.skills.registry import SkillRegistry
from app.storage import EvalReportRepository, JobRepository, RunRepository
from app.tools.search import build_search_provider


@dataclass(frozen=True)
class Services:
    settings: Settings
    skills: SkillRegistry
    runs: RunRepository
    artifacts: ArtifactCache
    approvals: ApprovalRepository
    eval_reports: EvalReportRepository
    jobs: JobRepository
    job_broker: RedisStreamBroker
    job_submission: JobSubmissionService
    agent: AurumAgent
    evaluator: EvalRunner
    mcp_clients: MCPClientManager


def build_services(app_settings: Settings = settings) -> Services:
    skills = SkillRegistry(app_settings.project_root / "skills")
    runs = RunRepository(app_settings.resolved_app_database_path)
    eval_reports = EvalReportRepository(app_settings.resolved_app_database_path)
    artifacts = ArtifactCache(
        app_settings.resolved_app_database_path,
        enabled=app_settings.artifact_cache_enabled,
        semantic_threshold=app_settings.cache_semantic_threshold,
        max_entries=app_settings.artifact_cache_max_entries,
    )
    approvals = ApprovalRepository(
        app_settings.resolved_app_database_path,
        ttl_seconds=app_settings.approval_ttl_seconds,
    )
    jobs = JobRepository(app_settings.resolved_app_database_path)
    redis_client = Redis.from_url(
        app_settings.redis_url,
        decode_responses=True,
        protocol=2,
        socket_connect_timeout=1,
        socket_timeout=2,
    )
    job_broker = RedisStreamBroker(
        redis_client,
        stream_name=app_settings.job_stream_name,
        group_name=app_settings.job_consumer_group,
    )
    job_submission = JobSubmissionService(jobs, job_broker)
    agent = AurumAgent(
        interpreter=build_interpreter(app_settings),
        market_repository=build_market_repository(app_settings),
        search_provider=build_search_provider(app_settings),
        skills=skills,
        runs=runs,
        artifacts=artifacts,
        approvals=approvals,
        router=build_intent_router(app_settings),
        market_cache_ttl_seconds=app_settings.market_cache_ttl_seconds,
        research_cache_ttl_seconds=app_settings.research_cache_ttl_seconds,
    )
    evaluator = EvalRunner(agent=agent, dataset_path=app_settings.resolved_eval_dataset_path)
    mcp_clients = MCPClientManager(
        [
            MCPServerBinding(
                spec=MCPServerSpec(
                    server_id="aurumlab-runtime-tools",
                    namespace="runtime",
                    transport="in_process",
                ),
                target=mcp_tool_provider,
            )
        ]
    )
    return Services(
        settings=app_settings,
        skills=skills,
        runs=runs,
        artifacts=artifacts,
        approvals=approvals,
        eval_reports=eval_reports,
        jobs=jobs,
        job_broker=job_broker,
        job_submission=job_submission,
        agent=agent,
        evaluator=evaluator,
        mcp_clients=mcp_clients,
    )
