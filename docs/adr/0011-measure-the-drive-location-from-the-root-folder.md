# ADR 0011: Measure the Drive location from the root folder

- Status: Accepted
- Date: 2026-09-18
- Amends: ADR 0004 (the Drive Location as configuration), ADR 0010 (which deferred this)

## Context

A deployment described its corpus twice. It named the Configured Root Folder, and it named
the Drive Location that folder was supposed to be in — a `DriveKind` and, for a Shared Drive,
the drive's ID — passed to `create_gateway()` and to `ScopedDrive()`, and read from
`GDRIVE_DRIVE_KIND` and `GDRIVE_SHARED_DRIVE_ID`. `initialize()` then checked the root
against that second description and refused a mismatch.

Drive already answers the question. `files.get` returns `driveId` on the root folder's own
metadata: absent for anything outside a Shared Drive, present and equal to the drive's ID for
anything inside one. So the configured location was not information the library lacked; it
was a restatement of information it was about to read, and the check was a check of the
operator's restatement rather than of Drive.

ADR 0010 removed the last thing that made the restatement load-bearing. Until then a wrong
kind silently scoped every query to a drive the root was not in and reported an empty corpus,
which is why the kind had no default. After ADR 0010 no query names a drive at all: the
Shared Drive ID is only ever asserted against item metadata. What remained was a mandatory
input whose only failure mode was being wrong, and a `ScopeViolation` that fired when it was.
ADR 0010 named this as the next decision and left it open, on the question of what happens
when the root moves.

## Decision

**The Drive Location is measured from the Configured Root Folder.** `ScopedDrive.initialize()`
reads the root, and after the identity, folder-ness and trashed checks pass it sets
`DriveLocation.of(root.drive_id)` — My Drive for an absent `driveId`, that Shared Drive for a
present one. Nothing else configures it.

- `create_gateway(credentials)`, `GoogleDriveGateway(service=..., credentials=...)` and
  `ScopedDrive(gateway=..., root_folder_id=...)` no longer take a location.
  `GDRIVE_DRIVE_KIND` and `GDRIVE_SHARED_DRIVE_ID` are gone.
- `DriveKind` and `DriveLocation` remain public: the measurement is worth reading, and
  `ScopedDrive.location` publishes it.
- **The measurement is once, and then it is an assertion.** Every containment check —
  enumeration, listing, search, read — still goes through `DriveLocation.contains`, unchanged.
  `_validated_root()` re-reads the root on every request and puts it through
  `_check_live_item`, so **a root that later moves to another Drive is refused**, exactly as a
  file that moves is. That is ADR 0010's open question answered: a root moves out of its
  corpus, it does not redefine it.
- `initialize()` must therefore be awaited before anything else is asked of a `ScopedDrive`.
  `ScopedDrive.location` raises `RuntimeError` before that rather than guessing, and so does
  every call that reads it.

**The gateway sends one request shape.** It no longer knows the location, so
`includeItemsFromAllDrives` is unconditional rather than `kind is SHARED_DRIVE`. It is still
`corpora=user` and still never a `driveId` (ADR 0010).

## Consequences

- **One description of a corpus: a folder.** An operator names `GDRIVE_ROOT_FOLDER_ID` and
  nothing else about where it lives. The class of misconfiguration where the kind or the drive
  ID disagreed with the folder cannot be expressed, so it cannot be diagnosed either — and it
  was the one the "no default for the kind" rule existed to catch.
- **`initialize()` is mandatory, not optional.** This is a behaviour change for an embedding
  adapter that skipped it: calls now raise `RuntimeError` naming the missing step, where they
  used to work off the configured location. `examples/mcp_server.py` already called it, as
  every entry point in this repository did.
- **A My Drive corpus carries Shared Drive rows it then discards.** `includeItemsFromAllDrives`
  on a My Drive deployment widens what Drive *returns*, never what the boundary *allows* —
  `contains` drops every row with a `driveId`. The cost is real though: enumeration is bounded
  by the identity's reach (ADR 0010), and that reach now includes any Shared Drive the
  identity can see, against the same 20-page budget. The mitigation is the one ADR 0010
  already asks for: keep the Drive Identity's grant to the corpus.
- **The boundary is not weaker.** It was never the configured location that bounded the
  corpus; it was the live ancestry proof under one root (ADR 0001, ADR 0006). The location
  check drops cross-drive items *within* that proof, and it still does, against a value read
  from the root instead of from the environment.
- The gateway protocol, the staleness window (ADR 0005), the search path and the audit records
  are unchanged.

## Alternatives considered

1. **Keep the location optional: measure it when absent, assert it when given.** Rejected. It
   keeps every line of the configuration path alive to serve a case whose only effect is to
   refuse a root the library would otherwise have served correctly, and it leaves two ways to
   describe one corpus — the thing this decision is removing.
2. **Measure lazily, in `_validated_root()`, instead of in `initialize()`.** Rejected, and it
   is the dangerous one. A root that moved to another drive would be re-measured on the next
   request and the corpus would follow it, silently. Measuring once is what makes the later
   reads an assertion rather than an observation.
3. **Re-measure on every `initialize()` call.** This is what the implementation does, because
   a second `initialize()` is an explicit re-initialization and indistinguishable from
   building a new `ScopedDrive`. Adapters call it once, before serving.
4. **Drop `DriveKind` and `DriveLocation` from the public API.** Rejected. Consumers read the
   measurement — the benchmark reports `drive_kind` — and `contains` is the boundary's own
   vocabulary.
