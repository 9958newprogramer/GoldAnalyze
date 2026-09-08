from mcp import Client

from app.mcp_server import mcp, services


async def test_mcp_discovers_and_calls_tools():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert "handle_agent_request" in names
        assert "analyze_gold_strategy" in names
        assert "list_aurumlab_skills" in names
        assert "resume_agent_run" in names

        result = await client.call_tool("list_aurumlab_skills")
        assert result.is_error is False
        skill_names = {skill["name"] for skill in result.structured_content["skills"]}
        assert skill_names == {
            "backtest-strategy",
            "query-market-data",
            "external-research",
            "general-response",
        }

        routed = await client.call_tool(
            "handle_agent_request", {"question": "查询黄金最近5根日K线。"}
        )
        assert routed.is_error is False
        assert routed.structured_content["route"]["intent"] == "query_market_data"
        assert routed.structured_content["plan"]["validated"] is True

        resources = await client.list_resources()
        uris = {str(resource.uri) for resource in resources.resources}
        assert "aurum://skills" in uris
        assert "aurum://skills/backtest-strategy" in uris
        assert "aurum://evals/latest" in uris
        assert "aurum://cache/stats" in uris


async def test_mcp_can_resume_but_cannot_self_approve_a_reviewed_tool():
    async with Client(mcp) as client:
        pending_result = await client.call_tool(
            "handle_agent_request",
            {
                "question": "搜索互联网资料：黄金与美元指数长期关系（MCP审批恢复测试）。",
                "cache_policy": "bypass",
            },
        )
        pending = pending_result.structured_content
        assert pending_result.is_error is False
        assert pending["status"] == "pending_approval"

        tool_names = {tool.name for tool in (await client.list_tools()).tools}
        assert "approve_agent_run" not in tool_names
        grant = services.approvals.approve(
            pending["approval"]["approval_id"],
            decided_by="test-control-plane",
        )
        resumed = await client.call_tool(
            "resume_agent_run",
            {
                "run_id": pending["run_id"],
                "approval_token": grant.approval_token,
            },
        )

        assert resumed.is_error is False
        assert resumed.structured_content["status"] == "completed"
        assert resumed.structured_content["approval"]["status"] == "consumed"
