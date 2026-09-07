"""Build shared application services without coupling them to FastAPI or MCP."""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.interpreter import build_interpreter
from app.agent.orchestrator import AurumAgent
from app.agent.router import build_intent_router
from app.config import Settings, settings
from app.domain.market_data import build_market_repository
from app.evals.runner import EvalRunner
from app.memory import ArtifactCache
from app.skills.registry import SkillRegistry
from app.storage import EvalReportRepository, RunRepository
from app.tools.search import build_search_provider


@dataclass(frozen=True)
class Services:
    settings: Settings
    skills: SkillRegistry
    runs: RunRepository
    artifacts: ArtifactCache
    eval_reports: EvalReportRepository
    agent: AurumAgent
    evaluator: EvalRunner


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
    agent = AurumAgent(
        interpreter=build_interpreter(app_settings),
        market_repository=build_market_repository(app_settings),
        search_provider=build_search_provider(app_settings),
        skills=skills,
        runs=runs,
        artifacts=artifacts,
        router=build_intent_router(app_settings),
        market_cache_ttl_seconds=app_settings.market_cache_ttl_seconds,
        research_cache_ttl_seconds=app_settings.research_cache_ttl_seconds,
    )
    evaluator = EvalRunner(agent=agent, dataset_path=app_settings.resolved_eval_dataset_path)
    return Services(
        settings=app_settings,
        skills=skills,
        runs=runs,
        artifacts=artifacts,
        eval_reports=eval_reports,
        agent=agent,
        evaluator=evaluator,
    )
