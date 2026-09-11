# Google Drive gotchas

These constraints are part of the design, not incidental implementation details.

## Hierarchy and scope

- Drive has no descendant query operator. `'<folder-id>' in parents` finds direct children only.
- `driveId` is populated only for Shared Drive items. Treat its absence as My Drive location metadata, not as missing metadata.
- `owners` is not populated for items in a Shared Drive: the drive owns them. `lastModifyingUser` is populated in both locations and is the person field a citation can rely on.
- `parents` is represented as a list, although current Drive items support one parent. Treat zero or multiple parents as unproven ancestry and fail closed.
- Shortcuts can point outside the Authorized Subtree. Every shortcut is excluded, and a shortcut target is never requested or followed.
- A cached discovery result is not authorization on its own. The item under authorization is always re-read from Drive; only the chain above it may come from the folder map, and that reuse is bounded by `GDRIVE_FOLDER_MAP_TTL_SECONDS` (ADR 0005).
- One `mimeType = folder` query under the `user` corpus returns every folder the identity can see, so a subtree costs one paginated call per thousand folders rather than one call per folder. It is bounded by the identity's **reach**, not by the corpus or the drive: correctness is unaffected, cost is not. Keep the Drive Identity's grant to the corpus, and read a probe's reach count as the discovery cost (ADR 0010).

## Shared-drive requests

- **Do not use `corpora=drive` with a `driveId`.** It addresses the drive itself, and Drive refuses it with 403 `teamDriveMembershipRequired` for an identity granted a folder *inside* the drive rather than membership — measured with no parent filter and again with a 59-parent search filter. A single-parent filter happened to pass, so the failure is intermittent rather than absent.
- Use `corpora=user` with `includeItemsFromAllDrives=true` and `supportsAllDrives=true`. Measured to return the same results as `corpora=drive` for a drive member: 453 of 453 folders, and identical listings and search hits.
- The configured Shared Drive ID is asserted on every item's `driveId`, never sent in the query.
- Omitting `includeItemsFromAllDrives` or `supportsAllDrives` produces empty results rather than an obvious error.

## My Drive requests

- Use `corpora=user`, omit `driveId`, and set `includeItemsFromAllDrives=false`.
- The `user` corpus can include files shared directly with the Drive Identity. Every query must remain parent-constrained and every result must still pass location and ancestry checks.
- Do not infer My Drive mode from a missing Shared Drive setting. Select it explicitly with `GDRIVE_DRIVE_KIND=my_drive`.

## Search behavior

- Default Drive ordering is not a reliable page partition. Paginate and deduplicate by item ID.
- Drive may return fewer than `pageSize` items and still hand back a `nextPageToken`. A request
  for N hits is satisfied when N are in hand or the token is gone, not after one page.
- Large disjunctions of parent clauses hit Drive query-complexity limits. Use at most 400 parent clauses per query.
- `fullText` also matches filenames, so apparent retrieval quality may partly be filename quality.
- Never accept raw `q` input. Escape keyword text and construct every query inside the gateway.
- **`fullText` and `orderBy` are mutually exclusive.** Drive rejects the request outright:
  *"Sorting is not supported for queries with fullText terms. Results are always in
  descending relevance order."* Send no `orderBy` on a search and keep the order Drive returns, merging parent
  batches by rank position in the application;
  keep `name_natural` for folder listings, which carry no `fullText` term.
- **Relevance is the only ranking signal a search has. Do not re-sort it.** Re-sorting hits by
  `modifiedTime` was measured on a 320-file corpus: for four questions the expected document sat
  at Drive ranks 1, 5, 1 and 12 and fell to 8, 93, 115 and 97, dropping three of the four out of
  a 10-result page. Recently touched files crowd out relevant ones in any active corpus.

## Content export

- Google Workspace exports are capped at 10 MB.
- CSV export of a Google Sheet returns one sheet; export XLSX to preserve every sheet.
- Export Slides as PPTX to preserve slide boundaries and speaker notes.
- Google Docs Markdown export does not include comments or suggestions.
- Check download capability and report unsupported or restricted content explicitly.

## References

- https://developers.google.com/workspace/drive/api/guides/enable-shareddrives
- https://developers.google.com/workspace/drive/api/guides/search-files
- https://developers.google.com/workspace/drive/api/guides/handle-errors
- https://developers.google.com/workspace/drive/api/guides/manage-downloads
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list

