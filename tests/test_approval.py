from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.approval import (
    ApprovalBinding,
    ApprovalBindingError,
    ApprovalRepository,
    ApprovalStateError,
    ApprovalTokenError,
    arguments_digest,
)
from app.tools.registry import ToolMetadata, ToolPolicy, ToolRegistry


def _binding(**changes: str) -> ApprovalBinding:
    base = ApprovalBinding(
        run_id="a" * 12,
        plan_id="b" * 12,
        step_id="external_search",
        tool_name="search_external_knowledge",
        arguments_digest=arguments_digest({"query": "bounded"}),
        effect="external",
        risk="medium",
        reason="External network access requires review.",
    )
    return replace(base, **changes)


def test_approval_token_is_hashed_and_consumed_once(tmp_path):
    repository = ApprovalRepository(tmp_path / "approvals.db")
    pending = repository.request(_binding())

    grant = repository.approve(pending.approval_id, decided_by="test-operator")

    assert pending.status == "pending"
    assert grant.approval.status == "approved"
    assert grant.approval_token not in repr(grant)
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT token_hash FROM approval_requests WHERE approval_id = ?",
            (pending.approval_id,),
        ).fetchone()
    assert row["token_hash"]
    assert row["token_hash"] != grant.approval_token

    consumed = repository.consume(grant.approval_token, _binding())

    assert consumed.status == "consumed"
    assert consumed.consumed_at is not None
    with repository.connect() as connection:
        stored = connection.execute(
            "SELECT token_hash FROM approval_requests WHERE approval_id = ?",
            (pending.approval_id,),
        ).fetchone()
    assert stored["token_hash"] is None
    with pytest.raises(ApprovalStateError, match="not active"):
        repository.consume(grant.approval_token, _binding())


def test_forged_token_is_rejected_without_revoking_valid_grant(tmp_path):
    repository = ApprovalRepository(tmp_path / "approvals.db")
    pending = repository.request(_binding())
    grant = repository.approve(pending.approval_id, decided_by="test-operator")
    forged = f"{pending.approval_id}.{'x' * 43}"

    with pytest.raises(ApprovalTokenError, match="invalid"):
        repository.consume(forged, _binding())

    assert repository.consume(grant.approval_token, _binding()).status == "consumed"


def test_approval_is_bound_to_run_plan_tool_and_arguments(tmp_path):
    repository = ApprovalRepository(tmp_path / "approvals.db")
    pending = repository.request(_binding())
    grant = repository.approve(pending.approval_id, decided_by="test-operator")

    with pytest.raises(ApprovalBindingError, match="different run"):
        repository.consume(grant.approval_token, _binding(run_id="c" * 12))
    with pytest.raises(ApprovalBindingError, match="does not match"):
        repository.consume(grant.approval_token, _binding(plan_id="d" * 12))
    with pytest.raises(ApprovalBindingError, match="does not match"):
        repository.consume(
            grant.approval_token,
            _binding(arguments_digest=arguments_digest({"query": "tampered"})),
        )

    assert repository.consume(grant.approval_token, _binding()).status == "consumed"


def test_pending_and_approved_grants_expire(tmp_path):
    current = [datetime(2026, 1, 1, tzinfo=UTC)]
    repository = ApprovalRepository(
        tmp_path / "approvals.db",
        ttl_seconds=30,
        now=lambda: current[0],
    )
    first = repository.request(_binding())
    current[0] += timedelta(seconds=31)

    with pytest.raises(ApprovalStateError, match="not pending"):
        repository.approve(first.approval_id, decided_by="test-operator")
    assert repository.get(first.approval_id).status == "expired"

    second = repository.request(_binding())
    grant = repository.approve(second.approval_id, decided_by="test-operator")
    current[0] += timedelta(seconds=31)
    with pytest.raises(ApprovalStateError, match="not active"):
        repository.validate_token(
            grant.approval_token,
            approval_id=second.approval_id,
            run_id=second.run_id,
        )
    assert repository.get(second.approval_id).status == "expired"


def test_concurrent_resume_can_consume_grant_only_once(tmp_path):
    repository = ApprovalRepository(tmp_path / "approvals.db")
    pending = repository.request(_binding())
    grant = repository.approve(pending.approval_id, decided_by="test-operator")

    def consume() -> str:
        try:
            return repository.consume(grant.approval_token, _binding()).status
        except ApprovalStateError:
            return "rejected-replay"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: consume(), range(2)))

    assert sorted(outcomes) == ["consumed", "rejected-replay"]


def test_tool_risk_matrix_is_runtime_validated_and_most_restrictive_wins():
    async def handler() -> str:
        return "ok"

    registry = ToolRegistry()
    registry.register("read_profile", handler, ToolMetadata(effect="read", risk="low"))
    registry.register(
        "external_lookup",
        handler,
        ToolMetadata(effect="external", risk="medium"),
    )
    registry.register(
        "admin_action",
        handler,
        ToolMetadata(effect="privileged", risk="high", requires_approval=True),
    )
    policy = ToolPolicy(
        policy_name="risk-matrix@1",
        allowed_tools={"read_profile", "external_lookup", "admin_action"},
        max_calls=3,
    )

    assert policy.evaluate(registry.get_definition("read_profile")).action == "allow"
    assert policy.evaluate(registry.get_definition("external_lookup")).action == "review"
    assert policy.evaluate(registry.get_definition("admin_action")).action == "deny"
    with pytest.raises(ValueError, match="external/write/privileged"):
        ToolMetadata(effect="external", risk="low")
