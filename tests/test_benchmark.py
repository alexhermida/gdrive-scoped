from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gdrive_scoped.bench.benchmark import (
    RetrievalBenchmark,
    compare_benchmarks,
    load_benchmark_report,
    save_benchmark_report,
)
from gdrive_scoped.bench.benchmark_cli import BenchmarkOptions, run_benchmark
from gdrive_scoped.bench.evaluation import EvaluationCase, load_cases, save_cases
from gdrive_scoped.drive import FOLDER_MIME_TYPE, DriveItem, SearchPage
from gdrive_scoped.env import Settings
from gdrive_scoped.location import DriveKind, DriveLocation


class BenchmarkGateway:
    def __init__(self) -> None:
        self.root = DriveItem("root", "Corpus", FOLDER_MIME_TYPE, "drive")
        self.folder = DriveItem("folder", "Plans", FOLDER_MIME_TYPE, "drive", parents=("root",))
        self.document = DriveItem(
            "document",
            "Timeline.md",
            "text/markdown",
            "drive",
            parents=("folder",),
            modified_time="2026-08-31T10:00:00Z",
        )
        self.items = {item.id: item for item in (self.root, self.folder, self.document)}
        self.downloads = 0

    async def get_item(self, item_id: str) -> DriveItem:
        return self.items[item_id]

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        return [item for item in self.items.values() if item.parents == (folder_id,)]

    async def list_folders(self) -> list[DriveItem]:
        return [item for item in self.items.values() if item.mime_type == FOLDER_MIME_TYPE]

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        # The same query without the keyword clause, which is what it is.
        return (await self.search_items(parent_ids, "")).items

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int = 1000
    ) -> SearchPage:
        return SearchPage(items=[self.document])

    async def download_item(self, item_id: str) -> bytes:
        self.downloads += 1
        return b"timeline"

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        raise AssertionError("Markdown should be downloaded, not exported")


@pytest.mark.anyio
async def test_benchmark_measures_search_and_cold_and_warm_document_reads() -> None:
    gateway = BenchmarkGateway()
    benchmark = RetrievalBenchmark(
        gateway=gateway,
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )
    cases = [
        EvaluationCase(
            question="When is the project due?",
            search_query="project timeline",
            expected_source="Plans/Timeline.md",
        )
    ]

    report = await benchmark.run(
        cases,
        iterations=2,
        warmup_iterations=0,
        read_max_chars=100,
    )

    [case] = report.cases
    assert report.iterations == 2
    assert len(case.samples) == 2
    assert all(sample.source_found for sample in case.samples)
    assert all(sample.content_chars == len("timeline") for sample in case.samples)
    assert case.search.sample_count == 2
    assert case.cold_read.sample_count == 2
    assert case.warm_read.sample_count == 2
    assert case.end_to_end.sample_count == 2
    assert report.drive_operations["download_item"].count == 2
    assert gateway.downloads == 2


@pytest.mark.anyio
async def test_benchmark_comparison_reports_median_latency_regressions() -> None:
    benchmark = RetrievalBenchmark(
        gateway=BenchmarkGateway(),
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )
    cases = [
        EvaluationCase(
            question="When is the project due?",
            search_query="project timeline",
            expected_source="Timeline.md",
        )
    ]
    report = await benchmark.run(cases, iterations=1, warmup_iterations=0)
    [case] = report.cases
    baseline_case = case.model_copy(
        update={"search": case.search.model_copy(update={"median_ms": 100.0})}
    )
    current_case = case.model_copy(
        update={"search": case.search.model_copy(update={"median_ms": 130.0})}
    )
    baseline = report.model_copy(update={"cases": [baseline_case]})
    current = report.model_copy(update={"cases": [current_case]})

    regressions = compare_benchmarks(
        current,
        baseline,
        max_regression_percent=20,
    )

    assert [regression.metric for regression in regressions] == ["search"]
    assert regressions[0].regression_percent == pytest.approx(30.0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("corpus_fingerprint", "other-root", "different Drive corpora"),
        ("read_max_chars", 1_000, "different read_max_chars"),
        ("folder_map_ttl_seconds", 0.0, "different folder_map_ttl_seconds"),
    ],
)
async def test_benchmark_comparison_refuses_a_baseline_that_measured_something_else(
    field: str, value: object, message: str
) -> None:
    benchmark = RetrievalBenchmark(
        gateway=BenchmarkGateway(),
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )
    cases = [
        EvaluationCase(
            question="When is the project due?",
            search_query="project timeline",
            expected_source="Timeline.md",
        )
    ]
    report = await benchmark.run(cases, iterations=1, warmup_iterations=0)
    baseline = report.model_copy(update={field: value})

    with pytest.raises(ValueError, match=message):
        compare_benchmarks(report, baseline, max_regression_percent=20)


@pytest.mark.anyio
async def test_benchmark_reports_missing_sources_without_timing_a_different_document() -> None:
    gateway = BenchmarkGateway()
    benchmark = RetrievalBenchmark(
        gateway=gateway,
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )
    cases = [
        EvaluationCase(
            question="Where is the missing document?",
            search_query="missing",
            expected_source="Missing.md",
        )
    ]

    report = await benchmark.run(cases, iterations=1, warmup_iterations=0)

    assert report.passed is False
    assert report.cases[0].cold_read.sample_count == 0
    assert report.cases[0].samples[0].source_found is False
    assert "download_item" not in report.drive_operations
    assert gateway.downloads == 0


@pytest.mark.anyio
async def test_benchmark_excludes_warmup_drive_calls_from_measured_totals() -> None:
    gateway = BenchmarkGateway()
    benchmark = RetrievalBenchmark(
        gateway=gateway,
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )
    cases = [
        EvaluationCase(
            question="When is the project due?",
            search_query="project timeline",
            expected_source="Timeline.md",
        )
    ]

    report = await benchmark.run(cases, iterations=1, warmup_iterations=1)

    assert gateway.downloads == 2
    assert report.drive_operations["download_item"].count == 1


@pytest.mark.anyio
async def test_benchmark_report_round_trips_as_a_json_artifact(tmp_path: Path) -> None:
    benchmark = RetrievalBenchmark(
        gateway=BenchmarkGateway(),
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )
    cases = [
        EvaluationCase(
            question="When is the project due?",
            search_query="project timeline",
            expected_source="Timeline.md",
        )
    ]
    report = await benchmark.run(cases, iterations=1, warmup_iterations=0)
    path = tmp_path / "reports" / "benchmark.json"

    save_benchmark_report(report, path)

    assert load_benchmark_report(path) == report


@pytest.mark.anyio
async def test_benchmark_command_writes_a_successful_live_report(tmp_path: Path) -> None:
    cases_path = tmp_path / "questions.json"
    cases_path.write_text(
        """[
          {
            "question": "When is the project due?",
            "search_query": "project timeline",
            "expected_source": "Timeline.md"
          }
        ]""",
        encoding="utf-8",
    )
    output_path = tmp_path / "benchmark.json"
    options = BenchmarkOptions(
        cases_path=cases_path,
        output_path=output_path,
        iterations=1,
        warmup_iterations=0,
        read_max_chars=100,
    )
    settings = Settings(
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )

    execution = await run_benchmark(options, settings, BenchmarkGateway())

    assert execution.exit_code == 0
    assert execution.regressions == []
    assert load_benchmark_report(output_path).passed is True

    [case] = execution.report.cases
    baseline_case = case.model_copy(
        update={"search": case.search.model_copy(update={"median_ms": 0.000_000_001})}
    )
    baseline = execution.report.model_copy(update={"cases": [baseline_case]})
    baseline_path = tmp_path / "baseline.json"
    save_benchmark_report(baseline, baseline_path)

    regressed = await run_benchmark(
        replace(
            options,
            baseline_path=baseline_path,
            max_regression_percent=0,
        ),
        settings,
        BenchmarkGateway(),
    )

    assert regressed.exit_code == 1
    assert "search" in [regression.metric for regression in regressed.regressions]


@pytest.mark.anyio
async def test_benchmark_command_derives_and_keeps_its_cases_when_the_file_is_absent(
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "derived" / "cases.json"
    options = BenchmarkOptions(
        cases_path=cases_path,
        output_path=tmp_path / "benchmark.json",
        iterations=1,
        warmup_iterations=0,
        read_max_chars=100,
    )
    settings = Settings(
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )

    execution = await run_benchmark(options, settings, BenchmarkGateway())

    assert execution.exit_code == 0
    assert [case.expected_source for case in execution.report.cases] == ["Plans/Timeline.md"]
    # Written, so the next run measures the same documents rather than
    # re-deriving them from a corpus that has moved on.
    assert [case.expected_source for case in load_cases(cases_path)] == ["Plans/Timeline.md"]
    assert execution.derived_cases_path == cases_path


@pytest.mark.anyio
async def test_benchmark_command_reuses_an_existing_cases_file_without_deriving(
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "cases.json"
    save_cases(
        [
            EvaluationCase(
                question="When is the project due?",
                search_query="project timeline",
                expected_source="Timeline.md",
            )
        ],
        cases_path,
    )
    options = BenchmarkOptions(
        cases_path=cases_path,
        output_path=tmp_path / "benchmark.json",
        iterations=1,
        warmup_iterations=0,
        read_max_chars=100,
    )
    settings = Settings(
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )

    execution = await run_benchmark(options, settings, BenchmarkGateway())

    assert [case.search_query for case in execution.report.cases] == ["project timeline"]
    assert execution.derived_cases_path is None


@pytest.mark.anyio
async def test_benchmark_command_says_so_when_no_case_can_be_derived(tmp_path: Path) -> None:
    class FindsNothingGateway(BenchmarkGateway):
        async def search_items(
            self, parent_ids: tuple[str, ...], keywords: str, limit: int = 1000
        ) -> SearchPage:
            return SearchPage(items=[])

    options = BenchmarkOptions(
        cases_path=tmp_path / "cases.json",
        output_path=tmp_path / "benchmark.json",
        iterations=1,
        warmup_iterations=0,
    )
    settings = Settings(
        location=DriveLocation(DriveKind.SHARED_DRIVE, "drive"),
        root_folder_id="root",
    )

    with pytest.raises(ValueError, match="Could not derive"):
        await run_benchmark(options, settings, FindsNothingGateway())
