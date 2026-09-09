"""Deterministic compiler and analysis functions for the non-financial incident Skill."""

from __future__ import annotations

import re

from app.models import (
    IncidentActionItem,
    IncidentAssessment,
    IncidentInputProfile,
    IncidentReviewResult,
    IncidentSpec,
)

INCIDENT_SCHEMA_VERSION = "incident-review-v1"


def _number(pattern: str, question: str) -> float | None:
    match = re.search(pattern, question, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def compile_incident_spec(question: str) -> IncidentSpec:
    """Compile only bounded numeric facts; never execute text embedded in the incident."""
    service_match = re.search(
        r"\b([A-Za-z0-9][A-Za-z0-9._-]{0,63})\s*(?:服务|service)\s*(?:事故|故障)?",
        question,
        flags=re.IGNORECASE,
    )
    error_rate = _number(r"错误率[^0-9]{0,6}([0-9]+(?:\.[0-9]+)?)\s*%", question)
    latency_match = re.search(
        r"P95[^0-9]{0,6}([0-9]+(?:\.[0-9]+)?)\s*(ms|毫秒|s|秒)",
        question,
        flags=re.IGNORECASE,
    )
    latency_ms = None
    if latency_match:
        latency_ms = float(latency_match.group(1))
        if latency_match.group(2).casefold() in {"s", "秒"}:
            latency_ms *= 1_000
    duration_match = re.search(r"持续[^0-9]{0,6}([0-9]+)\s*(分钟|分鐘|小时|小時)", question)
    duration_minutes = None
    if duration_match:
        duration_minutes = int(duration_match.group(1))
        if duration_match.group(2) in {"小时", "小時"}:
            duration_minutes *= 60
    affected = _number(r"影响[^0-9]{0,6}([0-9]+)\s*(?:个|個)?\s*请求", question)
    return IncidentSpec(
        service=service_match.group(1) if service_match else "unknown-service",
        error_rate_pct=error_rate,
        p95_latency_ms=latency_ms,
        duration_minutes=duration_minutes,
        affected_requests=int(affected) if affected is not None else None,
    )


def validate_incident_spec(spec: IncidentSpec) -> list[str]:
    warnings: list[str] = []
    fields = {
        "错误率": spec.error_rate_pct,
        "P95 延迟": spec.p95_latency_ms,
        "持续时间": spec.duration_minutes,
        "影响请求数": spec.affected_requests,
    }
    missing = [name for name, value in fields.items() if value is None]
    if missing:
        warnings.append(f"未提供{'、'.join(missing)}，风险等级仅基于已知事实。")
    warnings.append("根因状态固定为 unverified，未经证据验证不生成根因结论。")
    return warnings


def assess_incident_impact(spec: IncidentSpec) -> IncidentAssessment:
    score = 0
    findings: list[str] = []
    if spec.error_rate_pct is not None:
        value = spec.error_rate_pct
        score += (
            35
            if value >= 20
            else 28
            if value >= 10
            else 20
            if value >= 5
            else 10
            if value >= 1
            else 0
        )
        findings.append(f"观测错误率 {value:.2f}%。")
    if spec.p95_latency_ms is not None:
        value = spec.p95_latency_ms
        score += (
            25
            if value >= 5_000
            else 20
            if value >= 2_000
            else 14
            if value >= 1_000
            else 8
            if value >= 500
            else 0
        )
        findings.append(f"观测 P95 延迟 {value:.0f}ms。")
    if spec.duration_minutes is not None:
        value = spec.duration_minutes
        score += (
            25
            if value >= 120
            else 20
            if value >= 60
            else 14
            if value >= 30
            else 7
            if value >= 10
            else 0
        )
        findings.append(f"事故持续 {value} 分钟。")
    if spec.affected_requests is not None:
        value = spec.affected_requests
        score += (
            15
            if value >= 100_000
            else 12
            if value >= 10_000
            else 8
            if value >= 1_000
            else 4
            if value > 0
            else 0
        )
        findings.append(f"已知影响 {value} 个请求。")
    severity = (
        "SEV-1" if score >= 75 else "SEV-2" if score >= 50 else "SEV-3" if score >= 25 else "SEV-4"
    )
    return IncidentAssessment(severity=severity, risk_score=score, findings=findings)


def build_incident_action_plan(assessment: IncidentAssessment) -> list[IncidentActionItem]:
    urgent = "P0" if assessment.severity == "SEV-1" else "P1"
    return [
        IncidentActionItem(
            action_id="ACT-1",
            priority=urgent,
            owner_role="on-call",
            category="mitigate",
            action="确认影响面并评估回滚、限流或隔离故障实例；实际变更由值班人员执行。",
        ),
        IncidentActionItem(
            action_id="ACT-2",
            priority="P1",
            owner_role="service-owner",
            category="observe",
            action="对齐错误率、P95 延迟和影响请求数的时间窗口，补充可验证的时间线。",
        ),
        IncidentActionItem(
            action_id="ACT-3",
            priority="P2",
            owner_role="platform",
            category="prevent",
            action="将已确认触发条件固化为监控告警、容量演练和发布回归项。",
        ),
    ]


def summarize_incident_review(
    spec: IncidentSpec,
    assessment: IncidentAssessment,
    input_profile: IncidentInputProfile,
    action_items: list[IncidentActionItem],
) -> IncidentReviewResult:
    summary = (
        f"{spec.service} 事故的结构化风险等级为 {assessment.severity} "
        f"({assessment.risk_score}/100)，已生成 {len(action_items)} 项分级行动。"
        "根因仍未验证；本 Skill 只生成分析 Artifact，不执行变更、通知或外部请求。"
    )
    return IncidentReviewResult(
        service=spec.service,
        severity=assessment.severity,
        risk_score=assessment.risk_score,
        input_profile=input_profile,
        findings=assessment.findings,
        action_items=action_items,
        summary=summary,
    )
