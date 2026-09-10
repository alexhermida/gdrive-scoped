"""Command-line entry point for the opt-in live retrieval benchmark."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from gdrive_scoped.bench.benchmark import (
    BenchmarkRegression,
    BenchmarkReport,
    RetrievalBenchmark,
    compare_benchmarks,
    load_benchmark_report,
    save_benchmark_report,
)
from gdrive_scoped.bench.evaluation import load_cases
from gdrive_scoped.drive import DriveGateway, create_gateway
from gdrive_scoped.env import Settings, credentials_from_environ


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    cases_path: Path
    output_path: Path
    iterations: int = 3
    warmup_iterations: int = 1
    read_max_chars: int = 25_000
    baseline_path: Path | None = None
    max_regression_percent: float = 25.0


@dataclass(frozen=True, slots=True)
class BenchmarkExecution:
    report: BenchmarkReport
    regressions: list[BenchmarkRegression]
    exit_code: int


async def run_benchmark(
    options: BenchmarkOptions,
    settings: Settings,
    gateway: DriveGateway,
) -> BenchmarkExecution:
    """Run, persist, and optionally compare one live benchmark."""

    if options.max_regression_percent < 0:
        raise ValueError("max_regression_percent must not be negative")
    report = await RetrievalBenchmark(
        gateway=gateway,
        location=settings.location,
        root_folder_id=settings.root_folder_id,
        folder_map_ttl_seconds=settings.folder_map_ttl_seconds,
    ).run(
        load_cases(options.cases_path),
        iterations=options.iterations,
        warmup_iterations=options.warmup_iterations,
        read_max_chars=options.read_max_chars,
    )
    save_benchmark_report(report, options.output_path)
    regressions = (
        compare_benchmarks(
            report,
            load_benchmark_report(options.baseline_path),
            max_regression_percent=options.max_regression_percent,
        )
        if options.baseline_path is not None
        else []
    )
    exit_code = 0 if report.passed and not regressions else 1
    return BenchmarkExecution(report=report, regressions=regressions, exit_code=exit_code)


def parse_args(argv: list[str] | None = None) -> BenchmarkOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cases_path",
        nargs="?",
        type=Path,
        default=Path("evaluation/questions.local.json"),
    )
    parser.add_argument(
        "--output", dest="output_path", type=Path, default=Path("_tmp/benchmark.json")
    )
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--warmup-iterations", type=int, default=1)
    parser.add_argument("--read-max-chars", type=int, default=25_000)
    parser.add_argument("--baseline", dest="baseline_path", type=Path)
    parser.add_argument("--max-regression-percent", type=float, default=25.0)
    arguments = parser.parse_args(argv)
    return BenchmarkOptions(**vars(arguments))


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    settings = Settings.from_environ()
    execution = asyncio.run(
        run_benchmark(
            options, settings, create_gateway(credentials_from_environ(), settings.location)
        )
    )
    _print_summary(execution, options.output_path)
    return execution.exit_code


def _print_summary(execution: BenchmarkExecution, output_path: Path) -> None:
    report = execution.report
    status = "passed" if report.passed else "missing expected sources"
    print(
        f"{report.drive_kind} benchmark {status}: "
        f"{report.iterations} measured iteration(s), "
        f"{report.warmup_iterations} warmup(s)"
    )
    for case in report.cases:
        print(
            f"- {case.question}: "
            f"search {_format_ms(case.search.median_ms)}, "
            f"cold read {_format_ms(case.cold_read.median_ms)}, "
            f"warm read {_format_ms(case.warm_read.median_ms)}, "
            f"end-to-end {_format_ms(case.end_to_end.median_ms)}"
        )
    for regression in execution.regressions:
        print(
            f"REGRESSION {regression.search_query!r} {regression.metric}: "
            f"{regression.baseline_median_ms:.1f} ms -> "
            f"{regression.current_median_ms:.1f} ms "
            f"(+{regression.regression_percent:.1f}%)"
        )
    print(f"Report: {output_path}")


def _format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


if __name__ == "__main__":
    raise SystemExit(main())
