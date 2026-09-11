# ADR 0004: Support My Drive and Shared Drive locations

- Status: Accepted; the Shared Drive request policy superseded by ADR 0010
- Date: 2026-08-30

## Context

The first implementation required every Configured Root Folder to belong to one Shared Drive. Users with consumer Google Accounts have My Drive but cannot create or use Shared Drives. The folder-scoped authorization boundary does not fundamentally depend on Shared Drive ownership: current ancestry beneath one trusted root remains the decisive proof.

Google Drive does require different list policies. Shared Drive items carry a `driveId` and are searched with the `drive` corpus. My Drive items do not carry a `driveId` and are searched with the broader `user` corpus.

## Decision

One deployment configures one Drive Location: `shared_drive` or `my_drive`.

- Shared Drive mode requires an exact Shared Drive ID and retains the existing request policy.
- My Drive mode forbids a Shared Drive ID, uses the `user` corpus without a `driveId`, and excludes Shared Drive items.
- Scoped Drive checks location membership before proving current ancestry beneath the Configured Root Folder.
- My Drive mode permits an accessible personal-drive folder even when another user owns it.
- An omitted Drive kind defaults to Shared Drive for configuration compatibility.

*Amended by ADR 0010:* Shared Drive mode no longer sends `corpora=drive` with a `driveId`.
Every query uses the `user` corpus with `includeItemsFromAllDrives`, so membership of the
drive is not required; the Shared Drive ID is asserted on every item instead. The kind no
longer has a default anywhere (v0.5.0).

The application continues to request `drive.readonly`; its OAuth grant is broader than the Authorized Subtree, so the application-enforced boundary from ADR 0001 remains mandatory.

## Consequences

- Consumer Google Accounts can run the stdio server against one selected folder.
- One corpus per deployment and one Drive Identity remain unchanged.
- The gateway owns two explicit list request policies.
- `driveId` becomes optional application metadata rather than an always-present string.
- The `user` corpus can contain items beyond the configured root, so parent constraints and location/ancestry checks remain security-critical.
- Supporting the whole My Drive `root` alias, Google Picker, `drive.file`, or per-client Drive identities remains separate work.
