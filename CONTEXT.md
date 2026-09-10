# Domain glossary

## Drive Location

The storage location one deployment is configured for: either the Drive Identity's My Drive
context or one Shared Drive.

## Shared Drive

One supported Drive Location. Shared Drive items carry the configured Shared Drive ID in their
Drive metadata.

## My Drive

The personal-Drive location of a consumer or Workspace Google Account. My Drive mode covers
accessible items outside Shared Drives, including a folder shared directly with the Drive
Identity.

## Configured Root Folder

The folder selected as the root of the corpus. It belongs to the configured Drive Location.

## Authorized Subtree

The Configured Root Folder and every item that is currently its descendant. Membership follows
the current Drive hierarchy, so it changes when Drive does.

## Drive Identity

The Google identity whose credential the library uses to reach the configured Drive Location.
It can usually see more of Drive than the Authorized Subtree; that gap is what the boundary
exists to close.

## Adapter

Anything that puts the library on a wire or into an agent — an MCP server, a hosted provider, a
script. Adapters authenticate their own callers; the library never sees a caller's credential.

## Document

A non-folder item in the Authorized Subtree. A Document may have readable content or only
supported metadata.

## File ID

A Google Drive item ID, used directly as the handle for every operation. It grants no access on
its own: membership in the Authorized Subtree is proven against Drive on every call (ADR 0006).
