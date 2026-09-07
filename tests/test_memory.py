import json
from datetime import UTC, datetime, timedelta

import pytest

from app.bootstrap import build_services
from app.config import Settings
from app.memory import build_task_fingerprint
from app.models import StrategySpec
from app.tools.registry import ToolGateway, ToolPolicy, ToolRegistry


def test_task_fingerprint_is_stable_for_equivalent_structured_specs():
    left = StrategySpec(fast_window=10, slow_window=30)
    right = StrategySpec.model_validate(left.model_dump())

    first = build_task_fingerprint("backtest_strategy", left, "market-v1")
    second = build_task_fingerprint("backtest_strategy", right, "market-v1")

    assert first.fingerprint == second.fingerprint
    assert first.public_fingerprint == second.public_fingerprint
    assert len(first.public_fingerprint) == 16


async def test_market_artifact_expires_after_ttl(tmp_path):
    services = build_services(
        Settings(app_database_path=str(tmp_path / "runs.db"), market_cache_ttl_seconds=5)
    )
    source = await services.agent.run("查询黄金最近7根日K线。")
    assert source.market_query is not None
    repository_spec = StrategySpec(
        symbol=source.market_query.symbol,
        timeframe=source.market_query.timeframe,
        start_date=source.market_query.start_date,
        end_date=source.market_query.end_date,
    )
    task = build_task_fingerprint(
        "query_market_data",
        source.market_query,
        services.agent.market_repository.data_version(repository_spec),
    )

    expired = services.artifacts.lookup(task, now=datetime.now(UTC) + timedelta(seconds=6))

    assert expired.info.status == "miss"
    assert expired.info.reason == "artifact_expired"


async def test_artifact_payload_excludes_question_route_and_tool_audit(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runs.db")))
    source = await services.agent.run("黄金日K，14日均线上穿40日均线做多。")
    with services.artifacts.connect() as connection:
        row = connection.execute(
            "SELECT payload FROM artifact_cache WHERE source_run_id = ?", (source.run_id,)
        ).fetchone()

    payload = json.loads(row[0])
    assert "question" not in payload
    assert "route" not in payload
    assert "tool_audit" not in payload
    assert payload["metrics"] is not None


async def test_cache_capacity_evicts_oldest_artifact(tmp_path):
    services = build_services(
        Settings(app_database_path=str(tmp_path / "runs.db"), artifact_cache_max_entries=2)
    )
    first = await services.agent.run("查询黄金最近5根日K线。")
    await services.agent.run("查询黄金最近6根日K线。")
    await services.agent.run("查询黄金最近7根日K线。")

    assert services.artifacts.stats().entries == 2
    repeated_first = await services.agent.run(first.question)
    assert repeated_first.cache_status != "exact_hit"


async def test_corrupt_cached_snapshot_fails_open_and_self_heals(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runs.db")))
    question = "黄金日K，16日均线上穿47日均线做多。"
    source = await services.agent.run(question)
    with services.artifacts.connect() as connection:
        connection.execute(
            "UPDATE artifact_cache SET payload = ? WHERE source_run_id = ?",
            ("not-json", source.run_id),
        )

    recovered = await services.agent.run(question)
    healed = await services.agent.run(question)

    assert recovered.status == "completed"
    assert recovered.cache_status == "miss"
    assert recovered.cache is not None
    assert recovered.cache.reason.startswith("cache_lookup_failed:")
    assert recovered.tool_audit
    assert healed.cache_status == "exact_hit"
    assert healed.cache is not None and healed.cache.source_run_id == recovered.run_id


async def test_tool_gateway_request_memory_avoids_duplicate_call():
    calls = {"count": 0}

    async def handler(value: int) -> int:
        calls["count"] += 1
        return value * 2

    registry = ToolRegistry()
    registry.register("double", handler)
    policy = ToolPolicy(policy_name="test", allowed_tools={"double"}, max_calls=1)
    gateway = ToolGateway(registry, policy)

    first = await gateway.call("double", memory_key="same-input", value=3)
    second = await gateway.call("double", memory_key="same-input", value=3)

    assert first == second == 6
    assert calls["count"] == 1
    assert policy.calls == 1
    assert policy.audit_log[-1]["phase"] == "request_memory"


async def test_request_memory_cannot_bypass_tool_allowlist():
    async def handler(value: int) -> int:
        return value

    registry = ToolRegistry()
    registry.register("allowed", handler)
    registry.register("blocked", handler)
    policy = ToolPolicy(policy_name="test", allowed_tools={"allowed"}, max_calls=1)
    gateway = ToolGateway(registry, policy)
    await gateway.call("allowed", memory_key="shared", value=1)

    with pytest.raises(PermissionError):
        await gateway.call("blocked", memory_key="shared", value=1)

    assert policy.audit_log[-1]["reason"] == "not_allowlisted"
