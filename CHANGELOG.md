# Changelog

## Unreleased

### The Drive location is measured, not configured — **breaking**

- The Drive Location is read from the Configured Root Folder's own `driveId` instead of being
  configured beside it and asserted against it. Drive already records where the folder lives:
  absent `driveId` is My Drive, present names the Shared Drive. An operator had to restate
  that, and a wrong restatement described a corpus that did not exist. ADR 0011 has the
  decision, including what happens when the root moves.
- `create_gateway(credentials)`, `GoogleDriveGateway(service=..., credentials=...)` and
  `ScopedDrive(gateway=..., root_folder_id=...)` no longer take a `location`. `DriveKind` and
  `DriveLocation` stay exported — consumers still read the measurement — and
  `DriveLocation.of(item_drive_id)` builds one from Drive metadata.
- `ScopedDrive.location` is now a read-only property holding the measurement. Reading it
  before `await initialize()` raises `RuntimeError`, and so does every other call that checks
  containment: **`initialize()` is now mandatory before a scope serves anything**, where it
  used to be an optional up-front validation.
- `initialize()` no longer raises `ScopeViolation("Configured root does not belong to the
  configured Drive location")`. That check compared the root against an assertion about the
  root and could not fail meaningfully. The other three root checks — identity, folder-ness,
  trashed — are unchanged, and the measured location is still asserted on every item,
  including on the root itself: `_validated_root` re-reads it on every request, so a root that
  later moves to another drive is refused rather than re-measured.
- `GDRIVE_DRIVE_KIND` and `GDRIVE_SHARED_DRIVE_ID` are gone, and `Settings` has no `location`.
  `GDRIVE_ROOT_FOLDER_ID` is now the whole description of a corpus and remains required. A
  leftover pair in an existing `.env` is ignored rather than honoured or rejected.
- `includeItemsFromAllDrives` is sent on every `files.list`, because the gateway no longer
  knows the location. It cannot widen the corpus — `DriveLocation.contains` asserts the
  measured drive on every item — but a My Drive deployment now carries the identity's Shared
  Drive rows through enumeration before discarding them, against a page budget bounded by that
  identity's reach (ADR 0010). Keep the grant to the corpus.

### Also

- `ScopedDrive` refuses the alias `root` as its root folder, as the environment wrapper
  already did. Drive resolves the alias to the whole of a My Drive and the folder map was then
  keyed on the real ID, so every listing, search and read succeeded against the entire Drive;
  only the optional `initialize()` noticed. The root read now also refuses an answer whose ID
  is not the one asked for, which closes any other alias the same way.
- `ScopedDrive` requires a finite, non-negative folder-map TTL. Constructed directly with
  `inf`, the map never refreshed.
- `download` accepts only the very `AuthorizedItem` instances this scope issued. The proof is
  a plain dataclass, so nothing stopped a caller building one around any `DriveItem`; equality
  is now identity and the scope keeps a weak set of what it issued, so a proof cannot be
  forged, copied or carried to another root.
- `compare_benchmarks` and `--max-regression-percent` require a finite, non-negative
  threshold. `nan` compared false with every regression and `inf` was never exceeded, so the
  gate reported holding without ever closing.
- `compare_benchmarks` refuses reports that measure different cases instead of comparing the
  overlap. An added or renamed case used to pass the gate unmeasured, and no overlap at all
  passed it vacuously.
- `read_document` decodes the cursor after the proof and before the fetch. A malformed cursor
  used to be rejected only once the document had been downloaded and parsed.
- A parser that fails on the bytes Drive returned raises `ExtractionFailed`, a `DriveError`,
  instead of whatever the parser threw. `docs/api.md` now also says which errors are the
  library's and which are the caller's: arguments out of range raise builtin `ValueError`.
- Derived benchmark cases are read once before they are kept. "Readable" meant an extractor
  claimed the MIME type; a scan with no text or a file over the export cap was chosen for its
  size and aborted the benchmark at its first read. The next largest of the type is tried.
- Benchmark reports record each case's search `limit`, and comparison treats a different
  limit as a different case, because it is a different Drive request.

## v0.7.1 — four edges closed

- `GDRIVE_FOLDER_MAP_TTL_SECONDS` must be finite. `float()` accepted `inf` and `nan`, and an
  infinite window never refreshed the folder map: a folder moved out of the corpus would have
  kept serving its contents for the life of the process.
- The evaluation script prints `identity: <address>` first, as the census and the benchmark
  already did and the README already promised of every entry point.
- `compare_benchmarks` refuses a baseline measured with a different `read_max_chars` or
  `folder_map_ttl_seconds`, as it already refused a different corpus. The report recorded
  both because they change what the numbers mean, and then compared without checking them.
- A negative folder cursor is rejected as invalid. It used to pass the bounds check and page
  from the end of the listing.

## v0.7.0 — a folder grant is enough

- Shared Drive queries use `corpora=user` with `includeItemsFromAllDrives`, and never a
  `driveId`. `corpora=drive` addresses the drive itself and Drive refuses it — 403
  `teamDriveMembershipRequired` — for an identity granted a folder inside the drive rather
  than membership of it, with and without parent filters. The `user` corpus returned identical
  results for a drive member and for that grantee, folder enumeration and search alike, so
  membership of a Shared Drive is now optional and the configured Shared Drive ID is asserted
  on every item rather than sent. Discovery still enumerates in bulk; its cost is the
  identity's reach, one page per thousand folders. ADR 0010 has the measurements, and the
  root-first traversal that was built, measured at six times the cost, and set aside.
- The benchmark's `initialization` includes the cold folder enumeration, which is what it
  always claimed to measure; it used to time the root read alone.
- `GoogleDriveGateway.identity()` returns the address the credentials belong to, and the
  census and the benchmark print it first. A credential carries no name, and a run as the
  developer instead of the bot user enumerates a different reach — measured at 9,878 folders
  in 10 pages against the bot user's 123 in one — and describes a deployment that does not
  exist.
- `EnumerationBudgetExceeded` says which of its two causes happened: the page budget ran out,
  or Drive itself reported the enumeration incomplete (`incompleteSearch`, usually transient
  under the `user` corpus and worth one retry). It used to report both as the budget, which
  sent a reader hunting for a reach that was not there. `SearchPage` gains
  `page_budget_exhausted`.
- `.env.example` lists every variable the entry points read, with its constraints, and `just`
  loads `.env` on its own. An entry point run directly with `uv run` still needs them
  exported.
- The benchmark derives its own cases when the cases file does not exist, and writes them
  there: the largest readable document of each MIME type, each verified to come back from a
  search for a keyword taken from its own name. A first run against an unfamiliar corpus now
  measures that corpus instead of failing on paths from somebody else's, and the second run
  measures the same documents, which is what `--baseline` needs to compare anything.
  `--case-count` sets how many (1-10, default 6).
- The benchmark summary prints a per-case table with the extracted length, and the per-Drive-
  operation medians and p95s the report has always carried. A regression in one operation was
  invisible in the four aggregate numbers.
- `--max-regression-percent` defaults to 40 rather than 25. Everything measured is dominated by
  Drive round trips that vary by tens of percent run to run, so the gate is for catching a
  doubling, not a drift; 25 fired on noise.

## v0.6.0 — search in Drive's order, at the request's size

- Published under the MIT licence. Source distributions are built from an explicit allowlist
  and `just package` refuses one that carries anything else.
- Search keeps Drive's relevance order instead of re-sorting by modification time. Parent
  batches are merged by rank position, so a corpus that fits one batch gets Drive's order
  untouched. Measured on a 320-file corpus: the expected documents for four questions sat at
  Drive ranks 1, 5, 1 and 12 and the recency sort had pushed them to 8, 93, 115 and 97, dropping
  three of the four out of a 10-result page.
- Search asks Drive for one hit more than `limit` per batch, with the page sized to match, instead
  of paging through up to five thousand-row pages and counting. `truncated` is unchanged as a
  boolean. Measured on the same corpus: 15–35% less latency per request and a response an order
  of magnitude smaller for frequent terms.
- **Breaking:** `DriveGateway.search_items` takes a `limit`, and `SearchPage` gains `has_more`.
  Any external implementation of the gateway protocol needs both.
- `ScopedItem` and `DriveItem` carry `owners` and `last_modified_by`. Drive leaves `owners` empty
  for Shared Drive items, which is why the last modifying user is requested alongside.
- Any `text/*` type passes through as text even when nobody listed it, and so do YAML and
  NDJSON under their `application/` names. Dedicated extractors such as HTML still win.
- One audit record per completed operation, from `DocumentService`, alongside the existing
  decision records: `event`, the identifiers touched, and `caller`. Adapters that authenticate
  set `gdrive_scoped.audit_caller` for the span of a request; decision records carry it too.
- ADRs 0007, 0008 and 0009 record the search ordering, the request-sized pages and the
  cross-batch merge, with the measurements behind each.

## v0.5.0 — a library, and only a library

- Removed the in-repository MCP server. The library imports no MCP in any configuration, and
  the SDK is no longer an extra — `examples/mcp_server.py` is an adapter you can read and run,
  not a shipped product.
- Configuration moved to `gdrive_scoped.env`, for this repository's entry points only, and the
  variables are now `GDRIVE_*` rather than `GDRIVE_MCP_*` — the names a deployment already
  sets, so one environment file serves both. `GDRIVE_DRIVE_KIND` has no default: the wrong one
  returns an empty corpus rather than an error.
- `credentials_from_environ()` uses a configured refresh token, or Application Default
  Credentials when none is set. A partial OAuth triple is an error rather than a silent
  fall back to a different identity.
- Added `scripts/census.py`; the benchmark runs as `python -m gdrive_scoped.bench.benchmark_cli`.
  Neither goes through an MCP server any more.

## v0.4.0 — the types a census found

- `text/tab-separated-values`, `text/xml` and `application/xml` pass through as text, and a
  stdlib `html.parser` reduction handles `text/html`. Chosen from a census of a real corpus
  rather than guessed at: those were exactly its unreadable share, and adding them took it from
  97% to 99.2% readable.

## v0.3.0 — read hardening

- Capped a chunk at 64,000 UTF-8 bytes after the character cut, and lowered `max_chars` to
  40,000. A streaming transport counts bytes while `max_chars` counts characters, and CJK text
  is three bytes each.
- Partial reads say so: `ReadResult.total_chars` plus a `note` naming the span and how to
  continue.
- Bounded the parsed-document cache (LRU, 32), added a 25 MB download guard, and reported an
  extraction yielding only whitespace as `EmptyDocument` — with a different message for a
  scanned PDF than for a format that is never scanned.

## v0.2.0 — library, boundary on raw IDs, client hardening

- Restructured as a library: the core imports no MCP, reads no environment variable, takes
  credentials as an argument, and ships `py.typed`. Dropped the undeclared anyio and pydantic
  dependencies it had been inheriting.
- Added the `DriveError` hierarchy and explicit credential builders; `create_gateway(credentials,
  location)` replaced ADC discovery inside the gateway.
- Hardened the gateway for concurrent hosting: every `HttpError` mapped, `num_retries`, one
  `httplib2.Http` per worker thread, page budgets on search and enumeration, `incompleteSearch`
  surfaced, and `list_descendants`.
- Replaced opaque Document References with raw Drive IDs (ADR 0006), audited every authorization
  decision, and coalesced concurrent folder-map misses behind a lock.
- Added `census` — folder count and depth, file count, MIME histogram, readable share.
