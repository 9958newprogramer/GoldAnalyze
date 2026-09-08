import pytest

from app.bootstrap import build_services
from app.config import Settings
from app.evals.models import EvalCase
from app.evals.runner import evaluate_case, load_eval_cases, validate_dataset_coverage


async def test_golden_dataset_passes_and_is_persisted(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runs.db")))

    report = await services.evaluator.run()
    services.eval_reports.save(report)

    assert report.passed is True
    assert report.score == 100
    assert report.passed_cases == report.total_cases == 108
    assert report.coverage.adversarial_cases == 16
    assert report.coverage.approval_cases == 12
    assert report.coverage.cache_cases == 8
    assert services.eval_reports.latest() == report


async def test_eval_case_fails_when_expected_spec_differs(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runs.db")))
    run = await services.agent.run("黄金日K，10日均线上穿30日均线。")
    case = EvalCase(
        case_id="intentional_mismatch",
        question=run.question,
        expected_intent="backtest_strategy",
        expected_skill="backtest-strategy",
        expected_artifact="backtest",
        expected_spec={"fast_window": 11},
        required_stages=[event.stage for event in run.events],
    )

    result = evaluate_case(case, run, latency_ms=1)

    assert result.passed is False
    assert result.dimensions["task_accuracy"].passed is False
    assert "expected=11" in result.failures[0]


def test_golden_dataset_meets_diversity_contract():
    services = build_services()
    cases = load_eval_cases(services.settings.resolved_eval_dataset_path)
    coverage = validate_dataset_coverage(cases)

    assert len(cases) == 108
    assert len({case.case_id for case in cases}) == len(cases)
    assert coverage.unique_questions == 108
    assert coverage.intent_counts == {
        "backtest_strategy": 44,
        "external_research": 12,
        "other": 28,
        "query_market_data": 24,
    }
    assert coverage.timeframe_counts == {"1d": 34, "1h": 34}


def test_dataset_coverage_rejects_small_or_mislabeled_sets():
    services = build_services()
    cases = load_eval_cases(services.settings.resolved_eval_dataset_path)

    with pytest.raises(ValueError, match="案例总数"):
        validate_dataset_coverage(cases[:99])

    research_index = next(
        index for index, case in enumerate(cases) if case.approval_scenario == "approve"
    )
    mislabeled = list(cases)
    case = mislabeled[research_index]
    mislabeled[research_index] = case.model_copy(
        update={"tags": [tag for tag in case.tags if tag != "approval"]}
    )
    with pytest.raises(ValueError, match="缺少 approval 标签"):
        validate_dataset_coverage(mislabeled)
