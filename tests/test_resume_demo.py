import json

from app.resume_demo import run_demo


async def test_resume_demo_produces_sanitized_machine_readable_evidence():
    evidence = await run_demo(include_eval=False)

    assert evidence["schema_version"] == "resume-evidence-v1"
    assert evidence["passed"] is True
    assert all(evidence["checks"].values())
    assert evidence["runtime_evidence"]["skills"] == 5
    assert evidence["runtime_evidence"]["incident_tool_executions"] == 5
    assert evidence["runtime_evidence"]["incident_mcp_tool_source"] == "mcp"
    assert evidence["runtime_evidence"]["cache_saved_tool_calls"] == 5
    assert evidence["eval_evidence"] is None
    serialized = json.dumps(evidence)
    assert "approval_token" not in serialized
    assert "resume-demo-operator" not in serialized
