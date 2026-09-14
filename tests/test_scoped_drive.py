from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

import pytest

from gdrive_scoped.drive import SHORTCUT_MIME_TYPE, DriveItem, SearchPage
from gdrive_scoped.errors import ScopeViolation
from gdrive_scoped.location import DriveKind, DriveLocation
from gdrive_scoped.scope import ScopedDrive, audit_caller

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SHARED_DRIVE = DriveLocation(DriveKind.SHARED_DRIVE, "drive-1")
MY_DRIVE = DriveLocation(DriveKind.MY_DRIVE)


class FakeClock:
    """A clock the test moves by hand, so a TTL can be crossed without sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _corpus_with_a_nested_folder() -> tuple[MemoryGateway, DriveItem, DriveItem]:
    """A drive holding `root-1/Plans/Budget.md` and an unrelated folder beside the root."""

    drive_root = DriveItem("drive-1", "Shared drive", FOLDER_MIME_TYPE, "drive-1")
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1", parents=("drive-1",))
    outside = DriveItem("outside-1", "Outside", FOLDER_MIME_TYPE, "drive-1", parents=("drive-1",))
    plans = DriveItem("plans-1", "Plans", FOLDER_MIME_TYPE, "drive-1", parents=("root-1",))
    budget = DriveItem("file-1", "Budget.md", "text/markdown", "drive-1", parents=("plans-1",))
    return MemoryGateway(drive_root, root, outside, plans, budget), plans, budget


class RootGateway:
    def __init__(self, root: DriveItem) -> None:
        self._root = root

    async def get_item(self, item_id: str) -> DriveItem:
        assert item_id == self._root.id
        return self._root

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        return []

    async def list_folders(self) -> list[DriveItem]:
        return [self._root]

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        # The same query without the keyword clause, which is what it is.
        return (await self.search_items(parent_ids, "")).items

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int = 1000
    ) -> SearchPage:
        return SearchPage(items=[])

    async def download_item(self, item_id: str) -> bytes:
        raise AssertionError("Root validation must not download content")

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        raise AssertionError("Root validation must not export content")


class MemoryGateway:
    def __init__(self, *items: DriveItem) -> None:
        self.items = {item.id: item for item in items}
        self.folder_enumerations = 0

    async def get_item(self, item_id: str) -> DriveItem:
        return self.items[item_id]

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        return [item for item in self.items.values() if item.parents == (folder_id,)]

    async def list_folders(self) -> list[DriveItem]:
        self.folder_enumerations += 1
        return [item for item in self.items.values() if item.mime_type == FOLDER_MIME_TYPE]

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        # The same query without the keyword clause, which is what it is.
        return (await self.search_items(parent_ids, "")).items

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int = 1000
    ) -> SearchPage:
        # Answer the way Drive does: only items whose direct parent was named. A fake
        # that ignores `parent_ids` cannot fail a scoping test, which is the point of it.
        return SearchPage(
            items=[
                item
                for item in self.items.values()
                if item.parents
                and item.parents[0] in parent_ids
                and item.mime_type != FOLDER_MIME_TYPE
            ]
        )

    async def download_item(self, item_id: str) -> bytes:
        return b"content"

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        return b"content"


@pytest.mark.anyio
async def test_initialization_accepts_a_live_folder_in_the_configured_drive() -> None:
    root = DriveItem(
        id="root-1",
        name="Agent corpus",
        mime_type=FOLDER_MIME_TYPE,
        drive_id="drive-1",
    )
    scoped_drive = ScopedDrive(
        gateway=RootGateway(root),
        location=SHARED_DRIVE,
        root_folder_id="root-1",
    )

    initialized_root = await scoped_drive.initialize()

    assert initialized_root == root


@pytest.mark.anyio
async def test_initialization_accepts_a_live_folder_in_my_drive() -> None:
    root = DriveItem(
        id="root-1",
        name="Agent corpus",
        mime_type=FOLDER_MIME_TYPE,
        drive_id=None,
    )
    scoped_drive = ScopedDrive(
        gateway=RootGateway(root),
        location=MY_DRIVE,
        root_folder_id="root-1",
    )

    initialized_root = await scoped_drive.initialize()

    assert initialized_root == root


@pytest.mark.anyio
async def test_initialization_rejects_a_shared_drive_folder_in_my_drive_mode() -> None:
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    scoped_drive = ScopedDrive(RootGateway(root), MY_DRIVE, "root-1")

    with pytest.raises(ScopeViolation, match="Drive location"):
        await scoped_drive.initialize()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("root", "message"),
    [
        (
            DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-2"),
            "Drive location",
        ),
        (DriveItem("root-1", "Agent corpus", "text/plain", "drive-1"), "not a folder"),
        (
            DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1", trashed=True),
            "trashed",
        ),
    ],
)
async def test_initialization_rejects_an_invalid_root(root: DriveItem, message: str) -> None:
    scoped_drive = ScopedDrive(
        gateway=RootGateway(root),
        location=SHARED_DRIVE,
        root_folder_id="root-1",
    )

    with pytest.raises(ScopeViolation, match=message):
        await scoped_drive.initialize()


@pytest.mark.anyio
async def test_a_listed_item_can_be_looked_up_again_by_its_drive_id() -> None:
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    report = DriveItem(
        "file-1",
        "Quarterly report.md",
        "text/markdown",
        "drive-1",
        parents=("root-1",),
    )
    scoped_drive = ScopedDrive(
        gateway=MemoryGateway(root, report),
        location=SHARED_DRIVE,
        root_folder_id="root-1",
    )

    listed = await scoped_drive.list_folder()
    metadata = await scoped_drive.get_metadata(listed[0].id)

    assert listed == [metadata]
    assert metadata.name == "Quarterly report.md"
    assert metadata.relative_path == "Quarterly report.md"
    assert metadata.id == report.id


@pytest.mark.anyio
async def test_a_raw_id_outside_the_subtree_is_refused() -> None:
    """The boundary is the live ancestry proof, not the shape of the handle.

    An ID is readable to anyone who can see a `webViewLink`, so it was never
    the secret; what stops a caller reaching outside the corpus is that the
    parent chain is checked against the configured root on every call.
    """
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    outsider = DriveItem("file-9", "Elsewhere.md", "text/markdown", "drive-1", parents=("other",))
    scoped_drive = ScopedDrive(
        gateway=MemoryGateway(root, outsider),
        location=SHARED_DRIVE,
        root_folder_id="root-1",
    )

    with pytest.raises(ScopeViolation, match="outside the configured root"):
        await scoped_drive.get_metadata("file-9")


@pytest.mark.anyio
async def test_a_file_shared_directly_with_the_bot_user_is_refused() -> None:
    """Anyone in the organisation can share a file with the bot user, and it
    lands in the identity's corpus unbidden. Such an item has no parent inside
    the subtree, so ancestry cannot be proven and it is refused."""
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    gifted = DriveItem("file-9", "Unsolicited.md", "text/markdown", "drive-1", parents=())
    scoped_drive = ScopedDrive(
        gateway=MemoryGateway(root, gifted),
        location=SHARED_DRIVE,
        root_folder_id="root-1",
    )

    with pytest.raises(ScopeViolation, match="ancestry cannot be proven"):
        await scoped_drive.get_metadata("file-9")


@pytest.mark.anyio
async def test_access_is_lost_after_an_item_moves_outside_the_root() -> None:
    drive_root = DriveItem("drive-1", "Shared drive", FOLDER_MIME_TYPE, "drive-1")
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1", parents=("drive-1",))
    outside = DriveItem("outside-1", "Outside", FOLDER_MIME_TYPE, "drive-1", parents=("drive-1",))
    report = DriveItem("file-1", "Report.md", "text/markdown", "drive-1", parents=("root-1",))
    gateway = MemoryGateway(drive_root, root, outside, report)
    scoped_drive = ScopedDrive(gateway, SHARED_DRIVE, "root-1")
    [listed] = await scoped_drive.list_folder()
    gateway.items[report.id] = replace(report, parents=(outside.id,))

    with pytest.raises(ScopeViolation, match="outside the configured root"):
        await scoped_drive.get_metadata(listed.id)


@pytest.mark.anyio
async def test_content_fetch_honors_drive_download_restrictions() -> None:
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    restricted = DriveItem(
        "file-1",
        "Restricted.pdf",
        "application/pdf",
        "drive-1",
        parents=("root-1",),
        can_download=False,
    )
    scoped_drive = ScopedDrive(
        gateway=MemoryGateway(root, restricted),
        location=SHARED_DRIVE,
        root_folder_id="root-1",
    )
    [listed] = await scoped_drive.list_folder()

    with pytest.raises(ScopeViolation, match="does not permit downloading"):
        await scoped_drive.authorize_document(listed.id)


@pytest.mark.anyio
async def test_shortcuts_are_never_returned_from_an_authorized_folder() -> None:
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    shortcut = DriveItem(
        "shortcut-1", "Outside link", SHORTCUT_MIME_TYPE, "drive-1", parents=("root-1",)
    )
    scoped_drive = ScopedDrive(
        gateway=MemoryGateway(root, shortcut),
        location=SHARED_DRIVE,
        root_folder_id="root-1",
    )

    assert await scoped_drive.list_folder() == []


@pytest.mark.anyio
async def test_my_drive_listing_drops_a_cross_location_child() -> None:
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, None)
    report = DriveItem("file-1", "Report.md", "text/markdown", None, parents=("root-1",))
    cross_location = DriveItem(
        "file-2", "Shared report.md", "text/markdown", "drive-1", parents=("root-1",)
    )
    scoped_drive = ScopedDrive(MemoryGateway(root, report, cross_location), MY_DRIVE, "root-1")

    listed = await scoped_drive.list_folder()

    assert [item.name for item in listed] == ["Report.md"]


@pytest.mark.anyio
async def test_my_drive_access_is_lost_if_an_item_changes_location() -> None:
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, None)
    report = DriveItem("file-1", "Report.md", "text/markdown", None, parents=("root-1",))
    gateway = MemoryGateway(root, report)
    scoped_drive = ScopedDrive(gateway, MY_DRIVE, "root-1")
    [listed] = await scoped_drive.list_folder()
    gateway.items[report.id] = replace(report, drive_id="drive-1")

    with pytest.raises(ScopeViolation, match="different Drive location"):
        await scoped_drive.get_metadata(listed.id)


@pytest.mark.anyio
async def test_search_stops_returning_files_from_a_folder_that_left_the_subtree() -> None:
    gateway, plans, _ = _corpus_with_a_nested_folder()
    scoped_drive = ScopedDrive(gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=0.0)
    found = await scoped_drive.search("budget", limit=10)
    assert [item.name for item in found.items] == ["Budget.md"]

    gateway.items[plans.id] = replace(plans, parents=("outside-1",))

    assert (await scoped_drive.search("budget", limit=10)).items == []


@pytest.mark.anyio
async def test_search_never_names_a_file_outside_the_subtree() -> None:
    gateway, _, _ = _corpus_with_a_nested_folder()
    gateway.items["file-2"] = DriveItem(
        "file-2", "Secret budget.md", "text/markdown", "drive-1", parents=("outside-1",)
    )
    scoped_drive = ScopedDrive(gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=0.0)

    results = await scoped_drive.search("budget", limit=10)

    assert [item.name for item in results.items] == ["Budget.md"]


@pytest.mark.anyio
async def test_a_file_that_moves_out_is_refused_even_while_the_folder_map_is_warm() -> None:
    gateway, _, budget = _corpus_with_a_nested_folder()
    scoped_drive = ScopedDrive(
        gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=600.0, clock=FakeClock()
    )
    [found] = (await scoped_drive.search("budget", limit=10)).items
    assert (await scoped_drive.get_metadata(found.id)).name == "Budget.md"

    gateway.items[budget.id] = replace(budget, parents=("outside-1",))

    with pytest.raises(ScopeViolation, match="outside the configured root"):
        await scoped_drive.get_metadata(found.id)


@pytest.mark.anyio
async def test_a_trashed_file_is_refused_even_while_the_folder_map_is_warm() -> None:
    gateway, _, budget = _corpus_with_a_nested_folder()
    scoped_drive = ScopedDrive(
        gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=600.0, clock=FakeClock()
    )
    [found] = (await scoped_drive.search("budget", limit=10)).items
    assert (await scoped_drive.get_metadata(found.id)).name == "Budget.md"

    gateway.items[budget.id] = replace(budget, trashed=True)

    with pytest.raises(ScopeViolation, match="trashed"):
        await scoped_drive.get_metadata(found.id)


@pytest.mark.anyio
async def test_a_folder_that_leaves_the_subtree_keeps_serving_until_the_map_expires() -> None:
    """The whole staleness window, stated as a test.

    This is the exposure recorded in docs/adr/0005: a *folder* re-parenting is the one
    change the live item read cannot catch, because the file itself did not move. Every
    other way out of the subtree is refused immediately - see the two tests above.
    """

    clock = FakeClock()
    gateway, plans, _ = _corpus_with_a_nested_folder()
    scoped_drive = ScopedDrive(
        gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=60.0, clock=clock
    )
    [found] = (await scoped_drive.search("budget", limit=10)).items

    gateway.items[plans.id] = replace(plans, parents=("outside-1",))

    assert (await scoped_drive.get_metadata(found.id)).name == "Budget.md"

    clock.now += 60.0
    with pytest.raises(ScopeViolation, match="outside the configured root"):
        await scoped_drive.get_metadata(found.id)


@pytest.mark.anyio
async def test_a_zero_ttl_reads_the_folder_map_again_for_every_request() -> None:
    gateway, _, _ = _corpus_with_a_nested_folder()
    scoped_drive = ScopedDrive(gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=0.0)

    await scoped_drive.search("budget", limit=10)
    await scoped_drive.search("budget", limit=10)

    assert gateway.folder_enumerations == 2


@pytest.mark.anyio
async def test_a_warm_folder_map_is_reused_until_its_ttl_expires() -> None:
    clock = FakeClock()
    gateway, _, _ = _corpus_with_a_nested_folder()
    scoped_drive = ScopedDrive(
        gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=60.0, clock=clock
    )

    await scoped_drive.search("budget", limit=10)
    clock.now += 59.0
    await scoped_drive.search("budget", limit=10)
    assert gateway.folder_enumerations == 1

    clock.now += 1.0
    await scoped_drive.search("budget", limit=10)
    assert gateway.folder_enumerations == 2


@pytest.mark.anyio
async def test_a_folder_added_after_the_map_was_built_is_not_a_permanent_denial() -> None:
    clock = FakeClock()
    gateway, _, budget = _corpus_with_a_nested_folder()
    scoped_drive = ScopedDrive(
        gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=600.0, clock=clock
    )
    [found] = (await scoped_drive.search("budget", limit=10)).items

    gateway.items["later-1"] = DriveItem(
        "later-1", "Later", FOLDER_MIME_TYPE, "drive-1", parents=("root-1",)
    )
    gateway.items[budget.id] = replace(budget, parents=("later-1",))

    metadata = await scoped_drive.get_metadata(found.id)

    assert metadata.relative_path == "Later/Budget.md"


@pytest.mark.anyio
async def test_a_folder_with_two_parents_is_not_descended_into() -> None:
    drive_root = DriveItem("drive-1", "Shared drive", FOLDER_MIME_TYPE, "drive-1")
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1", parents=("drive-1",))
    outside = DriveItem("outside-1", "Outside", FOLDER_MIME_TYPE, "drive-1", parents=("drive-1",))
    # Reachable from inside the corpus and from outside it. Following `parents` without
    # insisting on exactly one would admit the folder, and every file underneath it.
    shared = DriveItem(
        "shared-1", "Shared", FOLDER_MIME_TYPE, "drive-1", parents=("root-1", "outside-1")
    )
    budget = DriveItem("file-1", "Budget.md", "text/markdown", "drive-1", parents=("shared-1",))
    gateway = MemoryGateway(drive_root, root, outside, shared, budget)
    scoped_drive = ScopedDrive(gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=0.0)

    assert (await scoped_drive.search("budget", limit=10)).items == []


@pytest.mark.anyio
async def test_concurrent_misses_enumerate_the_folder_map_once() -> None:
    """A cold server instance takes several requests at once, and each one
    misses the map. Without the lock every one of them enumerates the whole
    Drive location — the most expensive call there is, multiplied by the
    concurrency, at exactly the moment the instance is least able to afford it.
    """
    gateway, _, _ = _corpus_with_a_nested_folder()
    scoped_drive = ScopedDrive(gateway, SHARED_DRIVE, "root-1", folder_map_ttl_seconds=60.0)

    await asyncio.gather(*(scoped_drive.search("budget", limit=10) for _ in range(4)))

    assert gateway.folder_enumerations == 1


@pytest.mark.anyio
async def test_a_refusal_is_recorded_on_the_audit_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """In normal use this count is zero — search only names what it found
    inside the subtree. A refusal means something reached for an ID it was not
    given, which is worth a metric, so the fields have to be structured."""
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    outsider = DriveItem("file-9", "Elsewhere.md", "text/markdown", "drive-1", parents=("other",))
    scoped_drive = ScopedDrive(MemoryGateway(root, outsider), SHARED_DRIVE, "root-1")

    with (
        caplog.at_level(logging.WARNING, logger="gdrive_scoped.audit"),
        pytest.raises(ScopeViolation),
    ):
        await scoped_drive.get_metadata("file-9")

    (record,) = [entry for entry in caplog.records if entry.name == "gdrive_scoped.audit"]
    assert record.levelno == logging.WARNING
    assert getattr(record, "decision", None) == "refuse"
    assert getattr(record, "reason", None) == "outside_subtree"
    assert getattr(record, "file_id", None) == "file-9"


@pytest.mark.anyio
async def test_an_allowed_item_is_recorded_too(caplog: pytest.LogCaptureFixture) -> None:
    """Refusals alone cannot be read as a rate — a quiet log is either a clean
    corpus or a broken audit trail, and the allow records tell them apart."""
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    report = DriveItem("file-1", "Report.md", "text/markdown", "drive-1", parents=("root-1",))
    scoped_drive = ScopedDrive(MemoryGateway(root, report), SHARED_DRIVE, "root-1")

    with caplog.at_level(logging.INFO, logger="gdrive_scoped.audit"):
        await scoped_drive.get_metadata("file-1")

    (record,) = [entry for entry in caplog.records if entry.name == "gdrive_scoped.audit"]
    assert getattr(record, "decision", None) == "allow"
    assert getattr(record, "file_id", None) == "file-1"


@pytest.mark.anyio
async def test_metadata_carries_the_people_behind_an_item() -> None:
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    report = DriveItem(
        "file-1",
        "Report.md",
        "text/markdown",
        "drive-1",
        parents=("root-1",),
        owners=("Ada Lovelace",),
        last_modified_by="Grace Hopper",
    )
    scoped_drive = ScopedDrive(MemoryGateway(root, report), SHARED_DRIVE, "root-1")

    item = await scoped_drive.get_metadata("file-1")

    assert item.owners == ("Ada Lovelace",)
    assert item.last_modified_by == "Grace Hopper"


@pytest.mark.anyio
async def test_a_decision_names_the_caller_when_the_adapter_says_who(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The library authenticates nobody, so the adapter that does sets the
    caller for the span of a request and every record carries it."""
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")
    report = DriveItem("file-1", "Report.md", "text/markdown", "drive-1", parents=("root-1",))
    scoped_drive = ScopedDrive(MemoryGateway(root, report), SHARED_DRIVE, "root-1")

    token = audit_caller.set("alice@example.org")
    try:
        with caplog.at_level(logging.INFO, logger="gdrive_scoped.audit"):
            await scoped_drive.get_metadata("file-1")
    finally:
        audit_caller.reset(token)

    (record,) = [entry for entry in caplog.records if entry.name == "gdrive_scoped.audit"]
    assert getattr(record, "caller", None) == "alice@example.org"


@pytest.mark.parametrize("ttl", [float("inf"), float("nan"), -1.0])
def test_the_folder_map_window_must_be_finite_and_not_negative(ttl: float) -> None:
    """Constructed directly, `inf` slipped past the environment wrapper's check:
    the map never refreshed, and a folder moved out of the corpus kept serving
    its files for the life of the process."""
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")

    with pytest.raises(ValueError, match="finite and not negative"):
        ScopedDrive(RootGateway(root), SHARED_DRIVE, "root-1", folder_map_ttl_seconds=ttl)


def test_the_root_alias_is_refused_before_drive_can_resolve_it() -> None:
    """Drive resolves `root` to the whole of a My Drive. Only `initialize()`
    noticed, and it is optional: the root read keyed the folder map on the real
    ID and every listing, search and read succeeded against the entire Drive."""
    root = DriveItem("root-1", "Agent corpus", FOLDER_MIME_TYPE, "drive-1")

    with pytest.raises(ValueError, match="not the alias 'root'"):
        ScopedDrive(RootGateway(root), MY_DRIVE, "root")


class ResolvingGateway(RootGateway):
    """Answers a root read with the same item whatever ID was asked for, which
    is what Drive does with an alias."""

    async def get_item(self, item_id: str) -> DriveItem:
        return self._root


@pytest.mark.anyio
async def test_a_root_read_answered_with_a_different_item_is_refused() -> None:
    real_root = DriveItem("real-root", "Everything", FOLDER_MIME_TYPE, "drive-1")
    scoped_drive = ScopedDrive(ResolvingGateway(real_root), SHARED_DRIVE, "some-alias")

    with pytest.raises(ScopeViolation, match="unexpected root metadata"):
        await scoped_drive.list_folder()
    with pytest.raises(ScopeViolation, match="unexpected root metadata"):
        await scoped_drive.search("anything", limit=5)


@pytest.mark.anyio
async def test_a_proof_is_only_good_for_the_scope_that_issued_it() -> None:
    """Two scopes over the same corpus: a proof from one is not a proof for the
    other, so one cannot be carried across roots or assembled by hand."""
    gateway, _, budget = _corpus_with_a_nested_folder()
    issuing = ScopedDrive(gateway, SHARED_DRIVE, "root-1")
    other = ScopedDrive(gateway, SHARED_DRIVE, "root-1")
    authorized = await issuing.authorize_document(budget.id)

    assert await issuing.download(authorized, None) == b"content"
    with pytest.raises(ScopeViolation, match="not issued by this scope"):
        await other.download(authorized, None)
