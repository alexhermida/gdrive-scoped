# ADR 0003: Use Drive-native keyword retrieval for the PoC

- Status: Accepted
- Date: 2026-08-30

## Context

The PoC exists to determine whether folder-scoped Drive retrieval is useful without indexing, embeddings, or a vector store.

## Decision

Search uses Google Drive `fullText` queries constrained to the Authorized Subtree. Agents are instructed to issue focused keyword searches and read promising documents. The PoC is evaluated with 5–10 representative questions.

## Consequences

- The first version remains small and has no synchronization pipeline.
- Search is lexical, has limited relevance information, and may benefit from descriptive filenames.
- If evaluation quality is inadequate, indexing becomes a separately justified project rather than hidden PoC scope.

