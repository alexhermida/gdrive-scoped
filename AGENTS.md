# Agent working agreement

This repository is a **library**, `gdrive_scoped`, giving read-only access to one configured
Google Drive folder subtree. Adapters that put it on a wire live outside it; `examples/` holds
one, for trying it by hand.

## Start here

Before changing code:

1. Read `CONTEXT.md` for the canonical vocabulary.
2. Read `docs/architecture.md` and any relevant ADR under `docs/adr/`.
3. Preserve the invariants below.

## Invariants

- Everything is read-only. Do not add Drive write methods, write scopes, or mutating tools.
- One deployment exposes one Configured Root Folder inside one configured Drive Location: My
  Drive or one Shared Drive.
- Every item returned or read must be inside the current Authorized Subtree.
- **The core imports no MCP, reads no environment variable, and discovers no credentials.**
  `gdrive_scoped.env` is the single exception and is for this repository's own entry points —
  the benchmark, the scripts, the live tests, the example. Nothing under `gdrive_scoped` may
  import it. `tests/test_public_api.py` guards both halves of this.
- Nothing in `src/` imports the MCP SDK. It is a development dependency, for `examples/`.
- Tools never accept raw Google Drive query expressions. They *do* accept raw Drive item IDs:
  the boundary is the live ancestry proof, not the handle (ADR 0006).
- Shortcuts are not followed or returned.
- Nothing reaches the unrestricted Google Drive client directly. All access passes through the
  scoped application boundary.

## Development workflow

- Python 3.12 or newer; developed on 3.13 (`.python-version`).
- Manage environments and dependencies with `uv`.
- Develop in vertical TDD slices: one failing behavior, minimal implementation, then refactor
  while green.
- Test behavior through public interfaces. Fake only external boundaries such as Google Drive.
- Keep commits small and meaningful. Never push or open a PR unless explicitly requested.
- Record durable, consequential decisions as ADRs; record Drive-specific traps in
  `docs/gotchas.md`.
- Document nothing that lives in another repository — no cross-repo paths, plans or ADRs.

## Commands

```bash
just setup
just check          # format, lint, types, tests — hermetic
just census         # needs a configured Drive
just test-live      #   "
just evaluate       #   "
just benchmark      #   "
just example-stdio  #   "
```

Run an individual test while iterating:

```bash
uv run pytest tests/path/to/test_file.py -q
```
