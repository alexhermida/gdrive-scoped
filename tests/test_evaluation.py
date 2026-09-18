from __future__ import annotations

from pathlib import Path

import pytest

from gdrive_scoped.bench.evaluation import (
    EvaluationCase,
    derive_cases,
    evaluate_retrieval,
    load_cases,
    save_cases,
)
from gdrive_scoped.documents import DocumentService
from gdrive_scoped.drive import FOLDER_MIME_TYPE, DriveGateway, DriveItem, SearchPage
from gdrive_scoped.scope import ScopedDrive


async def _scope(gateway: DriveGateway) -> ScopedDrive:
    """A scope over `root-folder` that has measured its Drive location."""

    scoped_drive = ScopedDrive(gateway, "root-folder")
    await scoped_drive.initialize()
    return scoped_drive


class EvaluationGateway:
    def __init__(self) -> None:
        self.root = DriveItem("root-folder", "Corpus", FOLDER_MIME_TYPE, "drive")
        self.source = DriveItem(
            "source", "Timeline.md", "text/markdown", "drive", parents=("root-folder",)
        )

    async def get_item(self, item_id: str) -> DriveItem:
        return {"root-folder": self.root, "source": self.source}[item_id]

    async def list_folders(self) -> list[DriveItem]:
        return [self.root]

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        return [self.source] if folder_id == "root-folder" else []

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        # The same query without the keyword clause, which is what it is.
        return (await self.search_items(parent_ids, "")).items

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int = 1000
    ) -> SearchPage:
        return SearchPage(items=[self.source])

    async def download_item(self, item_id: str) -> bytes:
        return b"timeline"

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        return b"timeline"


def test_evaluation_cases_load_from_a_small_json_file(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text(
        '[{"question":"When?","search_query":"timeline","expected_source":"Timeline.md"}]',
        encoding="utf-8",
    )

    cases = load_cases(path)

    assert cases == [
        EvaluationCase(question="When?", search_query="timeline", expected_source="Timeline.md")
    ]


@pytest.mark.anyio
async def test_evaluation_reports_source_discovery_without_grading_answers() -> None:
    service = DocumentService(await _scope(EvaluationGateway()))
    cases = [
        EvaluationCase(question="When?", search_query="timeline", expected_source="timeline.md"),
        EvaluationCase(question="Where?", search_query="timeline", expected_source="missing.pdf"),
    ]

    outcomes = await evaluate_retrieval(service, cases)

    assert [outcome.passed for outcome in outcomes] == [True, False]
    assert outcomes[0].returned_sources == ["Timeline.md"]


class DerivationGateway:
    """A corpus of several types, one of them invisible to search.

    The sizes are deliberately out of type order: the largest item overall is
    the one no extractor claims, and the largest readable one is the PDF that
    search never returns. A derivation that skipped either check picks one of
    those two and measures a case that reads nothing.
    """

    def __init__(self) -> None:
        self.root = DriveItem("root-folder", "Corpus", FOLDER_MIME_TYPE, "drive")
        self.folder = DriveItem(
            "folder", "Plans", FOLDER_MIME_TYPE, "drive", parents=("root-folder",)
        )
        self.documents = [
            DriveItem(
                "archive", "Backups.zip", "application/zip", "drive", ("root-folder",), size=9_000
            ),
            DriveItem(
                "appendix", "Appendix.pdf", "application/pdf", "drive", ("root-folder",), size=5_000
            ),
            DriveItem("timeline", "Timeline.md", "text/markdown", "drive", ("folder",), size=900),
            DriveItem("roadmap", "Roadmap.md", "text/markdown", "drive", ("folder",), size=100),
            DriveItem("budget", "Budget.csv", "text/csv", "drive", ("root-folder",), size=500),
        ]

    async def get_item(self, item_id: str) -> DriveItem:
        folders = {"root-folder": self.root, "folder": self.folder}
        return folders.get(item_id) or next(
            document for document in self.documents if document.id == item_id
        )

    async def list_folders(self) -> list[DriveItem]:
        return [self.root, self.folder]

    async def list_children(self, folder_id: str) -> list[DriveItem]:
        return [document for document in self.documents if document.parents == (folder_id,)]

    async def list_descendants(self, parent_ids: tuple[str, ...]) -> list[DriveItem]:
        return [document for document in self.documents if document.parents[0] in set(parent_ids)]

    async def search_items(
        self, parent_ids: tuple[str, ...], keywords: str, limit: int = 1000
    ) -> SearchPage:
        if keywords == "Appendix":
            return SearchPage(items=[])
        return SearchPage(
            items=[
                document
                for document in self.documents
                if keywords.casefold() in document.name.casefold()
                and document.parents[0] in set(parent_ids)
            ]
        )

    async def download_item(self, item_id: str) -> bytes:
        return b"content"

    async def export_item(self, item_id: str, mime_type: str) -> bytes:
        return b"content"


async def _derivation_drive() -> ScopedDrive:
    return await _scope(DerivationGateway())


@pytest.mark.anyio
async def test_derived_cases_take_the_largest_readable_document_of_each_type() -> None:
    cases = await derive_cases(await _derivation_drive())

    assert [case.expected_source for case in cases] == ["Plans/Timeline.md", "Budget.csv"]
    assert [case.search_query for case in cases] == ["Timeline", "Budget"]


class UnreadableLargestGateway(DerivationGateway):
    """The largest Markdown file is a scan: its type is supported, its text is empty."""

    async def download_item(self, item_id: str) -> bytes:
        return b"" if item_id == "timeline" else b"content"


@pytest.mark.anyio
async def test_derived_cases_skip_a_document_that_does_not_read() -> None:
    """`supports()` vouches for the MIME type only. A case chosen for its size
    alone aborted the benchmark at its first read, so the read is now part of
    the verification and the next largest of the type is tried."""
    scoped_drive = await _scope(UnreadableLargestGateway())

    cases = await derive_cases(scoped_drive)

    assert [case.expected_source for case in cases] == ["Plans/Roadmap.md", "Budget.csv"]


@pytest.mark.anyio
async def test_derived_cases_stop_at_the_requested_count() -> None:
    cases = await derive_cases(await _derivation_drive(), wanted=1)

    assert [case.expected_source for case in cases] == ["Plans/Timeline.md"]


@pytest.mark.anyio
async def test_derived_cases_refuse_a_count_no_evaluation_file_could_hold() -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        await derive_cases(await _derivation_drive(), wanted=11)


@pytest.mark.anyio
async def test_derived_cases_round_trip_through_the_file_load_cases_reads(tmp_path: Path) -> None:
    cases = await derive_cases(await _derivation_drive())
    path = tmp_path / "derived" / "cases.json"

    save_cases(cases, path)

    assert load_cases(path) == cases
