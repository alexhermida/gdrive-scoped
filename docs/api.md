# Service contract

`DocumentService` is the library's application surface: four read-only operations over one
Authorized Subtree. Every item it returns, and every byte it reads, is proven to be inside
that subtree at the moment of the call (ADR 0006), with one bounded exception: a folder moved
out of the subtree keeps serving its files for up to the folder-map TTL, 60 seconds by default
(ADR 0005). Items are addressed by their Drive `file_id`; an ID from outside is refused every
time, not merely unresolvable.

An adapter maps these onto whatever its wire calls a tool — `examples/mcp_server.py` does it
in about forty lines.

## `search_documents(query, limit=10)`

- `query`: a focused keyword or short phrase. Raw Drive query syntax is never accepted.
- `limit`: 1–50.

Returns `SearchResult(items, truncated, incomplete)`. Recursive, lexical and deduplicated, in
Drive's relevance order; when the corpus needs more than one folder-query batch, the batches
are merged by rank position. Each batch is asked for one hit more than `limit`, which is all
`truncated` needs. `truncated` says more matched than `limit` allowed through; `incomplete` says
the search could not see the whole corpus.

## `list_folder(folder_id=None, cursor=None, limit=100)`

- `folder_id`: a folder in the subtree; omit for the Configured Root Folder.
- `cursor`: from the preceding page.
- `limit`: 1–200.

Returns `FolderPage(items, next_cursor)`. Direct children only. Shortcuts and trash are absent.

## `get_metadata(file_id)`

Revalidates current ancestry and returns a `ScopedItem`: current name, MIME type, relative
path, modification time, size, the Drive web link, the owners, and who last modified it. Drive
leaves `owners` empty for items in a Shared Drive, so `last_modified_by` is the person field that
is always there.

## `read_document(file_id, cursor=None, max_chars=25_000)`

- `file_id`: a non-folder item in the subtree.
- `cursor`: from the preceding read.
- `max_chars`: 1–40,000.

Returns `ReadResult` — normalized content, source metadata, a structural `location`,
`total_chars`, `next_cursor`, and on a partial read a `note` saying so in words. Page, slide,
sheet, section and table boundaries are preferred as cut points. A chunk is additionally
capped at 64,000 UTF-8 bytes after the character cut, because a transport counts bytes while
`max_chars` counts characters.

Parsed content is cached by file ID and Drive modification time — but never the authorization:
current ancestry is checked again before every chunk.

## Audit

Two kinds of record, both on the `gdrive_scoped.audit` logger and both structured through
`extra`, so a deployment can route them somewhere durable.

- **Decisions**, from Scoped Drive: one per allow or refuse, with `decision`, `reason` and
  `file_id`. Refusals are WARNING, and in normal use there are none.
- **Operations**, from the service: one per completed call, with `event` (`search`,
  `list_folder`, `get_metadata`, `read_document`) and what it touched: the query and the
  returned `file_ids`, the folder listed, or the chunk read as `start`, `chars` and `partial`.

Every record carries `caller`. The library authenticates nobody, so an adapter that does sets
`gdrive_scoped.audit_caller` for the span of a request. It is a `ContextVar`, which under asyncio
follows the task; unset, `caller` is `None`.

Records contain search queries, file IDs and the caller. That is what makes them the answer to
"what did the agent read, and for whom", and it also makes them sensitive: route the
`gdrive_scoped.audit` logger as you would any log that names people and documents.

## Failures

Every deliberate failure is one of the `gdrive_scoped.errors` hierarchy, all under
`DriveError`, and nothing inherits from a builtin. They cover items outside the Authorized
Subtree, items with no provable parent inside it, shortcuts, trash, download restrictions,
unsupported content types, invalid cursors or limits, export limits, rejected credentials and
upstream Drive errors.

Nothing ever falls back to a broader query or a broader credential scope. An adapter decides
what each error becomes on its own wire; catching `DriveError` covers leaves added later.
