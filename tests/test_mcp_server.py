from mcp import Client

from app.mcp_server import mcp


async def test_mcp_discovers_and_calls_tools():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert "handle_agent_request" in names
        assert "analyze_gold_strategy" in names
        assert "list_aurumlab_skills" in names

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
