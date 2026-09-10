from __future__ import annotations

import pytest

from gdrive_scoped.census import PARENT_BATCH_SIZE, Census, format_census, take_census
from gdrive_scoped.drive import FOLDER_MIME_TYPE, DriveItem, SearchPage
from gdrive_scoped.location import DriveKind, DriveLocation
from gdrive_scoped.scope import ScopedDrive

SHARED_DRIVE = DriveLocation(DriveKind.SHARED_DRIVE, "drive-1")


class CountingGateway:
    """Only what a census asks for, and a record of anything it should not."""

    def __init__(self, *items: DriveItem) -> None:
        self.items = {item.id: item for item in items}
        self.downloads: list[str] = []

    async def get_item(self, item_id: str) -> DriveItem:
        return self.items[item_id]

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        return [item for item in self.items.values() if item.parents == (folder_id,)]

    async def list_folders(self) -> list[DriveItem]:
        return [item for item in self.items.values() if item.mime_type == FOLDER_MIME_TYPE]

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        return [
            item
            for item in self.items.values()
            if item.parents and item.parents[0] in parent_ids and item.mime_type != FOLDER_MIME_TYPE
        ]

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int = 1000
    ) -> SearchPage:
        return SearchPage(items=await self.list_descendants(parent_ids))

    async def download_item(self, item_id: str) -> bytes:
        self.downloads.append(item_id)
        return b""

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        self.downloads.append(item_id)
        return b""


def folder(identifier: str, name: str, parent: str | None = None) -> DriveItem:
    return DriveItem(
        identifier,
        name,
        FOLDER_MIME_TYPE,
        "drive-1",
        parents=() if parent is None else (parent,),
    )


def document(identifier: str, name: str, mime_type: str, parent: str) -> DriveItem:
    return DriveItem(identifier, name, mime_type, "drive-1", parents=(parent,))


def corpus() -> tuple[ScopedDrive, CountingGateway]:
    """`root/Plans/Archive` holding a readable pair and one unreadable image."""

    gateway = CountingGateway(
        folder("root", "Corpus"),
        folder("plans", "Plans", "root"),
        folder("archive", "Archive", "plans"),
        document("a", "Notes.md", "text/markdown", "root"),
        document("b", "Budget.md", "text/markdown", "plans"),
        document("c", "Scan.png", "image/png", "archive"),
    )
    return ScopedDrive(gateway, SHARED_DRIVE, "root"), gateway


@pytest.mark.anyio
async def test_the_census_counts_the_subtree_and_its_depth() -> None:
    census = await take_census(corpus()[0])

    assert census.folders == 3
    assert census.max_depth == 2
    assert census.files == 3


@pytest.mark.anyio
async def test_the_census_reports_what_no_extractor_can_read() -> None:
    """The only honest way to prioritise extractors: the formats a corpus
    actually holds are never the ones anybody guesses."""
    census = await take_census(corpus()[0])

    assert census.mime_counts == {"text/markdown": 2, "image/png": 1}
    assert census.unreadable_mime_counts == {"image/png": 1}
    assert census.readable_files == 2
    assert census.readable_share == pytest.approx(2 / 3)


@pytest.mark.anyio
async def test_the_census_never_reads_a_document() -> None:
    """Counting a corpus must stay cheap enough to run against production."""
    scoped_drive, gateway = corpus()

    await take_census(scoped_drive)

    assert gateway.downloads == []


@pytest.mark.parametrize(
    ("folders", "expected"),
    [(0, 0), (1, 1), (PARENT_BATCH_SIZE, 1), (PARENT_BATCH_SIZE + 1, 2)],
)
def test_search_batches_say_whether_relevance_is_comparable(folders: int, expected: int) -> None:
    """More than one batch means Drive's relevance order cannot be merged —
    there is no score to merge on — which is why search re-sorts by date."""
    census = Census(
        folders=folders, max_depth=0, files=0, mime_counts={}, unreadable_mime_counts={}
    )

    assert census.search_batches == expected


def test_an_empty_corpus_reports_a_share_rather_than_dividing_by_zero() -> None:
    census = Census(folders=1, max_depth=0, files=0, mime_counts={}, unreadable_mime_counts={})

    assert census.readable_share == 0.0


@pytest.mark.anyio
async def test_the_report_marks_the_unreadable_types() -> None:
    report = format_census(await take_census(corpus()[0]))

    assert "readable: 2/3 (67%)" in report
    assert "! " in report
    assert "image/png" in report
