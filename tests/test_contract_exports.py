import hashlib
import json

from app.contracts.exporter import CONTRACT_MODELS, export_contracts


def test_contract_export_is_deterministic_and_hash_pinned(tmp_path):
    first = export_contracts(tmp_path)
    second = export_contracts(tmp_path)
    assert first == second
    assert len(first) == len(CONTRACT_MODELS) + 2

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["contract_version"] == "1.0.0"
    for relative_path, expected in manifest["files"].items():
        actual = hashlib.sha256((tmp_path / relative_path).read_bytes()).hexdigest()
        assert actual == expected


def test_exported_contracts_are_strict_and_have_fixed_channels(tmp_path):
    export_contracts(tmp_path)
    request_schema = json.loads(
        (tmp_path / "schemas/BacktestExecuteRequest.schema.json").read_text()
    )
    asyncapi = json.loads((tmp_path / "asyncapi.v1.json").read_text())
    openapi = json.loads((tmp_path / "backtest-service.openapi.json").read_text())

    assert request_schema["additionalProperties"] is False
    assert request_schema["properties"]["schema_version"]["const"] == "backtest-request-v1"
    addresses = {channel["address"] for channel in asyncapi["channels"].values()}
    assert addresses == {
        "goldanalyze.agent.commands.v1",
        "goldanalyze.agent.events.v1",
        "goldanalyze.backtest.commands.v1",
        "goldanalyze.backtest.events.v1",
    }
    assert "/internal/v1/backtests/execute" in openapi["paths"]
    security_header = openapi["components"]["schemas"]
    assert "BacktestExecuteRequest" in security_header
