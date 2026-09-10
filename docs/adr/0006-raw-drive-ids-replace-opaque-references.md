# ADR 0006: Raw Drive IDs replace opaque Document References

- Status: Accepted
- Date: 2026-09-09
- Supersedes: the Document Reference half of [ADR 0001](./0001-application-enforced-folder-boundary.md)

## Context

ADR 0001 decided that tools "do not accept Drive IDs" and that Document
References are opaque. `InMemoryReferenceStore` issued a `gdr_`-prefixed token
per item and resolved it back on the next call.

Two things have changed.

The library is being embedded in a horizontally scaled hosted deployment
rather than only run as a local stdio process. The reference store is process
memory. An instance restarting or scaling out invalidates every reference it
ever issued, so a handle a model was given one minute fails the next, for a
reason the model cannot see and cannot act on. The
failure mode is worse than the exposure it prevented: an unrecoverable "unknown
document reference" is indistinguishable, from the caller's side, from a
genuine refusal.

The exposure it prevented was also smaller than it looked. Every result carries
a `webViewLink`, and a Drive web link contains the file ID in plain sight. A
caller who received one search result already had that item's raw ID.

## Decision

Every public method on `ScopedDrive` and `DocumentService` takes a raw Drive
ID. `references.py` is deleted, `ScopedItem.reference` becomes `ScopedItem.id`.

The boundary is unchanged, and it was never the shape of the handle. It is the
live proof in `_authorize`: the item's own metadata is read from Drive on every
call, and its parent chain is checked against the Configured Root Folder. A
move, a trashing, a shortcut conversion, a location change or a lost permission
is refused immediately; only the chain *above* the item comes from the folder
map, which is the bounded staleness of [ADR 0005](./0005-bounded-folder-map-staleness.md).

## Consequences

- A raw Drive ID copied from a link is now a usable handle for anyone holding
  the tool — but only if it is inside the Authorized Subtree, and the access
  model already granted that: they could have found the same item by searching.
- An ID from *outside* the subtree is refused exactly as before, and a file
  shared directly with the Drive Identity has no parent inside the subtree, so
  its ancestry cannot be proven and it is refused. Both are now tests.
- Handles survive a restart, a redeploy and a scale-out event, which is what
  makes the library usable from a horizontally scaled server at all.
- `get_metadata` becomes useful in its own right: a user pastes a Drive link
  and the model can ask "is this in the corpus, and what is it" without paying
  for a read.
- `test_mcp_tool_rejects_a_copied_raw_drive_id` is deleted. It pinned the
  reference store's behaviour, not the boundary's, and keeping a renamed
  version would assert something this ADR decides is not true.
