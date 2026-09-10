# ADR 0002: Serve one corpus per deployment

- Status: Accepted; Drive Location wording amended by ADR 0004
- Date: 2026-08-30

## Context

A hosted deployment authenticates its callers separately from its own Google Drive connection. Supporting a different Google identity or folder mapping for every client would require per-user consent, token storage, and ACL-aware retrieval.

## Decision

One deployment uses one Drive Identity, one Shared Drive, and one Configured Root Folder. Every authorized caller sees the same Authorized Subtree. ADR 0004 later generalized the storage location to either My Drive or one Shared Drive without changing the one-corpus decision.

## Consequences

- Local stdio and future hosted deployments share the same application model.
- Client OAuth controls access to the deployment, not Drive visibility within it.
- Serving another corpus requires another configuration or deployment.
- Per-user Google Drive delegation is explicitly outside the PoC.
