# Testing strategy

## Principles

- Test observable behavior through the public document service.
- Fake Google Drive only at the external gateway boundary.
- Build vertical slices: one failing test, the smallest passing behavior, then refactor while green.
- Live Drive tests are optional and never required for the default test suite.

## Test layers

### Unit and application tests

Cover environment configuration, Drive Location request policy, cross-location rejection, query escaping, traversal, pagination, deduplication, current ancestry, shortcut rejection, raw-ID authorization, extractors, and read cursors.

### Boundary tests

The folder boundary is the product, so it is asserted directly rather than inferred from the tests above. Two things follow.

**Search is tested as a disclosure surface, not only as retrieval.** Returning a name, a relative path or a Document Reference for an item outside the Authorized Subtree is a boundary failure even when the read that follows would be refused, so `test_search_stops_returning_files_from_a_folder_that_left_the_subtree` and `test_search_never_names_a_file_outside_the_subtree` assert what search may not name.

**The staleness window is stated as tests, in both directions.** What the folder map cannot hide is pinned by `test_a_file_that_moves_out_is_refused_even_while_the_folder_map_is_warm` and `test_a_trashed_file_is_refused_even_while_the_folder_map_is_warm`; what it does hide is pinned by `test_a_folder_that_leaves_the_subtree_keeps_serving_until_the_map_expires`. A test that records an exposure is worth as much as one that records a guarantee: it fails if the exposure ever widens.

Both were checked by mutation rather than by passing alone - relaxing the single-parent rule and caching the item's own metadata each fail the intended test and only that test.

### Public API tests

`tests/test_public_api.py` holds the seam the layout exists for: importing `gdrive_scoped`
must pull in neither the MCP SDK nor `gdrive_scoped.env`. Both run in a subprocess, because a
test session that has already imported either would prove nothing in-process.

### Optional live tests

Use an explicitly configured Drive Location and Configured Root Folder. Live tests are marked and skipped unless their environment variables are present. They perform read-only operations only.

The base live test validates and lists the configured root. Set `GDRIVE_LIVE_SEARCH_QUERY` and `GDRIVE_LIVE_EXPECTED_SOURCE` to additionally search for an exact relative path, revalidate its metadata, and perform one bounded content read.

### Retrieval evaluation

Maintain 5–10 representative questions for the chosen corpus. Record whether search surfaces a useful source and whether the agent can answer after bounded reads. It is an evaluation, not a deterministic CI gate.

### Live retrieval benchmark

Reuse the retrieval evaluation cases to measure initialization, search, cold document reads, warm cached reads, and end-to-end retrieval. Instrument only the Google Drive gateway boundary so the report separates Drive operation counts and latency from application and extraction work.

The benchmark is opt-in, writes JSON beneath ignored `_tmp/` storage by default, and never runs under `just check`. Warmups are excluded from measured samples. A missing expected source fails the run rather than measuring an unrelated result. Baseline comparisons use median latency because live network measurements are noisy; use at least 10 iterations when treating p95 values as meaningful.
