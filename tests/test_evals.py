from app.bootstrap import build_services
from app.config import Settings
from app.evals.models import EvalCase
from app.evals.runner import evaluate_case, load_eval_cases


async def test_golden_dataset_passes_and_is_persisted(tmp_path):
    services = build_services(Settings(app_database_path=str(tmp_path / "runs.db")))

    report = await services.evaluator.run()
    services.eval_reports.save(report)

    assert report.passed is True
    assert report.score == 100
    assert report.passed_cases == report.total_cases == 16
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


def test_golden_dataset_has_unique_bounded_cases():
    services = build_services()
    cases = load_eval_cases(services.settings.resolved_eval_dataset_path)

    assert len(cases) == 16
    assert len({case.case_id for case in cases}) == len(cases)
