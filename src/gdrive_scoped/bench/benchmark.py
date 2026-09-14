"""Opt-in performance measurements for live folder-scoped retrieval."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from pydantic import BaseModel, ConfigDict

from gdrive_scoped.bench.evaluation import EvaluationCase
from gdrive_scoped.documents import MAX_READ_CHARS, DocumentService
from gdrive_scoped.drive import DriveGateway, DriveItem, SearchPage
from gdrive_scoped.location import DriveLocation
from gdrive_scoped.scope import DEFAULT_FOLDER_MAP_TTL_SECONDS, ScopedDrive, ScopedItem

Clock = Callable[[], int]


class LatencyStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    sample_count: int
    min_ms: float | None
    median_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class DriveOperationStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    count: int
    total_ms: float
    median_ms: float
    p95_ms: float


class BenchmarkSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    iteration: int
    source_found: bool
    result_count: int
    search_ms: float
    cold_read_ms: float | None
    warm_read_ms: float | None
    end_to_end_ms: float | None
    content_chars: int | None


class CaseBenchmark(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str
    search_query: str
    expected_source: str
    passed: bool
    samples: list[BenchmarkSample]
    search: LatencyStats
    cold_read: LatencyStats
    warm_read: LatencyStats
    end_to_end: LatencyStats


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    started_at: datetime
    drive_kind: str
    corpus_fingerprint: str
    iterations: int
    warmup_iterations: int
    #: Recorded because it changes what the numbers mean: it bounds the read the
    #: timings include, so a baseline with a different bound timed a different read.
    read_max_chars: int
    #: Recorded because it changes what the numbers mean: a run with a warm folder map
    #: is not comparable to one that re-enumerates on every request.
    folder_map_ttl_seconds: float
    #: Root validation plus the cold folder enumeration: what a fresh process pays
    #: before it can answer anything.
    initialization_ms: float
    passed: bool
    cases: list[CaseBenchmark]
    drive_operations: dict[str, DriveOperationStats]


class BenchmarkRegression(BaseModel):
    model_config = ConfigDict(frozen=True)

    search_query: str
    metric: str
    baseline_median_ms: float
    current_median_ms: float
    regression_percent: float


@dataclass(frozen=True, slots=True)
class _DriveCall:
    operation: str
    duration_ms: float


@dataclass(slots=True)
class _TimedDriveGateway:
    gateway: DriveGateway
    clock: Clock
    calls: list[_DriveCall] = field(default_factory=list)

    async def get_item(self, item_id: str) -> DriveItem:
        return await self._measure("get_item", lambda: self.gateway.get_item(item_id))

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        return await self._measure("list_children", lambda: self.gateway.list_children(folder_id))

    async def list_folders(self) -> list[DriveItem]:
        return await self._measure("list_folders", self.gateway.list_folders)

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        return await self._measure(
            "list_descendants", lambda: self.gateway.list_descendants(parent_ids)
        )

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int
    ) -> SearchPage:
        return await self._measure(
            "search_items", lambda: self.gateway.search_items(parent_ids, keywords, limit)
        )

    async def download_item(self, item_id: str) -> bytes:
        return await self._measure("download_item", lambda: self.gateway.download_item(item_id))

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        return await self._measure(
            "export_item", lambda: self.gateway.export_item(item_id, mime_type)
        )

    async def _measure[T](self, operation: str, call: Callable[[], Awaitable[T]]) -> T:
        started = self.clock()
        try:
            return await call()
        finally:
            self.calls.append(_DriveCall(operation, _elapsed_ms(started, self.clock())))


@dataclass(slots=True)
class RetrievalBenchmark:
    """Measure the real Document Service over one configured Drive gateway."""

    gateway: DriveGateway
    location: DriveLocation
    root_folder_id: str
    folder_map_ttl_seconds: float = DEFAULT_FOLDER_MAP_TTL_SECONDS
    clock: Clock = perf_counter_ns

    async def run(
        self,
        cases: list[EvaluationCase],
        *,
        iterations: int = 3,
        warmup_iterations: int = 1,
        read_max_chars: int = 25_000,
    ) -> BenchmarkReport:
        _validate_options(cases, iterations, warmup_iterations, read_max_chars)
        started_at = datetime.now(UTC)
        timed_gateway = _TimedDriveGateway(self.gateway, self.clock)
        scoped_drive = ScopedDrive(
            timed_gateway,
            self.location,
            self.root_folder_id,
            folder_map_ttl_seconds=self.folder_map_ttl_seconds,
        )

        # The root read alone is one metadata call; the folder enumeration behind
        # `folder_paths` is the part a cold process actually waits for.
        initialization_started = self.clock()
        await scoped_drive.initialize()
        await scoped_drive.folder_paths()
        initialization_ms = _elapsed_ms(initialization_started, self.clock())

        for _ in range(warmup_iterations):
            for case in cases:
                await self._run_case(scoped_drive, case, 0, read_max_chars)

        timed_gateway.calls.clear()
        samples_by_case: list[list[BenchmarkSample]] = [[] for _ in cases]
        for iteration in range(1, iterations + 1):
            for index, case in enumerate(cases):
                sample = await self._run_case(scoped_drive, case, iteration, read_max_chars)
                samples_by_case[index].append(sample)

        case_reports = [
            _summarize_case(case, samples)
            for case, samples in zip(cases, samples_by_case, strict=True)
        ]
        return BenchmarkReport(
            started_at=started_at,
            drive_kind=self.location.kind.value,
            corpus_fingerprint=sha256(self.root_folder_id.encode()).hexdigest()[:12],
            iterations=iterations,
            warmup_iterations=warmup_iterations,
            read_max_chars=read_max_chars,
            folder_map_ttl_seconds=self.folder_map_ttl_seconds,
            initialization_ms=initialization_ms,
            passed=all(case.passed for case in case_reports),
            cases=case_reports,
            drive_operations=_summarize_drive_calls(timed_gateway.calls),
        )

    async def _run_case(
        self,
        scoped_drive: ScopedDrive,
        case: EvaluationCase,
        iteration: int,
        read_max_chars: int,
    ) -> BenchmarkSample:
        service = DocumentService(scoped_drive)
        end_to_end_started = self.clock()
        search_started = self.clock()
        results = await service.search_documents(case.search_query, limit=case.limit)
        search_ms = _elapsed_ms(search_started, self.clock())
        source = _find_source(results.items, case.expected_source)
        if source is None:
            return BenchmarkSample(
                iteration=iteration,
                source_found=False,
                result_count=len(results.items),
                search_ms=search_ms,
                cold_read_ms=None,
                warm_read_ms=None,
                end_to_end_ms=None,
                content_chars=None,
            )

        cold_started = self.clock()
        content = await service.read_document(source.id, max_chars=read_max_chars)
        cold_read_ms = _elapsed_ms(cold_started, self.clock())
        end_to_end_ms = _elapsed_ms(end_to_end_started, self.clock())

        warm_started = self.clock()
        await service.read_document(source.id, max_chars=read_max_chars)
        warm_read_ms = _elapsed_ms(warm_started, self.clock())
        return BenchmarkSample(
            iteration=iteration,
            source_found=True,
            result_count=len(results.items),
            search_ms=search_ms,
            cold_read_ms=cold_read_ms,
            warm_read_ms=warm_read_ms,
            end_to_end_ms=end_to_end_ms,
            content_chars=len(content.content),
        )


def compare_benchmarks(
    current: BenchmarkReport,
    baseline: BenchmarkReport,
    *,
    max_regression_percent: float,
) -> list[BenchmarkRegression]:
    """Return median latency regressions above the allowed percentage."""

    if not math.isfinite(max_regression_percent) or max_regression_percent < 0:
        # nan compares false with every regression and inf is never exceeded:
        # either one leaves the gate open while reporting that it held.
        raise ValueError("max_regression_percent must be finite and not negative")
    if current.schema_version != baseline.schema_version:
        raise ValueError("Benchmark reports use different schema versions")
    if (
        current.drive_kind != baseline.drive_kind
        or current.corpus_fingerprint != baseline.corpus_fingerprint
    ):
        raise ValueError("Benchmark reports describe different Drive corpora")
    # Both are recorded because they change what the numbers mean; a baseline that
    # differs in either would produce a regression, or hide one, out of nothing.
    if current.read_max_chars != baseline.read_max_chars:
        raise ValueError("Benchmark reports were measured with different read_max_chars")
    if current.folder_map_ttl_seconds != baseline.folder_map_ttl_seconds:
        raise ValueError("Benchmark reports were measured with different folder_map_ttl_seconds")
    baseline_by_case = {_case_key(case): case for case in baseline.cases}
    # A case on one side only is a mismatch, not a skip. Comparing the overlap
    # would let an added or renamed case pass the gate without being measured
    # against anything, and no overlap at all would pass it vacuously.
    unmatched = {_case_key(case) for case in current.cases} ^ set(baseline_by_case)
    if unmatched:
        queries = ", ".join(sorted(search_query for _, search_query, _ in unmatched))
        raise ValueError(
            f"Benchmark reports measure different cases ({queries}); record a new baseline"
        )
    regressions: list[BenchmarkRegression] = []
    for current_case in current.cases:
        baseline_case = baseline_by_case[_case_key(current_case)]
        metrics = (
            ("search", current_case.search, baseline_case.search),
            ("cold_read", current_case.cold_read, baseline_case.cold_read),
            ("warm_read", current_case.warm_read, baseline_case.warm_read),
            ("end_to_end", current_case.end_to_end, baseline_case.end_to_end),
        )
        for metric, current_stats, baseline_stats in metrics:
            current_median = current_stats.median_ms
            baseline_median = baseline_stats.median_ms
            if current_median is None or baseline_median is None or baseline_median <= 0:
                continue
            regression_percent = (current_median / baseline_median - 1) * 100
            if regression_percent > max_regression_percent:
                regressions.append(
                    BenchmarkRegression(
                        search_query=current_case.search_query,
                        metric=metric,
                        baseline_median_ms=baseline_median,
                        current_median_ms=current_median,
                        regression_percent=regression_percent,
                    )
                )
    return regressions


def _case_key(case: CaseBenchmark) -> tuple[str, str, str]:
    return (case.question, case.search_query, case.expected_source)


def save_benchmark_report(report: BenchmarkReport, path: Path) -> None:
    """Write one portable JSON benchmark artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{report.model_dump_json(indent=2)}\n", encoding="utf-8")


def load_benchmark_report(path: Path) -> BenchmarkReport:
    """Load and validate one JSON benchmark artifact."""

    return BenchmarkReport.model_validate_json(path.read_text(encoding="utf-8"))


def _find_source(results: list[ScopedItem], expected_source: str) -> ScopedItem | None:
    expected = expected_source.casefold()
    return next(
        (item for item in results if expected in item.relative_path.casefold()),
        None,
    )


def _validate_options(
    cases: list[EvaluationCase],
    iterations: int,
    warmup_iterations: int,
    read_max_chars: int,
) -> None:
    if not cases:
        raise ValueError("Benchmark requires at least one evaluation case")
    if not 1 <= iterations <= 50:
        raise ValueError("Benchmark iterations must be between 1 and 50")
    if not 0 <= warmup_iterations <= 10:
        raise ValueError("Benchmark warmup iterations must be between 0 and 10")
    if not 1 <= read_max_chars <= MAX_READ_CHARS:
        raise ValueError(f"read_max_chars must be between 1 and {MAX_READ_CHARS}")


def _summarize_case(case: EvaluationCase, samples: list[BenchmarkSample]) -> CaseBenchmark:
    return CaseBenchmark(
        question=case.question,
        search_query=case.search_query,
        expected_source=case.expected_source,
        passed=all(sample.source_found for sample in samples),
        samples=samples,
        search=_latency_stats([sample.search_ms for sample in samples]),
        cold_read=_latency_stats(
            [sample.cold_read_ms for sample in samples if sample.cold_read_ms is not None]
        ),
        warm_read=_latency_stats(
            [sample.warm_read_ms for sample in samples if sample.warm_read_ms is not None]
        ),
        end_to_end=_latency_stats(
            [sample.end_to_end_ms for sample in samples if sample.end_to_end_ms is not None]
        ),
    )


def _latency_stats(samples: list[float]) -> LatencyStats:
    if not samples:
        return LatencyStats(
            sample_count=0,
            min_ms=None,
            median_ms=None,
            p95_ms=None,
            max_ms=None,
        )
    return LatencyStats(
        sample_count=len(samples),
        min_ms=min(samples),
        median_ms=median(samples),
        p95_ms=_percentile(samples, 0.95),
        max_ms=max(samples),
    )


def _summarize_drive_calls(calls: list[_DriveCall]) -> dict[str, DriveOperationStats]:
    durations_by_operation: dict[str, list[float]] = {}
    for call in calls:
        durations_by_operation.setdefault(call.operation, []).append(call.duration_ms)
    return {
        operation: DriveOperationStats(
            count=len(durations),
            total_ms=sum(durations),
            median_ms=median(durations),
            p95_ms=_percentile(durations, 0.95),
        )
        for operation, durations in sorted(durations_by_operation.items())
    }


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _elapsed_ms(started_ns: int, finished_ns: int) -> float:
    return (finished_ns - started_ns) / 1_000_000
