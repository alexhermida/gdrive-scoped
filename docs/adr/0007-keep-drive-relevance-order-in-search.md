# ADR 0007: Keep Drive's relevance order in search

- Status: Accepted
- Date: 2026-09-09

## Context

`ScopedDrive.search` splits the folders of the Authorized Subtree into batches of at most 400
parents, because Drive accepts no more `in parents` clauses per query. Each batch comes back
in descending relevance. Drive exposes no score, and rejects `orderBy` next to `fullText`, so
there is no number on which two batches could be merged.

The implementation answered that by re-sorting every candidate by modification time, always,
even when the corpus fitted one batch. The argument was comparability across batches. The
cost had not been measured.

It was measured on 2026-09-09 against a 64-folder, 320-file corpus that fits one batch, with
four questions written against documents known to be in it. For each, the rank of the
expected document in Drive's order and in the modified-time order:

| Query | Matches | Drive rank | Recency rank | Passes at limit |
|---|---|---|---|---|
| Query 1 | 22 | 1 | 8 | yes, 10 |
| Query 2 | 138 | 5 | 93 | no, 10 |
| Query 3 | 157 | 1 | 115 | no, 10 |
| Query 4 | 146 | 12 | 97 | no, 50 |

The four queries are described rather than quoted, so the corpus stays private: query 1
is a versioned document title, query 2 a short acronym naming a topic, query 3 a two-word
phrase, and query 4 a single common domain term.

Recall is not the problem: all four documents are among the matches. The recency sort pushes
three of them out of the result window, because in an active corpus whatever was touched this
week always beats whatever is relevant. Nothing in the repository had measured the re-sort
before this.

## Decision

Search no longer re-sorts. Each batch is kept in the order Drive returned it, and batches are
merged by rank position: the first hit of every batch, then the second of every batch, and so
on. The first sighting of an ID wins; later ones are dropped.

With one batch, which is every corpus under 400 folders, the result is Drive's order exactly.
With several, the n-th hit of one batch is treated as a peer of the n-th hit of every other.
It is the only merge available without scores, and the one that assumes least.

`truncated` and `incomplete` keep their meaning.

## Consequences

- The four measured questions pass at the same limits; the evaluation went from 1/4 to 4/4.
- Result order now depends on Drive's order, which Google documents as descending relevance
  but not as a contract. The match set already depended on Drive; now its order does too.
  Deduplication by ID stays, because one Drive page can repeat a file.
- In a multi-batch corpus the cross-batch order is an approximation. ADR 0009 measures it and
  accepts it.
- The full candidate set is no longer needed for anything, which is what makes ADR 0008
  possible.
- `docs/api.md`, `docs/architecture.md`, `docs/gotchas.md` and the comments in `drive.py` and
  `census.py` that justified the re-sort change with it.

## Alternatives considered

1. **Keep modification time.** Rejected by the table above.
2. **Drive order with one batch, modification time with several.** Rejected. It introduces a
   change of ranking semantics at 400 folders that nobody would notice until it failed, and
   for several batches it is still the order that measured worst.
3. **Interleave by position.** Chosen. One code path, identical to Drive's order in the common
   case, graceful in the rare one.

## What would change this

A real multi-batch corpus where interleaving measures badly. The answer then would be a merge
with a score of our own, not a return to modification time.
