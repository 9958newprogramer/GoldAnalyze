from __future__ import annotations

from datetime import timedelta

from app.contracts import (
    BacktestPlanRequest,
    BacktestPlanResponse,
    BacktestSubtaskPlan,
)


def plan_backtests(request: BacktestPlanRequest) -> BacktestPlanResponse:
    """按固定日期间隔为父任务生成确定性的批量回测子任务。"""

    subtasks: list[BacktestSubtaskPlan] = []

    anchor_date = request.start_date
    sequence = 1

    while anchor_date <= request.end_date:
        subtask_start = max(
            request.start_date,
            anchor_date - timedelta(days=request.lookback_days),
        )
        subtask_end = min(
            request.end_date,
            anchor_date + timedelta(days=request.forward_days),
        )

        subtasks.append(
            BacktestSubtaskPlan(
                subtask_id=f"{request.job_id}-{sequence:04d}",
                symbol=request.symbol,
                anchor_date=anchor_date,
                start_date=subtask_start,
                end_date=subtask_end,
            )
        )

        sequence += 1
        anchor_date += timedelta(days=request.interval_days)

    return BacktestPlanResponse(
        request_id=request.request_id,
        job_id=request.job_id,
        total=len(subtasks),
        subtasks=subtasks,
    )