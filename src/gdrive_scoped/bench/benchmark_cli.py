"""Command-line entry point for the opt-in live retrieval benchmark.

Cases come from the file named on the command line. When that file does not
exist they are derived from the live corpus and written to it, so a first run
against an unfamiliar corpus measures *that* corpus, and the next run measures
the same documents rather than whatever the corpus holds by then.

The four numbers answer different questions and are not interchangeable:

- **initialization** enumerates every folder in the Drive Location, not just
  the corpus. It is what a cold process pays before its first answer.
- **search** is one `files.list` per batch of parent folders, then a merge by
  rank. It never touches document content.
- **cold read** is download-or-export plus extraction, dominated by the file's
  size and format rather than by anything this code does.
- **warm read** is the same read served from the parsed-document cache within
  one process. It still proves ancestry against Drive on every call, and the
  cache is per process — so it is never the read latency a second process sees.
"""

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
from gdrive_scoped.bench.evaluation import (
    DEFAULT_CASE_COUNT,
    MAX_CASES,
    EvaluationCase,
    derive_cases,
    load_cases,
    save_cases,
)
from gdrive_scoped.drive import DriveGateway, create_gateway
from gdrive_scoped.env import Settings, credentials_from_environ
from gdrive_scoped.scope import ScopedDrive


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    cases_path: Path
    output_path: Path
    iterations: int = 3
    warmup_iterations: int = 1
    read_max_chars: int = 25_000
    baseline_path: Path | None = None
    #: Everything measured here is dominated by round trips to Drive, which
    #: vary by tens of percent between runs on an unloaded network. This is a
    #: gate for catching a doubling, not a drift — and at three iterations a
    #: single slow call already moves a median by 30%.
    max_regression_percent: float = 40.0
    #: How many cases to derive when `cases_path` does not exist.
    case_count: int = DEFAULT_CASE_COUNT


@dataclass(frozen=True, slots=True)
class BenchmarkExecution:
    report: BenchmarkReport
    regressions: list[BenchmarkRegression]
    exit_code: int
    #: Where cases were written, when this run derived them. `None` when it
    #: reused a file, which is what makes two runs comparable.
    derived_cases_path: Path | None = None


async def run_benchmark(
    options: BenchmarkOptions,
    settings: Settings,
    gateway: DriveGateway,
) -> BenchmarkExecution:
    """Run, persist, and optionally compare one live benchmark."""

    if options.max_regression_percent < 0:
        raise ValueError("max_regression_percent must not be negative")
    cases, derived_cases_path = await _resolve_cases(options, settings, gateway)
    report = await RetrievalBenchmark(
        gateway=gateway,
        location=settings.location,
        root_folder_id=settings.root_folder_id,
        folder_map_ttl_seconds=settings.folder_map_ttl_seconds,
    ).run(
        cases,
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
    return BenchmarkExecution(
        report=report,
        regressions=regressions,
        exit_code=exit_code,
        derived_cases_path=derived_cases_path,
    )


async def _resolve_cases(
    options: BenchmarkOptions, settings: Settings, gateway: DriveGateway
) -> tuple[list[EvaluationCase], Path | None]:
    """The cases file, or a derivation of it written where it was expected.

    Deriving on a missing file rather than failing is what lets a first run
    against an unfamiliar corpus measure that corpus. Writing the result is
    what stops the second run measuring different documents, which would make
    `--baseline` compare two unrelated things.
    """

    if options.cases_path.exists():
        return load_cases(options.cases_path), None

    scoped_drive = ScopedDrive(
        gateway=gateway,
        location=settings.location,
        root_folder_id=settings.root_folder_id,
        folder_map_ttl_seconds=settings.folder_map_ttl_seconds,
    )
    await scoped_drive.initialize()
    cases = await derive_cases(scoped_drive, options.case_count)
    if not cases:
        raise ValueError(
            "Could not derive a single case: no readable document in the corpus was "
            f"findable by a keyword from its own name. Write {options.cases_path} by hand."
        )
    save_cases(cases, options.cases_path)
    return cases, options.cases_path


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
    parser.add_argument("--max-regression-percent", type=float, default=40.0)
    parser.add_argument(
        "--case-count",
        type=_case_count,
        default=DEFAULT_CASE_COUNT,
        help=f"How many cases to derive when the cases file is absent (1-{MAX_CASES}).",
    )
    arguments = parser.parse_args(argv)
    return BenchmarkOptions(**vars(arguments))


def _case_count(value: str) -> int:
    """Rejected here rather than by the next run.

    `load_cases` caps a file at `MAX_CASES`. Accepting more would write a
    cases file that this run measures and every later one refuses to load.
    """

    count = int(value)
    if not 1 <= count <= MAX_CASES:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_CASES}")
    return count


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
    if execution.derived_cases_path is not None:
        print(
            f"cases: {len(report.cases)} derived from the corpus -> {execution.derived_cases_path}"
        )
    for case in report.cases:
        print(f"  {case.search_query!r} -> {case.expected_source}")

    print(
        f"\n{report.drive_kind}: {report.iterations} measured iteration(s), "
        f"{report.warmup_iterations} warmup(s), "
        f"folder map TTL {report.folder_map_ttl_seconds:.0f}s"
    )
    print(f"initialization (cold folder map): {report.initialization_ms:.0f} ms\n")

    header = f"{'case':<34}{'search':>10}{'cold read':>12}{'warm read':>12}{'chars':>9}"
    print(header)
    print("-" * len(header))
    for case in report.cases:
        chars = next(
            (sample.content_chars for sample in case.samples if sample.content_chars is not None),
            None,
        )
        print(
            f"{case.expected_source[-33:]:<34}"
            f"{_format_ms(case.search.median_ms):>10}"
            f"{_format_ms(case.cold_read.median_ms):>12}"
            f"{_format_ms(case.warm_read.median_ms):>12}"
            f"{'-' if chars is None else f'{chars:,}':>9}"
            f"{'' if case.passed else '   NOT FOUND'}"
        )

    # Per operation, because the four numbers above are sums of these and a
    # regression in one of them is invisible in any of the four.
    print("\nper Drive operation (measured iterations only)")
    for operation, stats in sorted(report.drive_operations.items()):
        print(
            f"  {operation:<18}{stats.count:>5} call(s)  "
            f"median {stats.median_ms:>7.0f} ms  p95 {stats.p95_ms:>7.0f} ms"
        )

    for regression in execution.regressions:
        print(
            f"\nREGRESSION {regression.search_query!r} {regression.metric}: "
            f"{regression.baseline_median_ms:.0f} ms -> "
            f"{regression.current_median_ms:.0f} ms "
            f"(+{regression.regression_percent:.0f}%)"
        )
    print(f"\nreport: {output_path}")


def _format_ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f} ms"


if __name__ == "__main__":
    raise SystemExit(main())
