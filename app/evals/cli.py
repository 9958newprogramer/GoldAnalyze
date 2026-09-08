"""Command-line quality gate for the versioned Agent golden dataset."""

from __future__ import annotations

import argparse
import asyncio

from app.bootstrap import build_services
from app.evals.runner import report_as_pretty_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the AurumLab Agent evaluation suite")
    parser.add_argument(
        "--threshold", type=float, default=90.0, help="overall pass threshold (0-100)"
    )
    parser.add_argument("--json", action="store_true", help="print the complete JSON report")
    return parser


async def _run(threshold: float, as_json: bool) -> int:
    services = build_services()
    report = await services.evaluator.run(threshold=threshold)
    services.eval_reports.save(report)
    if as_json:
        print(report_as_pretty_json(report))
    else:
        gate = "PASS" if report.passed else "FAIL"
        print(
            f"[{gate}] dataset={report.dataset_version} score={report.score:.2f} "
            f"cases={report.passed_cases}/{report.total_cases} duration={report.duration_ms:.2f}ms"
        )
        print(
            "  coverage "
            f"intents={report.coverage.intent_counts} "
            f"adversarial={report.coverage.adversarial_cases} "
            f"approval={report.coverage.approval_cases} cache={report.coverage.cache_cases}"
        )
        for result in report.results:
            marker = "PASS" if result.passed else "FAIL"
            print(f"  {marker:<4} {result.case_id:<24} {result.score:>6.2f}")
            for failure in result.failures:
                print(f"       - {failure}")
    return 0 if report.passed else 1


def main() -> None:
    args = _parser().parse_args()
    try:
        exit_code = asyncio.run(_run(args.threshold, args.json))
    except ValueError as exc:
        raise SystemExit(f"evaluation configuration error: {exc}") from exc
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
