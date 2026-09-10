# Security

## Reporting

Use GitHub's private vulnerability reporting on this repository: the *Security* tab, then
*Report a vulnerability*. Please do not open a public issue for anything that could expose a
document outside a configured folder.

## The model, in short

- **The Drive credential is broad.** The library is given `drive.readonly` over everything its
  Drive Identity can see. The folder boundary is enforced here, in application code, on every
  call (ADR 0001). Production deployments should additionally give that identity access to as
  little as possible.
- **Every item is re-read from Drive at the moment of the call** and its parent checked against
  the configured subtree. A file moved out, trashed, replaced by a shortcut or with its
  permissions revoked is refused immediately.
- **One bounded window is accepted.** A *folder* moved out of the subtree keeps serving its
  files for up to the folder-map TTL, 60 seconds by default. Setting the TTL to `0` closes it
  at the cost of an enumeration per request (ADR 0005).
- **Drive IDs are accepted as handles.** An ID from outside the subtree is refused; the boundary
  is the proof, not the shape of the handle (ADR 0006).
- **The library authenticates no callers.** Whatever embeds it owns caller authentication and
  decides what reaches a model. A caller's token is never passed to Google.
- **No content screening.** Document text reaches the caller as it is. A document containing
  instructions is text containing instructions; treat the corpus as trusted or screen it in
  the adapter.
- **Audit records are sensitive.** They contain search queries, file IDs and the caller. Route
  the `gdrive_scoped.audit` logger accordingly.

## Supported versions

The latest 0.x release. Earlier releases are not patched.
