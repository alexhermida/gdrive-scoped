# ADR 0010: Never address the drive; enumerate what the identity can see

- Status: Accepted; the configured Drive location superseded by ADR 0011
- Date: 2026-09-11
- Amends: ADR 0004 (the Shared Drive request policy)

## Context

For a Shared Drive, every `files.list` was sent as `corpora=drive` with the drive's
`driveId` — the shape Google documents for searching a shared drive (ADR 0004). That shape
asks Drive for the drive itself, and Drive grants it only to a *member* of the drive.

The least-privilege grant a deployment wants is the Configured Root Folder and nothing else:
the identity's reachable set *is* the corpus. Measured live against exactly that grant,
`corpora=drive` fails with **403 `teamDriveMembershipRequired`** — and not only for the
location-wide folder enumeration. Every combination was measured, as the folder-only identity
and as a drive member:

| filter | `corpora=drive` + `driveId` | `corpora=user` + `includeItemsFromAllDrives` |
| --- | --- | --- |
| none (the folder enumeration) | grantee **403**; member 453 | grantee 123, of which 59 in the drive; member 1,000+ |
| `'<root>' in parents` | both 8 | both 8 |
| 59 corpus folders in parents + `fullText` (a search) | grantee **403**; member 15 | both 15 |

Three facts fall out. `corpora=drive` is refused for the grantee, and not consistently — a
single-parent filter happened to pass — so it cannot be kept as "works unless". The `user`
corpus with `includeItemsFromAllDrives` answers every query identically for a member and for
the grantee: the member's 453 folders are the same 453 that `corpora=drive` returns, and the
grantee's 59 in-drive folders are the corpus, all six levels of it. And the `user` corpus
returns everything the identity can see, so its enumeration is bounded by the identity's
*reach*, not by the drive.

## Decision

**No query addresses a drive.** Every `files.list` uses the `user` corpus; a Shared Drive
location adds `includeItemsFromAllDrives`, a My Drive location excludes Shared Drive items.
`driveId` is never sent. The configured Shared Drive ID keeps its job as an assertion —
`DriveLocation.contains` checks it on every item enumerated, listed, searched or read — and
loses its job as a query parameter. `tests/test_google_drive_gateway.py` pins this as
`test_shared_drive_queries_never_address_the_drive_itself`.

**Discovery stays one bulk enumeration** (ADR 0005), now over everything the identity can
see, filtered to the configured location, with the subtree rebuilt from `parents` as before.

**No new configuration.** An operator describes the corpus: a root folder, and the location
it lives in. How the identity was granted access — membership of the drive, or the folder
itself — is not something the library asks about, because nothing it does depends on the
answer.

## Consequences

- **Membership of a Shared Drive is optional.** Being able to see the root folder is all the
  access the library needs, and the grant can be exactly the corpus — the shape ADR 0001
  hoped production would add as defence in depth.
- **The cost of discovery is the identity's reach, not the corpus.** One page per thousand
  folders the identity can see, corpus or not; 123 folders and 0.6 s for the measured grant,
  1,000 and more pages for a developer identity. This is the same property the deployment
  wants for security, seen from the other side: keep the Drive Identity's grant to the
  corpus, and discovery costs one page. A probe that counts the identity's visible folders
  is reporting the discovery cost. The existing 20-page budget still fails loudly when the
  reach outgrows it (`EnumerationBudgetExceeded`).
- **A wrong location is refused, not silently empty.** Nothing scopes a query to a drive any
  more, so the failure the "no default for the kind" rule guarded against — every query
  quietly addressing the wrong drive — no longer exists. The kind stays mandatory for a
  better reason: it is an assertion about where the root lives, checked on every item, and a
  defaulted assertion asserts nothing.
- The gateway protocol, the staleness window of ADR 0005, and the search path (400-parent
  batches, the rank merge of ADR 0009) are unchanged. The benchmark's `initialization` now
  includes the folder enumeration, which is what it always claimed to measure.

## Alternatives considered

1. **A second configuration axis — how the identity was granted access — selecting the
   query shape.** Rejected. It asks the operator to describe Google's access mechanics
   rather than the corpus, its default has to be guessed per location, and it was built on
   the belief that the `user` corpus returned nothing for a member, which the matrix
   disproved.
2. **A third `DriveKind` for "a folder inside a Shared Drive".** Rejected. `kind` is what
   `DriveLocation.contains` decides containment on; putting the grant into it puts a second
   meaning inside the one value the boundary reads.
3. **Keep `corpora=drive` for members and fall back to the `user` corpus on a 403.**
   Rejected. Two code paths for one result, and the fallback fires on the search path.
4. **`corpora=allDrives`.** Works in the matrix, but Google documents it as the shape to
   prefer `user` or `drive` over, and it is the one that can set `incompleteSearch`.
5. **Root-first traversal** — the root's subfolders, then theirs, one `in parents` query per
   level per 400 parents, so that nothing outside the corpus is ever named and the cost is
   bounded by the corpus rather than the reach. Built, tested and measured on the same
   corpus: 59 folders, 7 levels, **7 round trips and 3.9–4.2 s** against one page in 0.6 s.
   Set aside, not rejected. The cost lands on the first request after each folder-map TTL,
   so a 60 s TTL turns one search a minute into a four-second one; and the property it buys
   — cost bounded by the corpus — is one the deployment already gets by keeping the grant to
   the corpus, which it wants for its own reasons. It is the answer if a deployment's reach
   ever outgrows the page budget, and a folder-first public model does not require a
   folder-by-folder implementation to keep it.

## What would change this

A Drive Identity whose reach is legitimately far larger than any corpus it serves. One was
measured the same day: a developer's own credential, a member of many drives, enumerates
**9,878 folders in 10 pages and 13 s** where the deployment's identity enumerates 123 in one
page — and at that size the enumeration is exposed to short pages exhausting the budget and
to Drive's `incompleteSearch`, either of which now fails with a message that says which.

The decision for now is that **the library's own tooling runs as the deployment's identity**:
the census, the benchmark and the live tests describe the deployment, and they print the
identity they run as so that running them as somebody else is visible rather than silent.
Serving a wide-reach identity well is not what this library is for today.

If that changes, the cheapest route was measured too: `drives.get(driveId)` answers 200 to a
member and 404 to a folder-only grantee in 0.2 s, so membership can be decided once per
process and a member sent `corpora=drive` — 453 folders in one page for that developer —
while a grantee keeps the `user` corpus. That is alternative 3 with the decision remembered
instead of retried per request, and it needs no configuration. Root-first traversal
(alternative 5) is the other route; the `changes.list` feed of ADR 0005 removes the periodic
enumeration altogether. None of them changes the public model: configure folders, require
access to those folders, and let the library find the rest.

Deriving the Drive location from the root folder's own metadata, so that `kind` and the
Shared Drive ID become optional assertions rather than required inputs, is a separate
decision with its own question — what happens when the root moves — and is not made here.

*Decided by ADR 0011:* it is derived, and required nowhere. The root is re-read on every
request, so a root that moves to another drive is refused rather than re-measured. The
consequence recorded above — "the kind stays mandatory ... a defaulted assertion asserts
nothing" — no longer holds: there is no assertion to default, because there is no input.
`includeItemsFromAllDrives` is unconditional from that release on.
