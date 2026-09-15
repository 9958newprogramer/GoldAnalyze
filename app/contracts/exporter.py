"""Export deterministic OpenAPI/AsyncAPI/JSON Schema artifacts for Java consumers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.agent_service.api import app as agent_app
from app.backtest_service.api import app as backtest_app
from app.contracts import (
    AgentExecuteRequest,
    AgentExecuteResponse,
    AgentResultArtifact,
    AgentRunRequestedCommand,
    AgentRunResultEvent,
    BacktestDataVersionRequest,
    BacktestDataVersionResponse,
    BacktestExecuteRequest,
    BacktestExecuteResponse,
    BacktestRunRequestedCommand,
    BacktestRunResultEvent,
    EventCondition,
    EventHorizonStatistics,
    EventStudyEvent,
    EventStudyRequest,
    EventStudyResponse,
    MessageMetadata,
    ProblemDetails,
    ServiceHealth,
)

CONTRACT_MODELS = {
    model.__name__: model
    for model in (
        MessageMetadata,
        ProblemDetails,
        ServiceHealth,
        AgentExecuteRequest,
        AgentExecuteResponse,
        AgentResultArtifact,
        BacktestDataVersionRequest,
        BacktestDataVersionResponse,
        BacktestExecuteRequest,
        BacktestExecuteResponse,
        EventCondition,
        EventStudyRequest,
        EventStudyEvent,
        EventHorizonStatistics,
        EventStudyResponse,
        AgentRunRequestedCommand,
        AgentRunResultEvent,
        BacktestRunRequestedCommand,
        BacktestRunResultEvent,
    )
}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _asyncapi_document() -> dict[str, Any]:
    schemas = {name: {"$ref": f"./schemas/{name}.schema.json"} for name in CONTRACT_MODELS}
    return {
        "asyncapi": "3.0.0",
        "info": {
            "title": "GoldAnalyze Java/Python Events",
            "version": "1.0.0",
            "description": (
                "Fixed Kafka channels between the Java control plane and Python compute services. "
                "Topic names are deployment configuration and never come from message payloads."
            ),
        },
        "defaultContentType": "application/json",
        "channels": {
            "agentCommands": {
                "address": "goldanalyze.agent.commands.v1",
                "messages": {
                    "agentRunRequested": {
                        "payload": {"$ref": "#/components/schemas/AgentRunRequestedCommand"}
                    }
                },
            },
            "agentEvents": {
                "address": "goldanalyze.agent.events.v1",
                "messages": {
                    "agentRunResult": {
                        "payload": {"$ref": "#/components/schemas/AgentRunResultEvent"}
                    }
                },
            },
            "backtestCommands": {
                "address": "goldanalyze.backtest.commands.v1",
                "messages": {
                    "backtestRunRequested": {
                        "payload": {"$ref": "#/components/schemas/BacktestRunRequestedCommand"}
                    }
                },
            },
            "backtestEvents": {
                "address": "goldanalyze.backtest.events.v1",
                "messages": {
                    "backtestRunResult": {
                        "payload": {"$ref": "#/components/schemas/BacktestRunResultEvent"}
                    }
                },
            },
        },
        "operations": {
            "receiveAgentCommand": {
                "action": "receive",
                "channel": {"$ref": "#/channels/agentCommands"},
            },
            "sendAgentEvent": {
                "action": "send",
                "channel": {"$ref": "#/channels/agentEvents"},
            },
            "receiveBacktestCommand": {
                "action": "receive",
                "channel": {"$ref": "#/channels/backtestCommands"},
            },
            "sendBacktestEvent": {
                "action": "send",
                "channel": {"$ref": "#/channels/backtestEvents"},
            },
        },
        "components": {"schemas": schemas},
    }


def export_contracts(output_root: Path) -> dict[str, str]:
    output_root.mkdir(parents=True, exist_ok=True)
    schema_root = output_root / "schemas"
    for name, model in CONTRACT_MODELS.items():
        _write_json(schema_root / f"{name}.schema.json", model.model_json_schema())
    _write_json(output_root / "backtest-service.openapi.json", backtest_app.openapi())
    _write_json(output_root / "agent-service.openapi.json", agent_app.openapi())
    _write_json(output_root / "asyncapi.v1.json", _asyncapi_document())

    files = sorted(path for path in output_root.rglob("*.json") if path.name != "manifest.json")
    digests = {
        str(path.relative_to(output_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    manifest = {
        "schema_version": "contract-manifest-v1",
        "contract_version": "1.0.0",
        "files": digests,
    }
    _write_json(output_root / "manifest.json", manifest)
    return digests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("docs/contracts"),
        help="Directory for generated contract artifacts",
    )
    args = parser.parse_args()
    digests = export_contracts(args.output_root)
    print(f"exported {len(digests)} versioned contract artifacts to {args.output_root}")


if __name__ == "__main__":
    main()
