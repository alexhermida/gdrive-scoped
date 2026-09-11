# Architecture

## Purpose

The library answers one question: is Google Drive's native keyword retrieval over a restricted folder subtree useful enough to provide agent context without building an index?

One deployment exposes one Configured Root Folder in one Drive Location: My Drive or one Shared
Drive. The core is a library; an adapter puts it on a wire — an MCP server, a hosted provider, an
agent — and the core imports none of them, reads no environment variable, and takes its credentials
as an argument. `examples/mcp_server.py` is the smallest complete adapter.

## Component model

```text
adapters:   an MCP server   |   a hosted provider   |   an agent
                              \              |              /
                               v             v             v
                         Document service ---------> Extractor registry
                                  |
                                  v
                             Scoped Drive          <- the boundary
                                  |
                                  v
                         Google Drive gateway
                                  |
                                  v
                            Google Drive API
```

Dependencies point inward. A transport and Google are both adapters. An adapter contains no folder-authorization or extraction logic. The Google Drive gateway is the only module that knows the external API's request shapes, and the only one that maps its failures onto `gdrive_scoped.errors`.

## Deep boundaries

### Google Drive gateway

Offers only the read operations the application needs: fetch metadata, list direct children, enumerate every folder the identity can see, search within named parent folders for a requested number of hits, download blob content, and export Google Workspace content. It exposes no generic request method and no write operation.

Every list query uses the `user` corpus. A Shared Drive location adds `includeItemsFromAllDrives`; a My Drive location excludes Shared Drive items. No query addresses a drive by `driveId`: that requires the Drive Identity to be a *member* of the drive, and being granted the root folder is all it needs (ADR 0010). The configured Shared Drive ID is asserted on every item instead.

### Scoped Drive

Owns the security boundary. It validates the Configured Root Folder, discovers descendant folders for recursive search, and verifies current ancestry before returning metadata or bytes. No caller can bypass it to reach the gateway. Every decision it makes — allow or refuse — is recorded on the `gdrive_scoped.audit` logger with structured `decision`, `reason` and `file_id`.

Descendant folders are enumerated with one `mimeType = folder` query over everything the Drive Identity can see, filtered to the configured location, and reused for `GDRIVE_FOLDER_MAP_TTL_SECONDS` (default 60). The item under authorization is always read live; only the chain above it comes from that map. ADR 0005 records what the window exposes and what it cannot.

### Document service

Provides the four user-facing capabilities: recursive keyword search, bounded direct-folder pages, metadata retrieval, and bounded content reading. It returns structured application results, independent of any transport. Every completed operation is one record on the `gdrive_scoped.audit` logger with `event`, `caller` and the identifiers involved; an adapter that knows who is asking sets `audit_caller` for the span of the request.

### Extractor registry

Converts authorized bytes into normalized blocks with natural locations such as pages, slides, sheets, row ranges, headings, and paragraphs. Chunking is applied to normalized blocks rather than raw bytes.

Parsed documents are cached in process by file ID, MIME type, and Drive modification time. The cache holds bytes, never a decision: every cached read still performs a current ancestry check before serving content.

## Authorization flow

### Discovery

1. Enumerate every folder the Drive Identity can see with one paginated query, keep those in the configured Drive Location, then rebuild the subtree from `parents`. A folder with anything other than exactly one parent is not descended into, and neither is anything beneath it.
2. Search using server-generated, escaped Drive queries constrained to those parent folder IDs.
3. Require every candidate to belong to the configured Drive Location.
4. Exclude trash and shortcuts.
5. Deduplicate results.

Search queries containing more than 400 folder parents are split into batches. Results are merged by rank position, the first hit of every batch before the second of any, and then cut to the requested limit. A corpus that fits one batch keeps Drive's relevance order untouched. No re-sort is applied: relevance is the only ranking signal a keyword search has, and re-sorting by modification time was measured to bury the answers. Each batch asks Drive for one hit more than the limit, with the page sized to match; `truncated` comes from that extra hit or from Drive's continuation token, never from counting the whole match set.

### Read or metadata

1. Fetch current item metadata from Drive. This read is never served from a cache.
2. Verify the configured Drive Location: the exact Shared Drive ID, or the absence of a Shared Drive ID for My Drive, and reject trashed items and shortcuts.
3. Require exactly one parent, and require that parent to be a folder in the corpus map.
4. Reject the operation if ancestry cannot be proven.
5. Only then fetch or export content, without proving ancestry a second time.

This makes a file ID unusable as soon as its item moves outside the Authorized Subtree, is trashed, or loses its permissions - the item is re-read from Drive on every call. A folder re-parenting is the one change the live read cannot see, and is bounded by the folder map TTL (ADR 0005).

## Authentication boundaries

Two authentications, and they never meet. The **Drive Identity** is how this library reaches
Google: one `drive.readonly` credential passed in as an argument — a developer's Application
Default Credentials locally, a bot user's stored refresh token in a deployment. That credential can
usually see far more of Drive than the corpus; Scoped Drive is what restricts output to the
Authorized Subtree, and it is the only thing that does.

Authenticating **callers** is an adapter's job, not the library's, and a caller's token is never
passed to Google.

Nothing here holds process state a caller can depend on: file IDs are Drive's own and survive a
restart, which is what makes the core usable from a horizontally scaled server (ADR 0006). The
parsed-document cache is in memory, so a restart costs re-parsing and nothing else.

## Future seams

- Replace the folder map TTL with `changes.list`, so the map is current rather than recently fresh (ADR 0005).
