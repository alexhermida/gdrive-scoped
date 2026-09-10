# ADR 0009: Cross-batch merging by rank position is accepted as it is

- Status: Accepted
- Date: 2026-09-09
- Related: ADR 0007

## Context

When the subtree has more than 400 folders, search is split into batches and each batch
returns in its own relevance order, without scores. ADR 0007 merges them by position: the
first hit of every batch, then the second, and so on. A small batch with a single mediocre hit
places that hit in the first round, ahead of the second-best hit of a large batch. The
question was how much that costs.

No measured corpus has more than one batch, so it was simulated by splitting the 64 folders of
a real corpus into two and three batches in folder-map order and comparing the expected
document's rank with the single-batch rank:

| Query | 1 batch | 2 batches | 3 batches | batch sizes with 2 / with 3 |
|---|---|---|---|---|
| Query 1 | 1 | 2 | 2 | 1 and 21 / 0, 7 and 15 |
| Query 2 | 5 | 7 | 4 | 78 and 60 / 52, 44 and 42 |
| Query 3 | 1 | 1 | 1 | 83 and 74 / 62, 49 and 46 |
| Query 4 | 12 | 17 | 13 | 71 and 75 / 52, 45 and 49 |

The queries are the four of ADR 0007, described there.

The worst shift is five positions. The three queries that fitted a page of ten still fit with
two and with three batches. Query 1 with two batches is exactly the feared effect, a
one-folder batch with a single hit slipping in first, and it cost one position.

## Decision

Position merging stays, with no correction. No normalisation by batch size, no second query
for ordering, and the batch size stays at 400.

The census already reports how many batches a corpus needs (`search_batches`). That number is
the warning: above one, the order is a measured approximation and not a contract.

## Consequences

- One code path for search, with no modes.
- In a multi-batch corpus a document can appear a few positions below where Drive alone would
  put it. On the available data the shift is single-digit.
- The real degradation can only be measured on a real corpus of more than 400 folders. When one
  exists, it gets a cases file under `evaluation/` and this table is repeated.

## Alternatives considered

1. **Normalise rank by batch size.** Rejected. It has no basis: a batch's size depends on how
   the folders were cut, not on the relevance of its documents.
2. **A second query without folder clauses to obtain the global order.** Rejected. In a shared
   drive's global order the corpus documents are spread across the whole result set, so nearly
   everything would have to be paged to see them.
3. **Raise the batch size towards 500.** Rejected for now. Drive accepted 500 clauses and
   refused 1,000 when measured; 400 leaves headroom for a corpus that grows between two
   enumerations of the folder map. It moves the threshold and does not solve the merge.
4. **Accept the interleaving.** Chosen.

## What would change this

A real multi-batch corpus where the evaluation fails on order rather than recall. At that
size the conversation is probably about an index rather than this merge, with retrieval from
the index and authorisation from Drive on every read.
