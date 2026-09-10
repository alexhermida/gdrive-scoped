# ADR 0008: Search asks for `limit + 1` hits per batch, not pages of a thousand

- Status: Accepted
- Date: 2026-09-09
- Related: ADR 0007

## Context

`ScopedDrive.search` asked Drive for pages of 1,000 results per folder batch, up to five
pages, and then cut to `limit`, which is between 1 and 50. Two things wanted the full
candidate set: the modified-time re-sort, and `truncated`, which was computed by counting.

ADR 0007 removed the first. The second never needed the full set: it only needs to know
whether at least one more match existed than was returned, and Drive says so with
`nextPageToken`.

Measured on 2026-09-09 against a 64-folder, 320-file corpus, three alternating repetitions per
query, median latency and response size:

| Query | Matches | ms at pageSize 1000 | ms at pageSize 11 | bytes at 1000 | bytes at 11 |
|---|---|---|---|---|---|
| Query 1 | 22 | 884 | 751 | 11,156 | 6,643 |
| Query 2 | 138 | 888 | 784 | 66,728 | 5,571 |
| Query 3 | 157 | 1,027 | 713 | 74,901 | 5,859 |
| Query 4 | 146 | 946 | 606 | 69,689 | 5,744 |

The queries are the four of ADR 0007, described there.

Between 15% and 35% less latency per request, and a response an order of magnitude smaller
for frequent terms. On this corpus both variants make one request per batch, because no term
has more than 1,000 matches. The real difference is a large corpus with a common term: up to
five pages of a thousand to serve ten results.

## Decision

`ScopedDrive.search` asks the gateway for `limit + 1` items per batch. The gateway pages with
`pageSize = min(limit + 1, 1000)` until that many distinct items are in hand or the results are
exhausted, within the page budget that already existed, and reports whether more were on offer
(`SearchPage.has_more`, from `nextPageToken`). Drive may answer a short page with a token, so
"satisfied" means the count is met or the token is gone, whichever comes first.

`truncated` becomes: more merged candidates than `limit`, or any batch had more. It remains
exact as a boolean, which is all `SearchResult` exposes.

With several batches, `limit + 1` per batch still suffices: position merging takes the n-th
hit of every batch before the (n+1)-th of any, so the first `limit` merged hits can only come
from the first `limit` positions of each batch.

## Consequences

- Less latency and a response an order of magnitude smaller on every search with a frequent
  term. The worst case of five pages of a thousand for ten results is gone.
- **`DriveGateway` changes shape.** `search_items` takes a `limit`, and `SearchPage` gains
  `has_more`. Every external implementation of the protocol has to follow. The library is 0.x
  and the change is in the changelog, but it is a break and is named as one.
- A candidate the safety filter drops after the request can leave a batch with fewer than
  `limit + 1`. With the folders in the query that only happens for multi-parent items, which
  Drive no longer creates. Accepted: fewer than `limit` results come back, and `truncated`
  still tells the truth because it also reads `has_more`.
- `incomplete` from the page budget stays as a safeguard against endless short pages and is
  not reached in practice.
- The census and the evaluation are unaffected: they use `list_descendants`, which still
  enumerates whole.

## Alternatives considered

1. **Leave it.** Rejected. The original justification is gone, and the cost grows with the
   corpus rather than with what the caller asked for.
2. **Ask for exactly `limit`.** Rejected. Without the extra item, `truncated` would rest on
   `nextPageToken` alone, which Drive may omit on an exact page. The extra item makes it
   independent of that detail.
3. **Ask for `limit + 1`.** Chosen.

## What would change this

A consumer that needs the total match count. It would then be added as a separate operation
with its own explicit cost, not paid again on every search.
