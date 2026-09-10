# ADR 0005: Resolve ancestry from a folder map with a bounded staleness window

- Status: Accepted
- Date: 2026-09-01

## Context

Discovery used to walk the Authorized Subtree one folder at a time, because Drive has no
descendant operator. Measured live: **61 `list_children` calls at a median of 250 ms, 15.3 s
of a 16.8 s search**, repeated on every search. Reads then re-proved ancestry with one
`get_item` per level, twice per read.

Two changes remove nearly all of that:

1. One `mimeType = folder` query returns every folder in the Drive location. The subtree is
   arithmetic on `parents` after that, so enumeration costs one paginated query instead of
   one call per folder.
2. The resulting map answers the ancestry question, so a read costs one Drive call instead of
   one per level.

The second change is not free. Reusing a map across requests means Drive state read at T can
authorize a request served at T+N. The alternative considered was to keep enumerating per
request, which costs about 2.2 s per question against this corpus - the price of never
reusing a map.

## Decision

Reuse the folder map for `folder_map_ttl_seconds`, default **60 seconds**, configured by
`GDRIVE_FOLDER_MAP_TTL_SECONDS`.

Three properties bound what that window can expose:

- **The item's own metadata is always read live.** `_authorize` starts with a `get_item` on
  the item itself and validates it before consulting the map. Only the chain *above* the item
  comes from the map.
- **A map miss is retried against a fresh map.** A folder created after the map was built is a
  false denial, not a breach; refreshing can only turn a deny into an allow that a live map
  would also have granted.
- **`0` is honoured** and restores per-request enumeration exactly.

## Consequences

**What is still caught immediately**, whatever the map says, because the item is read live:
a file moved out of the subtree, a file moved to another Drive location, a trashed file, a
file replaced by a shortcut, and a file whose permissions were revoked (Drive answers 404).

**What the window exposes**, and the only thing it exposes: a **folder** moved out of the
Authorized Subtree keeps serving the files beneath it for up to the TTL - in search results,
in metadata, and in content. The files themselves did not move, so reading them live cannot
detect it.

`tests/test_scoped_drive.py` holds this as three executable statements:
`test_a_file_that_moves_out_is_refused_even_while_the_folder_map_is_warm`,
`test_a_trashed_file_is_refused_even_while_the_folder_map_is_warm`, and
`test_a_folder_that_leaves_the_subtree_keeps_serving_until_the_map_expires`.

**The window is narrower than it looks even at TTL=0**, because the old per-request walk was
not atomic either: 61 serial calls spanning 15 s produced a map whose first entry was 15
seconds older than its last. One query closes that to about a second. "Read within this
request" was always the real property; the TTL names it in seconds instead of leaving it
implicit.

**If the window ever needs to go**, the answer is probably not a shorter TTL but
`changes.list`: apply Drive's change feed incrementally so the map is *current* rather than
*recently fresh*. That removes the window instead of documenting it, at the cost of change-token
bookkeeping.
