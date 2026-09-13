import tomllib
from pathlib import Path

import yaml

from app import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_release_versions_are_consistent():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert project["version"] == __version__ == "1.0.0"
    assert compose["services"]["api"]["image"] == f"aurumlab:{__version__}"


def test_unified_compose_has_bounded_app_and_observability_services():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(services) == {"api", "worker", "redis", "jaeger", "otel-collector"}
    assert services["api"]["image"] == services["worker"]["image"] == "aurumlab:1.0.0"
    assert services["api"]["environment"]["APP_PROJECT_ROOT"] == "/app"
    assert services["api"]["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert (
        services["worker"]["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
        == "http://otel-collector:4318"
    )
    assert services["api"]["volumes"] == services["worker"]["volumes"]
    assert services["worker"]["command"] == ["aurumlab-worker"]
    assert services["worker"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["api"]["read_only"] is True
    assert services["worker"]["read_only"] is True
    assert services["api"]["cap_drop"] == ["ALL"]
    assert services["worker"]["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in services["api"]["security_opt"]
    assert "healthcheck" in services["api"]
    assert services["worker"]["healthcheck"]["test"] == [
        "CMD",
        "python",
        "scripts/worker_health.py",
    ]

    published_ports = [port for service in services.values() for port in service.get("ports", [])]
    assert published_ports
    assert all(port.startswith("127.0.0.1:") for port in published_ports)


def test_docker_build_context_excludes_secrets_and_runs_as_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert dockerfile.startswith("FROM python:3.12.14-slim-bookworm\n")
    assert "APP_PROJECT_ROOT=/app" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "pip install --requirement requirements.lock" in dockerfile
    assert "pip install --no-deps ." in dockerfile
    assert 'CMD ["aurumlab"]' in dockerfile
    assert {".git", ".venv", ".env", "var", "tests"} <= ignored

    locked = [
        line
        for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(locked) >= 40
    assert all("==" in line and ">=" not in line for line in locked)


def test_ci_runs_real_container_smoke_after_quality_gate():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    verify = workflow["jobs"]["verify"]
    java_verify = workflow["jobs"]["java-verify"]
    smoke = workflow["jobs"]["container-smoke"]

    verify_commands = "\n".join(step.get("run", "") for step in verify["steps"])
    assert "python -m venv .venv" in verify_commands
    assert ".venv/bin/python -m pip install -e '.[dev]'" in verify_commands

    java_commands = "\n".join(step.get("run", "") for step in java_verify["steps"])
    assert "./mvnw --batch-mode test" in java_commands
    assert set(smoke["needs"]) == {"verify", "java-verify"}
    commands = "\n".join(step.get("run", "") for step in smoke["steps"])
    assert "docker compose config --quiet" in commands
    assert "docker build --tag aurumlab:1.0.0 ." in commands
    assert "docker compose up -d --no-build --wait" in commands
    assert "scripts/demo_resume.py" in commands
    assert "docker compose down --volumes" in commands
