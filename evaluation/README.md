# Retrieval evaluation

The evaluation answers the PoC question without introducing an index or an LLM judge.

Each case contains:

- `question`: the representative user question, retained for human context.
- `search_query`: the focused keyword query an agent should issue.
- `expected_source`: a case-insensitive substring of the expected relative Drive path.
- `limit`: optional search result limit from 1 to 50; defaults to 10.

Copy `questions.example.json` to the gitignored `questions.local.json` and replace the placeholders with 5–10 real questions from the configured corpus. A case passes when the expected source occurs in a returned path.

This measures source discovery only. Review whether an agent can answer after reading the source separately.

## Performance benchmark

The live benchmark reuses these cases. For each case and measured iteration it:

1. Recursively discovers folders and runs the configured Drive keyword search.
2. Finds the expected relative source path.
3. Creates a fresh Document Service and performs a cold bounded read, including download/export and extraction.
4. Immediately repeats the read to measure the in-process parsed-document cache while preserving live ancestry validation.

The JSON report includes raw samples plus min, median, p95, and max latency for search, cold read, warm read, and search-to-cold-read end-to-end retrieval. It also includes measured Google Drive gateway call counts and timing by operation. Warmup and startup calls are excluded from those operation totals.

Run a reusable baseline with:

```bash
BENCHMARK_ITERATIONS=10 \
BENCHMARK_OUTPUT=_tmp/benchmark-baseline.json \
just benchmark
```

After a change, create and compare a new report:

```bash
uv run python -m gdrive_scoped.bench.benchmark_cli evaluation/questions.local.json \
  --output _tmp/benchmark-current.json \
  --iterations 10 \
  --warmup-iterations 1 \
  --baseline _tmp/benchmark-baseline.json \
  --max-regression-percent 20
```

The command exits nonzero if any expected source is missing or a matching case's median search, cold-read, warm-read, or end-to-end latency regresses beyond the threshold. Compare reports from the same folder, account, machine, and similar network conditions.
