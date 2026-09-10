# ADR 0001: Enforce the folder boundary in the application

- Status: Accepted; the opaque Document Reference half superseded by ADR 0006
- Date: 2026-08-30

## Context

Google Drive OAuth scopes do not express access to an arbitrary existing folder subtree. `drive.readonly` covers everything the Drive Identity may read, while `drive.file` is based on files created by or explicitly selected for an application.

A deployment must expose one existing folder subtree even when its Drive Identity can see more of the Shared Drive.

## Decision

All Drive access passes through a Scoped Drive boundary that proves current membership under the Configured Root Folder. Tools do not accept Drive IDs or raw Drive queries, and Document References are opaque. Content is fetched only after current ancestry has been verified.

*Amended by ADR 0006:* tools now accept raw Drive IDs. The boundary is the live ancestry proof on
every call, not the shape of the handle. Raw Drive queries remain refused.

## Consequences

- A caller cannot widen the corpus through arguments or copied Drive IDs.
- A bug that bypasses Scoped Drive could still use the broader Drive credential; production may add an ACL-restricted Drive Identity as defense in depth.
- Folder traversal and ancestry checks add Drive API calls.
- Authorization logic becomes the highest-priority test surface.

