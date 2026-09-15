"""Current-hierarchy authorization for one Google Drive folder subtree."""

from __future__ import annotations

import asyncio
import logging
import math
import time
import weakref
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import NoReturn

from gdrive_scoped.drive import FOLDER_MIME_TYPE, SHORTCUT_MIME_TYPE, DriveGateway, DriveItem
from gdrive_scoped.errors import ScopeViolation
from gdrive_scoped.location import DriveLocation

#: How long an enumerated folder map may keep answering ancestry questions. This is the
#: staleness window recorded in docs/adr/0005-bounded-folder-map-staleness.md: the time a
#: folder moved *out* of the Authorized Subtree keeps serving its contents. Zero disables
#: reuse and restores per-request enumeration.
DEFAULT_FOLDER_MAP_TTL_SECONDS = 60.0

#: Every authorization decision, one record each. Separate from the module logger so a
#: deployment can route it somewhere durable — refusals are the signal that something
#: named an item it should have scoped out, and in normal use there are none.
audit = logging.getLogger("gdrive_scoped.audit")

#: Who is asking, as far as the adapter knows. The library authenticates nobody,
#: so an adapter that does sets this for the span of a request — a ContextVar
#: follows the task, which under asyncio is the request — and every audit record
#: carries it. Unset, records say `None` rather than guess.
audit_caller: ContextVar[str | None] = ContextVar("gdrive_scoped_audit_caller", default=None)


@dataclass(frozen=True, slots=True)
class ScopedItem:
    """Authorized metadata safe to return through a tool."""

    id: str
    name: str
    mime_type: str
    relative_path: str
    modified_time: str | None = None
    size: int | None = None
    web_view_link: str | None = None
    #: Empty for Shared Drive items, by Drive's rule; see `last_modified_by`.
    owners: tuple[str, ...] = ()
    last_modified_by: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """Search hits, and what the caller cannot tell by counting them.

    Frozen but not `slots=True`: an adapter publishing this as structured
    output derives a schema from it, and a slotted dataclass has none.
    """

    items: list[ScopedItem]
    #: More items matched than `limit` allowed through. Distinct from
    #: `incomplete`: Drive could see the whole corpus, the answer was cut.
    truncated: bool = False
    #: The search did not see the whole corpus — Drive said so, or the page
    #: budget stopped it. "No results" from an incomplete search is not
    #: evidence of absence, and a model told otherwise will treat it as such.
    incomplete: bool = False


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class AuthorizedItem:
    """One item whose membership was proven, ready to be read without re-proving it.

    Only `ScopedDrive.authorize_document` issues these, and `download` recognises
    the very instances it issued. Equality is identity, so a proof cannot be
    forged around a `DriveItem`, copied, or carried to another scope.
    """

    item: DriveItem
    relative_path: str


@dataclass(slots=True)
class ScopedDrive:
    """Authorize every item against one Drive location and root folder."""

    gateway: DriveGateway
    location: DriveLocation
    root_folder_id: str
    folder_map_ttl_seconds: float = DEFAULT_FOLDER_MAP_TTL_SECONDS
    clock: Callable[[], float] = time.monotonic
    _folder_paths: dict[str, str] | None = field(default=None, init=False, repr=False)
    _folder_paths_expiry: float = field(default=0.0, init=False, repr=False)
    #: Bumped by every completed enumeration. A task that waited on the lock
    #: uses it to tell "nobody has refreshed since I looked" from "somebody
    #: just did, and their map is newer than the one I rejected".
    _folder_map_generation: int = field(default=0, init=False, repr=False)
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    #: The proofs this scope issued and callers still hold. `download` accepts
    #: nothing else. Weak, so a proof a caller dropped costs nothing to remember.
    _issued: weakref.WeakSet[AuthorizedItem] = field(
        default_factory=weakref.WeakSet, init=False, repr=False
    )

    def __post_init__(self) -> None:
        # Drive resolves the alias `root` to the whole of a My Drive, and the
        # root read below would then key the folder map on the real ID: every
        # listing, search and read would succeed against the entire Drive.
        if self.root_folder_id == "root":
            raise ValueError(
                "root_folder_id must be a folder ID, not the alias 'root'. "
                "The whole of a Drive is not a corpus."
            )
        # float() accepts inf and nan. An infinite window never refreshes the
        # folder map, so a folder moved out of the corpus would keep serving its
        # contents for the life of the process; nan is no policy at all.
        if not math.isfinite(self.folder_map_ttl_seconds) or self.folder_map_ttl_seconds < 0:
            raise ValueError(
                "folder_map_ttl_seconds must be finite and not negative; 0 closes the window"
            )

    async def initialize(self) -> DriveItem:
        """Validate and return the Configured Root Folder."""

        root = await self.gateway.get_item(self.root_folder_id)
        if root.id != self.root_folder_id:
            raise ScopeViolation("Drive returned unexpected root metadata")
        if not self.location.contains(root.drive_id):
            raise ScopeViolation("Configured root does not belong to the configured Drive location")
        if root.mime_type != FOLDER_MIME_TYPE:
            raise ScopeViolation("Configured root is not a folder")
        if root.trashed:
            raise ScopeViolation("Configured root is trashed")
        return root

    async def list_folder(self, folder_id: str | None = None) -> list[ScopedItem]:
        """List safe metadata for direct children of an authorized folder."""

        folder, folder_path = await self._authorize(folder_id or self.root_folder_id)
        if folder.mime_type != FOLDER_MIME_TYPE:
            raise ScopeViolation("That item is not a folder")

        safe_children: list[ScopedItem] = []
        for child in await self.gateway.list_children(folder.id):
            if not self._is_direct_safe_child(child, folder.id):
                continue
            relative_path = (
                child.name if folder.id == self.root_folder_id else f"{folder_path}/{child.name}"
            )
            safe_children.append(_to_scoped_item(child, relative_path))
        return safe_children

    async def get_metadata(self, file_id: str) -> ScopedItem:
        """Return metadata only after revalidating current ancestry."""

        item, relative_path = await self._authorize(file_id)
        return _to_scoped_item(item, relative_path)

    async def folder_paths(self) -> dict[str, str]:
        """Every folder in the Authorized Subtree, mapped to its relative path.

        The same map `search` scopes itself with, exposed because "what is in
        this corpus" is a question worth asking from outside — the census and
        the evaluation harness both start here.
        """

        return dict(await self._folder_map())

    async def search(self, keywords: str, limit: int) -> SearchResult:
        """Search every currently discovered descendant folder.

        Drive returns each batch in descending relevance, exposes no score, and
        refuses `orderBy` next to `fullText`. The batches are therefore merged
        by rank position - the first hit of every batch, then the second - and
        a corpus that fits one batch gets Drive's order untouched. Re-sorting
        by modification time was measured to bury the answers: on one real
        corpus, documents Drive ranked 1st and 5th fell to 115th and 93rd
        (ADR 0007; ADR 0009 accepts the merge).

        Each batch is asked for one hit more than `limit`. That is all
        `truncated` needs, and with position merging the first `limit` merged
        hits can only come from the first `limit` of every batch (ADR 0008).
        """

        folder_paths = await self._folder_map()
        folder_ids = tuple(folder_paths)
        ranked_batches: list[list[tuple[DriveItem, str]]] = []
        incomplete = False
        has_more = False

        for offset in range(0, len(folder_ids), 400):
            parent_batch = folder_ids[offset : offset + 400]
            page = await self.gateway.search_items(parent_batch, keywords, limit + 1)
            incomplete = incomplete or page.incomplete
            has_more = has_more or page.has_more
            batch: list[tuple[DriveItem, str]] = []
            for item in page.items:
                if not self._is_safe_search_result(item, folder_paths):
                    continue
                parent_path = folder_paths[item.parents[0]]
                relative_path = item.name if parent_path == "." else f"{parent_path}/{item.name}"
                batch.append((item, relative_path))
            ranked_batches.append(batch)

        candidates = _interleave_by_rank(ranked_batches)
        return SearchResult(
            items=[
                _to_scoped_item(item, relative_path) for item, relative_path in candidates[:limit]
            ],
            truncated=len(candidates) > limit or has_more,
            incomplete=incomplete,
        )

    async def authorize_document(self, file_id: str) -> AuthorizedItem:
        """Prove membership once for an item that is about to be read.

        Callers pass the result to `download` rather than naming the ID again, so one
        read performs one ancestry proof instead of the two it used to.
        """

        item, relative_path = await self._authorize(file_id)
        if item.mime_type == FOLDER_MIME_TYPE:
            raise ScopeViolation("Folders do not have readable document content")
        if not item.can_download:
            raise ScopeViolation("Drive does not permit downloading this item")
        proof = AuthorizedItem(item=item, relative_path=relative_path)
        self._issued.add(proof)
        return proof

    async def download(self, authorized: AuthorizedItem, export_mime_type: str | None) -> bytes:
        """Fetch bytes for an item authorized earlier in this same request, by this scope."""

        if authorized not in self._issued:
            _refuse(
                authorized.item.id, "foreign_proof", "Authorization was not issued by this scope"
            )
        if export_mime_type is None:
            return await self.gateway.download_item(authorized.item.id)
        return await self.gateway.export_item(authorized.item.id, export_mime_type)

    async def _authorize(self, item_id: str) -> tuple[DriveItem, str]:
        """Prove that one item is currently inside the Authorized Subtree.

        The item's own metadata is always read live, so a file that moved, was trashed,
        became a shortcut or changed Drive location is refused immediately, whatever the
        folder map says. Only the chain *above* it comes from the map, which is why the
        residual staleness is a folder re-parenting and nothing else.
        """

        if item_id == self.root_folder_id:
            root = await self._validated_root()
            return _allow(root, ".")

        item = await self.gateway.get_item(item_id)
        self._check_live_item(item)
        if len(item.parents) != 1:
            # Nothing to prove ancestry against. This is the "anyone in the
            # organisation can share any file with the Drive Identity" case: a
            # directly shared item has no parent inside the corpus, so it is
            # refused rather than silently readable.
            _refuse(item_id, "ancestry_unprovable", "Item ancestry cannot be proven")

        reused_map = self._cached_folder_map() is not None
        parent_path = (await self._folder_map()).get(item.parents[0])
        if parent_path is None and reused_map:
            # A folder created after the map was built is a false denial, not a breach.
            # One extra query can only turn a deny into an allow that a freshly read map
            # would have granted anyway, so refreshing here never widens the boundary.
            parent_path = (await self._folder_map(refresh=True)).get(item.parents[0])
        if parent_path is None:
            _refuse(item_id, "outside_subtree", "Item is outside the configured root")
        return _allow(item, item.name if parent_path == "." else f"{parent_path}/{item.name}")

    async def _validated_root(self) -> DriveItem:
        """Re-read the root itself. Not audited: it is not a caller's decision."""

        root = await self.gateway.get_item(self.root_folder_id)
        if root.id != self.root_folder_id:
            # Drive answered for something else: an alias resolved, or an ID that
            # does not name the folder it claims to. Nothing built on it can be trusted.
            _refuse(self.root_folder_id, "root_mismatch", "Drive returned unexpected root metadata")
        self._check_live_item(root)
        if root.mime_type != FOLDER_MIME_TYPE:
            _refuse(self.root_folder_id, "root_not_a_folder", "Configured root is not a folder")
        return root

    def _cached_folder_map(self) -> dict[str, str] | None:
        if self._folder_paths is not None and self.clock() < self._folder_paths_expiry:
            return self._folder_paths
        return None

    async def _folder_map(self, *, refresh: bool = False) -> dict[str, str]:
        """Return corpus folder ids to relative paths, reusing a map within its TTL."""

        if not refresh:
            cached = self._cached_folder_map()
            if cached is not None:
                return cached

        generation = self._folder_map_generation
        async with self._refresh_lock:
            # Concurrent misses coalesce here. Whoever arrives second finds the
            # generation advanced and reuses the map the first one built, rather
            # than enumerating the whole location again.
            if self._folder_map_generation != generation:
                cached = self._cached_folder_map()
                if cached is not None:
                    return cached

            # Read before the query, not after, so a slow enumeration shortens the
            # staleness window rather than extending it.
            started = self.clock()
            paths = await self._enumerate_folder_paths()
            self._folder_map_generation += 1
            if self.folder_map_ttl_seconds > 0:
                self._folder_paths = paths
                self._folder_paths_expiry = started + self.folder_map_ttl_seconds
            return paths

    async def _enumerate_folder_paths(self) -> dict[str, str]:
        """Map every corpus folder id to its relative path from one bulk folder query.

        Drive has no descendant operator, so a subtree can only be expressed by naming
        every folder in it - but naming them does not require *visiting* them one at a
        time. One `mimeType = folder` query returns every folder the Drive Identity can
        see, in the configured location or not; the location check drops the rest and
        the subtree is arithmetic on `parents` after that. Its cost is the identity's
        reach rather than the corpus - one page per thousand folders - which is why a
        deployment keeps that reach to the corpus (ADR 0010).
        """

        root = await self._validated_root()
        children_by_parent: dict[str, list[DriveItem]] = {}
        for folder in await self.gateway.list_folders():
            # Indexing by `parents[0]` only when there is exactly one parent keeps the
            # rule the direct-child walk enforced: a folder reachable by two paths is not
            # descended into, and neither is anything underneath it.
            if len(folder.parents) != 1 or not self._is_corpus_folder(folder):
                continue
            children_by_parent.setdefault(folder.parents[0], []).append(folder)

        paths = {root.id: "."}
        pending = deque([root])
        while pending:
            folder = pending.popleft()
            parent_path = paths[folder.id]
            for child in children_by_parent.get(folder.id, ()):
                if child.id in paths:
                    continue
                paths[child.id] = (
                    child.name if parent_path == "." else f"{parent_path}/{child.name}"
                )
                pending.append(child)
        return paths

    def _check_live_item(self, item: DriveItem) -> None:
        if not self.location.contains(item.drive_id):
            _refuse(item.id, "wrong_location", "Item belongs to a different Drive location")
        if item.trashed:
            _refuse(item.id, "trashed", "Item is trashed")
        if item.mime_type == SHORTCUT_MIME_TYPE:
            _refuse(item.id, "shortcut", "Drive shortcuts are not supported")

    def _is_corpus_folder(self, item: DriveItem) -> bool:
        return (
            item.mime_type == FOLDER_MIME_TYPE
            and self.location.contains(item.drive_id)
            and not item.trashed
        )

    def _is_direct_safe_child(self, item: DriveItem, folder_id: str) -> bool:
        return (
            self.location.contains(item.drive_id)
            and item.parents == (folder_id,)
            and not item.trashed
            and item.mime_type != SHORTCUT_MIME_TYPE
        )

    def _is_safe_search_result(self, item: DriveItem, folder_paths: dict[str, str]) -> bool:
        return (
            self.location.contains(item.drive_id)
            and len(item.parents) == 1
            and item.parents[0] in folder_paths
            and not item.trashed
            and item.mime_type not in {FOLDER_MIME_TYPE, SHORTCUT_MIME_TYPE}
        )


def _interleave_by_rank(
    batches: list[list[tuple[DriveItem, str]]],
) -> list[tuple[DriveItem, str]]:
    """Merge per-batch relevance lists by position; the first sighting of an ID wins.

    With one batch this is the identity. With several it is the only merge
    available: Drive hands back ranks, not scores, so the n-th hit of one batch
    is treated as a peer of the n-th hit of every other.
    """

    merged: list[tuple[DriveItem, str]] = []
    seen: set[str] = set()
    for position in range(max((len(batch) for batch in batches), default=0)):
        for batch in batches:
            if position >= len(batch):
                continue
            item, relative_path = batch[position]
            if item.id in seen:
                continue
            seen.add(item.id)
            merged.append((item, relative_path))
    return merged


def _refuse(item_id: str, reason: str, message: str) -> NoReturn:
    """Record one refusal and raise it.

    WARNING because in normal use this count is zero: search only ever names
    items it found inside the subtree, and a listing only ever names children.
    A refusal means something reached for an ID it was not given — worth
    seeing, and worth a metric.
    """

    audit.warning(
        "Refused a Drive item outside the authorized subtree",
        extra={
            "decision": "refuse",
            "reason": reason,
            "file_id": item_id,
            "caller": audit_caller.get(),
        },
    )
    raise ScopeViolation(message)


def _allow(item: DriveItem, relative_path: str) -> tuple[DriveItem, str]:
    audit.info(
        "Authorized a Drive item",
        extra={
            "decision": "allow",
            "reason": "ancestry_proven",
            "file_id": item.id,
            "caller": audit_caller.get(),
        },
    )
    return item, relative_path


def _to_scoped_item(item: DriveItem, relative_path: str) -> ScopedItem:
    return ScopedItem(
        id=item.id,
        name=item.name,
        mime_type=item.mime_type,
        relative_path=relative_path,
        modified_time=item.modified_time,
        size=item.size,
        web_view_link=item.web_view_link,
        owners=item.owners,
        last_modified_by=item.last_modified_by,
    )
